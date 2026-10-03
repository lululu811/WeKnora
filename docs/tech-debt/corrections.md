# 主进程复核：与 subagent 结论不一致处

## 1. tos/minio "30s 整请求超时截断大文件上传" —— 结论反向
- subagent (GoServiceLayer) 称 tos.go/minio.go 走 `DefaultSSRFSafeHTTPClientConfig()` 因 Timeout=30s 导致上传被截断。
- 实测：`config.Timeout` 只在 `internal/utils/security.go:805` 的 `NewSSRFSafeHTTPClientWithTransport` 里被消费为 `http.Client.Timeout`。
- tos (`tos.go:104-108`) 与 minio (`minio.go:36-40`) 传的是 `RoundTripper`（`SSRFValidatingRoundTripper{Base: NewSSRFSafeTransport(cfg)}`），**根本没走 http.Client**，因此 Timeout 字段被丢弃。
- 正确结论：tos/minio 是 **完全无超时**（既无 30s 截断，也无 s3 系那层 30min 兜底）。证据：transferCtx 计数 s3/oss/obs/ks3/cos=5，tos=0，minio=0；TLSHandshakeTimeout/ResponseHeaderTimeout 全部 7 个文件都是 0（都收敛在 object_storage_http.go 的 objectStorageTransport() 里，tos/minio 未调用）。
- 实际影响应改写为：对象存储端点 accept 后静默时，tos/minio 的 asynq worker / handler goroutine 可被无限期 pin 住（无 deadline、无 header 等待上限），而 s3/oss/obs/ks3/cos 会在 30min 内失败。修复方案（改用 objectStorageHTTPClientConfig/objectStorageTransport/objectStorageSetupContext/objectStorageTransferContext）不变且正确。

## 2. i18n "186 个 key 缺失" —— 数字未复现，方向性结论仍成立
- Frontend agent 称 `usage.staticKeys=5744`，其中 186 个带点号的 key 在语言包里不存在（kline 47 / stockCitation 26 / watchlist 25 ...）。
- 主进程独立复现（node 扫描 `src/**/*.vue|ts` 的 `t('a.b')` 静态 key，对比 en-US.ts 顶层命名空间）：5415 个静态 key，仅 4 个顶层命名空间不存在（klineCompare × 4）。
- 逐例核实关键结论成立：`kline.volumeLabel`（KLineWorkspace.vue:98）与 `kline.requestRejectedHint`（KLineWorkspace.vue:406）确实不在 en-US.ts 中；`npm run check-i18n` 13/13 全绿。
- 修订：精确数字待定（"186" 未复现，可能把子路径也计入了），但**门禁方向反了**这个结论成立 —— `localeKeyAudit.ts:388` 先遍历语言包已有的 key 再筛引用，代码写了但语言包没有的 key 进不了待检集。
