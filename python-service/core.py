"""
共享机制：常量、鉴权、错误体、代码校验、只读 SQL 校验、数据集路由。

这里放的是**各组路由都要用**的东西；路由本身在 `routers/` 下。
依赖方向是单向的：本模块不 import `routers/`，也不 import `main`。
"""

import datetime
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Header, HTTPException

# 搬迁前这些日志全都挂在 "main" 这一个 logger 下（单文件时代
# `logging.getLogger(__name__)` 恰好就是 "main"）。拆成多个模块之后仍沿用同一个
# 名字，日志采集侧的分类规则不需要跟着改。
logger = logging.getLogger("main")


SERVICE_VERSION = "2.1.0"


DUCKDB_DATABASES = ["market", "financials", "fund", "special", "futures", "index", "indicators"]


MAX_QUERY_ROWS = 100_000


DEFAULT_QUERY_LIMIT = 1_000


MAX_SCREEN_SYMBOLS = 20_000


# /api/quotes 一次最多查多少只。自选列表的规模量级（几十到几百），上限只是
# 为了防止一次请求把 DuckDB 拖成批量扫描。
MAX_QUOTE_SYMBOLS = 200


# 只读白名单：/query/ 只接受单条 SELECT
_READONLY_STATEMENT = re.compile(
    r"^\s*(?:WITH\b.*?)?SELECT\b", re.IGNORECASE | re.DOTALL
)


_FORBIDDEN_SQL = re.compile(
    r"\b(?:ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|CREATE|ALTER|DROP|"
    r"TRUNCATE|DELETE|INSERT|UPDATE|PRAGMA|SET\b|VACUUM|CALL|"
    r"read_csv|read_parquet|read_json|read_text|read_blob|read_ndjson|glob|sniff_csv|"
    r"parquet_scan|sqlite_scan|postgres_scan|mysql_scan|iceberg_scan|delta_scan)\b",
    re.IGNORECASE,
)


_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)


_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


# 6 位数字 + 交易所后缀。`.TI` 是同花顺的板块/指数代码（如 881101.TI = 种植业与林业），
# 与个股共用同一个形状，所以格式校验放在一起；「这个端点支不支持板块」是另一层判断，
# 见 `_require_stock_thscode` —— 只有 /api/kline 支持板块。
_THSCODE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ|HK|US|TI)$", re.IGNORECASE)


# ===== 鉴权 =====

API_KEY = os.getenv("WEKNORA_PY_SERVICE_API_KEY", "").strip()


async def require_api_key(authorization: Optional[str] = Header(default=None)) -> None:
    """
    设置 `WEKNORA_PY_SERVICE_API_KEY` 后，所有 /query/ 请求都要带
    `Authorization: Bearer <key>`。未设置则不鉴权（内网部署的既有行为）。
    """
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少 Authorization 头")
    if not secrets_compare(authorization.strip(), expected):
        raise HTTPException(status_code=403, detail="API key 无效")


def secrets_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


# ===== 公共辅助 =====

def fail(status_code: int, message: str, **extra) -> HTTPException:
    """统一错误体。

    `success` / `error` 放在**顶层**而不是 `detail` 里：调用方（包括存量
    调用方）读的是 `body["success"]` 和 `body["error"]`。`detail` 保留一份
    以兼容 FastAPI 默认形状。
    """
    payload: Dict[str, Any] = {"success": False, "error": message, "detail": message}
    payload.update(extra)
    return HTTPException(status_code=status_code, detail=payload)


def _require_thscode(thscode: str) -> str:
    """thscode 必须是 `600000.SH` 这种形态。挡掉注入串的最外层。"""
    code = (thscode or "").strip()
    if not _THSCODE.match(code):
        raise fail(422, f"thscode 格式非法：{thscode!r}，应为 6 位数字 + .SH/.SZ/.BJ/.HK/.US/.TI")
    return code.upper()


# 板块/指数（同花顺 .TI）与个股走两套数据集：库不同、视图不同、日期列名也不同。
_BOARD_SUFFIX = ".TI"


def _is_board(code: str) -> bool:
    """同花顺板块/指数代码（`.TI`）。"""
    return code.upper().endswith(_BOARD_SUFFIX)


