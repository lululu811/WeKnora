# python-service 数据源管理层设计

## 一、问题背景

python-service 需要接入多种数据源：
- **DuckDB** — 本地 A 股行情、财务、基金、指标等（7 个 .duckdb 文件）
- **远程行情 API** — 实时行情推送（腾讯/新浪/东财等）
- **Redis** — 热点数据缓存
- **MySQL/PostgreSQL**（可选）— 外部业务数据

当前问题：
- 每次请求都创建/销毁 DuckDB 连接，无复用
- 无并发控制，多个重查询可能打满内存
- 数据源散落各处，无统一管理
- 无缓存层，重复查询浪费资源

---

## 二、架构设计

```
python-service/
├── datasources/
│   ├── __init__.py
│   ├── base.py           # DataSource 抽象基类 + 接口定义
│   ├── registry.py       # 数据源注册表（全局单例）
│   ├── manager.py        # 数据源管理器（生命周期 + 健康检查）
│   │
│   ├── duckdb_source.py  # DuckDB 数据源（连接复用 + 并发控制）
│   ├── api_source.py     # 远程 API 数据源（连接池 + 限流 + 重试）
│   ├── redis_source.py   # Redis 缓存源（连接池 + 序列化）
│   ├── mysql_source.py   # MySQL 数据源（连接池 + 事务）
│   │
│   ├── pool.py           # 通用线程池/异步任务管理
│   ├── cache.py          # 多级缓存（内存 LRU → Redis → 数据源）
│   └── config.py         # 数据源配置加载
│
├── routes/               # HTTP 路由（调用 datasources）
└── main.py
```

---

## 三、核心接口设计

### 3.1 DataSource 抽象基类

```python
# datasources/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from enum import Enum
import asyncio

class DataSourceType(Enum):
    DUCKDB = "duckdb"
    API = "api"
    REDIS = "redis"
    MYSQL = "mysql"
    POSTGRES = "postgres"

class DataSourceStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

class DataSource(ABC):
    """数据源抽象基类"""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self._status = DataSourceStatus.UNKNOWN
    
    @property
    def source_type(self) -> DataSourceType:
        raise NotImplementedError
    
    @property
    def status(self) -> DataSourceStatus:
        return self._status
    
    @abstractmethod
    async def initialize(self) -> None:
        """初始化数据源（连接池、缓存等）"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
    
    @abstractmethod
    async def execute(self, query: str, params: Optional[Dict] = None) -> Any:
        """执行查询"""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """关闭数据源，释放资源"""
        pass
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, *args):
        await self.close()
```

### 3.2 数据源注册表

```python
# datasources/registry.py
import threading
from typing import Dict, Optional
from .base import DataSource

class DataSourceRegistry:
    """数据源注册表（全局单例）"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._sources = {}
        return cls._instance
    
    def register(self, source: DataSource) -> None:
        """注册数据源"""
        if source.name in self._sources:
            raise ValueError(f"数据源 '{source.name}' 已存在")
        self._sources[source.name] = source
    
    def get(self, name: str) -> Optional[DataSource]:
        """获取数据源"""
        return self._sources.get(name)
    
    def list_sources(self) -> Dict[str, DataSource]:
        """列出所有数据源"""
        return dict(self._sources)
    
    async def initialize_all(self) -> None:
        """初始化所有数据源"""
        for source in self._sources.values():
            await source.initialize()
    
    async def close_all(self) -> None:
        """关闭所有数据源"""
        for source in self._sources.values():
            await source.close()
    
    async def health_check_all(self) -> Dict[str, bool]:
        """所有数据源健康检查"""
        results = {}
        for name, source in self._sources.items():
            results[name] = await source.health_check()
        return results


# 全局单例
registry = DataSourceRegistry()
```

### 3.3 DuckDB 数据源（连接复用 + 并发控制）

