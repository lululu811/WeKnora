// Cross-stack conformance test.
//
// The three indicator stacks (Go / Python / TypeScript) deliberately keep their
// own formula implementations — this task unifies *metadata*, not code. But
// parameters and rounding conventions are exactly the things that drift, so this
// test pins them: it builds one synthetic K-line series, feeds the **same bytes**
// to a Go reference implementation and to the real frontend TypeScript module,
// and asserts the two agree within the tolerance declared in
// config/indicators.yaml.
//
// The Go side is written from the formula spec, not transliterated from the
// TypeScript, so a divergence means one of the two is wrong — not that the
// test agrees with itself.
//
// The Python stack is not executed here: it has no in-process indicator
// implementation either (it reads precomputed DuckDB columns, same as Go). What
// is checked for Python/Go is the *column contract* — see
// TestDuckDBColumnContractHoldsInBothStacks.
//
// Known, documented divergences are reported by
// TestFullPrecisionStackDivergesFromDisplayRounded, which logs the numbers
// instead of asserting them.
package indicators

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Shared fixture
// ---------------------------------------------------------------------------

// Bar is one synthetic candle. Field names match the frontend's KLineData so the
// same JSON feeds both sides.
type Bar struct {
	Open   float64 `json:"open"`
	High   float64 `json:"high"`
	Low    float64 `json:"low"`
	Close  float64 `json:"close"`
	Volume float64 `json:"volume"`
}

// buildFixture generates a deterministic pseudo-random walk.
//
// Deterministic on purpose: the same fixture must reproduce the same divergence
// numbers on every machine, otherwise a reported drift is impossible to
// reproduce. A plain LCG is used rather than math/rand so the sequence is fixed
// by this file, not by the Go version. Prices are rounded to 2 decimals because
// real K-line data is, and unrounded prices would add float noise to every
// comparison for no benefit.
func buildFixture(n int) []Bar {
	seed := uint64(20260929)
	next := func() float64 {
		// xorshift64*
		seed ^= seed >> 12
		seed ^= seed << 25
		seed ^= seed >> 27
		return float64((seed*2685821657736338717)%1000000) / 1000000.0
	}
	round2 := func(v float64) float64 { return math.Round(v*100) / 100 }

	bars := make([]Bar, 0, n)
	price := 50.0
	for i := 0; i < n; i++ {
		// Drift plus noise, clamped so the series stays plausible.
		price += (next() - 0.48) * 2.4
		if price < 5 {
			price = 5
		}
		if price > 200 {
			price = 200
		}
		open := round2(price)
		// Close is the next step so high/low always bracket it.
		price += (next() - 0.5) * 1.6
		close := round2(price)
		high := round2(math.Max(open, close) + next()*0.9)
		low := round2(math.Min(open, close) - next()*0.9)
		vol := math.Round((50000+next()*900000)/100) * 100
		bars = append(bars, Bar{Open: open, High: high, Low: low, Close: close, Volume: vol})
	}
	return bars
}

// ---------------------------------------------------------------------------
// Go reference implementation (written from the formula spec)
// ---------------------------------------------------------------------------

// round2 rounds to 2 decimals with the **exact** semantics of JavaScript's
// Number.prototype.toFixed, because that is what the TypeScript stack does and
// this reference has to be the shared expectation rather than a near-miss.
//
// Neither common Go idiom matches it. Measured on the conformance fixture
// (both rows are the four BBI moving averages summed and divided by 4):
//
//	bar 77:  40.06 + 40.69 + 41.88 + 43.87 = 166.5
//	         166.5 / 4                 = 41.625            (精确二进制值，落在 .5 上)
//	         JS  (166.5/4).toFixed(2)   = 41.63   <- 对：ECMA 规定取较大的 n
//	         Go  math.Round(v*100)/100  = 41.63   <- 对（恰好是 .5，away from zero）
//	         Go  strconv.FormatFloat    = 41.62   <- 错：Go 用 banker's rounding
//
//	bar 90:  43.47 + 42.39 + 41.36 + 41.44 = 168.66
//	         168.66 / 4                 = 42.16499999999999914735  (严格小于 42.165)
//	         JS  (168.66/4).toFixed(2)   = 42.16   <- 对：按精确值取最近者
//	         Go  math.Round(v*100)/100  = 42.17   <- 错：v*100 又把 double 舍入到 .5
//	         Go  strconv.FormatFloat    = 42.16   <- 对
//
// So math.Round is right on exact ties and wrong just below them;
// strconv.FormatFloat is the mirror image. ECMA-262 says: pick the integer n
// minimising |n/100 - x|, and on a tie pick the **larger** n. big.Float with
// enough precision reproduces that exactly.
func round2(v float64) float64 {
	// big.Rat is exact for a float64, so this reproduces ECMA-262 toFixed:
	// pick the integer minimising |n/100 - x|, ties to the larger magnitude.
	r := new(big.Rat).SetFloat64(v)
	r.Mul(r, big.NewRat(100, 1))

	neg := r.Sign() < 0
	num := new(big.Int).Set(r.Num())
	if neg {
		num.Neg(num)
	}
	den := r.Denom()

	q, rem := new(big.Int).QuoRem(num, den, new(big.Int))
	// Step away from zero when the remainder is at least half the denominator;
	// ">=" is what makes an exact tie round away from zero (toFixed's rule).
	twice := new(big.Int).Abs(rem)
	twice.Lsh(twice, 1)
	if twice.Cmp(den) >= 0 {
		q.Add(q, big.NewInt(1))
	}
	if neg {
		q.Neg(q)
	}

	out, _ := new(big.Rat).SetFrac(q, big.NewInt(100)).Float64()
	return out
}

