package spec

import "fmt"

// DeclaredSignalNames 是形态扫描工具 **实际会发出** 的全部信号名，一一对应，一个
// 多少一个不少。
//
// 这份清单是实现与声明之间的契约：工具给模型的说明是逐字交给 LLM 的，写错一个
// 名字模型就会去调、拿不到、再自己编一个。历史上有四个信号
// （PSAR翻转 / VWAP突破 / 量价齐升 / 量价背离）是"描述里广告了、代码里没实现"。
//
// 清单住在声明这一侧，是因为说明文字里的信号总数要由它算出来
// （patternScanDescription 里的 %d）——写死那个数，广告就会与实现悄悄脱节。
//
// 单子靠三个方向由 pattern 包的 signals_contract_test.go 把关，测试里没有正则：
//  1. 清单里每一条都必须被某组构造数据真正打出来（少写了 → 达不到 / 被删了 → 立刻红）
//  2. detectSignals 打出来的每一条都必须在清单里（新加了信号忘了登记 → 立刻红）
//  3. 清单里的每一条都必须出现在说明的正文里
//
// 增删信号时改这里，同时改下面的 patternScanDescription 里的清单文字。
var DeclaredSignalNames = []string{
	// Momentum
	"MACD金叉", "MACD死叉",
	"KDJ超卖金叉", "KDJ超买死叉",
	"J超卖回中", "J超买回中",
	"RSI6超卖", "RSI6超买",
	"RSI14超卖回升", "RSI14超买回落",
	"MACD动能衰减",
	"Stochastic超卖金叉", "Stochastic超买死叉",
	"CCI超卖", "CCI超买",
	"Williams%R超卖", "Williams%R超买",
	"MFI超卖", "MFI超买",
	"ER转高效", "ER转低效",
	// Trend
	"Supertrend翻转", "Supertrend翻空",
	"PSAR翻多", "PSAR翻空",
	"ADX多头趋势", "ADX空头趋势",
	"DI金叉", "DI死叉",
	"Aroon多头排列", "Aroon空头排列",
	"Vortex金叉", "Vortex死叉",
	"白线金叉黄线", "白线死叉黄线", "收盘上穿黄线", "收盘下穿黄线",
	"CHOP进入震荡", "CHOP重回趋势",
	"KC中轨多头带", "KC中轨空头带",
	// Volatility
	"布林带收口", "布林中轨收复", "布林中轨跌破",
	"Donchian上轨突破", "Donchian下轨跌破",
	"Keltner挤压", "Keltner挤压向上突破", "Keltner挤压向下突破",
	"NATR进入高波动档位", "NATR退出高波动档位",
	"ATR扩张",
	// Volume
	"CMF资金流入", "CMF资金流出",
	"上穿VWAP", "下穿VWAP",
	"价量背离", "缩量下跌吸筹",
	"PVI强于NVI吸筹", "NVI强于PVI派发",
	// 高级形态（阈值逐条来自 a-stock/strategies/sql/p9_needle_screen.sql）
	"单针下20",
	// Statistics
	"Z-Score超卖", "Z-Score超买",
	"线性回归上升", "线性回归下降",
	// Candlestick
	"Morning Star晨星", "Evening Star暮星", "Hammer锤子线", "Shooting Star流星",
	"Doji十字星", "Engulfing吞没", "Harami孕线", "Piercing刺透",
	"Dark Cloud乌云盖顶", "Three White Soldiers三白兵", "Three Black Crows三乌鸦",
}

