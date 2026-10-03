package handler

import (
	"strings"
	"testing"
	"unicode/utf8"
)

// normaliseReason 是「模型输出 → 可入库备注」的唯一闸门。它出错的方式很
// 安静：写坏了不会报错，只会让自选池里静静躺着一段废话或半个标签——这正是
// 入池备注一直不好用的原因之一。下面每条断言都对着模型真实会犯的毛病。
func TestNormaliseReason(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "裸句子原样保留",
			in:   "地产政策预期未兑现，回调不破年线就是布局位。",
			want: "地产政策预期未兑现，回调不破年线就是布局位。",
		},
		{
			name: "剥掉 markdown 包裹",
			in:   "**政策预期未兑现**，年线不破可布局。",
			want: "政策预期未兑现，年线不破可布局。",
		},
		{
			name: "剥掉列表符号与理由标签",
			in:   "- 理由：政策预期未兑现，年线不破可布局。",
			want: "政策预期未兑现，年线不破可布局。",
		},
		{
			name: "剥掉模型爱加的引号",
			in:   "“政策预期未兑现，年线不破可布局。”",
			want: "政策预期未兑现，年线不破可布局。",
		},
		{
			// 提示词要求一句话，但段落式输出很常见。只留第一句。
			name: "多句只留第一句",
			in:   "政策预期未兑现，年线不破可布局。\n另外成交量在放大。",
			want: "政策预期未兑现，年线不破可布局。",
		},
		{
			// 「无」是提示词里约定的放弃信号。存成 "无" 看着像有理由，
			// 实际上等于没写。
			name: "放弃信号归一为空",
			in:   "无",
			want: "",
		},
		{
			name: "放弃信号带句号也算空",
			in:   "无。",
			want: "",
		},
		{
			name: "空输入返回空",
			in:   "   \n ",
			want: "",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := normaliseReason(tc.in); got != tc.want {
				t.Fatalf("normaliseReason(%q)\n  got  %q\n  want %q", tc.in, got, tc.want)
			}
		})
	}
}

func TestNormaliseReasonFitsColumn(t *testing.T) {
	// stock_watches.note 是 varchar(200)。模型无视长度要求时，必须由这里
	// 截断到列宽内 —— 否则会像 anchor 标签那次一样，从标签中间被切开。
	long := strings.Repeat("政策预期未兑现年线不破可布局", 40)
	got := normaliseReason(long)

	if n := utf8.RuneCountInString(got); n > reasonOutputBudget {
		t.Fatalf("超出列宽 %d：实际 %d", reasonOutputBudget, n)
	}
	if got == "" {
		t.Fatal("截断后不该变成空串")
	}
	// 截在句读上，不截半个词。
	if strings.HasSuffix(got, "，") || strings.HasSuffix(got, "、") {
		t.Fatalf("不该以标点收尾：%q", got)
	}
}

func TestNormaliseReasonKeepsEmojiAndTagsHarmless(t *testing.T) {
	// 图表锚点标签曾经原样进过备注。这次模型再吐出来也必须被剥掉 ——
	// 提示词没禁它，禁了也不保证模型照做。
	in := `当前收盘价<anchor kind="level" value="31.81" label="当前价31.81"/>，年线不破。`
	got := normaliseReason(in)
	if strings.Contains(got, "<anchor") {
		t.Fatalf("锚点标签泄漏进备注：%q", got)
	}
}