// refEMA is the spec EMA: seed with the first close, k = 2/(n+1), recurse.
// The recursion runs at full precision; only the emitted value is rounded.
// intermediateRounded reproduces the stacks that feed the *rounded* output of
// one stage into the next (that is what the frontend does for DEMA/LongBBI/BBI).
func refEMA(closes []float64, n int, intermediateRounded bool) []*float64 {
	out := make([]*float64, len(closes))
	k := 2.0 / (float64(n) + 1)
	var ema *float64
	for i, c := range closes {
		if ema == nil {
			v := c
			ema = &v
		} else {
			v := c*k + *ema*(1-k)
			ema = &v
		}
		emitted := *ema
		if intermediateRounded {
			emitted = round2(emitted)
		}
		r := round2(emitted)
		out[i] = &r
	}
	return out
}

// refDEMA is DEMA = EMA(EMA(CLOSE, n), n). The inner EMA's *rounded* output is
// what the second stage consumes, matching the frontend and any DuckDB stack.
func refDEMA(closes []float64, n int) []*float64 {
	inner := refEMA(closes, n, false)
	var fed []float64
	for _, v := range inner {
		if v == nil {
			return nil
		}
		fed = append(fed, *v)
	}
	outer := refEMA(fed, n, false)
	// Re-apply the null mask from the inner stage.
	innerNull := make([]bool, len(inner))
	for i, v := range inner {
		innerNull[i] = v == nil
	}
	for i := range outer {
		if innerNull[i] {
			outer[i] = nil
		}
	}
	return outer
}

// refSMA is the spec simple moving average, null until n bars exist.
// out[i] is the rounded value, matching the frontend's toFixed(2).
func refSMA(closes []float64, n int) []*float64 {
	out := make([]*float64, len(closes))
	sum := 0.0
	for i := range closes {
		sum += closes[i]
		if i >= n {
			sum -= closes[i-n]
		}
		if i >= n-1 {
			v := round2(sum / float64(n))
			out[i] = &v
		}
	}
	return out
}

// refLongBBI is (MA(p1) + MA(p2) + MA(p3) + MA(p4)) / 4.
//
// The frontend has TWO variants of this formula and they are not the same
// indicator:
//
//   - calcLongBBI (stock-score.ts) — the yellow line. When some moving averages
//     are not formed yet it averages the ones that exist, so the line starts at
//     bar MA14 (index 13), not bar MA114 (index 113).
//   - calcBBI (indicators.ts) — the 牵牛绳. Returns null until all four exist, so
//     the line starts broken and only becomes continuous at index 23.
//
// degrade selects which one is being modelled. The default (true) is the
// yellow-line behaviour; Z_BBI passes false.
//
// The split is a real, pre-existing semantic difference inside the frontend
// itself, not a Go-vs-JS disagreement. It is documented in the report and
// covered by TestBbiAndLongBBIDifferBeforeTwentyFourBars.
func refLongBBI(closes []float64, periods []int, degrade bool) []*float64 {
	if len(periods) != 4 {
		panic("refLongBBI expects 4 periods")
	}
	mas := make([][]*float64, 4)
	for i, p := range periods {
		mas[i] = refSMA(closes, p)
	}
	out := make([]*float64, len(closes))
	for i := range closes {
		sum := 0.0
		n := 0
		for j := range mas {
			if mas[j][i] == nil {
				continue
			}
			sum += *mas[j][i]
			n++
		}
		if n == 0 {
			continue
		}
		if n < 4 && !degrade {
			continue
		}
		v := round2(sum / float64(n))
		out[i] = &v
	}
	return out
}

// refMACD is DIF = EMA(c, short) - EMA(c, long), DEA = EMA(DIF, signal) seeded
// with DIF[0], HIST = (DIF - DEA) * 2. The *2 is a historical convention shared
// with DuckDB's momentum_macd_*_hist column.
func refMACD(closes []float64, shortP, longP, signalP int) (dif, dea, hist []*float64) {
	kShort := 2.0 / (float64(shortP) + 1)
	kLong := 2.0 / (float64(longP) + 1)
	kSig := 2.0 / (float64(signalP) + 1)
	emaShort := closes[0]
	emaLong := closes[0]
	deaV := 0.0
	n := len(closes)
	dif = make([]*float64, n)
	dea = make([]*float64, n)
	hist = make([]*float64, n)
	for i := range closes {
		c := closes[i]
		emaShort = c*kShort + emaShort*(1-kShort)
		emaLong = c*kLong + emaLong*(1-kLong)
		d := emaShort - emaLong
		if i == 0 {
			deaV = d
		} else {
			deaV = d*kSig + deaV*(1-kSig)
		}
		rd, re, rh := round2(d), round2(deaV), round2((d-deaV)*2)
		dif[i], dea[i], hist[i] = &rd, &re, &rh
	}
	return dif, dea, hist
}

