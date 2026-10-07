package indicators

// 触发定义的校验守卫。
//
// 为什么这组测试存在：triggers: 里的 left/right 指向的是 storage 的列别名。
// 如果有人改了一个指标的 storage.columns 却没改触发，评估管线会去读一个
// **不存在的键**——而那不会报错，只会让该触发在全市场**一次都不出现**。
// "这个信号没有预测力"和"这个信号没被找到"在报告里长得一模一样，而这正是
// 评估结论最不该搞错的地方。所以必须在加载期就炸掉。
//
// 做法是端到端的：把仓库里真实的 indicators.yaml 复制到临时目录，只改
// triggers 那一段，再走 Load()。不构造"最小 registry"，免得测的是一个
// 现实中不存在的配置形状。

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writeVariantWithTriggers copies the real indicators.yaml into a temp dir,
// applies `mutate` to it, and returns the config dir ready for Load().
func writeVariantWithTriggers(t *testing.T, mutate func(string) string) string {
	t.Helper()
	root := FindRepoRoot(".")
	if root == "" {
		t.Fatalf("定位不到 config/%s", FileName)
	}
	src, err := os.ReadFile(filepath.Join(root, "config", FileName))
	if err != nil {
		t.Fatalf("读不到真实 %s: %v", FileName, err)
	}
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, FileName),
		[]byte(mutate(string(src))), 0o644); err != nil {
		t.Fatalf("写临时配置失败: %v", err)
	}
	return dir
}

func TestTriggerValidation(t *testing.T) {
	cases := []struct {
		name    string
		mutate  func(string) string
		wantErr string // 子串；空表示期望加载成功
	}{
		{
			name:    "原样可以通过",
			mutate:  func(s string) string { return s },
			wantErr: "",
		},
		{
			name: "left 指向不存在的别名",
			mutate: func(s string) string {
				return strings.Replace(s, "left: ztr_white", "left: ztr_nonexistent", 1)
			},
			wantErr: "not a declared storage column alias",
		},
		{
			name: "right 指向不存在的别名",
			mutate: func(s string) string {
				return strings.Replace(s, "right: ztr_yellow", "right: ztr_typo", 1)
			},
			wantErr: "not a declared storage column alias",
		},
		{
			name: "报错信息要列出当前合法别名",
			mutate: func(s string) string {
				return strings.Replace(s, "left: ztr_white", "left: ztr_nope", 1)
			},
			wantErr: "ztr_white", // 别名清单里应当看得到真正合法的那些
		},
		{
			name: "op 写错",
			mutate: func(s string) string {
				return strings.Replace(s, "op: cross_above", "op: cross_over", 1)
			},
			wantErr: "not in {cross_above, cross_below}",
		},
		{
			name: "kind 写错",
			mutate: func(s string) string {
				return strings.Replace(s, "kind: cross", "kind: crossover", 1)
			},
			wantErr: "not in {cross}",
		},
		{
			name: "id 重复",
			mutate: func(s string) string {
				return strings.Replace(s, "id: WHITE_CROSS_DOWN", "id: WHITE_CROSS_UP", 1)
			},
			wantErr: "duplicate id",
		},
		{
			name: "左右两侧相同",
			mutate: func(s string) string {
				return strings.Replace(s, "right: ztr_yellow", "right: ztr_white", 1)
			},
			wantErr: "left and right must differ",
		},
		{
			name:    "某侧留空",
			mutate:  func(s string) string { return strings.Replace(s, "left: ztr_white", `left: ""`, 1) },
			wantErr: "left side is empty",
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			dir := writeVariantWithTriggers(t, c.mutate)
			_, err := Load(dir)
			if c.wantErr == "" {
				if err != nil {
					t.Fatalf("期望加载成功，却报错: %v", err)
				}
				return
			}
			if err == nil {
				t.Fatalf("期望因 %q 失败，却加载成功了 —— 这条校验没生效", c.wantErr)
			}
			if !strings.Contains(err.Error(), c.wantErr) {
				t.Fatalf("报错信息不含 %q，实际: %v", c.wantErr, err)
			}
		})
	}
}

