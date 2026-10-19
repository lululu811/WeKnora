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

模块划分
--------
* `core.py` —— 共享机制：常量、鉴权、错误体、代码校验、只读 SQL 校验。
* `routers/` —— 按业务域拆开的路由（core / finance / market）。
* 本文件 —— app 创建、lifespan、异常处理、路由注册。
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core import DUCKDB_DATABASES, MAX_QUERY_ROWS, SERVICE_VERSION, logger
from routers import core_router, finance_router, market_router

# 拆包只是把代码搬了家，**公开名字不许消失**：测试与存量脚本一直从 `main` 上取
# 这些符号（tests/unit/test_market_snapshot.py、test_breadth_freshness.py、
# test_halo_decider.py、test_read_only_sql.py、test_screener.py）。
from core import _read_only_sql  # noqa: F401
from routers.finance import HaloVerifyRequest, halo_verify  # noqa: F401
from routers.market import (  # noqa: F401
    _BREADTH_ABSOLUTE_MAX_DAYS,
    _BREADTH_STALE_DAYS,
    _load_breadth_for,
    market_anomaly_reasons,
    market_commodities,
    market_dividend_calendar,
    market_dragon_tiger,
    market_etf_holders,
    market_etf_institutional_ranking,
    market_fund_holdings,
    market_futures_basis_rate,
    market_futures_warehouse,
    market_hot_stock_trend,
    market_hot_stocks,
    market_ladder_detail,
    market_limit_break_down,
    market_snapshot,
    market_squeeze_status,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


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
    # 缩量回踩（B1 形态）：放量拉升 → 缩量回踩 → 不破前低。
    #
    # ⚠️ **未经频率审计**，与本文件里其它策略不同。原因不是偷懒，是审计口径不兼容：
    # run_signal_audit.sh 的分母是"存在完整 7-bar 窗口的 (股票, bar) 对数"
    # （signal_frequency_audit.md:24），而这五条信号全部跨 70 根窗口。正确的分母
    # 应该是 N_i-69，用 N_i-6 会把频率**放大约 10 倍**，跑出来的 verdict 是假的。
    # 那份审计文档自己也警告过"分母口径不同、同一条件下能差一倍"。
    #
    # 另外，这五条是"每日选股"级别的形态判定，而 signals.go 服务的是"每根 bar
    # 报状态"的场景 —— 两者用途本就不同，混在一个引擎里并不是好主意。
    #
    # 实测（1,200 只真实样本，2026-09-30）：命中 11 只 = 0.92%，选择性正常。
    # 要给它补审计，需要先让 run_signal_audit.sh 支持可变窗口。
    #
    # 来源是用户自有的 a-stock/b1.md。搬过来时改了**三处**（都是有意偏差，
    # 逐条理由写在 volume.py 顶部，不要当 bug 改回去）：
    #   1. 窗口用 70 根而不是 52 周 —— b1.md 正文与它自己的 SQL 在这点上
    #      互相矛盾，而 70 根正好是选股池单次快照能负担的量级；
    #   2. from_btm 的分母跟着用 70 日低；
    #   3. J 的阈值不放进形态计算，单独做成 KDJ深度超卖 信号。
    #
    # min_count=5 = 五条全中，这是对 b1.md 形态筛选（严格 AND）的忠实翻译。
    # 想放宽就把这里改成 4 或 3 —— 但那是**有意的口径决定**，不要因为
    # "选不出票"就顺手调低，先去看是哪一条在卡（见 SHRINK_* 常量的注释）。
    #
    # 依赖 close/open/low/volume 四列跨 70 根，故本规则声明 min_bars=70
    # ≥ 70（见本规则的 min_bars）；默认窗口下这五条信号会一条都不发，
    # 而不是发出错的。
    "shrink_pullback": {
        "match_signals": ["缩量回踩", "下跌不放量", "守住前低", "低位回踩",
                          "KDJ深度超卖"],
        "min_count": 5,
        # 需要 70 根：shrink 看近 15 日最大量、dn_up 看近 15 日阴阳线均量、
        # 守前低看 70 日最低。**按这条规则单独声明**，不要去改全局
        # screener.LOOKBACK_DAYS —— 那会让每条策略都按 70 天取全市场，
        # 实测一次 /zettaranc/screen 就把 python-service OOM 掉。
        "min_bars": 70,
    },
    # 左侧转右侧（黄白线双闸门）。来源是用户自己的说法：「等市场从左侧转右侧后，
    # 从 BBI线升级为黄白线」「BBI线是保命用的，黄白线是赚钱用的」。
    #
    # 按那次 grill 定下的分工：白线定"什么时候进"，黄线定"能不能做"。
    # 白线是 DEMA10 快线、黄线是 LONGBBI(14/28/57/114) 慢线，于是有两种转右：
    #   白线金叉黄线 = 启动位（快线穿慢线），实测 0.91%
    #   收盘上穿黄线 = 已站到慢线之上（左侧 → 右侧），实测 4.38%
    #
    # ⚠️ min_count 是 1（**并集**）而不是 2，这一点是量出来的、不是随手定的：
    # 白线是收盘的 DEMA，比价格平滑，因此**总是先于价格**穿过黄线，两次穿越落在
    # 不同交易日。要求"同一根 K 线上既白线金叉又收盘上穿"在 1,200 只真实样本上
    # 选出 **0 只**。
    # 加迟滞窗口（近 3~5 根内上穿过、且今收仍在黄线上方）更严：实测 0.06% /
    # 0.23%，掉到 1% 以下，审计的 informative 档是 1%~20%。
    # 所以这里取并集：任一形式的转右即命中，选股器本来就按命中信号数打分排序，
    # 两条都中的票会排在只有一条的前面 —— 分层由排序表达，不需要同日 AND。
    #
    # 这四条信号在 Go 侧已过 run_signal_audit.sh（300 只 / 114,933 bar，
    # 0.9110% / 0.9519% / 4.3782% / 4.4739%，全部 informative）才回填到本侧，
    # 顺序依据是 query.go 顶部写死的对齐流程。
    "trend_right_combo": {
        "match_signals": ["白线金叉黄线", "收盘上穿黄线"],
        "min_count": 1,
        "direction": "bullish",
    },
    # 对称的转左组合。没有它，选股器只能选出"该买的票"、选不出"该躲开的票"，
    # 而规避持仓通常比选新票更急 —— 与 anomaly / overbought_combo 补 bearish
    # 是同一个理由。
    "trend_left_combo": {
        "match_signals": ["白线死叉黄线", "收盘下穿黄线"],
        "min_count": 1,
        "direction": "bearish",
    },
    # 长安三件套：当根 J 深度超卖 → 前一日放量长阳且 J 拐头 → 当根分歧转一致缩半量。
    #
    # 它在 Go 侧 signals.go 试过，**主动移出**了 —— 不是因为不触发：
    # 全库 5,572 只 × 十年 10,349,853 根 bar 上七条同时成立恰好 **117 次**
    # （0.0011%，约每 20 个交易日全市场一次）。1/88,000 的 bar 率不属于
    # "每根 K 报一次状态"，而属于"今天恰好出现这个形态"，所以放在形态层。
    #
    # run_signal_audit.sh 给的 `dead` 是抽样波动：300 只定距样本只覆盖全库 2.7%，
    # 期望 3.2 次，λ=3.2 时 P(0)≈4%。完整论证见 volume.py 顶部。
    #
    # **2026-10-04 用户决定：不重新标定阈值，就当一个形态留着。** 上面那个
    # 0.0011% 是"按 a-stock 原始七条阈值"的实测值，不是实现有 bug —— 要改
    # 阈值的话是策略判断，需要先想清楚放宽哪一条会让它退化成什么，不是调参问题。
    # 记在这里是为了下次有人（多半是我）看到"选中 0 只"又想去动它时，
    # 知道这是有意保留的现状，不是遗留缺陷。
    "changan_combo": {
        "match_signals": ["长安三件套"],
        "min_count": 1,
    },
    # 双枪放量：rows[7] 与 rows[0] 各为放量阳柱（量比≥1.5、实体≥2%），
    # 中间 rows[1..3] 均量低于基准的 80% 且至少一根阴线。
    #
    # ⚠️ 这条**没有**进 Go 侧：它要 15 根窗口，而 run_signal_audit.sh 统一只喂
    # 7 根窗口（signal_frequency_audit_test.go:49-50，auditWinSize = 7），
    # `len(rows) >= 15` 在审计里恒不成立，必然报 dead。那个 dead 说的是
    # "审计窗口不够"，不是"策略不成立"——与 shrink_pullback 的 70 根窗口同类。
    "double_gun_combo": {
        "match_signals": ["双枪放量"],
        "min_count": 1,
    },
    # 单针下 20：3 日涨幅的 15 根窗口百分位 <=20 且 21 日涨幅的 105 根窗口百分位
    # >=60 —— 长期强势趋势里的短期超跌。
    #
    # 这条与上面两个不同：它**过了** Go 侧审计（8.09%，informative），所以
    # signals.go 里有同名信号，这里只是把 Python 侧的字段名对齐到同一套口径。
    #
    # ⚠️ a-stock 的 p9_needle_screen.sql 至今仍引用改名前的
    # zettaranc_rsl_short_3 / rsl_long_21 —— 那两个列在库里已不存在（2026-10-01
    # 随 rsl_same_name_different_meaning 改名），那份 SQL 跑起来直接 Binder Error。
    "needle_under20_combo": {
        "match_signals": ["单针下20"],
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


# ===== 路由注册 =====
#
# 三组路由之间没有任何一条 (method, path) 重复，所以注册顺序不影响匹配结果。

app.include_router(core_router)
app.include_router(finance_router)
app.include_router(market_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "50052")))