// refKDJ is the spec 9,3,3: RSV over the n-bar high/low, K = ((kSmooth-1)K + RSV)/kSmooth
// seeded at 50, D likewise, J = kSmooth*K - (dSmooth-1)*D.
func refKDJ(bars []Bar, n, kSmooth, dSmooth int) (k, d, j []*float64) {
	m := len(bars)
	k = make([]*float64, m)
	d = make([]*float64, m)
	j = make([]*float64, m)
	kV, dV := 50.0, 50.0
	for i := 0; i < m; i++ {
		low, high := math.Inf(1), math.Inf(-1)
		start := i - n + 1
		if start < 0 {
			start = 0
		}
		for x := start; x <= i; x++ {
			low = math.Min(low, bars[x].Low)
			high = math.Max(high, bars[x].High)
		}
		rsv := 50.0
		if high != low {
			rsv = ((bars[i].Close - low) / (high - low)) * 100
		}
		kV = (float64(kSmooth-1)*kV + rsv) / float64(kSmooth)
		dV = (float64(dSmooth-1)*dV + kV) / float64(dSmooth)
		jV := float64(kSmooth)*kV - float64(dSmooth-1)*dV
		rk, rd, rj := round2(kV), round2(dV), round2(jV)
		k[i], d[i], j[i] = &rk, &rd, &rj
	}
	return k, d, j
}

// refPctRet is (close[i] - close[i-n]) / close[i-n] * 100, null for the first n bars.
// 名为 pct_ret 而非 rsl：它就是涨跌幅，不是相对强弱排名（见 Z_PCT_RET 的注释）。
func refPctRet(closes []float64, n int) []*float64 {
	out := make([]*float64, len(closes))
	for i := range closes {
		if i < n {
			continue
		}
		prev := closes[i-n]
		if prev <= 0 {
			continue
		}
		v := round2(((closes[i] - prev) / prev) * 100)
		out[i] = &v
	}
	return out
}

// refVolumeMA is the rolling mean of volume, null until the window is full.
func refVolumeMA(vols []float64, n int) []*float64 {
	out := make([]*float64, len(vols))
	sum := 0.0
	for i := range vols {
		sum += vols[i]
		if i >= n {
			sum -= vols[i-n]
		}
		if i >= n-1 {
			v := math.Round(sum / float64(n)) // .toFixed(0)
			out[i] = &v
		}
	}
	return out
}

// refZXBrick is the 同花顺知行砖型图 spec (stock-score.ts header, VAR1A..VAR6A):
//
//	VAR1A := (HHV(HIGH,4) - CLOSE) / (HHV(HIGH,4) - LLV(LOW,4)) * 100 - 90
//	VAR2A := SMA(VAR1A, 4, 1) + 100
//	VAR3A := (CLOSE - LLV(LOW,4)) / (HHV(HIGH,4) - LLV(LOW,4)) * 100
//	VAR4A := SMA(VAR3A, 6, 1)
//	VAR5A := SMA(VAR4A, 6, 1) + 100
//	VAR6A := VAR5A - VAR2A
//	砖型图 := IF(VAR6A > 4, VAR6A - 4, 0)
//
// where SMA(X,N,M) is the 通达信 form (M*X + (N-M)*Y')/N seeded with X[0].
// The divisor is floored at 0.0001 instead of the spec's raw denominator, which
// is the frontend's documented guard against a flat bar; it is reproduced here
// so the comparison stays about the formula, not about the guard.
func refZXBrick(bars []Bar, lookback int) []*float64 {
	m := len(bars)
	var1a := make([]float64, m)
	var3a := make([]float64, m)
	for i := 0; i < m; i++ {
		start := i - (lookback - 1)
		if start < 0 {
			start = 0
		}
		hhv, llv := math.Inf(-1), math.Inf(1)
		for x := start; x <= i; x++ {
			hhv = math.Max(hhv, bars[x].High)
			llv = math.Min(llv, bars[x].Low)
		}
		rng := math.Max(hhv-llv, 0.0001)
		var1a[i] = ((hhv-bars[i].Close)/rng)*100 - 90
		var3a[i] = ((bars[i].Close - llv) / rng) * 100
	}
	sma := func(vals []float64, n, mm int) []float64 {
		out := make([]float64, len(vals))
		var prev *float64
		for i, x := range vals {
			if prev == nil {
				v := x
				prev = &v
			} else {
				v := (float64(mm)*x + float64(n-mm)**prev) / float64(n)
				prev = &v
			}
			out[i] = *prev
		}
		return out
	}
	// VAR2A = SMA(VAR1A, 4, 1) + 100
	s1 := sma(var1a, 4, 1)
	for i := range s1 {
		s1[i] += 100
	}
	// VAR4A = SMA(VAR3A, 6, 1); VAR5A = SMA(VAR4A, 6, 1) + 100
	s4 := sma(var3a, 6, 1)
	s5 := sma(s4, 6, 1)
	for i := range s5 {
		s5[i] += 100
	}
	out := make([]*float64, m)
	for i := 0; i < m; i++ {
		v6 := s5[i] - s1[i]
		val := 0.0
		if v6 > 4 {
			val = v6 - 4
		}
		r := round2(val)
		out[i] = &r
	}
	return out
}

// readRepoFile reads a repo-relative file as bytes, failing the test when it is
// missing. Used to check the *other* stacks' source text without modifying it.
func readRepoFile(t *testing.T, root, rel string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatalf("读取 %s 失败: %v", rel, err)
	}
	return data
}

// ---------------------------------------------------------------------------
// The JS side
// ---------------------------------------------------------------------------

