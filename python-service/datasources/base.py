"""
数据源抽象基类和接口定义
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from enum import Enum


class DataSourceType(Enum):
    """数据源类型"""
    DUCKDB = "duckdb"
    API = "api"
    REDIS = "redis"
    MYSQL = "mysql"
    POSTGRES = "postgres"


class DataSourceStatus(Enum):
    """数据源状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class DataSource(ABC):
    """数据源抽象基类"""

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self._status = DataSourceStatus.UNKNOWN

    @property
    @abstractmethod
    def source_type(self) -> DataSourceType:
        """返回数据源类型"""
        pass

    @property
    def status(self) -> DataSourceStatus:
        """返回当前状态"""
        return self._status

    @abstractmethod
    async def initialize(self) -> None:
        """初始化数据源（连接池、缓存等）"""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查，返回 True/False"""
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

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name} type={self.source_type.value} status={self._status.value}>"
