"""
通用异步线程池 — 用于 CPU 密集型和阻塞 I/O 任务
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional


class AsyncThreadPool:
    """
    通用异步线程池

    将 CPU 密集型或阻塞 I/O 任务卸载到线程池执行，
    避免阻塞 asyncio 事件循环。
    """

    def __init__(self, max_workers: int = 8, thread_name_prefix: str = "pysvc"):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._max_workers = max_workers

    @property
    def max_workers(self) -> int:
        return self._max_workers

    async def submit(self, func: Callable, *args, **kwargs) -> Any:
        """
        提交任务到线程池执行

        Args:
            func: 要执行的函数（同步函数）
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果
        """
        loop = asyncio.get_event_loop()
        if kwargs:
            # 有 kwargs 时用 lambda 包装
            return await loop.run_in_executor(
                self._executor,
                lambda: func(*args, **kwargs)
            )
        return await loop.run_in_executor(self._executor, func, *args)

    def submit_sync(self, func: Callable, *args, **kwargs) -> Any:
        """
        同步提交（阻塞等待结果）— 用于非异步上下文

        Args:
            func: 要执行的函数
            *args: 函数参数

        Returns:
            函数执行结果
        """
        future = self._executor.submit(func, *args, **kwargs)
        return future.result()

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池"""
        self._executor.shutdown(wait=wait)


# 全局线程池实例
thread_pool = AsyncThreadPool(max_workers=8)
