"""
WeKnora Python Service

提供 DuckDB 查询与技术分析能力的金融数据服务。
使用统一的数据源管理层管理所有数据连接。

HTTP 契约
---------
* 成功 2xx，失败 4xx/5xx。**不再**用 200 + `{"success": false}` 表达错误
  —— 那种返回对 ingress、负载均衡、监控是不可见的。
* 错误体保留 `success: false` 和 `error` 字段，方便存量调用方平滑迁移。
* 业务错误 = 4xx（调用方的问题），依赖不可用 = 5xx（服务端的问题）。
"""

import asyncio
import datetime
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from datasources.cache import stable_hash

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SERVICE_VERSION = "2.1.0"

DUCKDB_DATABASES = ["market", "financials", "fund", "special", "futures", "index", "indicators"]

MAX_QUERY_ROWS = 100_000
DEFAULT_QUERY_LIMIT = 1_000
MAX_SCREEN_SYMBOLS = 20_000

# 只读白名单：/query/ 只接受单条 SELECT
_READONLY_STATEMENT = re.compile(
    r"^\s*(?:WITH\b.*?)?SELECT\b", re.IGNORECASE | re.DOTALL
)
_FORBIDDEN_SQL = re.compile(
    r"\b(?:ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|CREATE|ALTER|DROP|"
    r"TRUNCATE|DELETE|INSERT|UPDATE|PRAGMA|SET\b|VACUUM|CALL)\b",
    re.IGNORECASE,
)
_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_THSCODE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ|HK|US)$", re.IGNORECASE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 — 启动/关闭时初始化和释放数据源"""
    from datasources import registry, manager, config, cache
    from datasources.duckdb_source import DuckDBSource
    from datasources.redis_source import RedisSource

    for db_name in DUCKDB_DATABASES:
        db_path = config.get_db_path(db_name)
        if not os.path.exists(db_path):
            logger.warning("DuckDB 文件不存在，跳过：%s", db_path)
            continue
        try:
            registry.register(DuckDBSource(
                name=db_name,
                db_path=db_path,
                max_concurrent=config.duckdb_max_concurrent,
                max_rows=MAX_QUERY_ROWS,
            ))
            logger.info("已注册 DuckDB 数据源：%s (%s)", db_name, db_path)
        except Exception as exc:
            logger.error("注册 DuckDB 数据源 %s 失败：%s", db_name, exc)

    redis_cfg = config.get_redis_config()
    if redis_cfg.get("host"):
        try:
            redis_source = RedisSource(
                name="cache",
                host=redis_cfg["host"],
                port=redis_cfg["port"],
                db=redis_cfg["db"],
                password=redis_cfg.get("password"),
            )
            registry.register(redis_source)
            cache.set_redis(redis_source)
            logger.info("已注册 Redis 数据源：%s:%s", redis_cfg["host"], redis_cfg["port"])
        except Exception as exc:
            logger.error("注册 Redis 数据源失败：%s", exc)

    await manager.startup()
    try:
        yield
    finally:
        await manager.shutdown()


app = FastAPI(
    title="WeKnora Python Service",
    description="金融数据查询服务 — 统一数据源管理（DuckDB + Redis）",
    version=SERVICE_VERSION,
    lifespan=lifespan,
)


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


@app.exception_handler(HTTPException)
async def flat_error_handler(request, exc: HTTPException):
    """把 fail() 造出来的错误体摊平到顶层。"""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": str(exc.detail), "detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    """pydantic 校验失败也走同一套形状，不再和业务错误长得不一样。

    `error` 里带上 pydantic 的原始原因（"Input should be greater than or
    equal to 10" 之类），否则调用方只看到一个"参数不合法"，无法自查。
    """
    violations = jsonable_encoder(exc.errors())
    first = violations[0] if violations else {}
    field = ".".join(str(p) for p in first.get("loc", ())) or "body"
    reason = first.get("msg", "请求参数不合法")
    message = f"{field}: {reason}"
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": message,
            "detail": message,
            "violations": violations,
        },
    )


def _require_thscode(thscode: str) -> str:
    """thscode 必须是 `600000.SH` 这种形态。挡掉注入串的最外层。"""
    code = (thscode or "").strip()
    if not _THSCODE.match(code):
        raise fail(422, f"thscode 格式非法：{thscode!r}，应为 6 位数字 + .SH/.SZ/.BJ/.HK/.US")
    return code.upper()


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


# ===== 请求模型 =====