```python
# datasources/duckdb_source.py
import duckdb
import asyncio
from typing import Any, Dict, List, Optional
from .base import DataSource, DataSourceType, DataSourceStatus

class DuckDBSource(DataSource):
    """DuckDB 数据源 — 连接复用 + 并发控制"""
    
    def __init__(self, name: str, db_path: str, max_concurrent: int = 4):
        super().__init__(name, {"db_path": db_path})
        self.db_path = db_path
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
    
    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.DUCKDB
    
    async def initialize(self) -> None:
        """初始化 — 创建共享只读连接"""
        async with self._lock:
            if self._conn is None:
                # DuckDB 支持并发只读连接
                self._conn = duckdb.connect(self.db_path, read_only=True)
                self._status = DataSourceStatus.HEALTHY
    
    async def health_check(self) -> bool:
        try:
            if self._conn is None:
                return False
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            self._status = DataSourceStatus.UNHEALTHY
            return False
    
    async def execute(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """执行查询 — 带并发控制"""
        async with self._semaphore:
            if self._conn is None:
                await self.initialize()
            
            # 在线程池中执行 DuckDB 查询（避免阻塞事件循环）
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                self._execute_sync, 
                query, 
                params
            )
            return result
    
    def _execute_sync(self, query: str, params: Optional[Dict]) -> List[Dict]:
        """同步执行（在线程池中运行）"""
        cursor = self._conn.execute(query, params or {})
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return rows
    
    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
```

### 3.4 远程 API 数据源（连接池 + 限流）

```python
# datasources/api_source.py
import aiohttp
import asyncio
from typing import Any, Dict, Optional
from .base import DataSource, DataSourceType, DataSourceStatus

class APISource(DataSource):
    """远程 API 数据源 — 连接池 + 限流 + 重试"""
    
    def __init__(
        self, 
        name: str, 
        base_url: str,
        max_connections: int = 20,
        rate_limit: int = 50,  # 每秒请求数
        max_retries: int = 3
    ):
        super().__init__(name, {
            "base_url": base_url,
            "max_connections": max_connections,
        })
        self.base_url = base_url
        self.max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(rate_limit)
        self._lock = asyncio.Lock()
        self._connector = aiohttp.TCPConnector(
            limit=max_connections,
            ttl_dns_cache=300,
        )
    
    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.API
    
    async def initialize(self) -> None:
        async with self._lock:
            if self._session is None:
                self._session = aiohttp.ClientSession(
                    connector=self._connector,
                    timeout=aiohttp.ClientTimeout(total=30),
                )
                self._status = DataSourceStatus.HEALTHY
    
    async def health_check(self) -> bool:
        try:
            async with self._session.get(f"{self.base_url}/health") as resp:
                return resp.status == 200
        except Exception:
            self._status = DataSourceStatus.UNHEALTHY
            return False
    
    async def execute(self, query: str, params: Optional[Dict] = None) -> Any:
        """执行 API 请求 — query 作为路径，params 作为查询参数"""
        async with self._semaphore:
            for attempt in range(self.max_retries):
                try:
                    async with self._session.get(
                        f"{self.base_url}/{query}",
                        params=params
                    ) as resp:
                        resp.raise_for_status()
                        return await resp.json()
                except aiohttp.ClientError as e:
                    if attempt == self.max_retries - 1:
                        raise
                    await asyncio.sleep(2 ** attempt)  # 指数退避
    
    async def close(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None
```

### 3.5 通用线程池

```python
# datasources/pool.py
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any

class AsyncThreadPool:
    """通用异步线程池 — 用于 CPU 密集型和阻塞 I/O 任务"""
    
    def __init__(self, max_workers: int = 8):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
    
    async def submit(self, func: Callable, *args, **kwargs) -> Any:
        """提交任务到线程池"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: func(*args, **kwargs)
        )
    
    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)


# 全局线程池
thread_pool = AsyncThreadPool(max_workers=8)
```

### 3.6 多级缓存

