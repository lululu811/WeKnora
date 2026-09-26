"""
Redis 数据源 — 连接池 + 序列化
"""

import json
import asyncio
from typing import Any, Dict, Optional
from .base import DataSource, DataSourceType, DataSourceStatus

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


class RedisSource(DataSource):
    """
    Redis 数据源

    特性：
    - 异步连接池
    - JSON 序列化/反序列化
    - 自动重连
    """

    def __init__(
        self,
        name: str,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        max_connections: int = 20,
        decode_responses: bool = True
    ):
        if not HAS_REDIS:
            raise ImportError("RedisSource 需要安装 redis: pip install redis")

        super().__init__(name, {
            "host": host,
            "port": port,
            "db": db,
            "max_connections": max_connections,
        })
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.max_connections = max_connections
        self.decode_responses = decode_responses

        self._pool: Optional[aioredis.ConnectionPool] = None
        self._client: Optional[aioredis.Redis] = None
        self._lock = asyncio.Lock()

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.REDIS

    async def initialize(self) -> None:
        """初始化连接池"""
        async with self._lock:
            if self._client is None:
                self._pool = aioredis.ConnectionPool(
                    host=self.host,
                    port=self.port,
                    db=self.db,
                    password=self.password,
                    max_connections=self.max_connections,
                    decode_responses=self.decode_responses,
                )
                self._client = aioredis.Redis(connection_pool=self._pool)
                self._status = DataSourceStatus.HEALTHY

    async def health_check(self) -> bool:
        """健康检查 — PING"""
        try:
            if self._client is None:
                return False
            result = await self._client.ping()
            self._status = DataSourceStatus.HEALTHY
            return result
        except Exception:
            self._status = DataSourceStatus.UNHEALTHY
            return False

    async def execute(self, command: str, *args) -> Any:
        """
        执行 Redis 命令

        Args:
            command: Redis 命令（GET/SET/DEL 等）
            *args: 命令参数

        Returns:
            命令执行结果
        """
        if self._client is None:
            await self.initialize()

        try:
            return await self._client.execute_command(command, *args)
        except Exception as e:
            self._status = DataSourceStatus.UNHEALTHY
            raise

    # ===== 快捷方法 =====

    async def get(self, key: str) -> Optional[str]:
        """获取字符串值"""
        return await self.execute("GET", key)

    async def get_json(self, key: str) -> Optional[Any]:
        """获取 JSON 值"""
        val = await self.get(key)
        if val is None:
            return None
        return json.loads(val)

    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """设置字符串值"""
        if ttl:
            return await self.execute("SETEX", key, ttl, value)
        return await self.execute("SET", key, value)

    async def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """设置 JSON 值"""
        return await self.set(key, json.dumps(value, ensure_ascii=False), ttl)

    async def delete(self, key: str) -> int:
        """删除键"""
        return await self.execute("DEL", key)

    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        return bool(await self.execute("EXISTS", key))

    async def expire(self, key: str, seconds: int) -> bool:
        """设置过期时间"""
        return bool(await self.execute("EXPIRE", key, seconds))

    async def ttl(self, key: str) -> int:
        """获取剩余 TTL"""
        return await self.execute("TTL", key)

    async def clear_prefix(self, prefix: str) -> int:
        """按前缀批量删除（SCAN + UNLINK）。

        `/cache/clear` 用它清掉"只清了内存层、下一读又被 Redis 灌回来"的问题。
        """
        if self._client is None:
            return 0
        deleted = 0
        cursor = 0
        while True:
            cursor, keys = await self._client.scan(
                cursor=cursor, match=f"{prefix}*", count=500
            )
            if keys:
                deleted += int(await self._client.unlink(*keys))
            if cursor == 0:
                break
        return deleted

    async def close(self) -> None:
        """关闭连接池"""
        async with self._lock:
            if self._client is not None:
                await self._client.close()
                self._client = None
            if self._pool is not None:
                await self._pool.disconnect()
                self._pool = None
            self._status = DataSourceStatus.UNKNOWN