class QueryRequest(BaseModel):
    """DuckDB 只读查询请求"""
    db: str = Field(min_length=1, max_length=64)
    sql: str = Field(min_length=1, max_length=100_000)
    limit: int = Field(default=DEFAULT_QUERY_LIMIT, ge=1, le=MAX_QUERY_ROWS)
    # 绑定参数，按顺序消费 sql 里的 `?`。
    # Go 侧 hithink_finance.QueryDuckDB 走的就是这个字段，所以调用方不必
    # 再把 thscode 之类的值拼进 SQL 字符串。
    params: List[Any] = Field(default_factory=list, max_length=64)

    @field_validator("db")
    @classmethod
    def _known_db(cls, v: str) -> str:
        if v not in DUCKDB_DATABASES:
            raise ValueError(
                f"未知数据库 '{v}'，可用：{', '.join(DUCKDB_DATABASES)}"
            )
        return v

    def check_placeholders(self) -> None:
        want, got = self.sql.count("?"), len(self.params)
        if want != got:
            raise fail(400, f"sql 有 {want} 个占位符，但提供了 {got} 个绑定参数")


class AnalyzeRequest(BaseModel):
    """技术分析请求"""
    thscode: str
    days: int = Field(default=120, ge=10, le=250)


class ScanRequest(BaseModel):
    """信号扫描请求"""
    thscode: str
    days: int = Field(default=10, ge=10, le=60)


class ScreenRequest(BaseModel):
    """选股请求"""
    strategy: str
    limit: int = Field(default=20, ge=1, le=200)


# 策略 → 信号筛选规则映射
#
# `match_signals` 里的每个名字都必须真的能被 detect_signals 以
# `signal == "bullish"` 发出来，否则该策略永远选不出票。
# 旧版 `anomaly` 三条规则里两条是 neutral、剩下那条判据恒不成立，
# 策略是结构性死掉的。
STRATEGY_RULES: Dict[str, Dict[str, Any]] = {
    "B1": {
        "match_signals": ["KDJ超卖金叉", "RSI6超卖", "Stochastic超卖金叉",
                          "CCI超卖", "Williams%R超卖", "Z-Score超卖"],
        "min_count": 2,
    },
    "B2": {"match_signals": ["MACD金叉"], "min_count": 1},
    "SB1": {
        "match_signals": ["KDJ超卖金叉", "RSI6超卖", "Stochastic超卖金叉",
                          "CCI超卖", "Williams%R超卖", "Z-Score超卖", "MACD金叉"],
        "min_count": 3,
    },
    "shaofu": {
        "match_signals": ["MFI超卖", "Williams%R超卖", "KDJ超卖金叉"],
        "min_count": 2,
    },
    # 全市场快照只有指标、没有点位，所以突破类信号在这里用"资金流入 +
    # 趋势强度"代理。想要真正的"放量突破"请走 /zettaranc/analyze 的量价段。
    "limit_up": {
        "match_signals": ["CMF资金流入", "ADX多头趋势", "Aroon多头排列"],
        "min_count": 1,
    },
    # ATR扩张 / 布林带收口 是 neutral 信号，所以这条规则开了 allow_neutral。
    # （旧版这三条规则里两条是 neutral、一条判据恒不成立，整条策略恒返回 0。）
    "anomaly": {
        "match_signals": ["ATR扩张", "布林带收口", "CMF资金流出",
                          "Vortex死叉", "ADX空头趋势"],
        "min_count": 1,
        "allow_neutral": True,
    },
}


# ===== 路由 =====

def _generations() -> Dict[str, int]:
    """各 DuckDB 源的数据代次（底层文件变化 → 重开连接的次数）。"""
    from datasources import registry
    return {
        name: src.generation
        for name, src in registry.list_sources().items()
        if hasattr(src, "generation")
    }