```python
# datasources/cache.py
import asyncio
from functools import lru_cache
from typing import Any, Optional, Callable
import json
import hashlib

class MultiLevelCache:
    """多级缓存：内存 LRU → Redis → 数据源"""
    
    def __init__(self, redis_source=None, memory_size: int = 1000):
        self._redis = redis_source
        self._memory_cache = lru_cache(maxsize=memory_size)
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """多级缓存查询"""
        # Level 1: 内存缓存
        mem_result = self._memory_cache.get(key)
        if mem_result is not None:
            return mem_result
        
        # Level 2: Redis 缓存
        if self._redis:
            redis_result = await self._redis.execute("GET", key)
            if redis_result:
                data = json.loads(redis_result)
                self._memory_cache[key] = data  # 回填内存
                return data
        
        return None
    
    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """写入多级缓存"""
        self._memory_cache[key] = value
        
        if self._redis:
            await self._redis.execute("SETEX", key, ttl, json.dumps(value))
    
    async def delete(self, key: str) -> None:
        self._memory_cache.pop(key, None)
        if self._redis:
            await self._redis.execute("DEL", key)


# 全局缓存实例
cache = MultiLevelCache()
```

### 3.7 数据源管理器

```python
# datasources/manager.py
import asyncio
from typing import Dict
from .registry import registry
from .base import DataSourceStatus

class DataSourceManager:
    """数据源管理器 — 统一管理生命周期和健康检查"""
    
    def __init__(self):
        self._health_check_interval = 30  # 秒
    
    async def startup(self) -> None:
        """应用启动时调用"""
        await registry.initialize_all()
        # 启动后台健康检查
        asyncio.create_task(self._health_check_loop())
    
    async def shutdown(self) -> None:
        """应用关闭时调用"""
        await registry.close_all()
    
    async def _health_check_loop(self) -> None:
        """后台定期健康检查"""
        while True:
            await asyncio.sleep(self._health_check_interval)
            results = await registry.health_check_all()
            for name, healthy in results.items():
                source = registry.get(name)
                if healthy:
                    source._status = DataSourceStatus.HEALTHY
                else:
                    source._status = DataSourceStatus.UNHEALTHY
    
    def get_status(self) -> Dict[str, str]:
        """获取所有数据源状态"""
        return {
            name: source.status.value
            for name, source in registry.list_sources().items()
        }


manager = DataSourceManager()
```

---

## 四、FastAPI 集成

```python
# main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from datasources.manager import manager
from datasources.registry import registry
from datasources.duckdb_source import DuckDBSource
from datasources.redis_source import RedisSource
from datasources.api_source import APISource

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：注册并初始化数据源。库目录来自 DB_DIR，不要在代码里写死路径。
    registry.register(DuckDBSource("market", config.get_db_path("market")))
    registry.register(DuckDBSource("financials", config.get_db_path("financials")))
    registry.register(RedisSource("cache", host="localhost", port=6379))
    registry.register(APISource("quote_api", base_url="http://quote-api.example.com"))
    
    await manager.startup()
    yield
    # 关闭：释放所有数据源
    await manager.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"datasources": manager.get_status()}
```

---

## 五、总结

| 组件 | 作用 | 必要性 |
|---|---|---|
| **DataSourceRegistry** | 全局单例，统一管理所有数据源的注册和获取 | ✅ 必须 |
| **DataSourceManager** | 生命周期管理 + 后台健康检查 | ✅ 必须 |
| **DuckDBSource** | 连接复用 + 并发控制（信号量） | ✅ 必须 |
| **APISource** | 连接池 + 限流 + 重试 + 指数退避 | ✅ 必须 |
| **AsyncThreadPool** | CPU 密集型和阻塞 I/O 任务卸载 | ✅ 必须 |
| **MultiLevelCache** | 内存 LRU → Redis → 数据源 三级缓存 | ⚡ 强烈建议 |
| **RedisSource** | Redis 连接池 + 序列化 | ⚡ 推荐 |
| **MySQLSource** | 连接池 + 事务 | 按需 |

**关键设计原则：**
1. **数据源不可知** — 路由层只依赖 `DataSource` 接口，不关心底层是 DuckDB 还是 API
2. **连接复用** — 每个数据源维护自己的连接/会话，不在请求中反复创建销毁
3. **并发控制** — 通过 `asyncio.Semaphore` 限制每种数据源的并发访问数
4. **故障隔离** — 一个数据源异常不影响其他数据源，健康检查自动摘除不健康节点
5. **多级缓存** — 减少重复查询对数据源的压力
