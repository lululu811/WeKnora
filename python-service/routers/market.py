"""
大盘预览与市场面板路由：/api/market/*、/watchlist/industry-map。
"""

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from core import _iso_date, fail, logger

router = APIRouter()


# ===== 大盘预览（/dashboard）=====
#
# 三条数据链路，各自有坑，逐条记在这里免得下次重新踩：
#
# 1. **指数快照不来自 `index.v_index_latest`**。那个视图只装 `.TI` 同花顺板块
#    （实测 848 行全是 881xxx.TI / 9xxxxx.TI），四大指数 000001.SH / 399001.SZ /
#    399006.SZ / 000300.SH 一个都不在里面。指数行情在 `index.v_index_daily`，
#    按 thscode 取最近一根即可（每个约 1223 行，代价可忽略）。
#
# 2. **指数 60 日曲线不能走 `/api/kline`**。`/api/kline` 按**后缀**路由数据集
#    （`_dataset_for`）：`.TI` → index 库，其余 → market 库。四大指数是 `.SH/.SZ`
#    而非 `.TI`，所以会被路由到 market 库的 `v_daily_qfq`，而那里 0 行 ——
#    请求会 200 返回空数组，看起来像"没数据"而不是"路由错了"。所以 60 日序列
#    在这里直接查 `v_index_daily` 一并返回。
#
# 3. **龙虎榜上榜原因本地没有**。`v_dragon_tiger.board_type` 只有 `all` / `org`
#    两个值，语义是"全部榜/机构榜"这个**榜单口径**，不是交易所的上榜原因枚举
#    （日涨幅偏离值达 7% / 三日累计涨幅偏离达 20% / 日换手率达 20% …）。
#    原实现同源的 `special/dragon_tiger.go` 也写明"上榜原因(reason)不在本地数据里"。
#    所以这里不编造原因，改为把本地**确实有**的口径翻译成人话：
#    榜单类型 + 统计区间（range_days）。样稿里的原因文案需要另接交易所源才有。

# 四大指数：展示顺序即此顺序，与设计稿一致。
_MARKET_MAIN_INDICES: List[tuple[str, str]] = [
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000300.SH", "沪深300"),
]


