package spec

// analysis 家族的声明：四个经典技术分析工具。
//
// 这一族与前面几批不同：结果不是行，是**算出来的**（道氏理论、威科夫、形态
// 识别、支撑阻力）。取数要查两个 DuckDB 再在 Go 里按日期合并——两个库是独立
// 只读连接、ATTACH 不了——这些表达不成数据，于是取数与计算以钩子的形式留在
// analysis 包那边（Fetch / Compose）。
//
// 进声明的是这一族四个工具的**身份与契约**：名字、说明、参数、窗口。
// 换来的是：四个工具各自手写的那套「解析参数 → 夹窗口 → 报错措辞」收进一处，
// 而模型看到的东西由契约测试逐字守着。

// AnalysisLevels 是hithink.finance.analysis.levels。
var AnalysisLevels = Spec{
	Name: "hithink.finance.analysis.levels",
	Description: `支撑阻力位分析工具。自动识别关键价格位，辅助买卖点判断。

分析内容：
- 枢轴点（Pivot Points）：基于最新交易日的 H/L/C 计算 R1/R2/R3 和 S1/S2/S3
- 斐波那契回撤：基于近期波段高低点计算 23.6%/38.2%/50%/61.8%/78.6% 回撤位
- 前高前低：近期显著高低点作为支撑阻力
- 均线支撑阻力：MA5/10/20/60/120/250 作为动态支撑阻力
- 整数关口：心理价位（如 1300、1250 等）
- 布林带边界：BB Upper/Lower 作为动态阻力支撑
- VWAP：机构成本线

使用示例：thscode="600519.SH", days=120`,
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
			Desc:    "分析回溯天数（默认 {default}，最大 {max}）",
			Default: 120,
			Max:     intMax(250),
		},
	},
}

// AnalysisPattern 是hithink.finance.analysis.pattern。
var AnalysisPattern = Spec{
	Name: "hithink.finance.analysis.pattern",
	Description: `图表形态识别工具。基于经典技术分析理论，自动识别重要图表形态。

检测形态：
- 反转形态：头肩顶/底、双顶/底
- 持续形态：三角形整理（收敛区间）、楔形、旗形
- 蜡烛图形态：Hammer、Shooting Star、Doji、Engulfing、Morning/Evening Star 等
- 布林带形态：收口（变盘前兆）、触轨（超买超卖）
- 突破信号：价格突破形态边界 + 量能确认

使用示例：thscode="600519.SH", days=120`,
	Composed: true,
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺代码，如 600519.SH",
			Required: true,
			EmptyMsg: "thscode 不能为空",
		},
		{
			Name:    "days",
			Type:    "integer",
			Desc:    "分析天数（默认 {default}，最大 {max}）",
			Default: 120,
			Max:     intMax(250),
		},
	},
}

// AnalysisTrend 是hithink.finance.analysis.trend。
var AnalysisTrend = Spec{
	Name: "hithink.finance.analysis.trend",
	Description: `经典趋势分析工具。基于道氏理论、均线系统和趋势强度指标，判断股票的趋势方向、强度和潜在转折。

分析内容：
- 道氏理论：识别HH/HL（上升趋势）或LH/LL（下降趋势）结构
- 重剑 518 均线系统：5日/18日均线交叉、开口与金叉次数语义（一次反弹/二次突破/三次跃迁）
- 均线排列：多头排列（MA5>MA10>MA20>MA60）/ 空头排列 / 均线粘合
- 均线交叉：短期均线上穿/下穿长期均线
- 葛兰碧法则：价格与MA20的关系（突破/跌破/回踩/反弹）
- 趋势强度：ADX分级、趋势持续性判断
- Supertrend：当前趋势方向
- 趋势转折信号：均线金叉死叉、价格突破关键均线

返回结构化JSON，包含趋势判断、均线状态、关键信号和操作建议。

使用示例：thscode="600519.SH", days=120`,
	Composed: true,
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码，如 600519.SH（茅台）、300750.SZ（宁德时代）",
			Required: true,
			EmptyMsg: "thscode 不能为空",
		},
		{
			Name:    "days",
			Type:    "integer",
			Desc:    "分析时间窗口（交易日），默认{default}，最大{max}",
			Default: 120,
			Max:     intMax(250),
		},
	},
}

// AnalysisVolume 是hithink.finance.analysis.volume。
var AnalysisVolume = Spec{
	Name: "hithink.finance.analysis.volume",
	Description: `量价分析工具。基于威科夫方法和量价关系理论，分析资金流向和主力行为。

分析内容：
- 微观量价：重剑双上五四态（双上五/阴量上五/量上五价未上/价上五量未上/双下五）
- 威科夫阶段判断：吸筹/派发/上涨(Markup)/下跌(Markdown)
- 量价关系：量价齐升、量价背离、缩量回调、放量突破
- 假突破检测：Spring(向下假突破)/Upthrust(向上假突破)
- OBV 能量潮：趋势方向、与价格背离
- 资金流向：CMF/MFI 多空力量分析
- VWAP 偏离：价格相对 VWAP 的位置

使用示例：thscode="600519.SH", days=60`,
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
			Desc:    "查询天数（默认 {default}，最大 {max}）",
			Default: 60,
			Max:     intMax(250),
		},
	},
}
