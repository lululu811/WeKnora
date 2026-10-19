"""
选股 / 年报事实链路 / 自选股面板路由：/zettaranc/*、/halo/*、/api/finance/*。
"""

import asyncio
import datetime
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator

from core import MAX_QUERY_ROWS, _require_stock_thscode, fail, logger, require_api_key

router = APIRouter()


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


# ===== Zettaranc 技术分析 =====

@router.post("/zettaranc/screen")
async def zettaranc_screen(request: ScreenRequest) -> Dict[str, Any]:
    """全市场选股 — 一条 SQL 取回全市场指标后集合式判定"""
    # STRATEGY_RULES 的字面量留在 main.py：Go 侧
    # internal/agent/tools/zettaranc/strategies_test.go 用正则从 main.py 的**源码文本**
    # 里解析策略名，两边清单不一致时它会让 CI 变红。搬走会让那个测试读不到，
    # 所以这里在调用时刻再取一次，避开 main ↔ routers 的模块级导入环。
    from main import STRATEGY_RULES
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

    # 窗口按这条规则实际需要的取，不跟着某条形态策略的 70 根走 ——
    # 全局 70 会让全市场取数膨胀 3.5 倍，把容器 OOM 掉（见 screener.LOOKBACK_DAYS）。
    lookback = screener.lookback_for(rule)
    shards = screener.shard_codes(all_codes, lookback)
    if len(shards) > 1:
        logger.info(
            "选股 %s：%d 只切成 %d 片查询（单查询行数预算 %d）",
            request.strategy, len(all_codes), len(shards), screener.QUERY_ROW_BUDGET,
        )

    # 风险筛选要跑在 `limit` **之后**才合理吗？不 —— 那会让用户要 8 只却只拿到 2 只：
    # 形态命中 866，取前 8 送筛，淘汰 6 剩 2。淘汰率最高的三项阈值
    # （负债 >50% 且流动比 <1）本地分布下能筛掉四分之三。
    #
    # 所以先按「用户要的数量 ÷ 预估通过率」多取一批，筛完再截到 limit。
    # 预估通过率没有先验，就用 1/3 做保守估计：三项风险阈值全开时
    # 本地 p50 负债 0.40、流动比 1.50，p10 负债约 0.70、流动比 0.60，
    # 三分之一通过是贴近实际的乐观估计；多取的量由 OVERSAMPLE_CAP 封顶，
    # 免得"要 200 只"变成扫全市场。
    #
    # 放在分片循环**之前**：下面每片各自要判一次，得知道这次要判多少。
    OVERSAMPLE_CAP = 4
    risk_requested = any((
        request.max_debt_ratio is not None,
        request.min_current_ratio is not None,
        request.max_receivable_ratio is not None,
        request.require_profit,
        request.exclude_st,
    ))
    fetch_limit = request.limit * OVERSAMPLE_CAP if risk_requested else request.limit

    # 逐分片评估：取一片、判一片、**只留命中**，然后让这一片的行出作用域。
    #
    # 旧实现把 5 个分片的行全部累积进 rows / price_rows 再统一判，峰值是
    # 5,571 只 × lookback 天 × 2 张表。对默认 20 天窗口是 22 万行，扛得住；
    # 对 shrink_pullback 声明的 70 天窗口是 78 万行 Python dict，容器 3 GiB
    # 直接被打穿 —— 实测一次 /zettaranc/screen 就把 python-service OOM kill。
    # 逐片判完只留命中（几十到几百条），峰值降到单片，与窗口长短无关。
    hits: List[Dict[str, Any]] = []
    matched_total = 0
    incomplete_total = 0
    scanned_codes: set = set()
    indicator_as_of: Optional[str] = None
    price_as_of: Optional[str] = None
    universe_truncated = False
    price_merged = 0
    price_source = registry.get("market")
    any_rows = False

    for shard in shards:
        shard_csv = ",".join(shard)
        try:
            rows: List[Dict[str, Any]] = await indicators_src.execute(
                screener.build_indicator_snapshot_sql(),
                [shard_csv, lookback],
            )
        except Exception as exc:
            logger.warning("选股取指标失败(分片 %d 只): %s", len(shard), exc)
            raise fail(503, f"选股取数失败：{exc}") from exc
        if rows:
            any_rows = True
        if getattr(indicators_src, "last_result_truncated", False):
            # 分片本应保证不超预算；这里真被截断说明预算算错了，必须报出来
            # 而不是继续跑出一个"看起来正常、其实少了票"的结果。
            universe_truncated = True
            logger.error(
                "分片 %d 只仍被截断，QUERY_ROW_BUDGET=%d 对 lookback=%d 偏大",
                len(shard), screener.QUERY_ROW_BUDGET, lookback,
            )

        # 价量维度：v_indicators_daily 没有任何价格列，close/volume 只能从
        # market.v_daily_qfq 单独取，再按 (thscode, date) 并回指标行。
        # 两个库是独立只读连接，不能 JOIN，只能在 Python 侧合。
        price_rows: List[Dict[str, Any]] = []
        if price_source is not None:
            try:
                price_rows = await price_source.execute(
                    screener.build_price_snapshot_sql(),
                    [shard_csv, lookback],
                )
            except Exception as exc:
                # 价量拿不到不该让整个选股失败：没有价量只是"放量突破"这类
                # 形态信号不触发，其余指标信号照常。降级优先于报错。
                logger.warning("选股取价量失败(分片 %d 只): %s", len(shard), exc)
                price_source = None
            else:
                if getattr(price_source, "last_result_truncated", False):
                    universe_truncated = True
                    logger.error("价量分片 %d 只被截断", len(shard))

        if price_rows:
            price_merged += screener.merge_price_rows(rows, price_rows)

        scanned_codes.update(r.get("thscode") for r in rows if r.get("thscode"))
        for source, rows_ in (("ind", rows), ("px", price_rows)):
            latest = max((str(r.get("date")) for r in rows_ if r.get("date")),
                         default=None)
            if latest is None:
                continue
            if source == "ind":
                if indicator_as_of is None or latest > indicator_as_of:
                    indicator_as_of = latest
            elif price_as_of is None or latest > price_as_of:
                price_as_of = latest

        if rows:
            shard_result = await asyncio.to_thread(
                screener.screen, rows, rule, fetch_limit, names
            )
            hits.extend(shard_result.get("stocks", []))
            matched_total += shard_result.get("matched", 0)
            incomplete_total += shard_result.get("incomplete", 0)
        # rows / price_rows 在下一轮循环里被重新赋值，上一轮的整片数据随之出作用域
        # 被回收。**不要**把这两行提到循环外 —— 那就退回"全部分片累积"的旧行为。

    if not any_rows:
        raise fail(503, "指标快照为空")

    if price_source is not None and price_merged == 0:
        logger.warning(
            "价量与指标按 (thscode,date) 一条都没对上，形态信号将全部失效"
        )

    # 全市场口径下排序：各分片内部已按 score 排过序，这里归并后重排并截到
    # fetch_limit。分数相同时按 thscode 排，与 screener.screen 内部一致，
    # 保证同一批数据无论分几片都得到同一个结果。
    hits.sort(key=lambda s: (-float(s.get("score") or 0.0), str(s.get("thscode") or "")))
    hits = hits[:fetch_limit]

    # 截断必须在这里说清楚。
    #
    # 旧实现把 `scanned` 报成 `len(names)` —— 那是**清单长度**，不是实际
    # 参与筛选的标的数。指标快照查询被 `max_rows` 截断时（indicators.duckdb
    # 有 12GB，全市场 × lookback 天很容易撞上限），多出来的票被静默
    # 丢掉，而返回里的 `scanned` 仍然写着完整的 5,571 —— 调用方无从判断
    # 这次结果是不是全市场口径。
    #
    # `scanned_codes` / `indicator_as_of` / `price_as_of` 现在在分片循环里逐片
    # 累积（`rows` 出作用域后就拿不到了），语义与原来"全量算一次 max/集合"相同。
    #
    # 数据截止日。形态信号全部由最近 lookback 天的指标算出，
    # 不说截止日的话，「今天这只票超卖」和「三天前的快照」长得一样。
    # 两个库各自可能有各自的最新日期（同步进度不同），所以分开报。

    # 「没有指标行」的基准必须是**实际送去查的候选集**，不是全市场清单。
    # 板块筛选把候选集缩到 320 只之后，若仍拿 5,571 只的 names 去算差集，
    # 会报出"5,251 只没有指标行"——而它们只是**不在这个板块里**，
    # 这条警告会把一个正常的板块限定说成大规模数据缺失。
    candidate_set = set(all_codes)
    # 清单里有、但一行指标都没回来的票：不是被截断，就是该票确实没有指标数据。
    # 两种情况对调用方的含义不同，都不该藏进 `scanned` 里。
    no_indicator = len(candidate_set) - len(candidate_set & scanned_codes)
    dropped = sorted(candidate_set - scanned_codes)

    # 各分片各自的判定结果在这里归并成一个与旧 `screener.screen` 返回同形的
    # 结构，下游（风险筛选、截断、返回体）不需要知道分片这回事。
    result = {
        "stocks": hits,
        "matched": matched_total,
        "incomplete": incomplete_total,
    }

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