// series is a named column of values for one indicator.
type series struct {
	Key    string     `json:"key"`
	Values []*float64 `json:"values"`
}

// jsResult is what the oracle prints on stdout.
type jsResult struct {
	Indicators map[string][]series `json:"indicators"`
}

// runOracle executes frontend/conformance/indicator-oracle.ts with the fixture on
// stdin and returns its parsed results.
//
// Skips (rather than fails) when node/tsx or node_modules are unavailable: the
// Go test suite must stay runnable without a frontend install. The CI frontend
// job runs the same comparison from the TypeScript side, so the coverage is not
// lost — see frontend/conformance/indicator-oracle.ts.
func runOracle(t *testing.T, root string, bars []Bar) jsResult {
	t.Helper()
	frontend := filepath.Join(root, "frontend")
	if _, err := os.Stat(filepath.Join(frontend, "node_modules", "tsx")); err != nil {
		t.Skip("frontend/node_modules 缺失，跳过跨栈一致性检查（装完 npm install 再跑）")
	}
	if _, err := exec.LookPath("node"); err != nil {
		t.Skip("PATH 里没有 node，跳过跨栈一致性检查")
	}

	payload, err := json.Marshal(bars)
	if err != nil {
		t.Fatalf("序列化 fixture 失败: %v", err)
	}
	cmd := exec.Command("npx", "tsx", "--tsconfig", "tsconfig.app.json", "conformance/indicator-oracle.ts")
	cmd.Dir = frontend
	cmd.Stdin = bytes.NewReader(payload)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("oracle 执行失败: %v\nstderr:\n%s", err, stderr.String())
	}
	// The oracle may print warnings before the JSON payload; take the last
	// line that parses.
	var res jsResult
	for _, line := range bytes.Split(bytes.TrimSpace(stdout.Bytes()), []byte("\n")) {
		var probe jsResult
		if json.Unmarshal(line, &probe) == nil && probe.Indicators != nil {
			res = probe
		}
	}
	if res.Indicators == nil {
		t.Fatalf("oracle 没有输出可解析的 JSON（输出前 400 字节：%q）\nstderr:\n%s",
			truncate(stdout.String(), 400), stderr.String())
	}
	return res
}

// maDetail explains a one-cent BBI/LongBBI gap by printing both sides' moving
// averages at the offending bar. Without this the failure message just says
// "42.17 vs 42.16" and there is no way to tell a formula difference from a
// rounding tie-break.
func maDetail(js jsResult, id string, idx int) string {
	which := map[string]string{"Z_BBI": "__BBI_MA", "DG_YELLOW": "__LONG_BBI_MA"}[id]
	if which == "" {
		return ""
	}
	var parts []string
	for _, s := range js.Indicators[which] {
		if idx < len(s.Values) && s.Values[idx] != nil {
			parts = append(parts, fmt.Sprintf("js %s=%.2f", s.Key, *s.Values[idx]))
		}
	}
	if len(parts) == 0 {
		return ""
	}
	return "\n      JS 侧的均线：" + strings.Join(parts, " ")
}

// truncate shortens s for error messages; the oracle's payload is ~40 KB of
// numbers and printing all of it buries the actual problem.
func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// TestTieBreakingRoundingMatchesToFixed pins the two concrete disagreements
// that turned up while building this harness, so nobody "simplifies" round2
// back to math.Round(v*100)/100 and silently reintroduces them.
//
// Both cases are the four BBI moving averages summed and divided by 4. In both,
// JS is right and the obvious Go idioms are not:
//
//	166.5  / 4 = 41.625                 精确值落在 .5 上
//	  JS toFixed(2)            = 41.63  ECMA: 取较大的 n
//	  Go math.Round(v*100)/100 = 41.63  碰巧对
//	  Go strconv.FormatFloat   = 41.62  错：Go 用 banker's rounding
//
//	168.66 / 4 = 42.16499999999999914735  精确值严格小于 42.165
//	  JS toFixed(2)            = 42.16  按精确值取最近者
//	  Go math.Round(v*100)/100 = 42.17  错：v*100 把 double 又舍入到 .5
//	  Go strconv.FormatFloat   = 42.16  对
func TestTieBreakingRoundingMatchesToFixed(t *testing.T) {
	cases := []struct {
		name   string
		sum    float64
		want   float64
		naive  float64 // math.Round(v*100)/100
		format float64 // strconv 'f' 2 — kept for the report, not for the assert
	}{
		{
			name: "exact tie rounds up like toFixed",
			sum:  40.06 + 40.69 + 41.88 + 43.87, // 166.5
			want: 41.63,
		},
		{
			name: "just below a tie rounds down like toFixed",
			sum:  43.47 + 42.39 + 41.36 + 41.44, // 168.66
			want: 42.16,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			v := c.sum / 4
			if got := round2(v); got != c.want {
				t.Errorf("round2(%v/4) = %v, want %v（JS toFixed(2) 的结果）", c.sum, got, c.want)
			}
			format, _ := strconv.ParseFloat(strconv.FormatFloat(v, 'f', 2, 64), 64)
			t.Logf("%v/4 = %.20f；round2=%.2f，math.Round(v*100)/100=%.2f，FormatFloat=%.2f",
				c.sum, v, round2(v), math.Round(v*100)/100, format)
		})
	}
}