// TestTriggersSurviveTheGeneratedPythonModule 是端到端的另一半：yaml 里的
// 触发必须真的出现在 python-service/zettaranc/indicator_meta.py 里。
//
// 只有 Go loader 校验是不够的 —— 评估脚本读的是生成产物。如果 genmeta 忘了
// 把 triggers 发射过去，Go 侧全绿而 Python 侧拿到空列表，评估会报"该触发
// 从未出现"，也就是把 bug 读成一个真实的负面结论。
func TestTriggersSurviveTheGeneratedPythonModule(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	if len(reg.Triggers) == 0 {
		t.Fatal("yaml 里一条触发都没有 —— 要么被删空了，要么没加载进来")
	}
	py := string(readRepoFile(t, root,
		"python-service/zettaranc/indicator_meta.py"))
	if !strings.Contains(py, `"triggers"`) {
		t.Fatal("生成的 indicator_meta.py 里没有 triggers 字段，" +
			"运行 go test ./internal/indicators/ -run TestGenerated -update 重新生成")
	}
	for _, tr := range reg.Triggers {
		if !strings.Contains(py, `"`+tr.ID+`"`) {
			t.Errorf("触发 %s 没有出现在生成的 indicator_meta.py 里", tr.ID)
		}
		for _, alias := range []string{tr.Left, tr.Right} {
			if alias == "" {
				continue
			}
			if tr.Compute == TriggerComputeFrontend {
				// compute=frontend 的 left/right 是 "<公式>.<字段>"，由 Go 侧
				// FrontendFormulaFields 白名单校验；这里只确认公式名也到了
				// Python 那边，字段名在 frontend_formulas.FORMULAS 里。
				formula, field, ok := splitComputedRef(alias)
				if !ok {
					t.Errorf("触发 %s 的 %q 不是 <公式>.<字段> 形式", tr.ID, alias)
					continue
				}
				if !strings.Contains(py, `"`+formula+`.`+field+`"`) {
					t.Errorf("触发 %s 用的 %q 没有出现在生成的 indicator_meta.py 里",
						tr.ID, alias)
				}
				continue
			}
			if !strings.Contains(py, alias) {
				t.Errorf("触发 %s 用的别名 %s 不在生成产物里；别名必须来自 "+
					"storage.columns，不能手写", tr.ID, alias)
			}
		}
	}
}

// TestFrontendFormulaMirrorMatchesPython 守住 Go 的白名单与 Python 的
// FORMULAS 表**逐条一致**。
//
// 两边各有一份（Go 侧为了在加载期就报错，Python 侧是真正干活的地方）。
// 没有这道守卫的话，往 Python 加了新公式却忘了同步 Go，config 里引用它就会
// 在加载期被拒——而报错信息会让人以为是 yaml 写错了。
func TestFrontendFormulaMirrorMatchesPython(t *testing.T) {
	_, root := loadRepoRegistry(t)
	py := string(readRepoFile(t, root,
		"python-service/zettaranc/frontend_formulas.py"))
	for formula, fields := range FrontendFormulaFields {
		if !strings.Contains(py, `"`+formula+`"`) {
			t.Errorf("Go 白名单里的公式 %q 在 frontend_formulas.py 的 FORMULAS 里不存在",
				formula)
			continue
		}
		for _, f := range fields {
			if !strings.Contains(py, `"`+f+`"`) {
				t.Errorf("公式 %q 的字段 %q 在 Python 侧找不到", formula, f)
			}
		}
	}
}

// TestTriggerDecisionTimeHasNoLookahead 把 R4 锁定的口径写成断言：触发只能用
// 当前 bar 与前一根 bar 判定。eval_trigger_power.py 的 SQL 是按 cross_above /
// cross_below 两种形状生成的，这里守住"不要新增需要未来信息的 op"。
func TestTriggerDecisionTimeHasNoLookahead(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	// 目前只支持这两种，且都由「前一根 vs 当根」实现。任何新增 op 都必须
	// 先在这里补一条说明它怎么避免前视，否则就是悄悄放宽了不变量。
	if ValidTriggerOp("cross_above") != true || ValidTriggerOp("cross_below") != true {
		t.Fatal("两个基础 op 必须始终合法")
	}
	for _, bad := range []string{"", "cross", "crossover", "cross_above_now", "CROSS_ABOVE"} {
		if ValidTriggerOp(bad) {
			t.Errorf("%q 不该被当成合法 op", bad)
		}
	}
	for _, tr := range reg.Triggers {
		if tr.Kind != TriggerCross {
			t.Errorf("%s: kind %q 只支持 %q", tr.ID, tr.Kind, TriggerCross)
		}
		if !ValidTriggerOp(tr.Op) {
			t.Errorf("%s: op %q 非法", tr.ID, tr.Op)
		}
	}
}
