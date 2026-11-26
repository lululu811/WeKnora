---
status: accepted
---

# 发现工具单独一个包 —— `discover/` 不再住在包根

`hithink.finance.discover` 从 `hithink_finance` 包根搬进 `hithink_finance/discover/`。

## 为什么

它要有**声明**（名字、说明、参数），而声明住在 `spec` 包。于是出现了依赖环：

```
spec ──依赖──▶ hithink_finance（包根）   // 执行器要用 Config 与查询函数
包根  ──依赖──▶ spec                     // discover 的声明
```

环在这里不是"风格问题"，是编译不过。三条路：

1. **拆包根**：把包根里的运行时（`common.go` + `friendly_error.go`）抽成一个叶子包，
   让 `spec` 依赖它，包根就能反过来依赖 `spec`。代价是包根变成一层转发
   （类型别名 + 函数包装），而它原本是"共享机制"这一层的所在地。
2. **把声明留在包根**：discover 不进 `spec`。那它就没有声明——窗口、参数、说明
   各写各的，与这次深化要消灭的正是同一件事。
3. **discover 单独一个包**：它依赖包根（配置、查询、"哪些列被现成工具消费"那道
   判定）与 `spec` 包（声明），谁也不依赖谁。

选 3。

## 为什么不是 1

方案 1 要动的是**包根**——那是 42 个已迁工具与整个仓库都在用的共享层（`Config`、
`QueryDuckDBParams`、`CheckSyncWindow`、`FriendlyQueryError`）。把它拆成"叶子包 +
转发层"，换来的是 discover 不用搬。

方案 3 要动的是**discover 自己**（它本来就只有这一个工具）与一次注册 switch 的
改名。共享层一点没动。

## 那道判定为什么必须留在包根

"哪些列被现成工具消费"（`ConsumedIndicatorColumns`）靠 `//go:embed */*.go` 读各工具
子包的源码。`go:embed` 的模式不能引用父目录，所以**只有住在包根的那个包能读到
兄弟目录**。这道判定因此留在包根，由 `discover` 包调用。

## 代价

- `agent_service.go` 里 48 个 case 有一个改了：`hithink_finance.NewDiscoverToolWithConfig`
  → `discover.NewToolWithConfig`。其余 47 个一行没动。
- `discover_columns_test.go` 拆成两份：工具行为那部分搬进 `discover/`，判定那部分
  （`indicatorColumnsInSource` 与"embed 覆盖到各子包"）留在包根。
- 冻结快照里 `discover` 的两份探针都是 `kind="columns"`：`kind="tools"` 的输出取决于
  注册表里注册了什么，不是这个工具自己的行为，所以它不进快照，由 `discover` 包
  自己的测试守着。