// TestBbiAndLongBBIDifferBeforeTwentyFourBars documents the one genuine
// semantic fork *inside* the frontend: the 牵牛绳 (Z_BBI) and the yellow line
// (DG_YELLOW) average the same four periods but disagree on the warm-up window,
// because calcLongBBI degrades to a partial average while calcBBI returns null.
//
// This is not something config/indicators.yaml can fix — both behaviours are
// load-bearing for their own charts — but it must stay visible, because
// "BBI" meaning two different things inside one product is exactly the class of
// bug this registry exists to surface.
func TestBbiAndLongBBIDifferBeforeTwentyFourBars(t *testing.T) {
	closes := make([]float64, 30)
	for i := range closes {
		closes[i] = float64(100 + i)
	}
	yellow := refLongBBI(closes, []int{3, 6, 12, 24}, true)
	rope := refLongBBI(closes, []int{3, 6, 12, 24}, false)
	firstYellow, firstRope := -1, -1
	for i := range yellow {
		if firstYellow < 0 && yellow[i] != nil {
			firstYellow = i
		}
		if firstRope < 0 && rope[i] != nil {
			firstRope = i
		}
	}
	// MA3 forms at index 2; MA24 only at index 23.
	if firstYellow != 2 {
		t.Errorf("黄线首个非空值在第 %d 根, want 2（MA3 一成形就开始降级平均）", firstYellow)
	}
	if firstRope != 23 {
		t.Errorf("牵牛绳首个非空值在第 %d 根, want 23（必须等齐四条均线）", firstRope)
	}
	t.Logf("黄线从第 %d 根开始有值，牵牛绳从第 %d 根开始有值 —— 同一组周期，两种预热语义", firstYellow, firstRope)
}

// ---------------------------------------------------------------------------
// The conformance test
// ---------------------------------------------------------------------------

// TestCrossStackConformance is the whole point of config/indicators.yaml: the
// same K-line input, computed by Go and by the frontend's own TypeScript, must
// agree within the declared tolerance.
func TestCrossStackConformance(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	tol := reg.Conformance.AbsTolerance
	bars := buildFixture(reg.Conformance.FixtureBars)
	closes := make([]float64, len(bars))
	vols := make([]float64, len(bars))
	for i, b := range bars {
		closes[i] = b.Close
		vols[i] = b.Volume
	}
	js := runOracle(t, root, bars)

	// params() reads the periods straight out of indicators.yaml — this is the
	// link that makes the YAML the source of truth for both sides.
	params := func(id string, name string) int {
		v, ok := reg.Get(id).Param(name)
		if !ok {
			t.Fatalf("%s 没有名为 %s 的参数（config/indicators.yaml）", id, name)
		}
		return v
	}
	seriesParams := func(id string, idx int) []int {
		s := reg.Get(id).Series
		if idx >= len(s) {
			t.Fatalf("%s 只有 %d 条 series，取不到第 %d 条", id, len(s), idx)
		}
		return s[idx].Params
	}

	macdDif, macdDea, macdHist := refMACD(closes,
		params("Z_MACD", "short"), params("Z_MACD", "long"), params("Z_MACD", "signal"))
	kdjK, kdjD, kdjJ := refKDJ(bars,
		params("Z_KDJ", "n"), params("Z_KDJ", "k_smooth"), params("Z_KDJ", "d_smooth"))

	want := map[string]map[string][]*float64{
		"ZG_WHITE":  {"zg_white": refDEMA(closes, seriesParams("ZG_WHITE", 0)[0])},
		"DG_YELLOW": {"dg_yellow": refLongBBI(closes, seriesParams("DG_YELLOW", 0), true)},
		"Z_BBI":     {"bbi": refLongBBI(closes, seriesParams("Z_BBI", 0), false)},
		"Z_VOL": {
			"ma5":  refVolumeMA(vols, seriesParams("Z_VOL", 1)[0]),
			"ma10": refVolumeMA(vols, seriesParams("Z_VOL", 2)[0]),
		},
		"Z_MACD": {"dif": macdDif, "dea": macdDea, "macd": macdHist},
		"Z_KDJ":  {"k": kdjK, "d": kdjD, "j": kdjJ},
		// Z_RSL → Z_PCT_RET（2026-10-01）。此前 id 叫 RSL 却画的是百分比
		// 涨跌幅，而 DuckDB 的 zettaranc_rsl_rank_* 才是真正的相对强弱排名
		// ——同名不同义。改名后 series key 也随之改为 pct_ret_short/long。
		"Z_PCT_RET": {
			"pct_ret_short": refPctRet(closes, params("Z_PCT_RET", "short")),
			"pct_ret_long":  refPctRet(closes, params("Z_PCT_RET", "long")),
		},
		"ZX_BRICK": {"brick": refZXBrick(bars, seriesParams("ZX_BRICK", 0)[0])},
	}

	for id, columns := range want {
		id := id
		t.Run(id, func(t *testing.T) {
			got, ok := js.Indicators[id]
			if !ok {
				t.Fatalf("oracle 没有返回 %s 的结果", id)
			}
			for key, goVals := range columns {
				var jsVals []*float64
				found := false
				for _, s := range got {
					if s.Key == key {
						jsVals, found = s.Values, true
						break
					}
				}
				if !found {
					t.Fatalf("%s: oracle 没有返回 series %q", id, key)
				}
				if len(jsVals) != len(goVals) {
					t.Fatalf("%s/%s: 长度不一致 go=%d js=%d", id, key, len(goVals), len(jsVals))
				}
				var worstIdx int
				var worst float64
				for i := range goVals {
					g, j := goVals[i], jsVals[i]
					switch {
					case g == nil && j == nil:
						continue
					case g == nil || j == nil:
						t.Fatalf("%s/%s[%d]: null 不一致 go=%v js=%v", id, key, i, g, j)
					}
					diff := math.Abs(*g - *j)
					if diff > worst {
						worst, worstIdx = diff, i
					}
				}
				if worst > tol {
					t.Errorf("%s/%s: Go 与 JS 最大偏差 %.4f > 容差 %.4f（第 %d 根：go=%.2f js=%.2f，fixture=%d 根）%s",
						id, key, worst, tol, worstIdx, *goVals[worstIdx], *jsVals[worstIdx], len(goVals),
						maDetail(js, id, worstIdx))
					continue
				}
				t.Logf("%s/%s: %d 根最大偏差 %.4f（容差 %.4f）", id, key, len(goVals), worst, tol)
			}
		})
	}
}

