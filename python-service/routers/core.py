"""
核心路由：/health、/、/query/*、/cache/*，以及 K 线 / 报价 / 标注 / 形态 /
画像 / 代码表 / 指标这批（/api/kline、/api/quotes、/api/annotate、
/api/chart-pattern、/api/stock-profile、/api/symbols/*、/api/indicators，
以及各自的裸路径别名）。
"""

import datetime
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from core import (
    API_KEY,
    DEFAULT_QUERY_LIMIT,
    DUCKDB_DATABASES,
    MAX_QUERY_ROWS,
    MAX_QUOTE_SYMBOLS,
    SERVICE_VERSION,
    _MARKET_DATASET,
    _THSCODE,
    _apply_limit,
    _dataset_for,
    _is_board,
    _iso_date,
    _param_key,
    _read_only_sql,
    _require_stock_thscode,
    _require_thscode,
    fail,
    logger,
    require_api_key,
)
from datasources.cache import stable_hash
from datasources.duckdb_source import DuckDBCorruptReadError

router = APIRouter()


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


# ===== 路由 =====

def _generations() -> Dict[str, int]:
    """各 DuckDB 源的数据代次（底层文件变化 → 重开连接的次数）。"""
    from datasources import registry
    return {
        name: src.generation
        for name, src in registry.list_sources().items()
        if hasattr(src, "generation")
    }


@router.get("/health")
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


@router.get("/")
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


@router.get("/query/databases")
async def list_databases():
    """列出可用的 DuckDB 数据库"""
    from datasources import registry
    from datasources.base import DataSourceType

    databases = [
        name for name, source in registry.list_sources().items()
        if source.source_type == DataSourceType.DUCKDB
    ]
    return {"databases": databases}


@router.post("/query/")
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
    except DuckDBCorruptReadError as exc:
        # 读到写到一半的库文件 = 服务端状态问题（宿主机 ETL 正在写这个库），
        # 不是调用方 SQL 的问题 —— 4xx 会把调用方（含模型）引向改 SQL 的错方向。
        logger.warning("底层库正在被写入 db=%s: %s", request.db, exc)
        raise fail(503, str(exc)) from exc
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


@router.get("/cache/stats")
async def cache_stats():
    """缓存统计信息"""
    from datasources import cache
    return {"memory_size": cache.memory_size, "stats": cache.stats}


@router.post("/cache/clear")
async def cache_clear():
    """清空缓存（内存 + Redis 两级）"""
    from datasources import cache
    await cache.clear_all()
    return {"success": True, "message": "内存与 Redis 缓存均已清空"}


# ===== KLine 与形态图表 API (前端 KLineChart Pro 直连) =====

# 个股的复权视图。与 `_MARKET_DATASET.view_by_adjust` 是同一份数据，这里保留别名
# 是因为 annotate / chart-pattern 两个端点只服务个股，直接用这个名字更直白；
# 不要再写一份字面量，否则改视图名会漏掉一半。
_ADJUST_VIEWS = _MARKET_DATASET.view_by_adjust


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


