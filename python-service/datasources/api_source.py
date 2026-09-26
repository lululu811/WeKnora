"""
远程 API 数据源 — 连接池 + 限流 + 重试
"""

import asyncio
from typing import Any, Dict, Optional
from .base import DataSource, DataSourceType, DataSourceStatus

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class APISource(DataSource):
    """
    远程 API 数据源

    特性：
    - aiohttp 连接池
    - Semaphore 限流（控制每秒并发请求数）
    - 指数退避重试
    - 超时控制
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        max_connections: int = 20,
        rate_limit: int = 50,
        max_retries: int = 3,
        timeout: int = 30,
        headers: Optional[Dict[str, str]] = None
    ):
        if not HAS_AIOHTTP:
            raise ImportError("APISource 需要安装 aiohttp: pip install aiohttp")

        super().__init__(name, {
            "base_url": base_url,
            "max_connections": max_connections,
            "rate_limit": rate_limit,
            "max_retries": max_retries,
            "timeout": timeout,
        })
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self.headers = headers or {}

        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(rate_limit)
        self._lock = asyncio.Lock()
        self._connector: Optional[aiohttp.TCPConnector] = None

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.API

    async def initialize(self) -> None:
        """初始化 HTTP 会话和连接池"""
        async with self._lock:
            if self._session is None:
                self._connector = aiohttp.TCPConnector(
                    limit=self.config.get("max_connections", 20),
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True,
                )
                self._session = aiohttp.ClientSession(
                    connector=self._connector,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    headers=self.headers,
                )
                self._status = DataSourceStatus.HEALTHY

    async def health_check(self) -> bool:
        """健康检查 — 尝试访问 base_url"""
        try:
            if self._session is None:
                return False
            async with self._session.get(self.base_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                # 任何响应码都算健康（包括 404 等，只要服务器在响应）
                self._status = DataSourceStatus.HEALTHY
                return True
        except Exception:
            self._status = DataSourceStatus.UNHEALTHY
            return False

    async def execute(self, path: str, params: Optional[Dict] = None, method: str = "GET", json_data: Optional[Dict] = None) -> Any:
        """
        执行 API 请求

        Args:
            path: URL 路径（会拼接到 base_url 后）
            params: 查询参数
            method: HTTP 方法（GET/POST/PUT/DELETE）
            json_data: POST/PUT 请求体

        Returns:
            JSON 响应数据
        """
        async with self._semaphore:
            if self._session is None:
                await self.initialize()

            url = f"{self.base_url}/{path.lstrip('/')}"
            last_error = None

            for attempt in range(self.max_retries):
                try:
                    async with self._session.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_data
                    ) as resp:
                        resp.raise_for_status()
                        return await resp.json()
                except aiohttp.ClientError as e:
                    last_error = e
                    if attempt < self.max_retries - 1:
                        # 指数退避：1s, 2s, 4s
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise
                except asyncio.TimeoutError as e:
                    last_error = e
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise

            raise last_error

    async def get(self, path: str, params: Optional[Dict] = None) -> Any:
        """GET 请求快捷方法"""
        return await self.execute(path, params=params, method="GET")

    async def post(self, path: str, json_data: Optional[Dict] = None, params: Optional[Dict] = None) -> Any:
        """POST 请求快捷方法"""
        return await self.execute(path, params=params, method="POST", json_data=json_data)

    async def close(self) -> None:
        """关闭会话和连接池"""
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None
            if self._connector is not None:
                await self._connector.close()
                self._connector = None
            self._status = DataSourceStatus.UNKNOWN
