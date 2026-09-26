"""
数据源注册表 — 全局单例，管理所有数据源的注册和获取
"""

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
                    cls._instance._sources: Dict[str, DataSource] = {}
                    cls._instance._initialized = False
        return cls._instance

    def register(self, source: DataSource) -> None:
        """注册数据源"""
        if source.name in self._sources:
            raise ValueError(f"数据源 '{source.name}' 已存在")
        self._sources[source.name] = source

    def unregister(self, name: str) -> None:
        """注销数据源"""
        self._sources.pop(name, None)

    def get(self, name: str) -> Optional[DataSource]:
        """获取数据源，不存在返回 None"""
        return self._sources.get(name)

    def get_or_raise(self, name: str) -> DataSource:
        """获取数据源，不存在抛出 KeyError"""
        source = self._sources.get(name)
        if source is None:
            raise KeyError(f"数据源 '{name}' 未注册")
        return source

    def list_sources(self) -> Dict[str, DataSource]:
        """列出所有数据源"""
        return dict(self._sources)

    def has(self, name: str) -> bool:
        """检查数据源是否已注册"""
        return name in self._sources

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
            try:
                results[name] = await source.health_check()
            except Exception:
                results[name] = False
        return results

    def reset(self) -> None:
        """重置注册表（仅用于测试）"""
        self._sources.clear()


# 全局单例
registry = DataSourceRegistry()