# 指数快照条：上证50 / 科创50 / 北证50 / 中证500 / 中证1000
_MARKET_TICKER_INDICES: List[tuple[str, str]] = [
    ("000016.SH", "上证50"),
    ("000688.SH", "科创50"),
    ("899050.BJ", "北证50"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
]


# 权重 ETF：宽基六只，代码写死而不是让前端传 —— 这格的口径是"宽基"，
# 换一批就换了一个语义，不该由调用方每次拼一遍。
_MARKET_ETFS: List[tuple[str, str]] = [
    ("510300.SH", "300ETF"),
    ("510050.SH", "50ETF"),
    ("510500.SH", "500ETF"),
    ("588000.SH", "科创50ETF"),
    ("159915.SZ", "创业板ETF"),
    ("512100.SH", "1000ETF"),
]


# 60 日曲线的取数根数。样稿的折线窗口是 60 个交易日。
_MARKET_INDEX_SERIES_DAYS = 60


# 近 5 日涨停家数柱状图。
_MARKET_LIMIT_UP_TREND_DAYS = 5


# 市场状态评估的基准指数。选沪深300 而非上证：回测全部以它为基准
# （`scripts/backtest_market_state.py`），口径必须一致，否则对比无意义。
_MARKET_STATE_INDEX = "000300.SH"


# 龙虎榜榜单元数据 → 人话。本地只有这两个值（见文件头第 3 条）。
_DRAGON_BOARD_TYPE_LABELS = {"all": "全部榜", "org": "机构榜"}


def _f(value: Any) -> Optional[float]:
    """DuckDB 的 DOUBLE/DECIMAL 归一成 float。None 就是 None，不补 0。"""
    if value is None:
        return None
    return float(value)


def _dragon_board_tag(row: Dict[str, Any]) -> str:
    """把龙虎榜一行拼成一句人能读的来源说明。

    **只描述本地有的字段**。上榜原因（交易所枚举）本地不存在，编一个出来就是
    假数据；这里给的是"哪个榜单口径 + 几日统计"，都直接来自
    `board_type` / `range_days` 两列。
    """
    board = _DRAGON_BOARD_TYPE_LABELS.get((row.get("board_type") or "").lower())
    days = row.get("range_days")
    parts: List[str] = []
    if board:
        parts.append(board)
    if days is not None:
        try:
            d = int(days)
        except (TypeError, ValueError):
            d = 0
        parts.append(f"{d} 日累计" if d > 1 else "当日")
    return " · ".join(parts) if parts else "龙虎榜"


@router.get("/api/market/snapshot")
@router.get("/market/snapshot")
async def market_snapshot(
    days: int = Query(
        _MARKET_INDEX_SERIES_DAYS, ge=10, le=250, description="指数曲线回看交易日数"
    ),
):
    """大盘预览聚合快照 —— 指数、情绪、ETF 一次返回。

    为什么合并成一个接口：工作台入口横幅只想要"上证涨跌 + 涨停跌停家数"两三个数，
    大屏要的是全部九格。拆成 4~5 个接口意味着横幅和大屏各打一轮，而它们读的
    是同一批表、同一批交易日 —— 一次查询、一次往返。

    **降级策略**：每个数据块独立降级（与 `/api/stock-profile` 同一套约定）。
    任何一个库不可用只让对应块为空并记进 `unavailable`，不拖垮整页 ——
    涨跌停池没同步时，指数和 ETF 仍然要能显示。

    **空 ≠ 零**：查不到就是查不到。涨停家数缺失时 `limit_up` 是 null 而不是 0，
    因为 0 在金融语义里是"今天真的没有票涨停"，用它顶替缺失会读成一个假结论
    （同一约定见 `/api/quotes` 的 missing 字段说明）。
    """
    from datasources import registry

    unavailable: List[str] = []

    async def safe(db: str, sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
        """查不到就返回空列表并记录原因，绝不把异常冒给前端。"""
        src = registry.get(db)
        if src is None:
            unavailable.append(db)
            return []
        try:
            return await src.execute(sql, params or [])
        except Exception as exc:  # noqa: BLE001 —— 降级优先于报错
            logger.warning("market_snapshot 查询 %s 失败：%s", db, exc)
            unavailable.append(db)
            return []

    # ---- 指数：快照 + 60 日曲线 ----
    # 四大指数与快照条共 9 个代码，一次 LATERAL 取回每个代码最近 days 根，
    # 前端要哪条曲线自己按 code 取。快照 = 序列最后一根，前收 = 倒数第二根。
    index_rows = await safe(
        "index",
        """
        SELECT s.thscode AS thscode, q.trade_date AS trade_date,
               q.open AS open, q.high AS high, q.low AS low, q.close AS close
        FROM (VALUES {placeholders}) AS s(thscode)
        JOIN LATERAL (
            SELECT trade_date, open, high, low, close
            FROM v_index_daily
            WHERE thscode = s.thscode
            ORDER BY trade_date DESC
            LIMIT ?
        ) q ON TRUE
        ORDER BY s.thscode, q.trade_date
        """.format(
            placeholders=",".join("(?)" for _ in range(len(_MARKET_MAIN_INDICES) + len(_MARKET_TICKER_INDICES)))
        ),
        [c for c, _ in _MARKET_MAIN_INDICES]
        + [c for c, _ in _MARKET_TICKER_INDICES]
        + [days],
    )

    series_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for r in index_rows:
        series_by_code.setdefault(r["thscode"], []).append(r)

    def _index_snapshot(code: str, display: str) -> Optional[Dict[str, Any]]:
        bars = series_by_code.get(code) or []
        if not bars:
            return None
        last = bars[-1]
        prev = bars[-2] if len(bars) > 1 else None
        close = _f(last.get("close"))
        prev_close = _f(prev.get("close")) if prev else None
        change_pct = None
        if close is not None and prev_close:
            change_pct = (close - prev_close) / prev_close * 100
        return {
            "thscode": code,
            "name": display,
            "trade_date": _iso_date(last.get("trade_date")),
            "last": close,
            "prev_close": prev_close,
            "open": _f(last.get("open")),
            "high": _f(last.get("high")),
            "low": _f(last.get("low")),
            "change_pct": change_pct,
            # 曲线按时间正序返回：前端直接画，不用再反转。
            "series": [
                {"date": _iso_date(b.get("trade_date")), "close": _f(b.get("close"))}
                for b in bars
            ],
        }

    indices = [
        s for s in (_index_snapshot(code, name) for code, name in _MARKET_MAIN_INDICES) if s
    ]
    tickers = [
        s for s in (_index_snapshot(code, name) for code, name in _MARKET_TICKER_INDICES) if s
    ]

    # ---- 情绪：涨跌停 / 炸板 / 最高连板 / 近 5 日涨停 ----
    # 四个计数共用"最新交易日"这一个口径：先各自取 MAX(trade_date)，
    # 再按该日期 COUNT。如果四块各自的最新日不同（同步进度不一致），拼出来的
    # 情绪格会是不同日子加减出来的数 —— 那比缺一块更糟。
    latest_row = await safe(
        "special",
        "SELECT MAX(trade_date) AS d FROM v_limit_up_pool",
    )
    trade_date = _iso_date(latest_row[0]["d"]) if latest_row and latest_row[0].get("d") else None

    limit_up = limit_down = broken = max_streak = None
    streak_name: Optional[str] = None
    lu5: List[Dict[str, Any]] = []
    breadth: Dict[str, Optional[int]] = {"up": None, "flat": None, "down": None}

    if trade_date:
        counts = await safe(
            "special",
            """
            SELECT
              (SELECT count(*) FROM v_limit_up_pool WHERE trade_date = ?) AS limit_up,
              (SELECT count(*) FROM v_limit_down_pool WHERE trade_date = ?) AS limit_down,
              (SELECT count(*) FROM v_limit_break_pool WHERE trade_date = ?) AS broken
            """,
            [trade_date, trade_date, trade_date],
        )
        if counts:
            limit_up = int(counts[0]["limit_up"] or 0)
            limit_down = int(counts[0]["limit_down"] or 0)
            broken = int(counts[0]["broken"] or 0)

        # 最高连板：取连板数最大的一只，同数时按封单金额定先后（封单大的是市场龙头）。
        streak = await safe(
            "special",
            """SELECT name, continue_day_cnt FROM v_limit_up_pool
               WHERE trade_date = ? AND continue_day_cnt IS NOT NULL
               ORDER BY continue_day_cnt DESC, seal_money DESC LIMIT 1""",
            [trade_date],
        )
        if streak:
            max_streak = int(streak[0]["continue_day_cnt"] or 0)
            streak_name = streak[0].get("name") or None

        # 近 5 日涨停家数：取最近 5 个**有数据**的交易日（不是日历日 ——
        # 节假日和停市会让"最近 5 个自然日"里只有 2 天有数据，柱状图直接缺 3 根）。
        trend = await safe(
            "special",
            """SELECT trade_date, count(*) AS c FROM v_limit_up_pool
               GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?""",
            [_MARKET_LIMIT_UP_TREND_DAYS],
        )
        lu5 = [
            {"trade_date": _iso_date(r.get("trade_date")), "count": int(r.get("c") or 0)}
            for r in reversed(trend)
        ]

        # 涨跌家数：全市场聚合。走 market 库最近交易日的收盘 vs 前收。
        # 单独一个查询而不是塞进上面那个 —— 失败时只是这一项为 null。
        #
        # **窗口函数必须在"最近两个交易日"上算，不能先筛到最新日再 lag**：
        # 先 `WHERE date = max(date)` 之后每个 thscode 只剩一行，lag() 恒为 NULL，
        # 三个计数会一起返回 0（实测过：up/flat/down = 0/0/0，看着像"全市场平盘"）。
        # 正确顺序是「取最近 2 个交易日 → 按票分区算 lag → 再筛回最新日」。
        breadth_rows = await safe(
            "market",
            """
            WITH last2 AS (
                SELECT DISTINCT date FROM v_daily_qfq
                ORDER BY date DESC LIMIT 2
            ),
            d AS (
                SELECT thscode, date, close,
                       lag(close) OVER (PARTITION BY thscode ORDER BY date) AS prev
                FROM v_daily_qfq
                WHERE date IN (SELECT date FROM last2)
            ),
            latest AS (SELECT max(date) AS d FROM last2)
            SELECT
              count(*) FILTER (WHERE date = (SELECT d FROM latest) AND close > prev) AS up,
              count(*) FILTER (WHERE date = (SELECT d FROM latest) AND close = prev) AS flat,
              count(*) FILTER (WHERE date = (SELECT d FROM latest) AND close < prev) AS down
            FROM d
            """,
        )
        if breadth_rows:
            breadth = {
                "up": int(breadth_rows[0]["up"] or 0),
                "flat": int(breadth_rows[0]["flat"] or 0),
                "down": int(breadth_rows[0]["down"] or 0),
            }

    sentiment = {
        "trade_date": trade_date,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "broken": broken,
        # 炸板率 = 炸板 / (涨停 + 炸板)。分母为 0 时给 null 而不是 0 ——
        # "0% 的炸板率"是一个结论，"算不出来"是另一回事。
        "broken_rate": (
            round(broken / (limit_up + broken) * 100, 1)
            if limit_up is not None and broken is not None and (limit_up + broken) > 0
            else None
        ),
        "max_streak": max_streak,
        "streak_name": streak_name,
        "limit_up_trend": lu5,
        "breadth": breadth,
    }

    # ---- 权重 ETF ----
    etf_rows = await safe(
        "fund",
        """
        SELECT s.thscode AS thscode, q.name AS name, q.trade_date AS trade_date,
               q.last_price AS last_price, q.change_pct AS change_pct
        FROM (VALUES {placeholders}) AS s(thscode)
        JOIN LATERAL (
            SELECT name, trade_date, last_price, change_pct
            FROM v_etf_latest WHERE thscode = s.thscode LIMIT 1
        ) q ON TRUE
        ORDER BY s.thscode
        """.format(placeholders=",".join("(?)" for _ in _MARKET_ETFS)),
        [c for c, _ in _MARKET_ETFS],
    )
    etf_by_code = {r["thscode"]: r for r in etf_rows}
    # 按常量顺序输出（而不是按数据库返回顺序）—— 前端要的是设计稿里那六行的次序。
    etfs = []
    for code, display in _MARKET_ETFS:
        r = etf_by_code.get(code)
        if not r:
            continue
        etfs.append(
            {
                "thscode": code,
                "name": display,
                "trade_date": _iso_date(r.get("trade_date")),
                "last": _f(r.get("last_price")),
                "change_pct": _f(r.get("change_pct")),
            }
        )

    return {
        "code": 0,
        "trade_date": trade_date,
        "indices": indices,
        "tickers": tickers,
        "sentiment": sentiment,
        "etfs": etfs,
        "market_state": await _market_state_block(trade_date),
        "unavailable": sorted(set(unavailable)),
        "sources": {
            "index": "index.v_index_daily",
            "sentiment": "special.v_limit_{up,down,break}_pool",
            "breadth": "market.v_daily_qfq",
            "etf": "fund.v_etf_latest",
            "market_state": "zettaranc.market_state + data/market_breadth.csv",
        },
    }


async def _market_state_block(trade_date: Optional[str]) -> Dict[str, Any]:
    """
    取市场状态评估（composite + 短期热度 + 暴露参考）。

    走 `zettaranc.market_state` 纯计算，输入是 index 库的宽基 K 线与本地
    宽度缓存 CSV。任一环节失败都返回 `{error}` —— snapshot 的其余部分照常返回，
    降级优先于报错（与本函数其他查询一致）。

    **返回里带 `tradable: false`**：composite 的逐年 IC 反复反号，不能当方向
    信号用。调用方（前端）若要据此做仓位决策，会在数据里直接看到这个标记。

    Args:
        trade_date: 目标交易日 YYYY-MM-DD；None = 库中最新。
            **必须真的传到 SQL 里** —— 早前版本忽略了这个参数、永远取最新，
            导致宽度新鲜度检测形同虚设（传什么日期都是 lag=0）。
    """
    from zettaranc.market_state import compute_market_state

    try:
        from datasources import registry

        idx_src = registry.get("index")
        if idx_src is None:
            return {"error": "index 数据源未就绪"}

        if trade_date:
            rows = await idx_src.execute(
                """
                SELECT trade_date, close, volume, turnover
                FROM v_index_daily
                WHERE thscode = ? AND close IS NOT NULL
                  AND CAST(trade_date AS VARCHAR) <= ?
                ORDER BY trade_date DESC
                LIMIT 120
                """,
                [_MARKET_STATE_INDEX, trade_date],
            )
        else:
            rows = await idx_src.execute(
                """
                SELECT trade_date, close, volume, turnover
                FROM v_index_daily
                WHERE thscode = ? AND close IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 120
                """,
                [_MARKET_STATE_INDEX],
            )
        if not rows:
            return {"error": f"{_MARKET_STATE_INDEX} 无 K 线数据"}

        # v_index_daily 的日期列是 DATE，to_dict 后可能是 date 对象
        idx_rows = [
            {
                "trade_date": _iso_date(r.get("trade_date")),
                "close": _f(r.get("close")),
                "volume": _f(r.get("volume")) or 0.0,
                "turnover": _f(r.get("turnover")) or 0.0,
            }
            for r in rows
        ]

        b_row, b_hist, freshness = _load_breadth_for(_iso_date(idx_rows[0]["trade_date"]))
        state = compute_market_state(idx_rows, b_row, b_hist)
        state["as_of"] = _iso_date(idx_rows[0]["trade_date"])
        # 宽度缓存是静态文件，没有定时任务重建。新鲜度必须随响应返回 ——
        # 否则会用过期宽度算 composite，而调用方完全看不出来。
        state["breadth_freshness"] = freshness
        return state
    except Exception as exc:  # noqa: BLE001 —— 降级优先于报错
        logger.warning("market_state 计算失败：%s", exc)
        return {"error": str(exc)}


# 宽度缓存落后多少个**自然日**就报警。缓存是 `build_breadth_cache.py` 生成的
# 静态文件，没有任何调度会自动重建它 —— 5 个自然日（跨一个周末 + 一个长假）
# 就足以让 composite 的宽度/量能维度静默偏一周以上，所以这里宁可吵也不要静默。
_BREADTH_STALE_DAYS = 5


# 缓存距今超过多少自然日就报"数据同步可能中断"。比上面宽松，因为 A 股长假
# （春节/国庆）最长能停 9 天，交易日本来就少。超过 12 天基本是同步挂了。
_BREADTH_ABSOLUTE_MAX_DAYS = 12


def _load_breadth_for(
    date_str: str, csv_path: Optional[str] = None
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    从本地宽度缓存 CSV 取指定日期的行 + 截至该日（含）的 60 日历史。

    返回 `(current, history, freshness)`。`freshness` 说明缓存的最后一个交易日
    相对请求日期落后多少天 —— 缓存是静态文件、**没有定时任务会重建它**，
    数据同步后宽度指标不会自动跟新。不把这件事说出来，就会用两周前的宽度
    去算 composite，而且界面上完全看不出来。

    Args:
        date_str: 请求的交易日 YYYY-MM-DD
        csv_path: 缓存路径，None = `data/market_breadth.csv`。
            单元测试用它注入临时文件（tests/unit/test_breadth_freshness.py）。

    CSV 由 `scripts/build_breadth_cache.py` 生成，列见该脚本。
    """
    import csv as _csv
    from pathlib import Path as _Path

    freshness: Dict[str, Any] = {
        "source": "data/market_breadth.csv",
        "last_date": None,
        "lag_trading_days": None,
        "days_behind_today": None,
        "stale": True,
        "note": None,
    }

    path = _Path(csv_path) if csv_path else _Path(__file__).parent / "data" / "market_breadth.csv"
    freshness["source"] = str(path.name if csv_path else "data/market_breadth.csv")
    if not path.exists():
        freshness["note"] = "宽度缓存不存在，宽度/量能维度已退化为中性分"
        return None, [], freshness

    try:
        with path.open(encoding="utf-8") as fh:
            all_rows = list(_csv.DictReader(fh))
    except OSError as exc:
        freshness["note"] = f"宽度缓存读取失败：{exc}"
        return None, [], freshness

    if not all_rows:
        freshness["note"] = "宽度缓存为空，宽度/量能维度已退化为中性分"
        return None, [], freshness

    up_to = [r for r in all_rows if not date_str or r.get("date", "") <= date_str]
    if not up_to:
        freshness["note"] = "宽度缓存中没有该日期及之前的记录"
        return None, [], freshness

    def _to_row(r: Dict[str, str]) -> Dict[str, Any]:
        return {
            "n_stocks": int(r["n_stocks"]),
            "pct_new_high_120": float(r["pct_new_high_120"]),
            "pct_above_ma60": float(r["pct_above_ma60"]),
            "pct_above_ma20": float(r["pct_above_ma20"]),
            "total_turnover": float(r["total_turnover"]),
        }

    current = _to_row(up_to[-1])
    history = [_to_row(r) for r in up_to[-60:]]

    # 新鲜度：对比「缓存最后一天」与「指数实际最新日」。
    #
    # 注意不能用请求日期 date_str 当基准：调用方传未来日期（或节假日之后）时，
    # 缓存末行仍会被选中，若拿 date_str 比就会得出 lag=0 的假"新鲜"。
    # 正确做法是同时看 date_str 在缓存里落在第几根。
    last_date = up_to[-1].get("date")
    freshness["last_date"] = last_date

    cache_dates = [r["date"] for r in up_to]
    if last_date and last_date == date_str:
        lag = 0
    elif last_date and date_str:
        # 请求日期不在缓存内（缓存落后）：用两者的自然日差给个量级
        from datetime import date as _date
        try:
            lag = (
                _date.fromisoformat(date_str) - _date.fromisoformat(last_date)
            ).days
        except ValueError:
            lag = None
    else:
        lag = None

    freshness["lag_trading_days"] = lag
    if lag is None:
        freshness["stale"] = True
        freshness["note"] = (
            f"指数数据到 {date_str}，宽度缓存只到 {last_date}，无法对齐交易日序号"
        )
    elif lag > _BREADTH_STALE_DAYS:
        freshness["stale"] = True
        freshness["note"] = (
            f"宽度缓存落后指数数据 {lag} 个自然日（缓存到 {last_date}，指数到 {date_str}）。"
            f"重跑 scripts/build_breadth_cache.py 更新；"
            f"当前 composite 的宽度/量能维度基于过期数据。"
        )
    else:
        freshness["stale"] = False
        freshness["note"] = f"宽度缓存到 {last_date}（落后 {lag} 天，正常）"

    # 第二层检查：**整体数据新鲜度**。
    #
    # 上面那层只比"缓存 vs 指数"，两者同步滞后时查不出来 —— 实测 2026-10-06
    # 时 index 库与宽度缓存都停在 09-30，两者 lag=0 判为新鲜，但相对今天已
    # 落后 6 天。所以再比一次"绝对日期 vs 今天"，数据同步挂了要能报出来。
    if last_date:
        from datetime import date as _date, datetime as _dt
        try:
            age = (_dt.now().date() - _date.fromisoformat(last_date)).days
        except ValueError:
            age = None
        freshness["days_behind_today"] = age
        if age is not None and age > _BREADTH_ABSOLUTE_MAX_DAYS:
            freshness["stale"] = True
            freshness["note"] = (
                f"宽度缓存只到 {last_date}，距今 {age} 天。数据同步可能已中断，"
                f"重跑 scripts/build_breadth_cache.py。"
            )
        elif age is not None:
            # 没超阈值也要说清楚落后多少天 —— 否则看 note 以为数据是当天的
            freshness["note"] = (
                f"{freshness['note']}；缓存最后一天距今 {age} 天"
                f"（阈值 {_BREADTH_ABSOLUTE_MAX_DAYS} 天）"
            )

    return current, history, freshness


@router.get("/api/market/dragon-tiger")
@router.get("/market/dragon-tiger")
async def market_dragon_tiger(
    limit: int = Query(5, ge=1, le=50, description="返回条数（按净买额降序）"),
):
    """龙虎榜净买入榜（默认前五）。

    取最新交易日、按 `net_value` 降序。同一只票可能同时出现在 `all` 和 `org`
    两个榜单口径里（实测"我爱我家"同日两条，net_value 相同），**按 thscode 去重**，
    否则前五会被同一只票占掉两行。

    `org_net_value` 是 null 就返回 null —— 该票当日无机构席位数据，
    报 0 会被读成"机构净卖出 0 元"，那是另一回事。
    """
    from datasources import registry

    src = registry.get("special")
    if src is None:
        raise fail(503, "special 数据源未就绪")

    sql = """
        SELECT thscode, name, trade_date, board_type, net_value, org_net_value, range_days
        FROM (
            SELECT thscode, name, trade_date, board_type, net_value, org_net_value, range_days,
                   row_number() OVER (PARTITION BY thscode ORDER BY net_value DESC) AS rn
            FROM v_dragon_tiger
            WHERE trade_date = (SELECT MAX(trade_date) FROM v_dragon_tiger)
        ) t
        WHERE rn = 1
        ORDER BY net_value DESC
        LIMIT ?
    """
    try:
        rows = await src.execute(sql, [limit])
    except Exception as exc:
        logger.warning("龙虎榜查询失败: %s", exc)
        raise fail(503, f"查询龙虎榜失败: {exc}") from exc

    return {
        "code": 0,
        "trade_date": _iso_date(rows[0]["trade_date"]) if rows else None,
        "count": len(rows),
        "data": [
            {
                "thscode": r.get("thscode"),
                "name": r.get("name") or "",
                "tag": _dragon_board_tag(r),
                "net_value": _f(r.get("net_value")),
                "org_net_value": _f(r.get("org_net_value")),
            }
            for r in rows
        ],
    }


# ===== 批量行业归属查询（给 Go 侧 grading job 用） =====

class IndustryMapRequest(BaseModel):
    """批量查行业归属。"""
    thscodes: List[str] = Field(..., max_items=500, description="股票代码列表，最多 500")


@router.post("/watchlist/industry-map")
async def batch_industry_map(request: IndustryMapRequest) -> Dict[str, Any]:
    """批量返回每只股票的行业归属（一级 + 二级）。

    给 Go 侧的 StockWatchGradingJob 用：拿到行业归属后按二级行业分组，
    同行业的股票打包成一次 LLM 调用，减少调用次数。

    数据源：v_index_constituents × v_index_universe（tag='industry'），
    层级判定用指数编码前缀（881 = 一级 / 884 = 二级），与 halo/industry_map.py 一致。
    """
    from datasources import registry

    src = registry.get("index")
    if src is None:
        return {"code": 0, "data": {}}

    thscodes = [t.strip() for t in request.thscodes if t and t.strip()]
    if not thscodes:
        return {"code": 0, "data": {}}

    placeholders = ", ".join(["?"] * len(thscodes))
    sql = f"""
    SELECT c.thscode, u.thscode AS index_code, u.name AS industry_name,
           (SELECT COUNT(*) FROM v_index_constituents c2
             WHERE c2.index_thscode = u.thscode) AS members
    FROM v_index_constituents c
    JOIN v_index_universe u ON c.index_thscode = u.thscode
    WHERE c.thscode IN ({placeholders}) AND u.tag = 'industry'
    ORDER BY c.thscode, members DESC
    """
    try:
        rows = await src.execute(sql, thscodes)
    except Exception as exc:
        raise fail(503, f"查行业归属失败: {exc}") from exc

    # Group by thscode, apply level logic (same as halo/industry_map.py)
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        code = str(r.get("index_code") or "")
        prefix = code.split(".")[0][:3]
        level = {"881": 1, "884": 2}.get(prefix)
        if level is None:
            level = 2 if (r.get("members") or 0) <= 40 else 1
        grouped[r["thscode"]].append({
            "industry": r.get("industry_name"),
            "level": level,
        })

    result = {}
    for thscode in thscodes:
        memberships = grouped.get(thscode, [])
        l1 = next((m["industry"] for m in memberships if m["level"] == 1), None)
        l2 = next((m["industry"] for m in memberships if m["level"] == 2), None)
        result[thscode] = {
            "level1": l1,
            "level2": l2 or l1,  # fallback to level1 if no level2
        }

    return {"code": 0, "data": result}


# ══════════════════════════════════════════════════════════════════════════
# 板块 / 情绪 / 期股联动
#
# 这一组端点补的是「有数据但没有出口」的那部分：index 库里 848 个板块指数与
# 五年日线、special 库里的连板梯队、futures 库里的四个股指期货主力连续，
# 在此之前没有任何端点在读，前端 proxy 路由里也没有对应条目。
#
# 取数与口径全部在 `finance_panel/board.py`，这里只做参数校验与转交。
# ══════════════════════════════════════════════════════════════════════════


@router.get("/api/market/sectors")
async def market_sectors(
    tag: Optional[str] = Query(
        None, description="板块分类：industry 行业 / concept 概念 / tszs 特色 / region 地域，缺省全部"
    ),
    limit: int = Query(60, ge=1, le=400, description="返回条数（先全量排序再截断）"),
):
    """板块指数今日榜 —— 涨幅 + 放量倍数 + 板块内涨停家数。

    **排序是字典序而不是加权合成分**：涨停家数 → 放量倍数 → |涨跌幅|。
    理由是合成分必须给权重，而权重是拍的；字典序表达的是真实判断顺序 ——
    先看有没有涨停（最硬的证据），再看有没有放量（资金来没来），
    最后才看涨了多少（涨得多不代表有资金）。

    涨停家数跨库（special ↔ index），所以在 Python 侧合并后排序，见
    `board.list_sectors` 的实现说明。
    """
    from datasources import registry
    from finance_panel import board

    return await board.list_sectors(
        registry.get("index"), registry.get("special"), tag=tag, limit=limit
    )


@router.get("/api/market/limit-up-ladder")
async def market_limit_up_ladder(
    days: int = Query(30, ge=2, le=120, description="回看交易日数"),
):
    """连板梯队 —— 近 N 日 × 2/3/4/5/6/7+ 板的家数矩阵。

    涨停家数只说"今天热不热"，梯队说"热度在往上垒还是在塌"。2 板堆到 7 板，
    和每天都停在 2 板，是两种完全不同的市场。
    """
    from datasources import registry
    from finance_panel import board

    return await board.limit_up_ladder(registry.get("special"), days=days)


@router.get("/api/market/basis")
async def market_basis():
    """期股联动 —— 股指期货（主力连续）基差 + 5 年分位数。

    **基差日取期货与现货的共同最新交易日。** 实测期货更新到 T-1、现货已到 T；
    拿 T-1 的期货减 T 的现货会把一整天涨跌混进"基差"，那不是基差而是跨日差，
    而且算完之后看起来完全正常。所以响应里 `basis_date` / `futures_latest` /
    `spot_latest` 三个日期分开给，前端用不着也不该自己猜该配哪天。

    percentile 才是有用的那个数：「-1.4% 贴水」没有信息量（股指期货常年贴水），
    「-1.4% 处在 5 年 4% 分位」才是判断。
    """
    from datasources import registry
    from finance_panel import board

    return await board.index_futures_basis(
        registry.get("futures"), registry.get("index")
    )


@router.get("/api/market/commodities")
async def market_commodities():
    """期股联动 —— 核心大宗商品期货最新行情与关联 A 股板块提示。"""
    from datasources import registry
    from finance_panel import board

    return await board.list_commodities(registry.get("futures"))


@router.get("/api/market/fund-holdings")
async def market_fund_holdings(
    thscode: str = Query(..., description="股票同花顺代码，如 600519.SH"),
    limit: int = Query(20, ge=1, le=100, description="返回数量限制"),
):
    """个股公募与 ETF 机构重仓穿透明细。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_stock_fund_holdings(
        registry.get("fund"), thscode, limit=limit
    )


@router.get("/api/market/ladder-detail")
async def market_ladder_detail(
    min_board: int = Query(2, ge=1, le=10, description="最低连板数"),
    limit: int = Query(50, ge=1, le=100, description="最多返回条数"),
):
    """连板梯队详情 —— 空间板高度标杆与各板位个股清单、封单金额、封板时间。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_limit_ladder_detail(
        registry.get("special"), min_board=min_board, limit=limit
    )


@router.get("/api/market/anomaly-reasons")
async def market_anomaly_reasons(
    thscode: Optional[str] = Query(None, description="同花顺股票代码，空则返回全市场最新异动"),
    limit: int = Query(10, ge=1, le=50, description="返回条数限制"),
):
    """个股大涨/大跌异动归因分析与关键词列表。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_stock_anomaly_reasons(
        registry.get("special"), thscode=thscode, limit=limit
    )


@router.get("/api/market/limit-break-down")
async def market_limit_break_down(
    limit: int = Query(20, ge=1, le=100, description="返回条数限制"),
):
    """炸板池与跌停池 —— 盘中开板砸盘回落与跌停核按钮亏钱效应。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_limit_break_and_down(
        registry.get("special"), limit=limit
    )


@router.get("/api/market/dividend-calendar")
async def market_dividend_calendar(
    thscode: Optional[str] = Query(None, description="股票代码，如 600519.SH"),
    start_date: Optional[str] = Query(None, description="起始除权日 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="截止除权日 YYYY-MM-DD"),
    min_dividend: float = Query(0.0, ge=0, description="最低每股派现金额"),
    min_bonus: float = Query(0.0, ge=0, description="最低每股送转股比例"),
    sort_by: str = Query("date", description="排序方式: date | yield | bonus | dividend"),
    limit: int = Query(50, ge=1, le=200, description="返回条数限制"),
):
    """除权除息与分红送转事件日历 —— 派息金额、送转比例与预估股息率。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_dividend_calendar(
        registry.get("market"),
        thscode=thscode,
        start_date=start_date,
        end_date=end_date,
        min_dividend=min_dividend,
        min_bonus=min_bonus,
        sort_by=sort_by,
        limit=limit,
    )


@router.get("/api/market/hot-stocks")
async def market_hot_stocks(
    mode: str = Query("skyrocket", description="榜单模式：skyrocket (飙升黑马榜) | rank (人气总榜)"),
    limit: int = Query(50, ge=1, le=100, description="返回条数限制"),
):
    """全市场热度与飙升雷达 —— 资金关注度突增黑马与综合人气龙头。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_hot_stocks(
        registry.get("special"),
        mode=mode,
        limit=limit,
    )


@router.get("/api/market/hot-stock-trend")
async def market_hot_stock_trend(
    thscode: str = Query(..., description="股票代码，如 600519.SH、600418.SH"),
    limit: int = Query(30, ge=1, le=100, description="历史天数限制"),
):
    """个股人气排名历史趋势 —— 捕捉关注度升温与退潮。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_hot_stock_trend(
        registry.get("special"),
        thscode=thscode,
        limit=limit,
    )


@router.get("/api/market/futures-warehouse")
async def market_futures_warehouse(
    thscode: Optional[str] = Query(None, description="期货合约代码，如 AL2611.SHF"),
    limit: int = Query(30, ge=1, le=100, description="返回条数限制"),
):
    """期货注册仓单库存与日增减 —— 现货紧缺去库与累库滞销预警。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_futures_warehouse_receipts(
        registry.get("futures"),
        thscode=thscode,
        limit=limit,
    )


@router.get("/api/market/futures-basis-rate")
async def market_futures_basis_rate(
    thscode: Optional[str] = Query(None, description="期货合约代码，如 CU2611.SHF"),
    limit: int = Query(30, ge=1, le=100, description="返回条数限制"),
):
    """期货现货基差与升贴水率 —— Contango/Backwardation 与期现套利窗口。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_futures_basis_rate(
        registry.get("futures"),
        thscode=thscode,
        limit=limit,
    )


@router.get("/api/market/etf-holders")
async def market_etf_holders(
    thscode: str = Query(..., description="基金/ETF 代码，如 158000.SZ 或 510300.SH"),
    limit: int = Query(10, ge=1, le=20, description="返回前十大持有人数量"),
):
    """ETF/基金持有人结构（机构vs散户）与前十大持有人穿透明细。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_etf_holders_detail(
        registry.get("fund"),
        fund_thscode=thscode,
        limit=limit,
    )


@router.get("/api/market/etf-institutional-ranking")
async def market_etf_institutional_ranking(
    order_by: str = Query("institution", description="排序依据：institution (机构占比) 或 retail (散户占比)"),
    limit: int = Query(30, ge=1, le=100, description="返回条数限制"),
):
    """全市场基金/ETF 机构投资者占比排行与散户聚集度排行。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_etf_institutional_ranking(
        registry.get("fund"),
        order_by=order_by,
        limit=limit,
    )


@router.get("/api/market/squeeze-status")
async def market_squeeze_status(
    thscode: str = Query(..., description="股票代码，如 000001.SZ 或 600519.SH"),
    limit: int = Query(30, ge=1, le=100, description="回看天数"),
):
    """TTM Squeeze 波动挤压突破与超级趋势 Supertrend 顺势追踪。"""
    from datasources import registry
    from finance_panel import board

    return await board.get_squeeze_status(
        registry.get("indicators"),
        thscode=thscode,
        limit=limit,
    )


class IndustryMapRequest(BaseModel):
    thscodes: List[str] = Field(..., max_items=500, description="股票代码列表，最多 500")


@router.get("/api/market/auction")
async def market_auction(
    date: Optional[str] = Query(None, description="交易日 YYYY-MM-DD，缺省取库内最新"),
    min_float_cap: float = Query(
        5e9, gt=0, description="流通市值门槛（元），低于此体量的票竞价量比不可比"
    ),
    limit: int = Query(20, ge=1, le=200, description="异动榜条数"),
):
    """集合竞价 —— 盘前那一分钟的信息：高开低开分布 + 短线风向标 + 异动榜。

    收盘复盘回答"今天发生了什么"，这一块回答"**今天怎么开的**"。它是全市场
    唯一能比昨天更早拿到读数的时刻：5471 只票在 9:15–9:25 各报一次价。

    **异动榜必须过流通市值门槛。** 实测全市场量比>2 有 910 只，但排在最前面的
    "量比 70 倍"大半是几亿市值的小盘票 —— 那个倍数是流动性噪音不是资金信号；
    50 亿门槛下同样条件只剩 395 只，排第一的园林股份（52 亿、开盘涨停、量比 70）
    才是真信号。门槛可调，理由写在 `finance_panel/auction.py` 模块头。

    分布（高开/低开家数）是全市场统计，**不设门槛** —— 两个口径混在一张榜里，
    高开家数会被榜单的数量级盖过去。
    """
    from datasources import registry
    from finance_panel import auction

    return await auction.auction_overview(
        registry.get("special"), date=date, min_float_cap=min_float_cap, limit=limit
    )


@router.post("/api/market/technicals")
async def market_technicals(request: IndustryMapRequest) -> Dict[str, Any]:
    """批量技术指标读数（最新日）+ 派生判断。

    让「谁在动」能回答**为什么**：同样是涨 4%，在 250 日线上方 3% 和在下方 12%
    是两件事 —— 前者可能是延续，后者是反弹。只给量价倍数说不了这个。

    数据来自 `indicators.duckdb`（264 列、1036 万行、2016-09 起）。按代码取
    最新日实测 0.2 秒以内，**不做预聚合也不建索引** —— 那个形状的查询本来就快，
    预先加工反而要多维护一份口径。

    派生判断在服务端算（`above_sma20` / `ma_alignment` / `rsi_zone` …），
    与 `WatchPulse.vue` 头部"前端不重算任何指标"是同一条约定。
    """
    from datasources import registry
    from finance_panel import technicals

    return await technicals.latest_technicals(
        registry.get("indicators"), registry.get("market"), request.thscodes
    )


@router.post("/api/market/industry-map")
async def market_industry_map(request: IndustryMapRequest) -> Dict[str, Any]:
    """批量行业归属 `{thscode: {level1, level2}}`。

    前端 `finance/api/watchlist.ts` 早就声明了 `fetchIndustryMap`，但后端一直
    404。这里按它声明的形状实现，段位规则（881 一级 / 884 二级）从
    `halo.industry_map` 直接 import，不复制那条规则。

    查不到时 level1/level2 都是 null —— 调用方据此跳过行业判断，
    **不要拿股票名顶上**：那是另一个维度的名字，混用会让"所属行业"变成编的。
    """
    from datasources import registry
    from finance_panel import board

    result = await board.industry_map_batch(registry.get("index"), request.thscodes)
    # 前端声明的返回形状是 `{code, data}`，这里保持一致。
    return {"code": 0 if result.get("ok") else 500, "data": result.get("data", {})}