@app.get("/health")
async def health_check():
    """
    健康检查。**如实**反映数据源状态：任何数据源不健康，顶层就是 degraded /
    unhealthy，并返回 503 —— Docker HEALTHCHECK 和 K8S 探针据此重启容器。
    旧实现无论发生什么都返回 200 + "healthy"。
    """
    from datasources import manager
    from datasources.base import DataSourceStatus

    # 立刻复查一次，不要返回 30 秒前的后台快照
    await manager.refresh_health()
    statuses = manager.get_status()
    if not statuses:
        status, code = "unhealthy", 503
    elif all(v == DataSourceStatus.HEALTHY.value for v in statuses.values()):
        status, code = "healthy", 200
    elif any(v in (DataSourceStatus.HEALTHY.value, DataSourceStatus.DEGRADED.value)
             for v in statuses.values()):
        status, code = "degraded", 200
    else:
        status, code = "unhealthy", 503

    body = {
        "success": status == "healthy",
        "status": status,
        "version": SERVICE_VERSION,
        "datasources": statuses,
        # 数据代次：ETL 落库后 DuckDB 连接被重开的次数。持续增长说明上游
        # 在频繁重算，值得看一眼是不是有全量重跑在跑。
        "generations": _generations(),
    }
    if code != 200:
        raise HTTPException(status_code=code, detail=body)
    return body


@app.get("/")
async def root():
    """服务信息"""
    from datasources import manager
    return {
        "service": "WeKnora Python Service",
        "version": SERVICE_VERSION,
        "auth_required": bool(API_KEY),
        "datasources": manager.get_status(),
        "endpoints": [
            "/health",
            "/query/",
            "/query/databases",
            "/cache/stats",
            "/cache/clear",
            "/zettaranc/screen",
            "/zettaranc/analyze",
            "/zettaranc/scan",
            "/zettaranc/health",
        ],
    }


@app.get("/query/databases")
async def list_databases():
    """列出可用的 DuckDB 数据库"""
    from datasources import registry
    from datasources.base import DataSourceType

    databases = [
        name for name, source in registry.list_sources().items()
        if source.source_type == DataSourceType.DUCKDB
    ]
    return {"databases": databases}


@app.post("/query/")
async def query_duckdb(
    request: QueryRequest,
    _: None = Depends(require_api_key),
) -> Dict[str, Any]:
    """执行 DuckDB 只读查询。可用数据库通过 /query/databases 获取。"""
    from datasources import registry, cache
    from datasources.base import DataSourceType

    source = registry.get(request.db)
    if source is None:
        raise fail(503, f"数据源 '{request.db}' 未就绪")
    if source.source_type != DataSourceType.DUCKDB:
        raise fail(400, f"'{request.db}' 不是 DuckDB 数据源")

    request.check_placeholders()
    statement = _read_only_sql(request.sql)
    bounded = _apply_limit(statement, request.limit)

    # 缓存键要连绑定参数一起算，否则不同 thscode 撞到同一个 key。
    # 再并上数据代次：ETL 落库 → 数据源重开连接 → generation 变 → 老缓存
    # 自然失效，不需要谁记得去调 /cache/clear。
    cache_key = stable_hash(
        request.db,
        f"gen={getattr(source, 'generation', 0)}",
        bounded,
        *(_param_key(p) for p in request.params),
    )

    cached = await cache.get("query", cache_key)
    if cached is not None:
        return {
            "success": True, "db": request.db, "cached": True,
            "count": len(cached), "truncated": False, "data": cached,
        }

    try:
        rows = await source.execute(bounded, request.params or None)
    except Exception as exc:
        logger.warning("查询失败 db=%s: %s", request.db, exc)
        raise fail(400, f"查询失败：{exc}") from exc

    await cache.set("query", cache_key, rows)

    return {
        "success": True, "db": request.db, "cached": False,
        "count": len(rows),
        "truncated": bool(getattr(source, "last_result_truncated", False)),
        "data": rows,
    }


@app.get("/cache/stats")
async def cache_stats():
    """缓存统计信息"""
    from datasources import cache
    return {"memory_size": cache.memory_size, "stats": cache.stats}


@app.post("/cache/clear")
async def cache_clear():
    """清空缓存（内存 + Redis 两级）"""
    from datasources import cache
    await cache.clear_all()
    return {"success": True, "message": "内存与 Redis 缓存均已清空"}


# ===== Zettaranc 技术分析 =====