// NoisySignalNames 是"触发频率高到不再携带信息"的信号，默认**不**返回。
//
// 为什么要有这层：信号是按 bar 注入模型上下文的。一条在 20% 以上的 bar 上都成立
// 的信号，报的是**当前市场状态**（"趋势线斜率为正"）而不是**转折事件**；连续
// 若干天读到同一句话，模型会学会无视它 —— 真正的转折（1%~5% 那一档）就被淹没。
// 所以默认只给 informative，需要时用 include_noisy 显式索取。
//
// ⚠️ **名单的权威来源是 signal_frequency_audit.md**，由
// signal_frequency_audit_test.go 跑**真实的 detectSignals** 量出（300 只票跨步
// 抽样、279,360 个 7-bar 窗口、2022-08 起跨 2022 熊市与 2024-26 行情）。
//
// **不要用 scripts/classify_signals.py 决定这份名单。** 那个脚本是用 SQL 重写
// 判据的旁路，分母是"bar 数"而审计是"7-bar 窗口数"，同一条件下两者能差一倍
// （MACD动能衰减：SQL 20.43% vs 审计 10.25%，前者越过 20% 阈值、后者没越）。
// 2026-10-01 正是先信了 SQL 那一版，把 MACD动能衰减 错折了、又漏折了
// Donchian下轨跌破。SQL 脚本只能用来**核对判据有没有抄错**，不能定名单。
//
// 本名单 2026-10-01 改过两次，都是把 SQL 的结果换成审计的：
//
//	第一次（判据抄错）: 11 条 → 8 条。MACD动能衰减 原写成 hist>0、
//	  Aroon多头排列 原写成 up>down、CMF 门槛原写成 ±0.05、RSI6超卖 原写 <30、
//	  Keltner挤压 原写成状态量（真实是首次进入）。修正后 Aroon多头排列 /
//	  CMF资金流入 / Keltner挤压 掉出名单。
//	第二次（换权威）: 8 条 → 8 条，但**换了两条**。MACD动能衰减 掉出
//	  （审计 10.25%，informative）；Donchian下轨跌破 进来（审计 27.72%）。
//
// 名单是**数据结论**，不是价值判断：线性回归 47%/51% 报的是趋势方向，用户在
// "这只票现在什么趋势"的问题上可能真的想要它们。所以它们被**默认折叠**，不是
// 被删除 —— include_noisy=true 拿得到全量。
var NoisySignalNames = []string{
	"线性回归下降",       // 51.75%
	"线性回归上升",       // 46.70%
	"CMF资金流出",      // 31.90%
	"Donchian下轨跌破", // 26.99%
	"Williams%R超卖", // 26.14%
	"ADX多头趋势",      // 25.58%
	"Aroon空头排列",    // 23.90%
	"CCI超卖",        // 21.65%
}