def _require_stock_thscode(thscode: str) -> str:
    """
    只接受个股（.SH/.SZ/.BJ/.HK/.US），用于不支持板块的端点。

    板块目前只支持 K 线（`/api/kline`）：指标、形态、复权、画像这些端点读的是
    market / indicators 库，而 `index.v_index_daily` 只有裸 OHLCV，没有 *_qfq/_hfq，
    也没有指标列。这里必须给出**带原因**的拒绝，而不是让它掉进"格式非法"：
    后者会让人去改代码写法，而实际要做的是换一个标的。
    """
    code = _require_thscode(thscode)
    if _is_board(code):
        raise fail(
            422,
            f"该接口不支持板块/指数标的：{code}（板块只提供 K 线，不提供复权/指标/形态/画像）",
        )
    return code


@dataclass(frozen=True)
class _Dataset:
    """
    一个可画 K 线的数据集。

    取数差异只有三处，抽出来而不是在每个 SQL 分支里写 `if board`：数据源
    （registry 里的名字）、各复权档位对应的视图、日期列名。K 线的 SQL 分支有
    day/week/month × 有/无起始日 共 6 条，把差异复制进每一条必然会漏掉一条。
    """

    datasource: str
    view_by_adjust: Dict[str, str]
    date_column: str


_MARKET_DATASET = _Dataset(
    datasource="market",
    view_by_adjust={"none": "v_daily", "forward": "v_daily_qfq", "backward": "v_daily_hfq"},
    date_column="date",
)


# 板块/指数只有不复权裸行情（index.duckdb 里没有 *_qfq/_hfq 视图），三个复权档位
# 都指向同一视图。前端默认发 `adjust=forward`，所以这里必须**接受**它而不是报 400，
# 否则板块永远画不出来；口径由前端标注（复权选择器置灰并写明"板块为不复权"）。
_BOARD_DATASET = _Dataset(
    datasource="index",
    view_by_adjust={
        "none": "v_index_daily",
        "forward": "v_index_daily",
        "backward": "v_index_daily",
    },
    date_column="trade_date",
)


def _dataset_for(thscode: str) -> _Dataset:
    """按代码后缀选数据集：`.TI` → 板块/指数，其余 → 个股。"""
    return _BOARD_DATASET if _is_board(thscode) else _MARKET_DATASET


def _read_only_sql(sql: str) -> str:
    """
    校验并规范化只读查询。

    旧实现只判断 `"LIMIT" not in sql.upper()`，一个 `-- limit` 注释就能把
    限制整个绕开（实测 limit=2 返回 5571 行）。现在做三件事：
    只允许单条 SELECT、禁掉写/pragma 关键字、无条件追加或收紧 LIMIT。
    """
    # 先剥注释：既堵掉"注释里藏 LIMIT/关键字"这类绕过，也让外层包一层
    # LIMIT 的子查询不会被行尾注释吃掉右括号。
    statement = _SQL_COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if not statement:
        raise fail(400, "sql 不能为空")
    if ";" in statement:
        raise fail(400, "只允许单条语句，不支持多语句查询")
    if not _READONLY_STATEMENT.match(statement):
        raise fail(400, "只允许 SELECT 查询（可用 WITH ... SELECT）")
    forbidden = _FORBIDDEN_SQL.search(statement)
    if forbidden:
        raise fail(400, f"查询包含被禁止的关键字：{forbidden.group(0)}")
    return statement


def _param_key(value: Any) -> str:
    """把绑定参数渲染成稳定的缓存键片段。"""
    return f"{type(value).__name__}:{value!r}"


def _apply_limit(sql: str, limit: int) -> str:
    """把结果行数硬性限制在 `limit` 以内。

    永远用外层 wrapper 夹紧，而不是"SQL 里没有 LIMIT 就追加一个"——
    后者被一个 `-- limit` 注释就能绕开（实测 limit=2 返回了 5571 行）。
    """
    if limit <= 0:
        raise fail(422, "limit 必须为正整数")
    return f"SELECT * FROM ({sql}) LIMIT {limit}"


def _iso_date(d: Any) -> Optional[str]:
    """DuckDB 的 DATE 归一成 YYYY-MM-DD。取不到就返回 None，不猜、不补。"""
    if isinstance(d, datetime.date):
        return d.isoformat()
    if isinstance(d, str):
        return d[:10]
    return None
