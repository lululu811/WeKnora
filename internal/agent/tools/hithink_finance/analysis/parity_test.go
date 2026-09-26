package analysis

import (
	"math"
	"testing"
)

// 行序契约：FetchMarketData 用 `ORDER BY date DESC`，所以 rows[0] 是最新一根
// K 线。findSwings 从小到大遍历 i，返回的下标升序。两个方向叠加的结果是
// **列表开头 = 最近的摆动点**。下面所有 fixture 都按这个契约构造。

func mkRow(close, high, low, vol float64) marketRow {
	return marketRow{
		Open: close, High: high, Low: low, Close: close, Vol: vol,
		MA5: 0, MA10: 0, MA20: 0, MA60: 0, MA120: 0, MA250: 0,
	}
}

// mkRows 把"按时间正序（最老在前）的收盘价"转成服务实际返回的倒序行。
func mkRows(chronological []float64) []marketRow {
	rows := make([]marketRow, 0, len(chronological))
	for _, c := range chronological {
		rows = append(rows, mkRow(c, c*1.01, c*0.99, 1e6))
	}
	for i, j := 0, len(rows)-1; i < j; i, j = i+1, j-1 {
		rows[i], rows[j] = rows[j], rows[i]
	}
	return rows
}

// zigzag 通过给定的拐点生成正序收盘价。
func zigzag(peaks, troughs [][2]float64, n int) []float64 {
	type pt struct{ bar, val float64 }
	points := make([]pt, 0, len(peaks)+len(troughs))
	for _, p := range append(append([][2]float64{}, peaks...), troughs...) {
		points = append(points, pt{p[0], p[1]})
	}
	for i := 1; i < len(points); i++ {
		for j := i; j > 0 && points[j].bar < points[j-1].bar; j-- {
			points[j], points[j-1] = points[j-1], points[j]
		}
	}
	prices := make([]float64, n)
	for b := range prices {
		prices[b] = points[0].val
	}
	for i := 0; i+1 < len(points); i++ {
		b0, v0 := int(points[i].bar), points[i].val
		b1, v1 := int(points[i+1].bar), points[i+1].val
		for b := b0; b <= b1 && b < n; b++ {
			if b < 0 {
				continue
			}
			prices[b] = v0 + (v1-v0)*float64(b-b0)/float64(b1-b0)
		}
	}
	for b := int(int(points[len(points)-1].bar)); b < n; b++ {
		prices[b] = points[len(points)-1].val
	}
	return prices
}

// --- 摆动点方向 -------------------------------------------------------

func TestFindSwingsHeadIsMostRecent(t *testing.T) {
	rows := mkRows(zigzag(
		[][2]float64{{4, 12}, {16, 14}, {28, 16}},
		[][2]float64{{10, 11}, {22, 13}, {34, 15}}, 40))
	highs, _ := findSwings(rows, 5)
	if len(highs) < 2 {
		t.Fatalf("expected at least 2 swing highs, got %d", len(highs))
	}
	for i := 1; i < len(highs); i++ {
		if highs[i-1] >= highs[i] {
			t.Fatalf("swing indices must ascend: %v", highs)
		}
	}
	// rows 倒序 => 下标越小越新 => 价格应单调不增
	for i := 1; i < len(highs); i++ {
		if rows[highs[i-1]].High < rows[highs[i]].High {
			t.Fatalf("swing highs out of order: idx %d=%.2f idx %d=%.2f",
				highs[i-1], rows[highs[i-1]].High, highs[i], rows[highs[i]].High)
		}
	}
}

// --- 道氏结构 ---------------------------------------------------------

// 教科书级的 Higher High + Higher-Low 上升趋势曾经被判成 downtrend：
// 旧代码取 highs[len-1]/highs[len-2] 当"最近/次近"，拿到的是最老的两个摆动点。
func TestAnalyzeDowStructureUptrend(t *testing.T) {
	rows := mkRows(zigzag(
		[][2]float64{{4, 12}, {16, 14}, {28, 16}},
		[][2]float64{{10, 11}, {22, 13}, {34, 15}}, 40))
	dir, desc := analyzeDowStructure(rows)
	if dir != "uptrend" {
		t.Fatalf("pure Higher-High + Higher-Low series reported %q (%s)", dir, desc)
	}
}

