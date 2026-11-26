package utils

import (
	"net/http"
	"regexp"
	"strings"

	"github.com/gin-gonic/gin"
)

// 响应侧净化：内部错误不该原样进响应体。
//
// 日志侧早就有 SanitizeForLog（防日志注入），响应侧一直没有对等的东西，于是
// 一句 `Error 1146 (42S02): Table 'weknora.kb_shares' doesn't exist` 或者
// `dial tcp 10.0.3.17:5432: connection refused` 会直接出现在客户端看到的
// JSON 里——它把表名、内网地址、驱动名一并交了出去。
//
// 这里只做一件事：**认出「这是内部痕迹」，认出来就换成一句通用文案**。
// 认不出来的（用户面向的文案：参数错误、租户不存在、密码策略不满足……）
// 原样保留——把「参数错误：thscode 不能为空」也藏起来，调用方与用户就都
// 拿不到任何可行动的信息了。
//
// 它是**按内容**判断，不是按 HTTP 状态码判断：同一个 400 既可能是「参数填错」
// （该给用户看），也可能是「查库时炸了」（不该给用户看）。按状态码一刀切的
// 后果是前者被误伤——而误伤比漏报更难发现，因为文案本身读起来完全正常。

// internalErrorPatterns 是「内部痕迹」的特征。
//
// 挑的是**不会出现在用户面向文案里**的那些：SQL 引擎的错误形态、连接错误、
// 绝对路径、Go 运行时的 panic 文本、栈帧。用户文案是中文短句，撞不上这些。
var internalErrorPatterns = []*regexp.Regexp{
	// SQL 引擎的错误：带 SQLSTATE、驱动前缀，或直接点名表/列
	regexp.MustCompile(`(?i)\bSQLSTATE\b`),
	regexp.MustCompile(`(?i)\bError \d{3,5} \(`), // MySQL: Error 1062 (23000)
	regexp.MustCompile(`(?i)\bpq: `),             // lib/pq
	regexp.MustCompile(`(?i)\bsqlite3?:`),        // modernc / mattn
	regexp.MustCompile(`(?i)\bORA-\d{5}\b`),      // Oracle
	regexp.MustCompile(`(?i)\bno such table\b`),
	regexp.MustCompile(`(?i)\bno such column\b`),
	regexp.MustCompile(`(?i)\bduplicate entry\b`),
	regexp.MustCompile(`(?i)\bviolates (foreign key|unique|not null|check) constraint\b`),
	regexp.MustCompile(`(?i)\bUNIQUE constraint failed\b`),
	regexp.MustCompile(`(?i)\bsyntax error at or near\b`),
	regexp.MustCompile(`(?i)\brelation "[^"]+" does not exist\b`),
	regexp.MustCompile(`(?i)\bcolumn "[^"]+" does not exist\b`),

	// 网络与连接
	regexp.MustCompile(`(?i)\bdial tcp\b`),
	regexp.MustCompile(`(?i)\bconnection refused\b`),
	regexp.MustCompile(`(?i)\bno such host\b`),
	regexp.MustCompile(`(?i)\bi/o timeout\b`),
	regexp.MustCompile(`(?i)\bTLS handshake\b`),
	regexp.MustCompile(`(?i)\bconnection reset by peer\b`),
	regexp.MustCompile(`(?i)\bEOF\b`),

	// 文件系统路径与栈帧
	regexp.MustCompile(`(?m)(^|[\s"'=(])/(usr|home|Users|var|etc|opt|app|tmp|root|data)/`),
	regexp.MustCompile(`(?m)(^|[\s"'=(])[A-Za-z]:\\`),
	regexp.MustCompile(`(?m)^\s*[\w./\\-]+\.go:\d+`),

	// Go 运行时
	regexp.MustCompile(`(?i)\binterface conversion:`),
	regexp.MustCompile(`(?i)\bnil pointer dereference\b`),
	regexp.MustCompile(`(?i)\bruntime error:`),
	regexp.MustCompile(`(?i)\bpanic: `),
	regexp.MustCompile(`(?i)\bgoroutine \d+ \[`),
}

// Client 侧的通用文案。命中内部痕迹时按状态码给其中一句。
const (
	// ClientInternalErrorMessage 用于 5xx：问题在服务端。
	ClientInternalErrorMessage = "服务器内部错误，请稍后重试"
	// ClientRequestErrorMessage 用于 4xx：请求处理不了，但原因不该外泄。
	ClientRequestErrorMessage = "请求无法处理"
)

// HasInternalTrace 判断一段文本里有没有内部痕迹。
//
// 单独导出是因为它也是**守卫**用的：想知道「这段文案能不能下发」时，调用方
// 需要自己判断，而不是只能靠 SanitizeForClient 的替换结果反推。
func HasInternalTrace(msg string) bool {
	if msg == "" {
		return false
	}
	for _, re := range internalErrorPatterns {
		if re.MatchString(msg) {
			return true
		}
	}
	return false
}

// SanitizeForClient 把内部错误转成可以下发给客户端的文案。
//
// status 只决定「命中内部痕迹时给哪句通用文案」，不参与判断要不要净化。
func SanitizeForClient(status int, err error) string {
	if err == nil {
		return ""
	}
	return SanitizeForClientString(status, err.Error())
}

// SanitizeForClientString 是 SanitizeForClient 的字符串版本。
//
// 命中内部痕迹 → 换成通用文案；没命中 → 清掉控制字符后原样返回。
func SanitizeForClientString(status int, msg string) string {
	if msg == "" {
		return ""
	}
	if HasInternalTrace(msg) {
		if status >= http.StatusInternalServerError {
			return ClientInternalErrorMessage
		}
		return ClientRequestErrorMessage
	}
	return StripControlChars(msg)
}

// StripControlChars 把换行、制表符与其它控制字符清成空格。
//
// 控制字符在响应体里没有意义，却能让日志与前端显示被撑开——所以响应侧也要
// 清一遍。它与 SanitizeForLog 是同一件事，只是去处不同。
func StripControlChars(input string) string {
	if input == "" {
		return ""
	}
	sanitized := strings.ReplaceAll(input, "\n", " ")
	sanitized = strings.ReplaceAll(sanitized, "\r", " ")
	sanitized = strings.ReplaceAll(sanitized, "\t", " ")

	var builder strings.Builder
	for _, r := range sanitized {
		if r >= 32 || r == ' ' {
			builder.WriteRune(r)
		}
	}
	return builder.String()
}

// WriteError 写一条错误响应。
//
// 文案经 SanitizeForClient 净化：内部错误（SQL 报错、连接失败、绝对路径）
// 不下发。形状固定为 {"error": "..."}，与净化前一致——客户端读法不变。
//
// 它存在的理由是**让形状只有一处**：110 处调用点各写各的 `gin.H{"error": ...}`，
// 于是总有人写成 `gin.H{"msg": ...}`，总有人忘了净化。收到一处之后，
// 形状与净化就再也分不开了。
func WriteError(c *gin.Context, status int, err error) {
	c.JSON(status, gin.H{"error": SanitizeForClient(status, err)})
}
