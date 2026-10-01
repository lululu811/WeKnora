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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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
# /api/quotes 一次最多查多少只。自选列表的规模量级（几十到几百），上限只是
# 为了防止一次请求把 DuckDB 拖成批量扫描。
MAX_QUOTE_SYMBOLS = 200

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
    """选股请求

    形态信号只回答"图形像不像"，不回答"会不会暴雷"。可选的筛选参数
    构成第二道关：板块限定 + 财务风险代理 + ST 排除。

    全部可选且默认为空 —— 不传就是纯形态选股，与之前行为一致。
    """
    strategy: str
    limit: int = Field(default=20, ge=1, le=200)

    # ---- 板块 ----
    # 板块名（支持片段匹配，如 "半导体" 命中「半导体」「半导体设备」）。
    # 按名字反查成分股而不是展开全量成员关系：后者有 122,368 行，
    # 超过单查询 100k 上限，展开必被静默截断。
    sector: Optional[str] = Field(
        default=None, description="板块名片段，如「半导体」「白酒」；按成分股反查"
    )

    # ---- 财务风险代理（financials.v_balance_sheet，覆盖全部 A 股）----
    # 商誉/质押/减持/审计意见/监管处罚本地库**没有**，所以这里只提供
    # 真正算得出的三个比率。它们的出处是 balance sheet，不是包装。
    max_debt_ratio: Optional[float] = Field(
        default=None, ge=0, le=5,
        description="资产负债率上限（total_debt/assets_total）。本地分布 p50=0.40 p90=0.71",
    )
    min_current_ratio: Optional[float] = Field(
        default=None, ge=0,
        description="流动比率下限（total_current_assets/total_debt）。本地分布 p50=1.50 p10=0.60",
    )
    max_receivable_ratio: Optional[float] = Field(
        default=None, ge=0, le=1,
        description="应收账款占总资产上限。本地分布 p50 约 0.03",
    )

    # ---- 开关 ----
    require_profit: bool = Field(
        default=False, description="要求最新期归母净利润为正（financials.v_income_statement）"
    )
    exclude_st: bool = Field(
        default=False,
        description="排除 ST/*ST。库里没有 ST 标记列，只能按 v_symbol.name 匹配，全 A 204 只",
    )