func TestAnalyzeDowStructureDowntrend(t *testing.T) {
	rows := mkRows(zigzag(
		[][2]float64{{10, 20}, {24, 18}, {38, 16}},
		[][2]float64{{2, 17}, {17, 15}, {31, 13}, {44, 12}}, 48))
	dir, desc := analyzeDowStructure(rows)
	if dir != "downtrend" {
		t.Fatalf("pure Lower-High + Lower-Low series reported %q (%s)", dir, desc)
	}
}

// --- 楔形 -------------------------------------------------------------

// rows 倒序时 `rows[i] - rows[i+1]` 是"新 - 老"，正数代表上升。
// 旧代码把正数映射成"下降楔形/bullish"，于是每个干净上涨趋势都被
// 标成下降楔形，desc 还写着"高点和低点都在下降"。
func TestDetectWedgeRising(t *testing.T) {
	chron := make([]float64, 25)
	for i := range chron {
		chron[i] = 10 + float64(i)*0.3
	}
	found := detectWedge(mkRows(chron))
	if found == nil {
		t.Fatal("expected a rising wedge")
	}
	if found["name"] != "上升楔形" || found["direction"] != "bearish" {
		t.Fatalf("rising highs/lows reported %v / %v", found["name"], found["direction"])
	}
}

func TestDetectWedgeFalling(t *testing.T) {
	chron := make([]float64, 25)
	for i := range chron {
		chron[i] = 25 - float64(i)*0.3
	}
	found := detectWedge(mkRows(chron))
	if found == nil {
		t.Fatal("expected a falling wedge")
	}
	if found["name"] != "下降楔形" || found["direction"] != "bullish" {
		t.Fatalf("falling highs/lows reported %v / %v", found["name"], found["direction"])
	}
}

// --- 旗形 -------------------------------------------------------------

// 旗杆在**更老**的一侧，整理段在最近的一侧。旧代码把两段装反，
// 标准牛熊旗形一个都识别不出来。
func TestDetectFlagBull(t *testing.T) {
	rows := mkRows([]float64{
		10.0, 10.8, 11.6, 12.4, 13.2, 14.0, 14.6, 15.0, 15.4, 15.8,
		16.0, 15.9, 15.8, 15.7, 15.6, 15.5, 15.4, 15.3,
	})
	found := detectFlag(rows)
	if found == nil {
		t.Fatal("expected a 牛市旗形 (pole up then drift)")
	}
	if found["direction"] != "bullish" {
		t.Fatalf("got %v / %v", found["name"], found["direction"])
	}
}

func TestDetectFlagBear(t *testing.T) {
	rows := mkRows([]float64{
		20.0, 19.2, 18.4, 17.6, 16.8, 16.0, 15.4, 15.0, 14.6, 14.2,
		14.0, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7,
	})
	found := detectFlag(rows)
	if found == nil {
		t.Fatal("expected a 熊市旗形 (pole down then drift)")
	}
	if found["direction"] != "bearish" {
		t.Fatalf("got %v / %v", found["name"], found["direction"])
	}
}

// --- 头肩顶 -----------------------------------------------------------

// target 曾经写成 `head - (head - neckline)`，恒等于 neckline。
func TestHeadAndShouldersTargetBelowNeckline(t *testing.T) {
	rows := mkRows(zigzag(
		[][2]float64{{10, 12}, {30, 15}, {50, 12}},
		[][2]float64{{4, 9}, {20, 10}, {40, 10}, {56, 10.5}}, 60))
	found := detectHeadAndShoulders(rows)
	if found == nil {
		t.Fatal("expected a 头肩顶")
	}
	levels := found["key_levels"].(map[string]float64)
	head, neckline, target := levels["head"], levels["neckline"], levels["target"]
	if target >= neckline {
		t.Fatalf("target %.2f must sit below the neckline %.2f", target, neckline)
	}
	if head <= neckline {
		t.Fatalf("head %.2f should be above the neckline %.2f", head, neckline)
	}
	if math.Abs(target-(2*neckline-head)) > 1e-9 {
		t.Fatalf("target %.4f is not the measured move 2*neckline-head = %.4f",
			target, 2*neckline-head)
	}
}

func TestHeadAndShouldersShoulderOrder(t *testing.T) {
	rows := mkRows(zigzag(
		[][2]float64{{10, 12}, {30, 15}, {50, 12}},
		[][2]float64{{4, 9}, {20, 10}, {40, 10}, {56, 10.5}}, 60))
	levels := detectHeadAndShoulders(rows)["key_levels"].(map[string]float64)
	if levels["left_shoulder"] >= levels["head"] || levels["right_shoulder"] >= levels["head"] {
		t.Fatalf("shoulders must be below the head: %+v", levels)
	}
}