// patternScanDescription 是形态扫描工具给模型看的说明。
//
// 里面的信号总数是**算出来的**（len(DeclaredSignalNames)），不是写死的——
// 清单变了，广告跟着变。
//
// 说明必须与 detectSignals 实际发出的信号一一对应：这份文字是逐字交给 LLM 的，
// 广告一个没实现的信号，比不广告更糟。
const patternScanDescription = `扫描股票的技术形态信号。从 DuckDB indicators 库的 227 个预计算指标中，
综合检测 %d 种买卖形态，返回结构化信号列表。

下面列的是本工具**真的会返回**的信号名称（与 signals.go 逐条对应）：

- 动量：MACD金叉、MACD死叉、KDJ超卖金叉、KDJ超买死叉、J超卖回中、J超买回中、
  RSI6超卖、RSI6超买、RSI14超卖回升、RSI14超买回落、Stochastic超卖金叉、
  Stochastic超买死叉、CCI超卖、CCI超买、Williams%%R超卖、Williams%%R超买、
  MFI超卖、MFI超买、ER转高效、ER转低效
- 趋势：Supertrend翻转、Supertrend翻空、PSAR翻多、PSAR翻空、ADX多头趋势、
  ADX空头趋势、DI金叉、DI死叉、Aroon多头排列、Aroon空头排列、Vortex金叉、
  Vortex死叉、白线金叉黄线、白线死叉黄线、收盘上穿黄线、收盘下穿黄线、
  CHOP进入震荡、CHOP重回趋势、KC中轨多头带、KC中轨空头带、
  MACD动能衰减
  双线口径：「白线」是 DEMA10 快线，「黄线」是 LONGBBI(14/28/57/114) 慢线。
  白线金叉/死叉黄线 报快线穿越慢线（启动位），收盘上穿/下穿黄线 报价格穿越慢线
  （左侧转右侧的那一步）—— 四条都是转折而非状态。这里**不**报"收盘在黄线上方"，
  也不报"黄线斜率向上"：实测前者 48%%、后者 46%% 的 K 线都成立，报的是行情状态。
- 波动：布林带收口、布林中轨收复、布林中轨跌破、Donchian上轨突破、
  Donchian下轨跌破、Keltner挤压、Keltner挤压向上突破、Keltner挤压向下突破、
  NATR进入高波动档位、NATR退出高波动档位、ATR扩张
- 量价：CMF资金流入、CMF资金流出、价量背离、缩量下跌吸筹、PVI强于NVI吸筹、
  NVI强于PVI派发、上穿VWAP、下穿VWAP
- 高级形态：「单针下20」是 3 日涨幅的 15 根窗口百分位 <=20 且 21 日涨幅的 105 根窗口
  百分位 >=60 —— 长期强势趋势里的短期超跌。
- 统计：Z-Score超卖、Z-Score超买、线性回归上升、线性回归下降
- 蜡烛图：Morning Star晨星、Evening Star暮星、Hammer锤子线、Shooting Star流星、
  Doji十字星、Engulfing吞没、Harami孕线、Piercing刺透、Dark Cloud乌云盖顶、
  Three White Soldiers三白兵、Three Black Crows三乌鸦（共 11 种）

少数信号的触发门槛（其余见 signals.go 注释）：
- J超卖回中/J超买回中：J 越出 0~100 的极值区后回到带内，J<0 看多、J>100 后回落看空。
- ER转高效/ER转低效：效率比跨过 1/3（趋势至少要完成全程三分之一才值得跟随）。
- CHOP进入震荡：CHOP 升破 61.8，趋势信号失真，宜减仓；CHOP重回趋势：跌破 38.2。
- Keltner挤压：布林带收进 Keltner 通道；Keltner挤压向上/向下突破：挤压后首次站上/跌破外轨。
- NATR进入高波动档位：NATR 升破 5%%，止损需放宽、仓位减半；退出后恢复正常。
- 价量背离：5 日价格涨而 PVT 降；缩量下跌吸筹：5 日价格跌而 PVT 升。
- PVI强于NVI吸筹/NVI强于PVI派发：放量日与缩量日的资金强弱发生翻转。

凡是用到价格、通道边界、累计量指标的信号，缺失值（query.go 的 COALESCE 补 0）
都会被显式跳过——缺数据不会伪造出信号，所以某些标的可能少于 %d 条。

**默认只返回"转折类"信号。** 另有 %d 条状态类信号在 20%%~52%% 的窗口上都成立
（线性回归升降、CMF 资金流出、Donchian下轨跌破、Williams%%R超卖、ADX多头趋势、
Aroon空头排列、CCI超卖），报的是**当前状态**而不是转折事件 —— 连着几天读到同一句
话，模型会学会无视它，真正的转折就被淹没。所以它们默认折叠，只在返回的
suppressed 字段里报出**名字和条数**（不静默丢弃：你看到"没有 CCI超卖"
时要知道那个条件其实是成立的）。

用户问"这只票趋势方向如何""资金是流出还是流入""是不是在破位"这类**要状态**
的问题时，用 include_noisy=true 重调；问"有什么转折信号"就用默认。

每个信号的 strength 是 0~1 的相对强度（规则基准分，**尚未**按数据完整度折扣）。

使用示例：
- 默认（只看转折）：thscode="600519.SH", days=10
- 连状态一起看：thscode="600519.SH", days=10, include_noisy=true`

// PatternScan 是技术形态信号扫描。
//
// 取数（两个 DuckDB：指标 + 行情）与信号识别都是 Go 侧的活——识别要跨库按日期
// 合并，表达不成数据——于是它们以 Fetch / Compose 两个钩子的形式留在 pattern 包，
// 声明这边只管身份与契约：名字、说明、参数、窗口。
var PatternScan = Spec{
	Name: "hithink.finance.pattern.scan",
	Description: fmt.Sprintf(patternScanDescription,
		len(DeclaredSignalNames), len(DeclaredSignalNames), len(NoisySignalNames)),
	Composed: true,
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "days",
			Type:    "integer",
			Desc:    "扫描天数（默认 10，用于检测交叉信号）",
			Default: 10,
			Max:     intMax(60),
		},
		{
			Name:    "include_noisy",
			Type:    "boolean",
			Desc:    "是否同时返回高频状态类信号（默认 false）。这些信号在 20%~52% 的 bar 上都成立，报的是当前状态而非转折事件，默认返回会让真正的转折被淹没。仅在用户明确要看趋势方向/资金流向状态时置 true。",
			Default: false,
		},
	},
}