@app.post("/zettaranc/screen")
async def zettaranc_screen(request: ScreenRequest) -> Dict[str, Any]:
    """全市场选股 — 一条 SQL 取回全市场指标后集合式判定"""
    from datasources import registry
    from zettaranc import screener

    rule = STRATEGY_RULES.get(request.strategy)
    if rule is None:
        raise fail(422, f"未知策略：{request.strategy}。可用策略：{list(STRATEGY_RULES)}")

    indicators_src = registry.get("indicators")
    market_src = registry.get("market")
    if indicators_src is None:
        raise fail(503, "indicators 数据源未就绪")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    # 早期版本逐只调 scan_patterns：5571 只 = 5571 次往返，实测 36~51 秒。
    # 现在两条集合式查询：先要清单，再按清单一次性取回最近两天的指标。
    # 判定复用 detect_signals，所以选股池和 /zettaranc/scan 口径一致。
    try:
        universe = await market_src.execute(
            screener.build_universe_sql(), [screener.MAX_UNIVERSE]
        )
    except Exception as exc:
        logger.warning("选股取清单失败: %s", exc)
        raise fail(503, f"获取股票清单失败：{exc}") from exc

    if not universe:
        raise fail(503, "股票清单为空")

    names = {u.get("thscode"): u.get("name", "") for u in universe}
    codes = ",".join(c for c in names if c)

    try:
        rows = await indicators_src.execute(
            screener.build_indicator_snapshot_sql(),
            [codes, screener.LOOKBACK_DAYS],
        )
    except Exception as exc:
        logger.warning("选股取指标失败: %s", exc)
        raise fail(503, f"选股取数失败：{exc}") from exc

    if not rows:
        raise fail(503, "指标快照为空")

    result = await asyncio.to_thread(
        screener.screen, rows, rule, request.limit, names
    )

    return {
        "success": True,
        "strategy": request.strategy,
        "universe": len(names),
        "scanned": len(names),
        "incomplete": result["incomplete"],
        "matched": result["matched"],
        "unsupported_signals": screener.unsupported_signals(rule),
        "stocks": result["stocks"],
    }


@app.post("/zettaranc/analyze")
async def zettaranc_analyze(request: AnalyzeRequest) -> Dict[str, Any]:
    """综合技术分析 — 趋势 + 量价 + 形态 + 支撑阻力"""
    from datasources import registry
    from zettaranc import (
        fetch_market_data, analyze_trend, analyze_volume,
        analyze_chart_pattern, analyze_levels,
    )

    thscode = _require_thscode(request.thscode)
    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")
    indicators_src = registry.get("indicators")

    try:
        rows = await fetch_market_data(
            market_src, thscode, request.days, indicators_source=indicators_src
        )
    except ValueError as exc:
        raise fail(404, str(exc)) from exc
    except Exception as exc:
        raise fail(503, f"数据加载失败：{exc}") from exc

    result: Dict[str, Any] = {
        "thscode": thscode,
        "requested_days": request.days,
        "days": len(rows),
        "latest_date": rows[0].get("date"),
    }

    # 数据不足的段也要出现（值为 null），并在 insufficient_data 里说明原因。
    # 旧实现是静默不返回这个 key，调用方无法区分"数据不足"和"服务坏了"。
    skipped: List[str] = []

    if len(rows) >= 20:
        try:
            result["trend"] = analyze_trend(rows, thscode=thscode)
        except Exception as exc:
            result["trend"] = None
            skipped.append(f"trend: {exc}")
    else:
        result["trend"] = None
        skipped.append(f"trend: 需要 20 根 K 线，仅 {len(rows)} 根")

    if len(rows) >= 10:
        try:
            result["volume"] = analyze_volume(rows)
        except Exception as exc:
            result["volume"] = None
            skipped.append(f"volume: {exc}")
        try:
            result["chart_pattern"] = analyze_chart_pattern(rows)
        except Exception as exc:
            result["chart_pattern"] = None
            skipped.append(f"chart_pattern: {exc}")
    else:
        result["volume"] = None
        result["chart_pattern"] = None
        skipped.append(f"volume/chart_pattern: 需要 10 根 K 线，仅 {len(rows)} 根")

    try:
        result["levels"] = analyze_levels(rows)
    except ValueError as exc:
        result["levels"] = None
        skipped.append(f"levels: {exc}")

    result["insufficient_data"] = skipped
    result["complete"] = not skipped

    return {"success": True, "result": result}


@app.post("/zettaranc/scan")
async def zettaranc_scan(request: ScanRequest) -> Dict[str, Any]:
    """技术信号扫描 — 30+ 种买卖形态"""
    from datasources import registry
    from zettaranc import scan_patterns

    thscode = _require_thscode(request.thscode)
    indicators_src = registry.get("indicators")
    if indicators_src is None:
        raise fail(503, "indicators 数据源未就绪")

    try:
        result = await scan_patterns(indicators_src, thscode, request.days)
    except ValueError as exc:
        raise fail(404, str(exc)) from exc
    except Exception as exc:
        raise fail(503, f"扫描失败：{exc}") from exc

    return {"success": True, "result": result}