@router.get("/api/kline")
@router.get("/kline")
async def get_kline(
    symbol: str = Query(..., description="标的代码：个股 600519.SH / 板块指数 881101.TI"),
    period: str = Query("day", description="K线周期: day | week | month"),
    adjust: str = Query("forward", description="复权类型: none | forward | backward（板块无复权数据，三种取值等价）"),
    from_date: Optional[str] = Query(None, alias="from", description="起始日期 YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, alias="to", description="结束日期 YYYY-MM-DD"),
    limit: int = Query(5000, ge=1, le=20000, description="最大K线根数"),
):
    """
    K 线数据查询接口 — 输出与 KLineChart Pro 兼容的 OHLCV 数组

    数据集按代码后缀路由（见 `_Dataset`）：`.TI` 走 index 库的 `v_index_daily`
    （日期列叫 `trade_date`，且没有复权列），其余走 market 库。
    """
    from datasources import registry

    thscode = _require_thscode(symbol)
    dataset = _dataset_for(thscode)
    if adjust not in dataset.view_by_adjust:
        raise fail(400, f"无效的复权类型: {adjust}，可用: none, forward, backward")
    if period not in ("day", "week", "month"):
        raise fail(400, f"无效的周期: {period}，可用: day, week, month")

    src = registry.get(dataset.datasource)
    if src is None:
        raise fail(503, f"{dataset.datasource} 数据源未就绪")

    view = dataset.view_by_adjust[adjust]
    date_col = dataset.date_column
    conditions = ["thscode = ?"]
    params: List[Any] = [thscode]

    if from_date:
        conditions.append(f"{date_col} >= ?")
        params.append(from_date)
    if to_date:
        conditions.append(f"{date_col} <= ?")
        params.append(to_date)

    where_clause = " AND ".join(conditions)

    if period in ("week", "month"):
        trunc = f"date_trunc('week', {date_col})" if period == "week" else f"date_trunc('month', {date_col})"
        if from_date:
            sql = f"""
                SELECT
                    {trunc} as date,
                    arg_min(open, {date_col}) as open,
                    max(high) as high,
                    min(low) as low,
                    arg_max(close, {date_col}) as close,
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
                        arg_min(open, {date_col}) as open,
                        max(high) as high,
                        min(low) as low,
                        arg_max(close, {date_col}) as close,
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
                SELECT {date_col} as date, open, high, low, close, volume, turnover
                FROM {view}
                WHERE {where_clause}
                ORDER BY date ASC
                LIMIT {limit}
            """
        else:
            sql = f"""
                SELECT date, open, high, low, close, volume, turnover
                FROM (
                    SELECT {date_col} as date, open, high, low, close, volume, turnover
                    FROM {view}
                    WHERE {where_clause}
                    ORDER BY {date_col} DESC
                    LIMIT {limit}
                ) sub
                ORDER BY date ASC
            """

    try:
        rows = await src.execute(sql, params)
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


def _mean_of(rows: List[Dict[str, Any]], field: str) -> Optional[float]:
    """一段 K 线的某列均值。任一行为 None 就整体返回 None —— 不跳过、不用部分凑。"""
    vals = [r.get(field) for r in rows]
    if not vals or any(v is None for v in vals):
        return None
    return sum(float(v) for v in vals) / len(vals)


@router.get("/api/quotes")
@router.get("/quotes")
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
    unsupported: List[str] = []
    for token in symbols.split(","):
        raw = token.strip()
        if not raw:
            continue
        code = raw.upper()
        if not _THSCODE.match(code):
            invalid.append(raw)
        elif _is_board(code):
            # 板块/指数没有个股快照：v_daily_qfq 里没有 .TI 行，放过去只会被算进
            # missing，调用方就把它显示成"查不到该标的的行情数据"——把"不支持"
            # 说成了"没有数据"。单独一列出来，调用方才能给出正确的下一步。
            unsupported.append(code)
        elif code not in requested:
            requested.append(code)

    if len(requested) > MAX_QUOTE_SYMBOLS:
        raise fail(422, f"一次最多查询 {MAX_QUOTE_SYMBOLS} 只标的，收到 {len(requested)} 只")

    if not requested:
        return {"code": 0, "data": {}, "missing": [], "invalid": invalid, "unsupported": unsupported}

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
        "unsupported": unsupported,
    }


@router.get("/api/annotate")
@router.get("/annotate")
async def get_annotations(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
    days: int = Query(120, ge=10, le=1000, description="回溯天数"),
    patterns: str = Query(
        "b1,key_k,s1,violent_k,pole_yang,pile_yang,capping_yin,fake_yin,rebound_trap",
        description="形态类型，逗号分隔 (b1,key_k,s1,violent_k,pole_yang,pile_yang,capping_yin,fake_yin,rebound_trap)",
    ),
    adjust: str = Query("forward", description="复权类型: none | forward | backward"),
):
    """
    形态标注计算接口 — 识别指定股票的买卖形态 (B1建仓波, S1, 关键K, 暴力K, 以及重剑无锋立杆阳/打桩阳/灌顶阴/伪阴线/回光返照)
    注意：这里的 B1 是 K 线**形态标注**（建仓波后第一次缩量回调、J<13），
    和选股策略 oversold_combo（超卖信号组合）不是一回事，别混用。
    """
    from datasources import registry
    from zettaranc.annotator import annotator

    thscode = _require_stock_thscode(symbol)
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


