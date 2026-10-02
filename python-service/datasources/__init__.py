"""
python-service 数据源管理层

提供统一的数据源抽象、注册、管理和缓存能力。

使用方式：
    from datasources import registry, manager, cache, config
    from datasources.duckdb_source import DuckDBSource

    # 注册数据源。库目录来自 DB_DIR（默认 ~/.hithink-finance），不要在代码里写死路径。
    registry.register(DuckDBSource("market", config.get_db_path("market")))

    # 启动管理器
    await manager.startup()

    # 使用数据源
    source = registry.get_or_raise("market")
    result = await source.execute("SELECT * FROM symbols LIMIT 10")
"""

from .base import DataSource, DataSourceType, DataSourceStatus
from .registry import registry, DataSourceRegistry
from .manager import manager, DataSourceManager
from .config import config, DataSourceConfig
from .cache import cache, MultiLevelCache, LRUCache
from .pool import thread_pool, AsyncThreadPool
from .duckdb_source import DuckDBSource
from .api_source import APISource
from .redis_source import RedisSource

__all__ = [
    # 基础类
    "DataSource",
    "DataSourceType",
    "DataSourceStatus",
    # 注册表
    "registry",
    "DataSourceRegistry",
    # 管理器
    "manager",
    "DataSourceManager",
    # 配置
    "config",
    "DataSourceConfig",
    # 缓存
    "cache",
    "MultiLevelCache",
    "LRUCache",
    # 线程池
    "thread_pool",
    "AsyncThreadPool",
    # 数据源实现
    "DuckDBSource",
    "APISource",
    "RedisSource",
]