// TestZMainCompositeMatchesItsComponents guards the composite: Z_MAIN draws three
// independent lines and each must equal the standalone indicator of the same
// name. If Z_MAIN ever gets its own copy of a formula, this catches the fork.
func TestZMainCompositeMatchesItsComponents(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	bars := buildFixture(reg.Conformance.FixtureBars)
	closes := make([]float64, len(bars))
	for i, b := range bars {
		closes[i] = b.Close
	}
	js := runOracle(t, root, bars)

	main := js.Indicators["Z_MAIN"]
	component := func(id string) map[string][]*float64 {
		out := map[string][]*float64{}
		for _, s := range js.Indicators[id] {
			out[s.Key] = s.Values
		}
		return out
	}
	pairs := map[string]string{"zg_white": "ZG_WHITE", "dg_yellow": "DG_YELLOW", "bbi": "Z_BBI"}
	for _, s := range main {
		id, ok := pairs[s.Key]
		if !ok {
			t.Errorf("Z_MAIN 出现未知的线 %q（config/indicators.yaml 的 series 里没有）", s.Key)
			continue
		}
		solo := component(id)[s.Key]
		if solo == nil {
			t.Fatalf("%s 没有 series %q", id, s.Key)
		}
		if len(solo) != len(s.Values) {
			t.Fatalf("Z_MAIN/%s 长度 %d != %s 长度 %d", s.Key, len(s.Values), id, len(solo))
		}
		for i := range solo {
			a, b := s.Values[i], solo[i]
			if a == nil || b == nil {
				continue
			}
			if math.Abs(*a-*b) > reg.Conformance.AbsTolerance {
				t.Errorf("Z_MAIN/%s[%d]=%.2f 与独立指标 %s 的 %.2f 不一致", s.Key, i, *a, id, *b)
			}
		}
	}
}

// TestAliasIndicatorMatchesItsTarget pins Z_BRICK to ZX_BRICK. They are the same
// chart drawn under two ids; if they ever disagree the user sees two different
// "砖型图" depending on which id their saved config happens to contain.
func TestAliasIndicatorMatchesItsTarget(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	bars := buildFixture(reg.Conformance.FixtureBars)
	js := runOracle(t, root, bars)
	target := reg.Get("Z_BRICK").AliasOf
	if target == "" {
		t.Fatal("Z_BRICK 应该声明 alias_of: ZX_BRICK")
	}
	a, b := js.Indicators["Z_BRICK"], js.Indicators[target]
	if len(a) != len(b) {
		t.Fatalf("Z_BRICK 与 %s 的 series 数量不同", target)
	}
	for i := range a {
		if a[i].Key != b[i].Key {
			t.Fatalf("series %d 的 key 不同: %q vs %q", i, a[i].Key, b[i].Key)
		}
		for x := range a[i].Values {
			if math.Abs(*a[i].Values[x]-*b[i].Values[x]) > reg.Conformance.AbsTolerance {
				t.Errorf("Z_BRICK/%s[%d] = %.2f != %s 的 %.2f",
					a[i].Key, x, *a[i].Values[x], target, *b[i].Values[x])
			}
		}
	}
}

// TestFullPrecisionStackDivergesFromDisplayRounded documents — rather than
// asserts — the one real semantic fork between the stacks.
//
// The frontend rounds to 2 decimals at the *output* of each stage, and feeds
// that rounded value into the next stage (DEMA's second EMA, BBI's four moving
// averages). A stack that keeps full precision between stages therefore produces
// slightly different numbers for the same bars. This is not a bug in either
// side; it is a convention that was never written down. config/indicators.yaml's
// `precision` field now records it, and this test keeps the divergence visible
// so it is a decision rather than a surprise.
func TestFullPrecisionStackDivergesFromDisplayRounded(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	bars := buildFixture(reg.Conformance.FixtureBars)
	closes := make([]float64, len(bars))
	for i, b := range bars {
		closes[i] = b.Close
	}
	whiteSeries := reg.Get("ZG_WHITE").Series[0].Params[0]

	displayRounded := refDEMA(closes, whiteSeries) // feeds rounded inner EMA (the frontend)
	fullPrecision := refDEMAFullPrecision(closes, whiteSeries)

	var worst float64
	for i := range displayRounded {
		if displayRounded[i] == nil || fullPrecision[i] == nil {
			continue
		}
		worst = math.Max(worst, math.Abs(*displayRounded[i]-*fullPrecision[i]))
	}
	t.Logf("ZG_WHITE (DEMA %d): 全精度递归与「每级四舍五入」在 %d 根 fixture 上最大偏差 %.2f（容差 %.2f）",
		whiteSeries, len(closes), worst, reg.Conformance.AbsTolerance)
	switch {
	case worst == 0:
		t.Logf("两种约定在数值上完全等价 —— 改成全精度不会改变任何一根 K 线")
	case worst <= reg.Conformance.AbsTolerance/2:
		t.Logf("偏差小于半个显示单位：只是看起来有差异，不会改变任何一次金叉/死叉或评分档位")
	default:
		t.Logf("偏差已达一个显示单位：全精度栈与前端在 DEMA/BBI/多空线这类两级指标上会画出可见不同的线，" +
			"并且可能改变交叉的判定位置。谁对谁错取决于业务口径，本次不做裁决。")
	}
}