@router.get("/api/chart-pattern")
@router.get("/chart-pattern")
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

    thscode = _require_stock_thscode(symbol)
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


@router.get("/api/stock-profile")
@router.get("/stock-profile")
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
    if _is_board(symbol):
        # 与"格式非法"分开报：格式非法的下一步是改写法，板块的下一步是换标的。
        # 与"数据源查不到"（`unavailable`）也分开：板块是**本来就没有**资金面/估值/
        # 归属，重试多少次都一样；混进 unavailable 会被读成同步出了问题。
        return {"code": 0, "symbol": symbol.upper(), "capital": None, "valuation": None,
                "sectors": [], "unavailable": [],
                "unsupported": ["板块/指数没有资金面与管理层数据（仅个股提供）"]}

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


@router.get("/api/symbols/search")
@router.get("/symbols/search")
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
    data = [
        {
            "thscode": r["thscode"],
            "ticker": r["ticker"],
            "name": r["name"],
            "exchange": r["exchange"],
            "asset_type": r.get("asset_type", "a-share"),
        }
        for r in rows
    ]

    # 板块/指数来自另一个库（index.v_index_universe），与 v_symbol 是两个独立的
    # DuckDB 连接（没有 ATTACH），只能在 Python 里合并；不合并的话搜索框永远搜不到
    # 板块——v_symbol 里 5571 行全是 a-share，一行 .TI 都没有。
    #
    # v_index_universe 只有 (thscode, name, tag)：没有 ticker/exchange 列，所以
    # ticker 取 6 位数字、exchange 固定 TI，asset_type 用 tag（industry /
    # cn_concept / tzs / region）。调用方按 asset_type 区分"板块"与个股。
    index_src = registry.get("index")
    if index_src is not None:
        board_rows = await index_src.execute(
            """
            SELECT thscode, name, tag
            FROM v_index_universe
            WHERE thscode LIKE ? OR name LIKE ?
            ORDER BY name ASC
            LIMIT ?
            """,
            [like_pat, like_pat, limit],
        )
        data.extend(
            {
                "thscode": r["thscode"],
                "ticker": r["thscode"].split(".")[0],
                "name": r["name"],
                "exchange": "TI",
                "asset_type": r.get("tag") or "board",
            }
            for r in board_rows
        )

    return {"code": 0, "data": data}


# 股票代码格式：6 位数字 + 交易所后缀。用于 /symbols/resolve 的入参白名单，
# 只有通过这个正则的值才会拼进 SQL 查询，天然免疫注入。
#
# 故意**不含** `.TI`：这个端点服务的是"从模型回答的文本里抽出代码再校验回填名称"
# 这条链路（正文点票签 / 提及条）。板块的文本识别是单独一期的活（`881101` 与
# 订单号、日期在文本里长得一样，放开要连带改前端两处抽取器的判据），在那之前
# 让板块代码在这里解析失败并被丢弃，比猜测成别的标的更安全。
THSCODE_RE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


@router.post("/api/symbols/resolve")
@router.post("/symbols/resolve")
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


@router.get("/api/indicators")
@router.get("/indicators")
async def get_indicators(
    symbol: str = Query(..., description="股票代码，如 600519.SH"),
    days: int = Query(500, ge=1, le=5000),
    categories: str = Query("zettaranc", description="指标类别"),
):
    """
    个股指标时序数据接口
    """
    from datasources import registry

    thscode = _require_stock_thscode(symbol)
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