@router.post("/zettaranc/analyze")
async def zettaranc_analyze(request: AnalyzeRequest) -> Dict[str, Any]:
    """综合技术分析 — 趋势 + 量价 + 形态 + 支撑阻力

    服务 API，**不再由 agent 工具调用**（2026-10-05 起 Go 侧 zettaranc.analyze 复合
    工具已删除；单票分析走四个原子工具 hithink.finance.analysis.trend / .volume /
    .pattern / .levels，各返回一段，由模型自行组合）。保留给外部调用方与前端。
    """
    from datasources import registry
    from zettaranc import (
        fetch_market_data, analyze_trend, analyze_volume,
        analyze_chart_pattern, analyze_levels,
    )

    thscode = _require_stock_thscode(request.thscode)
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

    # 2026 重剑无锋战法全套指标体系与风控总闸门评估
    if len(rows) >= 5:
        bars_asc = list(reversed(rows))
        try:
            from zettaranc import (
                analyze_micro_volume,
                analyze_ma518,
                evaluate_monthly_gate,
                evaluate_weekly_dual_up5,
                analyze_macro_volume,
                analyze_td_setup,
                analyze_sun_moon,
                classify_trading_system,
                evaluate_bottom_fishing_conditions,
                evaluate_risk_guardrail,
            )
            mv_res = analyze_micro_volume(bars_asc)
            ma518_res = analyze_ma518(bars_asc)
            monthly_gate_res = evaluate_monthly_gate(bars_asc)
            weekly_dual_res = evaluate_weekly_dual_up5(bars_asc)
            macro_vol_res = analyze_macro_volume(bars_asc) if len(bars_asc) >= 30 else None
            td_res = analyze_td_setup(bars_asc)
            sun_moon_res = analyze_sun_moon(bars_asc)
            system_res = classify_trading_system(
                bars_asc,
                ma518_result=ma518_res,
                micro_vol_result=mv_res,
                monthly_gate=monthly_gate_res,
                weekly_dual=weekly_dual_res,
                sun_moon_result=sun_moon_res,
                macro_vol_result=macro_vol_res,
            )
            bf_res = evaluate_bottom_fishing_conditions(bars_asc, ma518_result=ma518_res, td_result=td_res)
            guardrail_res = evaluate_risk_guardrail(
                bars_asc,
                monthly_gate=monthly_gate_res,
                ma518_result=ma518_res,
                sun_moon_result=sun_moon_res,
                system_result=system_res,
            )

            result["heavy_sword"] = {
                "micro_volume": mv_res,
                "ma518_system": ma518_res,
                "monthly_gate": monthly_gate_res,
                "weekly_dual_up5": weekly_dual_res,
                "macro_volume": macro_vol_res,
                "td_setup": td_res,
                "sun_moon": sun_moon_res,
                "trading_system": system_res,
                "bottom_fishing": bf_res,
                "guardrail": guardrail_res,
            }
        except Exception as exc:
            result["heavy_sword"] = None
            skipped.append(f"heavy_sword: {exc}")
    else:
        result["heavy_sword"] = None
        skipped.append(f"heavy_sword: 需要至少 5 根 K 线，仅 {len(rows)} 根")

    result["insufficient_data"] = skipped
    result["complete"] = not skipped

    return {"success": True, "result": result}