// refDEMAFullPrecision is the same DEMA with no intermediate rounding: the
// convention a DuckDB/TA-Lib style stack would use.
func refDEMAFullPrecision(closes []float64, n int) []*float64 {
	ema := func(vals []float64) []float64 {
		out := make([]float64, len(vals))
		k := 2.0 / (float64(n) + 1)
		prev := vals[0]
		for i, v := range vals {
			if i == 0 {
				prev = v
			} else {
				prev = v*k + prev*(1-k)
			}
			out[i] = prev
		}
		return out
	}
	inner := ema(closes)
	outer := ema(inner)
	out := make([]*float64, len(outer))
	for i := range outer {
		r := outer[i]
		out[i] = &r
	}
	return out
}

// TestDuckDBColumnContractHoldsInBothStacks is the Go/Python half of the
// conformance story: those two stacks don't compute indicators in process, they
// read precomputed DuckDB columns. So the contract to hold is the column name.
//
// For every column config/indicators.yaml declares, this asserts the exact
// spelling still appears in both stacks' SQL. A column renamed in one stack only
// is precisely the silent-drift failure this registry exists to prevent — and it
// fails here instead of returning zeros at runtime.
//
// Note: this reads the other stacks' source as *text*. It does not modify them.
//
// 2026-10-01: 这条测试曾断言"必须有声明的列"，那时 Z_MACD / Z_KDJ / Z_VOL
// 声明走 v_indicators_daily 的预计算列。那三条声明是**假的** —— 工作台从未
// 读过它们（/api/indicators 至今零调用方），已在同一天改成 backend: frontend。
// 于是 11 个工作台指标现在**全部**是 frontend 实现，本文件**一条** DuckDB 列
// 声明都没有了。
//
// 这不是"守卫失效"，而是它守的东西确实搬走了：工作台的 11 个图表指标本来就
// 全在浏览器算，indicators.yaml 的定位是"工作台图表指标"，不是"agent 侧指标
// 目录"。Go/Python 栈仍在读 100+ 个 DuckDB 列（data_loader.py 的
// INDICATOR_COLUMNS），但那些列由那边自己维护，不经本文件。
//
// 所以断言从"必须有列"改成"声明了列就必须拼写正确" —— 前者是让测试为一个
// 已经不存在的前提硬性失败，后者守的是仍然成立的那半条不变量。
func TestDuckDBColumnContractHoldsInBothStacks(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	cols := reg.DeclaredColumns()
	if len(cols) == 0 {
		// 不是失败。当前全部指标都是 frontend 实现（本函数上方有说明）。
		// 一旦有人重新声明 DuckDB 列，这个测试会立刻恢复它的逐列校验。
		t.Skip("indicators.yaml 当前没有声明任何 DuckDB 列（工作台 11 个指标均为 " +
			"frontend 实现）。若将来某个指标改回读列，本测试会自动重新生效。")
	}
	goSrc := readRepoFile(t, root, "internal/agent/tools/hithink_finance/analysis/data.go")
	pySrc := readRepoFile(t, root, "python-service/zettaranc/data_loader.py")

	for _, c := range cols {
		if !bytes.Contains(goSrc, []byte(c.Column)) {
			t.Errorf("Go 栈 data.go 里找不到列 %s（指标 %s 声明要用它）%s",
				c.Column, c.Indicator, gapNote(reg.GapFor(c.Indicator, "go")))
		}
		if !bytes.Contains(pySrc, []byte(c.Column)) {
			t.Errorf("Python 栈 data_loader.py 里找不到列 %s（指标 %s 声明要用它）%s",
				c.Column, c.Indicator, gapNote(reg.GapFor(c.Indicator, "python")))
		}
	}
}

// gapNote names the known_gaps entry behind a failure, so the message says
// "go_stack_missing_kdj" instead of just "column not found". The test still
// fails: recording a gap is not the same as closing it.
func gapNote(gap *KnownGap) string {
	if gap == nil {
		return " —— 改名或删列了？（config/indicators.yaml 的 known_gaps 里也没有登记）"
	}
	return fmt.Sprintf("\n      已知缺口 %s（config/indicators.yaml known_gaps）：%s\n      修法：%s",
		gap.ID, strings.TrimSpace(gap.Desc), strings.TrimSpace(gap.Resolution))
}

