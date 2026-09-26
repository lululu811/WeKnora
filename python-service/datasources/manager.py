"""
数据源管理器 — 统一管理生命周期和健康检查
"""

import asyncio
import logging
from typing import Dict, Optional
from .registry import registry
from .base import DataSourceStatus
from .cache import cache

logger = logging.getLogger(__name__)


class DataSourceManager:
    """
    数据源管理器

    职责：
    - 统一管理所有数据源的初始化和关闭
    - 后台定期健康检查
    - 自动摘除不健康的数据源
    """

    def __init__(self, health_check_interval: int = 30):
        self._health_check_interval = health_check_interval
        self._health_check_task: Optional[asyncio.Task] = None
        self._running = False

    async def startup(self) -> None:
        """应用启动时调用 — 初始化所有数据源并启动健康检查"""
        logger.info("正在初始化数据源...")
        try:
            await registry.initialize_all()
            logger.info(f"已初始化 {len(registry.list_sources())} 个数据源")
        except Exception as e:
            logger.error(f"数据源初始化失败：{e}")
            raise

        # 启动后台健康检查
        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info("数据源健康检查已启动")

    async def shutdown(self) -> None:
        """应用关闭时调用 — 关闭所有数据源"""
        self._running = False

        # 停止健康检查
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        # 关闭所有数据源
        logger.info("正在关闭数据源...")
        await registry.close_all()
        logger.info("所有数据源已关闭")

    async def _health_check_loop(self) -> None:
        """后台定期健康检查"""
        while self._running:
            try:
                await asyncio.sleep(self._health_check_interval)
                results = await registry.health_check_all()

                for name, healthy in results.items():
                    source = registry.get(name)
                    if source is None:
                        continue
                    if healthy:
                        if source.status != DataSourceStatus.HEALTHY:
                            logger.info(f"数据源 {name} 恢复健康")
                            source._status = DataSourceStatus.HEALTHY
                    else:
                        if source.status == DataSourceStatus.HEALTHY:
                            logger.warning(f"数据源 {name} 健康检查失败")
                            source._status = DataSourceStatus.UNHEALTHY

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康检查异常：{e}")

    def get_status(self) -> Dict[str, str]:
        """获取所有数据源状态"""
        return {
            name: source.status.value
            for name, source in registry.list_sources().items()
        }

    def get_healthy_sources(self) -> Dict[str, str]:
        """获取所有健康的数据源"""
        return {
            name: source.source_type.value
            for name, source in registry.list_sources().items()
            if source.status == DataSourceStatus.HEALTHY
        }

    def get_detailed_status(self) -> Dict[str, Dict[str, str]]:
        """带类型的详细状态，供 /health 排查用"""
        return {
            name: {"type": source.source_type.value, "status": source.status.value}
            for name, source in registry.list_sources().items()
        }

    async def refresh_health(self) -> Dict[str, bool]:
        """立刻跑一次健康检查。/health 每次调用都走这里，
        否则调用方看到的只是后台循环（最多 30 秒前）的快照。"""
        return await registry.health_check_all()


# 全局管理器实例
manager = DataSourceManager()