@app.get("/zettaranc/health")
async def zettaranc_health():
    """Zettaranc 模块健康检查：真去查一次指标数据，而不是硬编码 healthy"""
    from datasources import registry
    indicators_src = registry.get("indicators")
    if indicators_src is None:
        raise HTTPException(status_code=503, detail={
            "success": False, "status": "unhealthy", "module": "zettaranc",
            "error": "indicators 数据源未就绪",
        })
    try:
        await indicators_src.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "success": False, "status": "unhealthy", "module": "zettaranc",
            "error": str(exc),
        })
    return {"success": True, "status": "healthy", "module": "zettaranc"}


# ===== KLine 与形态图表 API (前端 KLineChart Pro 直连) =====

_ADJUST_VIEWS = {
    "none": "v_daily",
    "forward": "v_daily_qfq",
    "backward": "v_daily_hfq",
}


def _date_to_epoch_sec(d: Any) -> int:
    """DuckDB 日期对象或 ISO 字符串转换为 UTC 秒级时间戳"""
    if isinstance(d, datetime.date):
        return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp())
    elif isinstance(d, str):
        parts = d[:10].split("-")
        if len(parts) == 3:
            dt = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            return int(datetime.datetime(dt.year, dt.month, dt.day, tzinfo=datetime.timezone.utc).timestamp())
    return int(d)