// TestKnownGapsAreStillTrue guards the known_gaps list in the other direction.
//
// gapNote 只在**列名对不上**时才被调用，而当前登记的三条缺口都不是列名问题：
// 前端重算已声明的 duckdb 指标、砖型图有两份未交叉验证的实现、四块砖只在
// 前端。结果是 known_gaps 变成一份纯文档 —— 缺口被修好后没有任何东西会提醒
// 人删掉它，于是清单只增不减，几条之后就不再可信（这正是本文件开头
// "写在这里只是为了说明这个失败是已知的" 想避免的状态）。
//
// 这里给每条缺口一个**可机械检查的断言**：条件一旦不再成立就报错，让人把条目
// 移走。仍成立则通过 —— 缺口本身由各自对应的测试负责报警。
// 本清单**现在是空的**（2026-10-01 全部关闭），而那正是本测试想要的状态：
// 清单只增不减是它的天敌，一旦缺口都修完却没有东西提示删条目，它就会退化成
// 一份没人核对的陈旧文档。所以空清单**通过**，并在说明里点出这一点 ——
// 让下一个读到这个空的人知道"这里是空的"是有意为之，而不是忘了填。
//
// 反向不变量仍然守着：有人登记了新缺口却没写断言分支，会在这里报错。
func TestKnownGapsAreStillTrue(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	if len(reg.KnownGaps) == 0 {
		t.Log("known_gaps 为空 —— 全部已知跨栈失真已关闭。" +
			"这是期望状态；若你刚修好一条缺口，记得把对应条目删掉。")
		return
	}

	frontendSrc := readRepoFile(t, root,
		"frontend/src/components/workspace/kline/indicators.ts")

	for _, g := range reg.KnownGaps {
		switch g.ID {
		case "frontend_recomputes_declared_duckdb_indicators":
			// 缺口成立的条件：前端仍然自己算 MACD/KDJ/VOL，即 indicators.ts
			// 里有 calcMACD/calcKDJ 的**函数声明**。**两者都不存在**才算修好 ——
			// 只改一个不构成"前端改读 DuckDB 列"，此时把条目删掉会让剩下的
			// 重算实现重新变成无人记录的缺口。
			//
			// 匹配的是带参数的完整声明，不是 "calcMACD" 这个裸词：后者是
			// calcMACDFROMDUCKDB 这类新名字的子串，会让缺口在真的修好之后
			// 仍然报"仍成立"，于是条目永远删不掉。
			const frontendCalcMACD = "export function calcMACD("
			const frontendCalcKDJ = "export function calcKDJ("
			hasMACD := bytes.Contains(frontendSrc, []byte(frontendCalcMACD))
			hasKDJ := bytes.Contains(frontendSrc, []byte(frontendCalcKDJ))
			if !hasMACD && !hasKDJ {
				t.Errorf("known_gaps[%s] 已不成立：indicators.ts 里既没有 %q 也没有 %q，"+
					"说明前端已改为读取 DuckDB 预计算列。请删掉这条 known_gaps 条目。",
					g.ID, frontendCalcMACD, frontendCalcKDJ)
			}


		default:
			t.Errorf("known_gaps 里有条目 %q，但本测试没有为它写断言 —— "+
				"新的缺口要么补上断言，要么别登记（无断言的条目等于纯文档）", g.ID)
		}
	}
}

// TestPeriodsMatchTheDuckDBColumnNames checks the numbers inside the column
// names themselves. `momentum_macd_12_26_9_macd` encodes (12,26,9); if
// indicators.yaml ever says MACD is 12,26,12 while DuckDB still says 9, the
// indicator means two different things depending on which stack answers.
func TestPeriodsMatchTheDuckDBColumnNames(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	cols := reg.DeclaredColumns()
	if len(cols) == 0 {
		// 与上面两条同因：2026-10-01 起本文件没有声明的 DuckDB 列，循环体
		// 一次都不执行。不加这道门的话它会**静默地空转通过** —— 一个跑
		// 零次断言却显示 PASS 的测试比没有更危险，它让人以为这条不变量
		// 还守着。
		t.Skip("当前没有声明的 DuckDB 列，列名里的周期无从比对")
	}
	for _, c := range cols {
		got := periodsInColumnName(c.Column)
		if len(got) == 0 {
			continue // e.g. volume_obv, statistics_zscore_20 has one
		}
		ind := reg.Get(c.Indicator)
		declared := ind.ParamValues()
		// The declared params must be a superset-compatible prefix: every
		// period encoded in the column name must appear, in order, in the
		// indicator's parameters.
		i := 0
		for _, p := range got {
			found := false
			for ; i < len(declared); i++ {
				if declared[i] == p {
					found = true
					i++
					break
				}
			}
			if !found {
				t.Errorf("列 %s 里编码的周期 %v 与 %s 声明的 %v 对不上",
					c.Column, got, ind.ID, declared)
			}
		}
	}
}

// periodsInColumnName pulls the numeric runs out of a DuckDB column name.
func periodsInColumnName(col string) []int {
	var out []int
	cur := -1
	for _, ch := range col {
		if ch >= '0' && ch <= '9' {
			if cur < 0 {
				cur = 0
			}
			cur = cur*10 + int(ch-'0')
			continue
		}
		if cur >= 0 {
			out = append(out, cur)
			cur = -1
		}
	}
	if cur >= 0 {
		out = append(out, cur)
	}
	return out
}
