"""
routers 包：按业务域拆开的 FastAPI 路由。

每个子模块导出一个 `router`（APIRouter 实例），由 `main.py` 统一注册到 app 上。
"""

from routers.core import router as core_router
from routers.finance import router as finance_router
from routers.market import router as market_router

__all__ = ["core_router", "finance_router", "market_router"]