# 策略 → 信号筛选规则映射
#
# `match_signals` 里的每个名字都必须真的能被 detect_signals 以
# `signal == "bullish"` 发出来，否则该策略永远选不出票。
# 旧版 `anomaly` 三条规则里两条是 neutral、剩下那条判据恒不成立，
# 策略是结构性死掉的。
#
# 命名：策略名必须自解释，且不能和标注层的形态名撞名。
# 原先这条规则叫 "B1"，而 zettaranc.annotator.detect_build_wave_b1 发出的
# 形态标注也叫 B1（建仓波回调买点）—— 同一个名字两套语义，模型读策略清单
# 时会把两者当成同一个东西。所以选股策略一律用描述性英文名：
#   oversold_combo  = 超卖共振（≥2 个超卖信号），与建仓波 B1 无关
#   SB1 的 "S…B1" 前缀是历史叫法，指的就是"超卖组合这一组再加 MACD 金叉"，
#   与建仓波 B1 同样无关；它没有歧义，故保留原名不扩大改动面。
STRATEGY_RULES: Dict[str, Dict[str, Any]] = {
    "oversold_combo": {
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
    # 异常/风险筛选：命中越多项风险信号，排名越靠前。
    #
    # 这条规则过去写着 `allow_neutral: True`，而筛选侧的判据是
    # `s["signal"] == "bullish" or allow_neutral` —— 开了 allow_neutral
    # 之后整个条件对所有方向短路成真，bearish 信号照收不误；score 又累加
    # 被夹到 [0,1] 的无符号 strength，于是命中 3 个看跌信号的票稳定排在
    # 命中 1 个中性信号的票前面。策略顶着"异常检测"的名字选出一批看跌票。
    #
    # 现在用显式 direction 表达意图。ATR扩张 / 布林带收口 是 neutral
    # （波动放大本身没有方向），所以从 bullish 名单里去掉；只留真带方向的
    # 三项风险信号，并用 direction="bearish" 把方向写进契约。
    "anomaly": {
        "match_signals": ["CMF资金流出", "Vortex死叉", "ADX空头趋势"],
        "min_count": 1,
        "direction": "bearish",
    },
    # 波动率异动：ATR 扩张与布林带收口都是 neutral，把 volatility 放到
    # 独立策略里，否则只能靠 allow_neutral 绕开方向判据。
    "volatility_spike": {
        "match_signals": ["ATR扩张", "布林带收口"],
        "min_count": 1,
        "allow_neutral": True,
    },
    # 放量突破：涨幅 > 3% 且量比 > 1.5。依赖 close + volume，
    # 价量维度接进选股池之前这条策略根本选不出票（信号在 SCREEN_UNSUPPORTED 里）。
    "vol_breakout": {
        "match_signals": ["放量突破"],
        "min_count": 1,
    },
    # Donchian 上轨突破：收盘站上通道上轨。与单只扫描
    # /zettaranc/analyze 的判定共用 detect_signals，口径一致。
    "donchian_break": {
        "match_signals": ["Donchian上轨突破"],
        "min_count": 1,
    },

    # ---- 以下三组覆盖此前"能算但没有任何策略用"的信号 ----
    #
    # 审计发现 detect_signals 能发 28 种，策略只引用了 18 种。缺的 10 种
    # 全部是 bearish 方向或单根形态，也就是说选股器**只能选出想买的票，
    # 选不出"该躲开"的票** —— 而规避持仓通常比选新票更急。

    # 超买/见顶：与 oversold_combo 严格镜像。超卖组合找超卖金叉，这组找超买。
    "overbought_combo": {
        "match_signals": ["RSI6超买", "CCI超买", "Williams%R超买",
                          "Z-Score超买", "MFI超买"],
        "min_count": 2,
        "direction": "bearish",
    },
    # 趋势转空：死叉 + 空头排列，与 anomaly（风险异动）互补 ——
    # anomaly 看资金流与波动，这组看趋势结构本身。
    #
    # 这条规则过去写着 `Vortex金叉`。direction="bearish" 的过滤是**逐信号**
    # 做的（screener.evaluate_group：allowed={"bearish"}），金叉是 bullish，
    # 于是它被静默丢掉、永远不参与计数 —— 三条规则实际只有两条在用，
    # min_count=2 就退化成"必须 MACD死叉 与 Aroon空头排列 同一根 bar 同时
    # 发生"，命中率远低于策略名字暗示的水平，而且没有任何报错。
    #
    # 换成方向相反的直接对偶 `Vortex死叉`（bearish），三条规则全部可用。
    # 名字、min_count、direction 一律没动。
    "trend_down": {
        "match_signals": ["MACD死叉", "Vortex死叉", "Aroon空头排列"],
        "min_count": 2,
        "direction": "bearish",
    },
    # Vortex 多头交叉的独立出口。min_count=1，与 hammer_reversal /
    # shooting_star_reversal 同一套「单信号单策略」的拆法。
    #
    # 为什么需要它：Vortex金叉原先唯一的引用就是 trend_down，而那条规则是
    # direction="bearish" —— 金叉是 bullish，被逐信号过滤掉，等于**算了但
    # 永远选不出来**。trend_down 改用死叉之后，这条 bullish 信号就没有任何
    # 策略引用了，test_no_signal_is_computed_but_unreachable 会变红。
    # 补一个真实的 bullish 出口，而不是把金叉塞回某条看跌规则里。
    "vortex_bull": {
        "match_signals": ["Vortex金叉"],
        "min_count": 1,
    },
    # 单根 K 线形态：锤头线（底部反转）与流星线（顶部反转）成对给出。
    # min_count=1 —— 单根形态本身就是一次信号，要求两个反而选不出票。
    # Hammer 是 bullish、Shooting Star 是 bearish，混在一个策略里会因为
    # direction 过滤而互相抵消，所以拆成两条单信号策略。
    "hammer_reversal": {
        "match_signals": ["Hammer锤子线"],
        "min_count": 1,
    },
    "shooting_star_reversal": {
        "match_signals": ["Shooting Star流星"],
        "min_count": 1,
        "direction": "bearish",
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

    # `limit_up` 走真实涨停池做候选集。
    #
    # 这条策略过去只匹配 CMF资金流入 / ADX多头趋势 / Aroon多头排列 三个**代理
    # 指标**，从头到尾没读过 special.v_limit_up_pool —— 名字叫「涨停选股」，
    # 实际筛的是"资金流入且趋势向上"，涨停与否毫无关系。库里躺着 29,657 行
    # 真实涨停记录却没用，是拿代理指标冒充目标概念。
    limit_up_pool: Optional[List[Dict[str, Any]]] = None
    pool_restricted = False
    if request.strategy == "limit_up":
        special_src = registry.get("special")
        if special_src is None:
            raise fail(503, "special 数据源未就绪，limit_up 策略需要真实涨停数据")
        try:
            limit_up_pool = await special_src.execute(
                screener.build_limit_up_pool_sql()
            )
        except Exception as exc:
            logger.warning("取涨停池失败: %s", exc)
            raise fail(
                503,
                f"取涨停池失败：{exc}。limit_up 策略只接受真实涨停数据，"
                f"不会退回代理指标",
            ) from exc

        if not limit_up_pool:
            raise fail(
                503,
                "涨停池为空（special.v_limit_up_pool 无数据）。"
                "limit_up 策略只接受真实涨停数据，不会退回代理指标",
            )

        pool_codes = {r.get("thscode") for r in limit_up_pool if r.get("thscode")}
        # 涨停池是一张**每日截断的榜**（实测每个交易日恰好 50 行），不是全市场。
        # 用它当候选集意味着本次只在这几十只里选，返回里必须写明池子规模，
        # 否则「涨停选股选出 20 只」会被误读成「全市场只有 20 只涨停」。
        pool_restricted = True
        names = {c: names.get(c, "") for c in pool_codes}
        if not names:
            raise fail(503, "涨停池里的代码都不在 A 股清单内，无法选股")

    all_codes = [c for c in names if c]

    # 板块限定放在取数**之前**：先缩小候选集，后面的指标/价量查询都只跑
    # 板块内的票。「半导体 + oversold_combo」从 5,571 只缩到 188 只，取数量和耗时
    # 同比下降，而不是先全市场选完再在内存里过滤。
    sector_filter: Optional[Dict[str, Any]] = None
    if request.sector:
        index_src = registry.get("index")
        if index_src is None:
            raise fail(503, "index 数据源未就绪，无法按板块筛选")
        from zettaranc import filters as screen_filters

        sector_rows: List[Dict[str, Any]] = []
        try:
            # 先精确匹配。用户说"银行"要的是行业「银行」42 只，不是
            # 概念「参股银行」194 只 + 股份制 + 国有的并集。
            sector_rows = await index_src.execute(
                screen_filters.build_sector_filter_sql(), [request.sector]
            )
            matched_mode = "exact"
            matched_names: List[Dict[str, Any]] = []
            if not sector_rows:
                # 精确没命中才回退片段匹配，并**列出到底命中了哪几个板块**。
                # 不说的话，用户拿到一个并集数字却无从复核。
                pattern = screen_filters.escape_like_pattern(request.sector)
                sector_rows = await index_src.execute(
                    screen_filters.build_sector_filter_sql_fuzzy(), [pattern]
                )
                matched_names = await index_src.execute(
                    screen_filters.build_sector_names_sql(), [pattern]
                )
                matched_mode = "fuzzy"
        except Exception as exc:
            logger.warning("按板块筛选失败: %s", exc)
            raise fail(503, f"按板块筛选失败：{exc}") from exc

        sector_codes = {r.get("thscode") for r in sector_rows if r.get("thscode")}
        if not sector_codes:
            raise fail(
                404,
                f"板块「{request.sector}」没有匹配到任何成分股。"
                f"可以试更短的片段，或先调用 index.sector.membership 查准确名称。",
            )
        sector_filter = {
            "name": request.sector,
            "constituents": len(sector_codes),
            "match_mode": matched_mode,
            "matched_sectors": matched_names,
            "source": screen_filters.SECTOR_SOURCE,
        }
        all_codes = [c for c in all_codes if c in sector_codes]
        if not all_codes:
            raise fail(
                404,
                f"板块「{request.sector}」的 {len(sector_codes)} 只成分股"
                f"都不在当前候选集内",
            )

    shards = screener.shard_codes(all_codes)
    if len(shards) > 1:
        logger.info(
            "选股 %s：%d 只切成 %d 片查询（单查询行数预算 %d）",
            request.strategy, len(all_codes), len(shards), screener.QUERY_ROW_BUDGET,
        )

    rows: List[Dict[str, Any]] = []
    price_rows: List[Dict[str, Any]] = []
    universe_truncated = False
    price_source = registry.get("market")

    for shard in shards:
        shard_csv = ",".join(shard)
        try:
            part = await indicators_src.execute(
                screener.build_indicator_snapshot_sql(),
                [shard_csv, screener.LOOKBACK_DAYS],
            )
        except Exception as exc:
            logger.warning("选股取指标失败(分片 %d 只): %s", len(shard), exc)
            raise fail(503, f"选股取数失败：{exc}") from exc
        rows.extend(part)
        if getattr(indicators_src, "last_result_truncated", False):
            # 分片本应保证不超预算；这里真被截断说明预算算错了，必须报出来
            # 而不是继续跑出一个"看起来正常、其实少了票"的结果。
            universe_truncated = True
            logger.error(
                "分片 %d 只仍被截断，QUERY_ROW_BUDGET=%d 对 LOOKBACK_DAYS=%d 偏大",
                len(shard), screener.QUERY_ROW_BUDGET, screener.LOOKBACK_DAYS,
            )

        # 价量维度：v_indicators_daily 没有任何价格列，close/volume 只能从
        # market.v_daily_qfq 单独取，再按 (thscode, date) 并回指标行。
        # 两个库是独立只读连接，不能 JOIN，只能在 Python 侧合。
        if price_source is not None:
            try:
                price_rows.extend(await price_source.execute(
                    screener.build_price_snapshot_sql(),
                    [shard_csv, screener.LOOKBACK_DAYS],
                ))
            except Exception as exc:
                # 价量拿不到不该让整个选股失败：没有价量只是"放量突破"这类
                # 形态信号不触发，其余指标信号照常。降级优先于报错。
                logger.warning("选股取价量失败(分片 %d 只): %s", len(shard), exc)
                price_source = None
            else:
                if getattr(price_source, "last_result_truncated", False):
                    universe_truncated = True
                    logger.error("价量分片 %d 只被截断", len(shard))

    if not rows:
        raise fail(503, "指标快照为空")

    price_merged = 0
    if price_source is not None and price_rows:
        price_merged = screener.merge_price_rows(rows, price_rows)
        if price_merged == 0:
            logger.warning(
                "价量与指标按 (thscode,date) 一条都没对上，形态信号将全部失效"
            )

    # 截断必须在这里说清楚。
    #
    # 旧实现把 `scanned` 报成 `len(names)` —— 那是**清单长度**，不是实际
    # 参与筛选的标的数。指标快照查询被 `max_rows` 截断时（indicators.duckdb
    # 有 12GB，全市场 × LOOKBACK_DAYS 天很容易撞上限），多出来的票被静默
    # 丢掉，而返回里的 `scanned` 仍然写着完整的 5,571 —— 调用方无从判断
    # 这次结果是不是全市场口径。
    scanned_codes = {r.get("thscode") for r in rows if r.get("thscode")}

    # 数据截止日。形态信号全部由最近 LOOKBACK_DAYS 天的指标算出，
    # 不说截止日的话，「今天这只票超卖」和「三天前的快照」长得一样。
    # 两个库各自可能有各自的最新日期（同步进度不同），所以分开报。
    indicator_as_of = max(
        (str(r.get("date")) for r in rows if r.get("date")), default=None
    )
    price_as_of = max(
        (str(r.get("date")) for r in price_rows if r.get("date")), default=None
    )
    # 「没有指标行」的基准必须是**实际送去查的候选集**，不是全市场清单。
    # 板块筛选把候选集缩到 320 只之后，若仍拿 5,571 只的 names 去算差集，
    # 会报出"5,251 只没有指标行"——而它们只是**不在这个板块里**，
    # 这条警告会把一个正常的板块限定说成大规模数据缺失。
    candidate_set = set(all_codes)
    # 清单里有、但一行指标都没回来的票：不是被截断，就是该票确实没有指标数据。
    # 两种情况对调用方的含义不同，都不该藏进 `scanned` 里。
    no_indicator = len(candidate_set) - len(candidate_set & scanned_codes)
    dropped = sorted(candidate_set - scanned_codes)

    # 风险筛选要跑在 `limit` **之后**才合理吗？不 —— 那会让用户要 8 只却只拿到 2 只：
    # 形态命中 866，取前 8 送筛，淘汰 6 剩 2。淘汰率最高的三项阈值
    # （负债 >50% 且流动比 <1）本地分布下能筛掉四分之三。
    #
    # 所以先按「用户要的数量 ÷ 预估通过率」多取一批，筛完再截到 limit。
    # 预估通过率没有先验，就用 1/3 做保守估计：三项风险阈值全开时
    # 本地 p50 负债 0.40、流动比 1.50，p10 负债约 0.70、流动比 0.60，
    # 三分之一通过是贴近实际的乐观估计；多取的量由 OVERSAMPLE_CAP 封顶，
    # 免得"要 200 只"变成扫全市场。
    OVERSAMPLE_CAP = 4
    risk_requested = any((
        request.max_debt_ratio is not None,
        request.min_current_ratio is not None,
        request.max_receivable_ratio is not None,
        request.require_profit,
        request.exclude_st,
    ))
    fetch_limit = request.limit * OVERSAMPLE_CAP if risk_requested else request.limit
    result = await asyncio.to_thread(
        screener.screen, rows, rule, fetch_limit, names
    )

    # ---- 财务风险代理筛选 ----
    # 只在用户明确要筛时才去查 financials。默认不查：多一次跨库查询，
    # 而绝大多数调用方只想看形态。
    risk_filter_applied: Optional[Dict[str, Any]] = None
    risk_rejects: Dict[str, str] = {}
    if (
        request.max_debt_ratio is not None
        or request.min_current_ratio is not None
        or request.max_receivable_ratio is not None
        or request.require_profit
        or request.exclude_st
    ):
        from zettaranc import filters as screen_filters

        financials_src = registry.get("financials")
        if financials_src is None:
            raise fail(503, "financials 数据源未就绪，无法做风险筛选")

        stocks = result["stocks"]
        codes = [s["thscode"] for s in stocks if s.get("thscode")]

        risk_map: Dict[str, Dict[str, Any]] = {}
        profit_map: Dict[str, Dict[str, Any]] = {}
        st_set: set = set()

        if (
            request.max_debt_ratio is not None
            or request.min_current_ratio is not None
            or request.max_receivable_ratio is not None
        ):
            try:
                for r in await financials_src.execute(
                    screen_filters.build_risk_filter_sql()
                ):
                    if r.get("thscode") in set(codes):
                        risk_map[r["thscode"]] = r
            except Exception as exc:
                logger.warning("取风险指标失败: %s", exc)
                raise fail(503, f"取财务风险指标失败：{exc}") from exc

        if request.require_profit:
            try:
                for r in await financials_src.execute(
                    screen_filters.build_profit_filter_sql()
                ):
                    if r.get("thscode") in set(codes):
                        profit_map[r["thscode"]] = r
            except Exception as exc:
                logger.warning("取利润数据失败: %s", exc)
                raise fail(503, f"取利润数据失败：{exc}") from exc

        if request.exclude_st:
            market_for_st = registry.get("market")
            if market_for_st is not None:
                try:
                    for r in await market_for_st.execute(
                        screen_filters.build_st_names_sql()
                    ):
                        if r.get("thscode") in set(codes):
                            st_set.add(r["thscode"])
                except Exception as exc:
                    logger.warning("取 ST 名单失败: %s", exc)
                    raise fail(503, f"取 ST 名单失败：{exc}") from exc

        kept: List[Dict[str, Any]] = []
        for s in stocks:
            code = s.get("thscode")
            entry = {
                "name": s.get("name", ""),
                "risk": risk_map.get(code),
                "profit": profit_map.get(code),
                "is_st": code in st_set,
            }
            reason = screen_filters.evaluate_risk_filters(
                entry,
                max_debt_ratio=request.max_debt_ratio,
                min_current_ratio=request.min_current_ratio,
                max_receivable_ratio=request.max_receivable_ratio,
                require_profit=request.require_profit,
                exclude_st=request.exclude_st,
            )
            if reason is not None:
                risk_rejects[code] = reason
                continue
            # 把实际数值与出处挂回结果，用户才能复核"为什么它过了"
            s["risk"] = {
                k: risk_map[code].get(k)
                for k in ("period", "debt_ratio", "current_ratio", "receivable_ratio")
            } if code in risk_map else None
            s["is_st"] = code in st_set
            s["risk_sources"] = [
                screen_filters.BALANCE_SOURCE,
                screen_filters.INCOME_SOURCE,
                screen_filters.ST_SOURCE,
            ]
            if entry.get("risk_missing"):
                s["risk_missing"] = entry["risk_missing"]
            kept.append(s)

        result["stocks"] = kept[: request.limit]
        risk_filter_applied = {
            "max_debt_ratio": request.max_debt_ratio,
            "min_current_ratio": request.min_current_ratio,
            "max_receivable_ratio": request.max_receivable_ratio,
            "require_profit": request.require_profit,
            "exclude_st": request.exclude_st,
            "evaluated": len(stocks),
            "rejected": len(risk_rejects),
            "passed": len(kept),
            "returned": len(result["stocks"]),
            # 请求 8 只但只给回 2 只时，这里要说清是"候选池不够"而不是
            # "只有 2 只合格"。两者对用户的行动含义完全不同。
            "short_of_request": max(0, request.limit - len(result["stocks"])),
            "oversample_factor": OVERSAMPLE_CAP,
            "sources": {
                "balance": screen_filters.BALANCE_SOURCE,
                "income": screen_filters.INCOME_SOURCE,
                "st": screen_filters.ST_SOURCE,
            },
        }

    warnings: List[str] = []
    if indicator_as_of:
        try:
            lag = (
                datetime.date.today() - datetime.date.fromisoformat(indicator_as_of[:10])
            ).days
        except ValueError:
            lag = -1
        if lag > 7:
            warnings.append(
                f"指标数据截止 {indicator_as_of}，距今 {lag} 天。"
                f"「当前超卖/金叉」是那一天的状态，不是今天的。"
            )
    if sector_filter:
        detail = ""
        if sector_filter["match_mode"] == "fuzzy" and sector_filter["matched_sectors"]:
            names = "、".join(
                f"{m.get('name')}({m.get('constituents')}只)" for m in sector_filter["matched_sectors"][:5]
            )
            detail = f"，按**片段**匹配命中：{names}"
            if len(sector_filter["matched_sectors"]) > 5:
                detail += " 等"
            detail += "。这些板块的并集，含概念板块（概念板块会把只是参股/沾边的公司也算进来）"
        warnings.append(
            f"候选集已限定在板块「{sector_filter['name']}」的 "
            f"{sector_filter['constituents']} 只成分股内{detail}"
            f"（出处：{sector_filter['source']}）"
        )
    if risk_filter_applied:
        if risk_filter_applied["short_of_request"]:
            warnings.append(
                f"请求 {request.limit} 只，按 {OVERSAMPLE_CAP}× 超取 "
                f"{risk_filter_applied['evaluated']} 只送筛后只剩 "
                f"{risk_filter_applied['returned']} 只通过 —— "
                f"**候选池不够**，不是市场里只有这么多合格的。"
                f"可放宽阈值或换策略。"
            )
        warnings.append(
            f"风险筛选淘汰 {risk_filter_applied['rejected']}/"
            f"{risk_filter_applied['evaluated']} 只："
            + "；".join(list(risk_rejects.values())[:3])
            + ("…" if len(risk_rejects) > 3 else "")
        )
        warnings.append(
            "风险维度只覆盖本地库能算出的三项（资产负债率/流动比率/应收占比）"
            "与 ST 名称匹配。商誉、股权质押、大股东减持、审计意见、监管处罚"
            "本地无数据源，**这些暴雷信号本次未被检查**。"
        )
    if pool_restricted and limit_up_pool:
        pool_dates = sorted({r.get("trade_date") for r in limit_up_pool if r.get("trade_date")})
        span = f"{pool_dates[0]} ~ {pool_dates[-1]}" if pool_dates else "未知区间"
        warnings.append(
            f"候选集来自 special.v_limit_up_pool 的真实涨停记录"
            f"（{len(limit_up_pool)} 条，{span}）。"
            f"该池是每日截断榜（每个交易日固定条数），不是全市场 —— "
            f"本次只在这 {len(names)} 只里筛选。"
        )
    if universe_truncated:
        warnings.append(
            f"取数被单次查询行数上限（{MAX_QUERY_ROWS} 行）截断，"
            f"只有 {len(scanned_codes)} 只票参与了本次筛选，结果不代表全市场。"
            f"请调小 QUERY_ROW_BUDGET / LOOKBACK_DAYS 后重试。"
        )
    if dropped:
        preview = "、".join(dropped[:10]) + ("…" if len(dropped) > 10 else "")
        warnings.append(
            f"{len(dropped)} 只票没有返回任何指标行，已排除在筛选之外：{preview}"
        )

    stocks = result["stocks"]
    if limit_up_pool:
        # 一只票在窗口内有多条涨停记录（连板股每天一行，连板数递增）。
        # 归并逻辑放在 screener 里，和它的单元测试同处，避免两处实现走样。
        by_code = screener.collapse_limit_up_pool(limit_up_pool)

        for s in stocks:
            hit = by_code.get(s.get("thscode"))
            if not hit:
                continue
            # 涨停事实来自 special 库，附上出处，模型才能区分
            # 「指标推断的强势」和「确实涨停过」
            s["limit_up"] = {
                "trade_date": hit.get("trade_date"),
                "continue_day_cnt": hit.get("continue_day_cnt"),
                "limit_up_time": hit.get("limit_up_time"),
                "seal_money": hit.get("seal_money"),
                "source": "special.v_limit_up_pool",
            }
        # 排序键必须用 `or 0` 兜住 None：continue_day_cnt 缺失时
        # `-(None)` 会 TypeError，把整个选股请求打挂。
        stocks.sort(
            key=lambda s: (
                -((s.get("limit_up") or {}).get("continue_day_cnt") or 0),
                -(s.get("score") or 0),
                s.get("thscode", ""),
            )
        )

    return {
        "success": True,
        "strategy": request.strategy,
        # 实际送去查的候选集大小。板块/涨停池限定后它会小于全市场 5,571，
        # 这正是"universe"该表达的意思：本次筛选覆盖了多少只。
        "universe": len(all_codes),
        "market_size": len(names),
        # 实际参与筛选的标的数，不是清单长度
        "scanned": len(scanned_codes),
        "scanned_from_universe": len(names),
        "incomplete": result["incomplete"],
        "matched": result["matched"],
        "unsupported_signals": screener.unsupported_signals(rule),
        "truncated": universe_truncated,
        # 价量维度是否并上。0 表示「放量突破」这类形态信号本次全部失效，
        # 必须让调用方知道，否则"没选出票"会被读成"市场里没有"。
        "price_merged_rows": price_merged,
        "price_available": price_source is not None,
        # 形态信号的出处。工具层（internal/agent/tools/hithink_finance）每一行
        # 结果都带 `_source`，这里是对齐：一次选股返回几十只票，模型据此
        # 回答"凭什么说它超卖"时要有可引用的表名，否则和凭空断言无异。
        # 价量是可选维度，取不到时据实去掉而不是写个空壳。
        "sources": {
            "signals": [
                "indicators.v_indicators_daily",
                *(
                    ["market.v_daily_qfq"]
                    if price_source is not None and price_merged > 0
                    else []
                ),
            ],
            "universe": "market.dim_symbol",
            "indicator_as_of": indicator_as_of,
            "price_as_of": price_as_of,
            "lookback_days": screener.LOOKBACK_DAYS,
        },
        "shards": len(shards),
        "sector_filter": sector_filter,
        "risk_filter": risk_filter_applied,
        "risk_rejects": risk_rejects,
        "no_indicator_count": max(0, no_indicator),
        "pool_size": len(limit_up_pool) if limit_up_pool else None,
        "pool_restricted": pool_restricted,
        "warnings": warnings,
        "stocks": stocks,
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


class FourBricksRequest(BaseModel):
    thscode: str
    days: int = 120


@app.post("/zettaranc/four-bricks")
async def zettaranc_four_bricks(request: FourBricksRequest) -> Dict[str, Any]:
    """四块砖 — 短线/趋势/多空/阴阳 四项多空状态。

    2026-10-01 新增。此前这四项只在工作台前端算（indicators.ts 的
    calcFourBricksDetails），服务端取不到，所以 agent_system_prompt 要求 agent
    对"四块砖什么状态"一律回答"算不出来，请去看 K 线工作台"。现在两边共用
    zettaranc/four_bricks.py 一份实现。

    返回**只有数值状态，没有战法解释**："红2 = 黄金买点"这类解读属于知识库，
    不在这里生成第二套说法。
    """
    from datasources import registry
    from zettaranc import analyze_four_bricks, fetch_market_data

    thscode = _require_thscode(request.thscode)
    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    try:
        rows = await fetch_market_data(market_src, thscode, request.days)
    except ValueError as exc:
        raise fail(404, str(exc)) from exc
    except Exception as exc:
        raise fail(503, f"数据加载失败：{exc}") from exc

    return {
        "success": True,
        "result": {"thscode": thscode, **analyze_four_bricks(rows)},
    }


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


# ===== HALO 年报事实链路 =====


class HaloSyncRequest(BaseModel):
    """抓取并抽取一只股票的年报事实。"""

    thscode: str = Field(..., description="6 位股票代码，如 600519")
    report_type: str = Field("annual", description="annual / h1 / q1 / q3")
    force: bool = Field(False, description="忽略缓存强制重跑")

    @field_validator("report_type")
    @classmethod
    def _check_report_type(cls, v: str) -> str:
        allowed = {"annual", "h1", "q1", "q3"}
        if v not in allowed:
            raise ValueError(f"report_type 必须是 {sorted(allowed)} 之一")
        return v


class HaloQueryRequest(BaseModel):
    """读取已落库的年报事实。"""

    thscode: str = Field(..., description="6 位股票代码")
    period: Optional[str] = Field(None, description="报告期日期，如 2025-12-31")
    report_type: Optional[str] = Field(None, description="annual / h1 / q1 / q3")
    fields: Optional[List[str]] = Field(None, description="只要指定字段")
    scope: Optional[str] = Field(None, description="consolidated / parent")
    only_verified: bool = Field(True, description="是否只返回可信记录")


@app.post("/halo/sync", dependencies=[Depends(require_api_key)])
async def halo_sync(request: HaloSyncRequest) -> Dict[str, Any]:
    """抓取巨潮年报 → 解析 → 抽取 → 对账 → 落表。

    耗时以分钟计（下载 1–10 MB 的 PDF 并逐页解析），所以同步重活放在线程池里跑，
    不占事件循环。
    """
    from halo import pipeline
    from halo.store import FactStore

    try:
        store = FactStore()
    except Exception as exc:
        raise fail(503, f"事实库不可用：{exc}") from exc

    try:
        result = await pipeline.sync_filing_async(
            request.thscode,
            report_type=request.report_type,
            force=request.force,
            store=store,
        )
    except Exception as exc:
        # 巨潮无该股票对应披露文件是业务错误（4xx），其余一律算服务端问题（5xx）。
        from halo.cninfo_source import NoFilingFoundError
        if isinstance(exc, NoFilingFoundError):
            raise fail(404, str(exc)) from exc
        raise fail(503, f"年报抽取失败：{exc}") from exc

    return jsonable_encoder({
        "thscode": result.thscode,
        "report_type": result.report_type,
        "period": result.period,
        "cached": result.cached,
        "pages_extracted": result.pages_extracted,
        "anchors": result.anchors,
        "written": result.written,
        "status_summary": result.status_summary,
        "missing_fields": result.missing_fields,
        "note": result.note,
        "records": result.records,
    })


class HaloScoreRequest(BaseModel):
    """对一只股票出评分结果。"""

    thscode: str = Field(..., description="6 位股票代码，如 600519")
    period: Optional[str] = Field(None, description="报告期日期，默认取最新已落库期次")
    report_type: str = Field("annual", description="annual / h1 / q1 / q3")
    scope: str = Field("consolidated", description="consolidated / parent")
    include_external: bool = Field(
        False,
        description="是否额外拉外网数据（治理/风险硬信号、估值历史分位、研报评级）。"
                    "默认关闭：分析是按需行为，外网慢且可能触发 IP 封禁。",
    )


@app.post("/halo/score", dependencies=[Depends(require_api_key)])
async def halo_score(request: HaloScoreRequest) -> Dict[str, Any]:
    """评分：Python 算完能量化的部分，并返回 7 个定性维度的待判槽位。

    七个定性维度（护城河/滞胀/ESG/管理层/资金面/估值/风险）由调用方判分，
    本端点只负责把每项的**量化锚点**算好一起返回，避免判分时凭印象。

    综合分不由本端点给出：各维度分收齐后调
    ``verify_comprehensive`` 复算校验（容差 0.05 + 评级同档）。
    """
    from halo import analyze as halo_analyze
    from halo.store import FactStore

    try:
        store = FactStore()
    except Exception as exc:
        raise fail(503, f"事实库不可用：{exc}") from exc

    result = await halo_analyze.analyze(
        request.thscode,
        store=store,
        period=request.period,
        report_type=request.report_type,
        scope=request.scope,
        include_external=request.include_external,
    )
    return jsonable_encoder(result)


class HaloVerifyRequest(BaseModel):
    """综合分复算校验。"""

    thscode: str = Field(..., description="6 位股票代码")
    period: Optional[str] = Field(None, description="报告期，默认取最新已落库期次")
    report_type: str = Field("annual", description="annual / h1 / q1 / q3")
    scores: Dict[str, float] = Field(
        ...,
        description="七个定性维度的分数（0-10）：moat/stag/esg/management/"
                    "shareholder/valuation/risk。HALO 与成长性由服务端从事实库"
                    "重算，**不接受传入值** —— 数据层不让模型覆写。",
    )
    declared_total: Optional[float] = Field(None, description="你声明的综合分")
    declared_rating: Optional[str] = Field(None, description="你声明的评级")


@app.post("/halo/verify", dependencies=[Depends(require_api_key)])
async def halo_verify(request: HaloVerifyRequest) -> Dict[str, Any]:
    """把七个定性维度的分与事实库重算的 HALO/成长性合成，复算并校验综合分。

    服务端只接受**定性维度**的分数；HALO 六维与成长性一律从事实库重算。
    理由与评分内核一致：让模型做算术会漏掉风险的负向项，而权重是确定的。
    """
    from halo import analyze as halo_analyze
    from halo.scoring import verify_comprehensive
    from halo.store import FactStore

    allowed = {"moat", "stag", "esg", "management", "shareholder", "valuation", "risk"}
    extra = sorted(set(request.scores) - allowed)
    if extra:
        raise fail(422, f"不接受这些维度：{extra}；只接受 {sorted(allowed)}")
    missing = sorted(allowed - set(request.scores))
    if missing:
        raise fail(422, f"缺少维度分数：{missing}。缺一维就无法复算综合分。")

    try:
        store = FactStore()
    except Exception as exc:
        raise fail(503, f"事实库不可用：{exc}") from exc

    result = await halo_analyze.analyze(
        request.thscode, store=store, period=request.period,
        report_type=request.report_type,
    )
    halo = result.get("halo") or {}
    if not halo.get("ok"):
        raise fail(422, halo.get("reason") or "HALO 不可计算，综合分无法复算")
    growth = result.get("growth") or {}
    if not growth or not growth.get("score") and not growth.get("total"):
        raise fail(422, "成长性不可计算，综合分无法复算")

    scores: Dict[str, float] = {
        "halo": float(halo["score"]),
        "growth": float(growth["score"]),
    }
    for k, v in request.scores.items():
        scores[k] = float(v)

    verdict = verify_comprehensive(
        request.declared_total, scores, declared_rating=request.declared_rating,
    )
    return jsonable_encoder({
        "thscode": result.get("thscode"),
        "period": result.get("period"),
        "server_recomputed": {
            "halo": {"score": halo["score"], "rating": halo["rating"]},
            "growth": {"score": growth["score"], "rating": growth["rating"]},
        },
        "accepted_ai_scores": scores,
        "verdict": verdict,
        "ok": bool(verdict.get("ok")),
    })


@app.post("/halo/query", dependencies=[Depends(require_api_key)])
async def halo_query(request: HaloQueryRequest) -> Dict[str, Any]:
    """读取年报事实。只读 SQLite，秒级返回。"""
    from halo import pipeline
    from halo.store import FactStore

    try:
        store = FactStore()
    except Exception as exc:
        raise fail(503, f"事实库不可用：{exc}") from exc

    return jsonable_encoder(pipeline.query_facts(
        request.thscode,
        store,
        period=request.period,
        report_type=request.report_type,
        fields=request.fields,
        scope=request.scope,
        only_verified=request.only_verified,
    ))


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


def _iso_date(d: Any) -> Optional[str]:
    """DuckDB 的 DATE 归一成 YYYY-MM-DD。取不到就返回 None，不猜、不补。"""
    if isinstance(d, datetime.date):
        return d.isoformat()
    if isinstance(d, str):
        return d[:10]
    return None


def _mean_of(rows: List[Dict[str, Any]], field: str) -> Optional[float]:
    """一段 K 线的某列均值。任一行为 None 就整体返回 None —— 不跳过、不用部分凑。"""
    vals = [r.get(field) for r in rows]
    if not vals or any(v is None for v in vals):
        return None
    return sum(float(v) for v in vals) / len(vals)


@app.get("/api/quotes")
@app.get("/quotes")
async def get_quotes(
    symbols: str = Query(
        ..., description="逗号分隔的 thscode 列表，如 600519.SH,000001.SZ（最多 200 只）"
    ),
):
    """批量行情快照 —— 取多只标的最新收盘价、前收盘价与涨跌幅。

    为什么需要它：自选列表按行渲染，逐行调 `/api/kline` 就是 N 次往返，而
    这个页面要回答的问题只有一个 —— "我盯的这些票今天什么状态"。一次请求
    取每只票的最后两根 K 线（最新 = 现价，倒数第二 = 前收盘）恰好够用。

    为什么返回 map 而不是数组：调用方按 thscode 逐行取用，数组会逼它在前端
    再建一次索引。

    `missing` / `invalid` 是**显式**字段而不是悄悄省略：
      * missing  = 格式合法但本地库里没有（未上市/退市/改代码）
      * invalid  = 连格式都不对（调用方的 bug）
    两者都不能被抹成 0 或空行 —— 0 在金融语义里是"真的等于零"，用它冒充
    缺失比留空危险得多（同一约定见 stock-profile 的字段约定）。

    涨跌幅在只有一根 K 线时是 **null 而不是 0**：新股上市首日没有"前一天"，
    报 0% 是在编一个不存在的读数。

    另外附 `ma5 / ma20 / vol_ma5 / volume_ratio`：这四个是由同一份 OHLCV
    直接算出的**读数**（不是判断，更不是建议），目的是让调用方在同一个响应里
    拿到「价格 / 涨跌幅 / 量比 / 离 MA20 多远」，从而只发一次请求。历史不足时
    同样是 null（例：只有 10 根 K 线 → `ma20 = null`），绝不用已有部分凑一个数。
    `volume_ratio` 的均量基准取**前 5 根（不含今日）**，否则放量当天会被自己抬高。
    """
    from datasources import registry

    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    requested: List[str] = []
    invalid: List[str] = []
    for token in symbols.split(","):
        raw = token.strip()
        if not raw:
            continue
        code = raw.upper()
        if not _THSCODE.match(code):
            invalid.append(raw)
        elif code not in requested:
            requested.append(code)

    if len(requested) > MAX_QUOTE_SYMBOLS:
        raise fail(422, f"一次最多查询 {MAX_QUOTE_SYMBOLS} 只标的，收到 {len(requested)} 只")

    if not requested:
        return {"code": 0, "data": {}, "missing": [], "invalid": invalid}

    # VALUES 的每一行必须带括号：`VALUES ?` 是语法错误（DuckDB 的 parser 在
    # 这里就报 "syntax error at or near ?"），`VALUES (?)` 才是合法的行构造。
    placeholders = ",".join("(?)" for _ in requested)
    # 每只标的两根 K 线走 LATERAL 子查询，而不是"先取全市场最大交易日再筛"：
    # 后者要在全表上算一次 max(date)，且会把停牌超过窗口的票判成无数据。
    sql = f"""
        SELECT s.thscode AS thscode, q.date AS date, q.open AS open, q.high AS high,
               q.low AS low, q.close AS close, q.volume AS volume, q.turnover AS turnover
        FROM (VALUES {placeholders}) AS s(thscode)
        JOIN LATERAL (
            SELECT date, open, high, low, close, volume, turnover
            FROM v_daily_qfq
            WHERE thscode = s.thscode
            ORDER BY date DESC
            -- 61 根而不是 2 根：调用方除了最新价与前收盘，还需要 5/20 日均线与
            -- 5 日均量来算「量比」和「是否站上 MA20」。上界是硬的（每票 ≤61 行，
            -- 200 票也只有 ~1.2 万行），不是无节制地拉历史。
            LIMIT 61
        ) q ON TRUE
        ORDER BY s.thscode, q.date
    """
    name_placeholders = ",".join("?" for _ in requested)
    try:
        rows = await market_src.execute(sql, requested)
        # 名称只做展示，但和价格查询共用同一个 try：两条查询打的是同一个
        # DuckDB，"名称挂了但价格还在"这种半边可用状态没有维护价值，
        # 而且两处失败应该长得一样（同一个 503），不能一个 503 一个 500。
        name_rows = await market_src.execute(
            f"SELECT thscode, name, exchange FROM v_symbol WHERE thscode IN ({name_placeholders})",
            requested,
        )
    except Exception as exc:
        logger.warning("批量行情查询失败: %s", exc)
        raise fail(503, f"查询行情失败: {exc}") from exc

    names = {r["thscode"]: r for r in name_rows}

    # 每个 thscode 最多两行，按日期升序：[-1] 最新，[-2] 前收盘。
    bars: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        bars.setdefault(r["thscode"], []).append(r)

    data: Dict[str, Dict[str, Any]] = {}
    for code, series in bars.items():
        latest = series[-1]
        prev = series[-2] if len(series) > 1 else None
        close = latest.get("close")
        prev_close = prev.get("close") if prev else None
        change = None
        change_pct = None
        if close is not None and prev_close:
            change = close - prev_close
            change_pct = change / prev_close * 100
        # 均线与量比：全部从同一份 OHLCV 直接算出，不含任何「观点」——
        # 它们存在的意义是让调用方（自选页 / 用户自定义条件判定）不必再发一次请求。
        # 历史不足就返回 null：只有 10 根就说 ma20 = null，绝不拿已有部分凑一个数。
        ma5 = _mean_of(series[-5:], "close") if len(series) >= 5 else None
        ma20 = _mean_of(series[-20:], "close") if len(series) >= 20 else None
        # 量比的基准取**前 5 根（不含今日）**：把今天算进均量会自己抬高自己，
        # 放量当天反而看不出放量。
        vol_ma5 = _mean_of(series[-6:-1], "volume") if len(series) >= 6 else None
        volume = latest.get("volume")
        volume_ratio = None
        if volume is not None and vol_ma5:
            volume_ratio = volume / vol_ma5
        meta = names.get(code) or {}
        data[code] = {
            "thscode": code,
            "name": meta.get("name") or "",
            "exchange": meta.get("exchange") or "",
            "date": _iso_date(latest.get("date")),
            "open": latest.get("open"),
            "high": latest.get("high"),
            "low": latest.get("low"),
            "close": close,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "volume": volume,
            "turnover": latest.get("turnover"),
            "ma5": ma5,
            "ma20": ma20,
            "vol_ma5": vol_ma5,
            "volume_ratio": volume_ratio,
        }

    return {
        "code": 0,
        "data": data,
        "missing": [c for c in requested if c not in data],
        "invalid": invalid,
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
    形态标注计算接口 — 识别指定股票的买卖形态 (B1建仓波, S1, 关键K, 暴力K)
    注意：这里的 B1 是 K 线**形态标注**（建仓波后第一次缩量回调、J<13），
    和选股策略 oversold_combo（超卖信号组合）不是一回事，别混用。
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


@app.get("/api/chart-pattern")
@app.get("/chart-pattern")
async def get_chart_pattern(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
    days: int = Query(250, ge=30, le=1000, description="回溯天数"),
    adjust: str = Query("forward", description="复权类型: none | forward | backward"),
):
    """
    形态识别接口 — 几何形态（头肩/双顶底/三角/楔形/旗形）+ 艾略特波浪。

    与 `/zettaranc/analyze` 的区别：那个返回的是**给人读的分析**（描述文本为主），
    这个返回的是**给图画的坐标**——每个形态都带关键点（日期 + 价格）与参考线
    （颈线/目标位/趋势边界），前端可以直接落成 overlay。

    行序注意：`analyze_chart_pattern` / `detect_elliott_waves` 都要求
    **rows[0] 是最新一根**，所以这里**不能**像 annotate 那样先 reverse。
    """
    from datasources import registry
    from zettaranc.candles import (
        CANDLE_PATTERNS,
        build_candle_sql,
        detect_candle_series,
        summarize_candles,
        validate_indicator_columns,
    )
    from zettaranc.data_loader import MARKET_FIELDS, normalize_row
    from zettaranc.divergence import build_divergence_sql, detect_divergence
    from zettaranc.pattern import analyze_chart_pattern
    from zettaranc.waves import detect_elliott_waves

    thscode = _require_thscode(symbol)
    if adjust not in _ADJUST_VIEWS:
        raise fail(400, f"无效的复权类型: {adjust}")

    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    view = _ADJUST_VIEWS[adjust]
    # 列名必须按分析层的约定来：检测器读的是 `vol`，而底层列叫 `volume`。
    # 直接 `SELECT volume` 会让 detect_double_top_bottom 抛 KeyError —— 这个坑
    # 只有走真实数据才会暴露（单测喂的是带 `vol` 的合成行）。约定集中在
    # data_loader.MARKET_COLUMNS，这里与它保持一致。
    fetch_limit = days + 60
    sql = f"""
        SELECT date, open, high, low, close, volume AS vol, turnover
        FROM {view}
        WHERE thscode = ?
        ORDER BY date DESC
        LIMIT {fetch_limit}
    """

    try:
        raw_rows = await market_src.execute(sql, [thscode])
    except Exception as exc:
        raise fail(503, f"查询 K 线数据失败: {exc}") from exc

    if not raw_rows:
        raise fail(404, f"未找到股票 {thscode} 的行情数据")

    # 与分析层同口径：date 走字符串，数值缺失保留 None（不用 0 冒充）。
    rows = [normalize_row(r, MARKET_FIELDS) for r in raw_rows]

    # 数据不足时如实说明，而不是静默返回空——调用方要能区分"没形态"和"没数据"。
    insufficient: List[str] = []
    chart_pattern = None
    waves = None

    try:
        chart_pattern = analyze_chart_pattern(rows)
    except ValueError as exc:
        insufficient.append(f"chart_pattern: {exc}")

    try:
        waves = detect_elliott_waves(rows)
    except Exception as exc:  # pragma: no cover - 防御性
        insufficient.append(f"waves: {exc}")

    # ---- 蜡烛形态 ----
    #
    # 走**独立查询**而不是把 24 个 cdl 列塞进 data_loader 的共享字段表：
    # /zettaranc/analyze 不需要这些列，塞进去等于让每次分析多传 24 列。
    candlesticks: List[Dict[str, Any]] = []
    candle_summary: Dict[str, Any] = {}
    divergences: List[Dict[str, Any]] = []
    indicators_src = registry.get("indicators")
    if indicators_src is None:
        insufficient.append("candlesticks: indicators 数据源未就绪")
    else:
        try:
            candle_rows = await indicators_src.execute(build_candle_sql(), [thscode, fetch_limit])
        except Exception as exc:
            candle_rows = []
            insufficient.append(f"candlesticks: 查询失败 {exc}")

        # 值域校验：这些列里混着 12 个被污染的（触发率 45%~72%、值域几十万种浮点），
        # 静默采信会在大部分 K 线上画出假形态。宁可少画，也不要画错的。
        bad_cols = validate_indicator_columns(candle_rows)
        if bad_cols:
            logger.warning("蜡烛形态列值域异常 thscode=%s: %s", thscode, bad_cols[:5])
            insufficient.append(f"candlesticks: {len(bad_cols)} 列值域异常，已跳过")

        candlesticks = detect_candle_series(candle_rows)
        candle_summary = summarize_candles(candlesticks)

        # ---- 指标背离 ----
        #
        # 与蜡烛形态共用同一个指标数据源，但**另发一条查询**：两者需要的列毫无交集
        # （那边是 cdl_*，这边是 dif/rsi14），塞进一条 SELECT 只会让两边互相牵连。
        try:
            div_rows = await indicators_src.execute(
                build_divergence_sql(), [thscode, fetch_limit]
            )
            divergences = detect_divergence(rows, div_rows)
        except Exception as exc:
            divergences = []
            insufficient.append(f"divergences: {exc}")

    return {
        "code": 0,
        "symbol": thscode,
        "days": len(rows),
        "data_source": "duckdb",
        "chart_pattern": chart_pattern,
        "waves": waves,
        "candlesticks": candlesticks,
        "candle_summary": candle_summary,
        "divergences": divergences,
        # 形态**目录**：前端下拉要用，放在这里是为了让它只有一份真相源。
        # 前端自己抄一份的话，后端点新增一种、前端不知道，用户就永远勾不到它。
        "candle_catalog": [
            {"key": p["key"], "name": p["name"], "desc": p["desc"]} for p in CANDLE_PATTERNS
        ],
        "insufficient_data": insufficient,
    }


@app.get("/api/stock-profile")
@app.get("/stock-profile")
async def stock_profile(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
):
    """
    个股速览：资金面 + 估值 + 板块归属，一次返回。

    为什么合并成一个接口而不是三个：这这些数据是给**鼠标划过就触发的悬浮框**用的，
    扫描 5 个 ticker 就会打 5 次。三个独立接口 = 15 个并发请求；合并成一个 = 5 个。

    三个区块**各自独立降级**：任何一个库查不到，只影响它自己那一块（该块为空并
    在 sources 里说明原因），不会让整张卡失败。查不到就是查不到，不返回 0 顶替 ——
    前端拿到 null 就显示「无数据」。
    """
    from datasources import registry

    if not _THSCODE.match(symbol):
        return {"code": 0, "symbol": symbol, "capital": None, "valuation": None,
                "sectors": [], "unavailable": ["代码格式非法"]}

    unavailable: list[str] = []

    async def safe(db: str, sql: str, params: list) -> list:
        """查不到就返回空列表并记录原因，绝不把异常冒给前端。"""
        src = registry.get(db)
        if src is None:
            unavailable.append(db)
            return []
        try:
            return await src.execute(sql, params)
        except Exception as exc:  # noqa: BLE001 —— 降级优先于报错
            logger.warning("stock_profile 查询 %s 失败：%s", db, exc)
            unavailable.append(db)
            return []

    # ---- 资金面 ----
    limit_up = await safe(
        "special",
        """SELECT trade_date, continue_day_cnt, limit_up_time, seal_money
           FROM v_limit_up_pool WHERE thscode = ?
           ORDER BY trade_date DESC LIMIT 10""",
        [symbol],
    )
    limit_break = await safe(
        "special",
        """SELECT trade_date, open_times, price_change_ratio
           FROM v_limit_break_pool WHERE thscode = ?
           ORDER BY trade_date DESC LIMIT 10""",
        [symbol],
    )
    dragon = await safe(
        "special",
        """SELECT trade_date, board_type, net_value, net_rate, org_net_value
           FROM v_dragon_tiger WHERE thscode = ?
           ORDER BY trade_date DESC LIMIT 10""",
        [symbol],
    )
    hot = await safe(
        "special",
        """SELECT capture_date, period, rank, heat
           FROM v_hot_stock WHERE thscode = ?
           ORDER BY capture_date DESC LIMIT 10""",
        [symbol],
    )

    capital: dict | None = None
    if any([limit_up, limit_break, dragon, hot]):
        capital = {
            "limit_up": limit_up or None,
            "limit_break": limit_break or None,
            "dragon_tiger": dragon or None,
            "hot": hot or None,
            "sources": {
                "limit_up": "special.v_limit_up_pool",
                "limit_break": "special.v_limit_break_pool",
                "dragon_tiger": "special.v_dragon_tiger",
                "hot": "special.v_hot_stock",
            },
        }
    else:
        capital = None

    # ---- 估值（只取一行最新快照）----
    val_rows = await safe(
        "financials",
        """SELECT snapshot_date, pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm
           FROM v_valuation_latest WHERE thscode = ? LIMIT 1""",
        [symbol],
    )
    valuation = None
    if val_rows:
        r = val_rows[0]
        valuation = {
            "snapshot_date": r.get("snapshot_date"),
            "pe_ttm": r.get("pe_ttm"),
            "pe_mrq": r.get("pe_mrq"),
            "pb_mrq": r.get("pb_mrq"),
            "ps_ttm": r.get("ps_ttm"),
            "pcf_ttm": r.get("pcf_ttm"),
            "source": "financials.v_valuation_latest",
        }

    # ---- 板块归属：constituents（个股→板块）+ universe（板块名/类型）----
    sector_rows = await safe(
        "index",
        """SELECT u.name, u.tag
           FROM v_index_constituents c
           JOIN v_index_universe u ON u.thscode = c.index_thscode
           WHERE c.thscode = ?
           ORDER BY u.tag, u.name""",
        [symbol],
    )
    sectors = [{"name": r.get("name"), "tag": r.get("tag")} for r in sector_rows] or []

    return {
        "code": 0,
        "symbol": symbol,
        "capital": capital,
        "valuation": valuation,
        "sectors": sectors,
        "sector_source": "index.v_index_constituents ⋈ index.v_index_universe",
        "unavailable": sorted(set(unavailable)),
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


# 股票代码格式：6 位数字 + 交易所后缀。用于 /symbols/resolve 的入参白名单，
# 只有通过这个正则的值才会拼进 SQL 查询，天然免疫注入。
THSCODE_RE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


@app.post("/api/symbols/resolve")
@app.post("/symbols/resolve")
async def resolve_symbols(request: Request):
    """
    批量校验股票代码 + 回填权威名称

    存在的理由：从模型回答里抽出来的股票代码是**未经校验的自由文本**，实测两类
    坏东西会同时混进候选池：
      1. 幻觉代码——模型把 600487（亨通光电）写成 688487。688 号段本地有 617 只
         且分布稠密，688485/486/488/489 都在，唯独 688487 是从未发行的空洞。
         这样的代码进了候选池，点下去就是一片黑，因为本地没有它的行情。
      2. name 是代码——抽取正则 `([\\u4e00-\\u9fa5A-Za-z0-9]{2,8})[（(](\\d{6})[)）]`
         会把 `600105（600101.SH）` 里的纯数字当股票名，于是池子出现
         "600105 600101.SH" 这种 name 与 code 相同的条目。

    这个接口一次查询解决两件事：剔掉本地不存在的代码，并用 v_symbol 的权威名称
    覆盖抽取阶段猜出来的 name。valid=false 的项前端应从候选池剔除。
    """
    from datasources import registry

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    raw = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return {"code": 0, "data": []}

    # 严格校验格式后再入参：只放行 6 位数字 + .SH/.SZ/.BJ，天然免疫注入。
    symbols: list[str] = []
    for item in raw[:200]:  # 限长防滥用
        if not isinstance(item, str):
            continue
        code = item.strip().upper()
        if THSCODE_RE.match(code):
            symbols.append(code)
    unique = list(dict.fromkeys(symbols))
    if not unique:
        return {"code": 0, "data": []}

    market_src = registry.get("market")
    if market_src is None:
        raise fail(503, "market 数据源未就绪")

    placeholders = ",".join("?" for _ in unique)
    rows = await market_src.execute(
        f"SELECT thscode, ticker, name, exchange FROM v_symbol WHERE thscode IN ({placeholders})",
        unique,
    )
    by_code = {r["thscode"]: r for r in rows}

    return {
        "code": 0,
        "data": [
            {
                "thscode": code,
                "valid": code in by_code,
                # 回退到代码本身而不是留空，是为了让前端能显示 "688487.SH"
                # 这个可诊断的标签，而不是一个空白 chip。
                "name": (by_code.get(code) or {}).get("name") or code,
                "ticker": (by_code.get(code) or {}).get("ticker") or code.split(".")[0],
                "exchange": (by_code.get(code) or {}).get("exchange") or code.split(".")[1],
            }
            for code in unique
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
               zettaranc_brick_value, zettaranc_rsl_rank_15, zettaranc_rsl_rank_105
        FROM (
            SELECT date, zettaranc_zg_white_10, zettaranc_dg_yellow_14, zettaranc_bbi,
                   zettaranc_brick_value, zettaranc_rsl_rank_15, zettaranc_rsl_rank_105
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
