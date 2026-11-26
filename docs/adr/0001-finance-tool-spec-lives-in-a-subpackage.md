---
status: accepted
---

# 金融工具的声明放在子包里，不放在包根

`internal/agent/tools/hithink_finance/spec/` 是子包，不是 `hithink_finance` 包根下的
一个文件。

原因有两个扫描器：`schema_contract_test.go` 用 `os.ReadDir(".")` 之后**只下钻子目录**，
`discover.go` 用 `//go:embed */*.go` 也**只覆盖一层**。声明放包根，这两个扫描器会
同时瞎掉 —— 而它们是这批工具唯一还在生效的防线（SQL 引用的表列对不上快照会红、
「哪一列真的被消费」是健康检查的一部分）。

代价是 spec 包要 import 上层的 `hithink_finance` 拿 `QueryDuckDBParams` 等共享机制，
所以依赖方向只能是 spec → hithink_finance 一条；反过来不行（`discover.go` 与
`dead_columns.go` 在包根，不能再 import spec）。

**后果**：以后再往这批工具里加声明，声明必须放在 `spec/` 子包里。放包根不会编译
报错，但两个扫描器会静默失效 —— 所以 `spec` 包的契约测试要一直守着这条。