class FourBricksRequest(BaseModel):
    thscode: str
    days: int = 120


@router.post("/zettaranc/four-bricks")
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

    thscode = _require_stock_thscode(request.thscode)
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


@router.post("/zettaranc/scan")
async def zettaranc_scan(request: ScanRequest) -> Dict[str, Any]:
    """技术信号扫描 — 30+ 种买卖形态"""
    from datasources import registry
    from zettaranc import scan_patterns

    thscode = _require_stock_thscode(request.thscode)
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


@router.get("/zettaranc/health")
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


@router.post("/halo/sync", dependencies=[Depends(require_api_key)])
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
    include_announcements: bool = Field(
        False,
        description="是否额外取近期全类型公告（巨潮，一次请求）。默认关闭，理由同上："
                    "巨潮同样限速，公告只在出报告/归档时需要。",
    )


@router.post("/halo/score", dependencies=[Depends(require_api_key)])
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
        include_announcements=request.include_announcements,
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


@router.post("/halo/verify", dependencies=[Depends(require_api_key)])
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

    # 判分层影子记录（旁路）：把「LLM 这次给了什么分」和「判分依据是什么锚点」
    # 落成一份可离线比对的现场。基线与证据在此处天然同处 —— request.scores 是
    # LLM 刚交的 7 个分，result["ai_slots"] 是上面 analyze() 算出来的量化锚点。
    # 整段包 try/except：判分层挂掉只该少一条影子记录，绝不能影响下面的返回。
    # 注意本段**不向返回体添加任何字段**，影子不外泄是第一铁律。
    try:
        from halo import decider as halo_decider

        await halo_decider.collect_shadow(
            thscode=str(result.get("thscode") or request.thscode),
            period=str(result.get("period") or request.period or ""),
            report_type=request.report_type,
            llm_scores=dict(request.scores),
            slots=result.get("ai_slots") or [],
            recheck=verdict,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("影子记录跳过（%s）：%s", request.thscode, exc)

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


@router.post("/halo/query", dependencies=[Depends(require_api_key)])
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


# ===== 自选股「量价异动面板」 =====
#
# 三块能力：量价异动（/finance/pulse）+ 财经日历抓取与读取。
# 规则计算全在 finance_panel/pulse.py 这个纯函数里，端点只负责取数与序列化；
# 缺失一律进 missing 数组，绝不用 0 顶替（与 /api/quotes 同一约定）。


class PulseRequest(BaseModel):
    """量价异动查询请求体。

    `codes` 同时接受 "301190" 与 "301190.SZ" 两种写法 —— 归一化在 SQL 里
    用 v_symbol 的 ticker 映射做，不在 Python 侧硬编码推导。
    """

    codes: List[str] = Field(..., max_items=500, description="股票代码列表，最多 500")
    date: Optional[str] = Field(None, description="交易日 YYYY-MM-DD，缺省取库内最新交易日")
    window: int = Field(5, ge=2, le=250, description="中枢窗口（交易日），默认 5——与目标产品逐点对账得出（近 5 日均量）")
    low: float = Field(0.618, gt=0, le=10, description="缩量阈值")
    high: float = Field(1.382, gt=0, le=50, description="放量阈值")

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("date 必须是 YYYY-MM-DD")
        return v


@router.post("/api/finance/pulse")
async def finance_pulse(request: PulseRequest) -> Dict[str, Any]:
    """自选股量价异动 —— 当日成交量相对中枢的倍数 + 涨跌幅四象限。

    **口径**：
    * 中枢 = 当日之前 `window` 个交易日成交量的算术均值（不含当日），取自
      `v_daily` 的原始成交量；
    * 倍数 = 当日成交量 / 中枢，`< low` 缩量、`> high` 放量；
    * 涨跌幅 = `v_daily_qfq` 前复权收盘价之比 —— **除权日原始价是断崖，
      用原始 close 会读出 -50% 这种假暴跌**，这是必须用 qfq 的原因。

    **缺失 ≠ 0**：窗口不足、中枢为 0、收盘价缺失的标的进 `missing` 并附
    `reason`，不会以 multiple=0 混进 `items`（0 倍数在这里是除零的伪装）。

    `items` 按 `abs(multiple - 1)` 降序 —— 离常态越远越靠前，前端不用再排。

    DuckDB 全程只读，本接口不写入任何行情库。
    """
    from datasources import registry
    from finance_panel import service as pulse_service

    src = registry.get("market")
    if src is None:
        raise fail(503, "market 数据源未就绪")

    try:
        return await pulse_service.compute_pulses(
            src,
            request.codes,
            target_date=request.date,
            window=request.window,
            low=request.low,
            high=request.high,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("量价异动查询失败: %s", exc)
        raise fail(503, f"计算量价异动失败: {exc}") from exc


@router.post("/api/finance/calendar/sync")
async def finance_calendar_sync(
    past_days: int = Query(3, ge=0, le=30, description="回看天数"),
    future_days: int = Query(14, ge=1, le=90, description="前瞻天数"),
) -> Dict[str, Any]:
    """抓取东方财富财经日历并落库（默认过去 3 天 ~ 未来 14 天）。

    抓取限速 ≥1 秒/次、带重试 —— 东财高频请求会返回空 result。

    **抓取失败返回 `{ok: false, reason}`，不是 5xx**：日历是面板辅助块，
    抓不到只影响"数据有多新"，不该让整个同步任务报成失败；GET 仍会返回
    库里已有数据。
    """
    from finance_panel import calendar as cal
    from finance_panel import store as cal_store

    try:
        events = cal.fetch_calendar(past_days=past_days, future_days=future_days)
    except Exception as exc:  # noqa: BLE001 —— 抓取失败不抛给调用方
        logger.warning("财经日历抓取失败: %s", exc)
        return {"ok": False, "reason": f"东财抓取失败: {exc}", "fetched": 0}

    if not events:
        # 空结果既可能是"这段区间真的没有事件"，也可能是东财限速/改版。
        # 两种都不该被静默当成成功，显式回 ok=false 让调用方决定是否重试。
        return {
            "ok": False,
            "reason": "东财返回空数据（可能是限速或接口变更），未覆盖本地日历",
            "fetched": 0,
        }

    try:
        written = cal_store.upsert_events(events, source="eastmoney")
    except Exception as exc:  # noqa: BLE001
        logger.warning("财经日历落库失败: %s", exc)
        return {"ok": False, "reason": f"落库失败: {exc}", "fetched": 0}

    return {"ok": True, "fetched": written}


@router.get("/api/finance/calendar")
async def finance_calendar_get(
    days: int = Query(7, ge=1, le=90, description="从今天起往后看几天"),
) -> Dict[str, Any]:
    """读本地财经日历，按日期升序。

    **只读本地 SQLite，不发任何外网请求** —— 东财被 WAF 拦掉时这个接口
    照样返回库里已有数据（`ok=true`，`stale` 标记最后同步时间）。
    """
    from finance_panel import store as cal_store

    today = datetime.date.today()
    end = today + datetime.timedelta(days=days)
    # 从今天往前留 3 天：当天可能有盘前补录/改期的事件。
    start = today - datetime.timedelta(days=3)

    try:
        events = cal_store.query_events(
            start_date=start.isoformat(), end_date=end.isoformat()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("财经日历读取失败: %s", exc)
        return {"ok": False, "reason": f"读取本地日历失败: {exc}", "events": []}

    return {"ok": True, "count": len(events), "events": events}


# ===== ETF 大资金追踪 =====
#
# 两条数据线，各自独立：
#   A. 份额变动（/api/finance/etf-flow/*）—— 高频代理指标，东财抓、SQLite 落库；
#   B. 披露持有人（/api/finance/etf-holdings/*）—— 低频权威披露，巨潮 PDF 规则抽取。
#
# 与 finance_panel 既有能力一致的约定：
# * sync 类端点**失败也回固定 shape**（`ok=false` + reason），不是 5xx —— 抓不到只
#   影响"数据有多新"，不该把同步任务报成失败；
# * GET 类端点**只读本地**，不发外网请求；
# * 缺失一律 null + reason，**绝不用 0 顶替**（0 份额 = "没数据"的伪装）。


@router.post("/api/finance/etf-flow/sync")
async def finance_etf_flow_sync() -> Dict[str, Any]:
    """对追踪池内 ETF 增量抓取份额（东财 F10 规模变动页），落 finance_panel.sqlite。

    **增量**：按库内每只 ETF 的最新份额日期续抓，不重复下历史。

    **口径**：东财该接口是**季频报告期**份额（03-31/06-30/09-30/12-31），
    不是日频。落库时 `granularity=quarterly` 如实标注，分析层据此说明
    `share_change_pct` 实为"相对上一报告期"。这是实测结论 —— datacenter-web
    的 ETF 份额 reportName 未能探测到（详见 finance_panel/etf_shares.py 头注释）。

    失败返回 `{ok: false, reason}`，不是 5xx。
    """
    from finance_panel import etf_shares as shares
    from finance_panel import store as panel_store

    codes = shares.pool_codes()
    if not codes:
        return {"ok": False, "per_etf": {}, "reason": "ETF 追踪池为空（检查 etf_pool.yaml）"}

    try:
        last_dates = panel_store.latest_share_dates(codes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取份额增量起点失败: %s", exc)
        last_dates = {c: None for c in codes}

    try:
        synced = shares.sync_pool(codes, last_dates=last_dates)
    except Exception as exc:  # noqa: BLE001 —— 抓取失败不抛给调用方
        logger.warning("ETF 份额抓取失败: %s", exc)
        return {"ok": False, "per_etf": {}, "reason": f"东财抓取失败: {exc}"}

    per_etf: Dict[str, Any] = {}
    written = 0
    for code, info in (synced.get("per_etf") or {}).items():
        rows = info.get("rows") or []
        if rows:
            for r in rows:
                r["thscode"] = code
            try:
                n = panel_store.upsert_shares(rows)
                written += n
                per_etf[code] = {"fetched": n}
            except Exception as exc:  # noqa: BLE001
                per_etf[code] = {"fetched": 0, "reason": f"落库失败: {exc}"}
        else:
            per_etf[code] = {"fetched": 0, "reason": info.get("reason") or "无新数据"}

    return {
        "ok": bool(written),
        "per_etf": per_etf,
        "fetched": written,
        "reason": None if written else "无新增份额数据（库内已最新，或东财返回空）",
    }


@router.get("/api/finance/etf-flow")
async def finance_etf_flow_get(
    date: Optional[str] = Query(None, description="行情交易日 YYYY-MM-DD，缺省取库内最新"),
    window: int = Query(5, ge=2, le=60, description="累计变动窗口（份额观测点数）"),
) -> Dict[str, Any]:
    """池内 ETF 资金流向：最新份额 + 变动% + 近窗口累计变动% + 成交额 + 信号。

    **口径提醒**：`share_change_pct` 是"相对上一**观测点**"而非日度 —— 现有份额源
    是季频（`granularity=quarterly`）。字段里同时给出 `granularity` 与
    `change_basis`，不靠字段名暗示精度。

    **信号**：`|share_change_pct| > 2%` 或成交额放大倍率 `> 1.382`。
    任一指标缺失时该指标不参与判定，另一个仍可触发；两者都缺 → signal=false。

    `items` 按 `abs(share_change_pct)` 降序，**null 排末尾**（缺失不当 0）。
    """
    from datasources import registry
    from finance_panel import etf_flow as flow
    from finance_panel import etf_shares as shares

    src = registry.get("fund")
    if src is None:
        raise fail(503, "fund 数据源未就绪")

    pool = shares.load_pool()
    try:
        return await flow.compute_flows(src, pool, window=window, target_date=date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ETF 资金流向查询失败: %s", exc)
        raise fail(503, f"计算 ETF 资金流向失败: {exc}") from exc


@router.post("/api/finance/etf-holdings/sync")
async def finance_etf_holdings_sync() -> Dict[str, Any]:
    """抓池内 ETF 的定期报告公告（巨潮，限速由 halo CninfoSource 内置），抽取前十名持有人。

    **增量**：只处理库内没有的报告期 —— 定期报告一年四期，全量重抓会打到巨潮限流
    且绝大多数时候是同一份 PDF 重下。

    **PDF 走 pypdf 文本层 + 锚点规则抽取**，不使用 LLM 读数。对不上份额总量的行标
    `disputed` 并**保留原始值**，绝不改写。

    无新公告也返回固定 shape `{ok, per_etf, message}`，不是 5xx。
    """
    from etfholdings import sync as holdings_sync
    from finance_panel import etf_shares as shares
    from finance_panel import store as panel_store

    pool = shares.load_pool()
    if not pool:
        return {"ok": False, "per_etf": {}, "message": "ETF 追踪池为空（检查 etf_pool.yaml）"}

    codes = [p["code"] for p in pool]
    try:
        known = panel_store.known_report_periods(codes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取已有报告期失败: %s", exc)
        known = {c: [] for c in codes}

    try:
        synced = holdings_sync.sync_pool(pool, known)
    except Exception as exc:  # noqa: BLE001 —— 同步失败不抛给调用方
        logger.warning("ETF 持有人同步失败: %s", exc)
        return {"ok": False, "per_etf": {}, "message": f"巨潮同步失败: {exc}"}

    written = 0
    if synced.get("rows"):
        try:
            written = panel_store.upsert_holdings(synced["rows"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("持有人落库失败: %s", exc)
            return {
                "ok": False,
                "per_etf": synced.get("per_etf", {}),
                "message": f"落库失败: {exc}",
            }

    per_etf: Dict[str, Any] = {}
    for code, info in (synced.get("per_etf") or {}).items():
        per_etf[code] = {"fetched": info.get("fetched", 0), "reason": info.get("reason")}

    return {
        "ok": written > 0,
        "per_etf": per_etf,
        "written": written,
        "message": (
            f"新增 {written} 条持有人记录" if written
            else synced.get("message") or "无新增披露记录"
        ),
    }


@router.get("/api/finance/etf-holdings")
async def finance_etf_holdings_get(
    thscode: str = Query(..., description="ETF 代码，6 位裸码或带后缀，如 510300"),
) -> Dict[str, Any]:
    """读某只 ETF 的披露持有人，按报告期倒序、同期按占比降序。

    **只读本地 SQLite，不发外网请求**。无数据返回 `{ok: true, items: [], message}`
    而不是 KeyError —— 空结果是最常见的正常路径（该基金还没披露定期报告）。

    `status` 三态：verified（占比与份额总量对得上 ±5%）/ disputed（对不上，
    保留原始值）/ pending（缺交叉数据）。
    """
    from finance_panel import store as panel_store

    # 代码归一：接受 "510300" 与 "510300.SH" 两种写法。
    code = thscode.strip()
    bare = code.split(".")[0]

    try:
        items = panel_store.query_holdings(bare)
        if not items:
            items = panel_store.query_holdings(code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取披露持有人失败: %s", exc)
        return {"ok": False, "items": [], "message": f"读取本地披露数据失败: {exc}"}

    return {
        "ok": True,
        "items": items,
        "message": None if items else "该 ETF 尚无已抽取的披露持有人记录（先跑 sync）",
    }
