package utils

import "strings"

// 公开示例值清单。
//
// 密钥的"占位符"曾经有三份各写各的清单：getJwtSecret 认两个、桌面壳认两个、
// 而配置生成器写出来的是另外两个。于是生成器写的 "change-me-in-production"
// **不在** getJwtSecret 的名单里——它被拿去真的签 token 了。而这个值写在
// 仓库里、写在每一个由生成器产出的 .env 里：任何知道它的人都能伪造任意用户
// 的 token，包括管理员。
//
// 清单只有一份，且**任何一处碰到密钥的地方都要过它**：配置生成器写出来的、
// 文档示例里的、桌面壳兜底用的、开发脚本注入的，一律进这里。

// PlaceholderSecrets 是"看起来配了、其实是公开值"的密钥清单。
//
// 判定用大小写不敏感的相等——`.env` 里写成 `CHANGE-ME-in-production` 与
// `change-me-in-production` 是同一个意思。
var PlaceholderSecrets = []string{
	// 历史名单：getJwtSecret 与桌面壳认的那两个
	"weknora-jwt-secret",
	"CHANGE-ME-jwt-secret",
	// 配置生成器写进 .env 的那批（cmd/config）
	"change-me-in-production",
	"change-me-in-production-32b",
	"change-me-32-bytes-long!!",
	"change-me",
	// 常见的其它写法
	"change-me-jwt-secret",
	"changeme",
	"your-secret-key",
}

// IsPlaceholderSecret 判断一个密钥值是不是公开示例值。
//
// 比较前先去掉首尾空白——`.env` 里写成 `JWT_SECRET= change-me ` 这种带空格
// 的样子很常见，而它进到 os.Getenv 里通常还带着。
//
// 空串**不算**占位符：空是"没配"，与"配了个公开值"是两件事，处置也不同
// （前者在启动期被拒绝，后者同样被拒绝，但话术要分开说）。
func IsPlaceholderSecret(value string) bool {
	v := strings.TrimSpace(value)
	if v == "" {
		return false
	}
	for _, p := range PlaceholderSecrets {
		if strings.EqualFold(v, p) {
			return true
		}
	}
	return false
}