@app.get("/api/kline")
@app.get("/kline")
async def get_kline(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
    period: str = Query("day", description="K线周期: day | week | month"),
    adjust: str = Query("forward", description="复权类型: none | forward | backward"),
    from_date: Optional[str] = Query(None, alias="from", description="起始日期 YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, alias="to", description="结束日期 YYYY-MM-DD"),
    limit: int = Query(5000, ge=1, le=20000, description="最大K线根数"),
):
    """
    K 线数据查询接口 — 输出与 KLineChart Pro 兼容的 OHLCV 数组
    """
    from datasources import registry

    thscode = _require_thscode(symbol)
    if adjust not in _ADJUST_VIEWS:
        raise fail(400, f"无效的复权类型: {adjust}，可用: none, forward, backward")
    if period not in ("day", "week", "month"):
        raise fail(400, f"无效的周期: {period}，可用: day, week, month")

    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    view = _ADJUST_VIEWS[adjust]
    conditions = ["thscode = ?"]
    params: List[Any] = [thscode]

    if from_date:
        conditions.append("date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("date <= ?")
        params.append(to_date)

    where_clause = " AND ".join(conditions)

    if period in ("week", "month"):
        trunc = "date_trunc('week', date)" if period == "week" else "date_trunc('month', date)"
        if from_date:
            sql = f"""
                SELECT
                    {trunc} as date,
                    arg_min(open, date) as open,
                    max(high) as high,
                    min(low) as low,
                    arg_max(close, date) as close,
                    sum(volume) as volume,
                    sum(turnover) as turnover
                FROM {view}
                WHERE {where_clause}
                GROUP BY {trunc}
                ORDER BY date ASC
                LIMIT {limit}
            """
        else:
            sql = f"""
                SELECT date, open, high, low, close, volume, turnover
                FROM (
                    SELECT
                        {trunc} as date,
                        arg_min(open, date) as open,
                        max(high) as high,
                        min(low) as low,
                        arg_max(close, date) as close,
                        sum(volume) as volume,
                        sum(turnover) as turnover
                    FROM {view}
                    WHERE {where_clause}
                    GROUP BY {trunc}
                    ORDER BY date DESC
                    LIMIT {limit}
                ) sub
                ORDER BY date ASC
            """
    else:
        if from_date:
            sql = f"""
                SELECT date, open, high, low, close, volume, turnover
                FROM {view}
                WHERE {where_clause}
                ORDER BY date ASC
                LIMIT {limit}
            """
        else:
            sql = f"""
                SELECT date, open, high, low, close, volume, turnover
                FROM (
                    SELECT date, open, high, low, close, volume, turnover
                    FROM {view}
                    WHERE {where_clause}
                    ORDER BY date DESC
                    LIMIT {limit}
                ) sub
                ORDER BY date ASC
            """

    try:
        rows = await market_src.execute(sql, params)
    except Exception as exc:
        logger.warning("K线查询失败: %s", exc)
        raise fail(503, f"查询 K 线数据失败: {exc}") from exc

    return {
        "code": 0,
        "data": [
            {
                "ts": _date_to_epoch_sec(r["date"]),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
                "turnover": r.get("turnover", 0.0),
            }
            for r in rows
        ],
    }


@app.get("/api/annotate")
@app.get("/annotate")
async def get_annotations(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
    days: int = Query(120, ge=10, le=1000, description="回溯天数"),
    patterns: str = Query("b1,key_k,s1,violent_k", description="形态类型，逗号分隔"),
    adjust: str = Query("forward", description="复权类型: none | forward | backward"),
):
    """
    形态标注计算接口 — 识别指定股票的买卖形态 (B1, S1, 关键K, 暴力K)
    """
    from datasources import registry
    from zettaranc.annotator import annotator

    thscode = _require_thscode(symbol)
    if adjust not in _ADJUST_VIEWS:
        raise fail(400, f"无效的复权类型: {adjust}")

    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    view = _ADJUST_VIEWS[adjust]
    fetch_limit = days + 60
    sql = f"""
        SELECT date, open, high, low, close, volume, turnover
        FROM {view}
        WHERE thscode = ?
        ORDER BY date DESC
        LIMIT {fetch_limit}
    """

    try:
        rows = await market_src.execute(sql, [thscode])
    except Exception as exc:
        raise fail(503, f"查询 K 线数据失败: {exc}") from exc

    if not rows:
        raise fail(404, f"未找到股票 {thscode} 的行情数据")

    rows.reverse()
    pattern_list = [p.strip() for p in patterns.split(",") if p.strip()]
    annotations = annotator.annotate_all(rows, pattern_list)

    cutoff_date = str(rows[-min(days, len(rows))]["date"])
    filtered_ann = [a for a in annotations if str(a["date"]) >= cutoff_date]

    return {
        "code": 0,
        "symbol": thscode,
        "days": len(rows),
        "data_source": "duckdb",
        "pattern_types": pattern_list,
        "annotation_count": len(filtered_ann),
        "annotations": filtered_ann,
    }


@app.get("/api/symbols/search")
@app.get("/symbols/search")
async def search_symbols(
    q: str = Query("", description="搜索关键词 (股票代码/简称)"),
    limit: int = Query(30, ge=1, le=100),
):
    """
    股票代码/名称模糊搜索
    """
    from datasources import registry

    query_str = q.strip().replace("'", "").replace("%", "")
    if not query_str:
        return {"code": 0, "data": []}

    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    like_pat = f"%{query_str}%"
    sql = """
        SELECT thscode, ticker, name, exchange, asset_type
        FROM v_symbol
        WHERE ticker LIKE ? OR name LIKE ? OR thscode LIKE ?
        ORDER BY (ticker = ?) DESC, ticker ASC
        LIMIT ?
    """
    rows = await market_src.execute(sql, [like_pat, like_pat, like_pat, query_str.upper(), limit])
    return {
        "code": 0,
        "data": [
            {
                "thscode": r["thscode"],
                "ticker": r["ticker"],
                "name": r["name"],
                "exchange": r["exchange"],
                "asset_type": r.get("asset_type", "a-share"),
            }
            for r in rows
        ],
    }


@app.get("/api/indicators")
@app.get("/indicators")
async def get_indicators(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
    days: int = Query(500, ge=1, le=5000),
    categories: str = Query("zettaranc", description="指标类别"),
):
    """
    个股指标时序数据接口
    """
    from datasources import registry

    thscode = _require_thscode(symbol)
    indicators_src = registry.get("indicators")
    if indicators_src is None:
        raise fail(503, "indicators 数据源未就绪")

    sql = """
        SELECT date, zettaranc_zg_white_10, zettaranc_dg_yellow_14, zettaranc_bbi,
               zettaranc_brick_value, zettaranc_rsl_short_3, zettaranc_rsl_long_21
        FROM (
            SELECT date, zettaranc_zg_white_10, zettaranc_dg_yellow_14, zettaranc_bbi,
                   zettaranc_brick_value, zettaranc_rsl_short_3, zettaranc_rsl_long_21
            FROM v_indicators_daily
            WHERE thscode = ?
            ORDER BY date DESC
            LIMIT ?
        ) sub
        ORDER BY date ASC
    """
    rows = await indicators_src.execute(sql, [thscode, days])
    return {
        "code": 0,
        "symbol": thscode,
        "count": len(rows),
        "data": rows,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "50052")))
