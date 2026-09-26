"""
数据源配置加载
"""

import os
import json
from typing import Any, Dict, Optional


class DataSourceConfig:
    """数据源配置"""

    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._load_from_env()

    def _load_from_env(self) -> None:
        """从环境变量加载配置"""
        # DuckDB 配置
        self._config["db_dir"] = os.getenv("DB_DIR", os.path.expanduser("~/.hithink-finance"))

        # Redis 配置
        #
        # host 默认为空 = 不启用。旧实现默认 "localhost"，于是每次启动都会去连
        # 一个并不存在的 Redis，/health 永远挂着一个 unhealthy 数据源 ——
        # 而 requirements 里写的"Redis（可选）"在实现上从来没成立过。
        self._config["redis"] = {
            "host": os.getenv("REDIS_HOST", ""),
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "db": int(os.getenv("REDIS_DB", "0")),
            "password": os.getenv("REDIS_PASSWORD"),
        }

        # API 配置
        self._config["api"] = {
            "quote_base_url": os.getenv("QUOTE_API_URL", ""),
            "quote_api_key": os.getenv("QUOTE_API_KEY", ""),
        }

        # 并发控制
        self._config["duckdb_max_concurrent"] = int(os.getenv("DUCKDB_MAX_CONCURRENT", "4"))
        self._config["api_max_connections"] = int(os.getenv("API_MAX_CONNECTIONS", "20"))
        self._config["api_rate_limit"] = int(os.getenv("API_RATE_LIMIT", "50"))
        self._config["thread_pool_workers"] = int(os.getenv("THREAD_POOL_WORKERS", "8"))

        # 缓存配置
        self._config["cache_memory_size"] = int(os.getenv("CACHE_MEMORY_SIZE", "1000"))
        self._config["cache_default_ttl"] = int(os.getenv("CACHE_DEFAULT_TTL", "300"))

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def get_db_path(self, db_name: str) -> str:
        """获取 DuckDB 文件路径"""
        db_dir = self._config["db_dir"]
        return os.path.join(db_dir, f"{db_name}.duckdb")

    def get_redis_config(self) -> Dict[str, Any]:
        return dict(self._config["redis"])

    def get_api_config(self) -> Dict[str, Any]:
        return dict(self._config["api"])

    @property
    def db_dir(self) -> str:
        return self._config["db_dir"]

    @property
    def duckdb_max_concurrent(self) -> int:
        return self._config["duckdb_max_concurrent"]

    @property
    def api_max_connections(self) -> int:
        return self._config["api_max_connections"]

    @property
    def api_rate_limit(self) -> int:
        return self._config["api_rate_limit"]

    @property
    def thread_pool_workers(self) -> int:
        return self._config["thread_pool_workers"]

    @property
    def cache_memory_size(self) -> int:
        return self._config["cache_memory_size"]

    @property
    def cache_default_ttl(self) -> int:
        return self._config["cache_default_ttl"]


# 全局配置实例
config = DataSourceConfig()
