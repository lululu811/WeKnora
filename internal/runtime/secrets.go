package runtime

import (
	"context"
	"fmt"
	"os"
	"strings"

	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/utils"
)

// 启动期密钥校验。
//
// JWT_SECRET 有两个坑，一个比另一个严重：
//
//  1. **公开示例值被当成真密钥**。配置生成器写进 .env 的是
//     "change-me-in-production"，而 getJwtSecret 的占位符名单里没有它——
//     它被拿去真的签 token 了。而这个值写在仓库里，于是**任何知道它的人都能
//     伪造任意用户的 token，包括管理员**。
//
//  2. **未配置时每进程随机**。单副本是"每次重启所有人被登出"；多副本是
//     "A 签的 token 在 B 验签失败"——用户表现为随机掉线，而且没有一条日志
//     指向它。
//
// 两个坑的修法不同。第 1 个是**明确的错误配置**：用的是公开值，等于没有
// 密钥，必须在启动时拒绝。第 2 个在开发场景是合法的（本地反复重启不必每次
// 登录），所以留了一个显式的开关；生产部署不打开它，服务就起不来。

// AllowEphemeralJWTSecretEnv 是"允许临时密钥"的开关名。
//
// 它的用途只有一个：让**开发**能不起一个真密钥就跑起来（本地反复重启时
// 登录态也不必每次失效）。生产部署不要设它——那时服务会拒绝启动，逼着
// 部署者真的配一个。
//
// 值是 "true" / "1" 才生效；设成别的值等同于没设。
const AllowEphemeralJWTSecretEnv = "WEKNORA_ALLOW_EPHEMERAL_JWT_SECRET"

// ValidateStartupSecrets 校验启动必须的密钥。
//
// 返回错误而不是自己退出：调用方决定怎么处置（cmd/server 直接退出；
// cmd/desktop 在这之前已经持久化了自己的密钥，所以这里只会通过）。
//
// 两种拒绝：
//
//   - **公开示例值** —— 无条件拒绝。这是明确的错误配置：公开值等于没有密钥，
//     任何知道它的人都能伪造管理员的 token。
//   - **未配置** —— 默认拒绝，除非显式打开 AllowEphemeralJWTSecretEnv。
//     未配的后果是每次重启全员被登出、多副本部署则随机掉线。
func ValidateStartupSecrets() error {
	secret := strings.TrimSpace(os.Getenv("JWT_SECRET"))

	switch {
	case utils.IsPlaceholderSecret(secret):
		return fmt.Errorf(
			"JWT_SECRET 仍是公开示例值（%q）。\n"+
				"  这是**已知值**，不是密钥：任何人拿到它都能伪造任意用户的 token，\n"+
				"  包括管理员。它出现在仓库里、出现在每一个由配置生成器产出的 .env 里。\n"+
				"  请生成一个随机值并写进环境变量：openssl rand -base64 32", secret)
	case secret == "":
		if ephemeralJWTSecretAllowed() {
			logger.Warnf(context.Background(),
				"[startup] JWT_SECRET 未设置，且 %s=true —— 本次启动使用临时随机密钥。\n"+
					"  后果：每次重启，所有已登录用户的 token 全部失效；多副本部署时各副本\n"+
					"  密钥互不相同，token 在副本间不通用（用户表现为「随机掉线」）。\n"+
					"  生产部署请去掉该开关并设置 JWT_SECRET。",
				AllowEphemeralJWTSecretEnv)
			return nil
		}
		return fmt.Errorf(
			"JWT_SECRET 未设置。\n"+
				"  未设置时本进程会临时生成一个随机密钥，后果是：\n"+
				"    - 每次重启，所有已登录用户的 token 全部失效（全员被登出）；\n"+
				"    - 多副本部署时，各副本的密钥互不相同，A 签的 token 在 B 上验签失败，\n"+
				"      用户表现为「随机掉线」，且没有任何日志指向它。\n"+
				"  请生成一个随机值并写进环境变量：openssl rand -base64 32\n"+
				"  （本地开发可以设 %s=true 临时跳过——生产不要设）",
			AllowEphemeralJWTSecretEnv)
	}

	return nil
}

// ephemeralJWTSecretAllowed 判断"允许临时密钥"的开关有没有打开。
//
// 大小写不敏感、首尾空白不算——环境变量的写法五花八门，"TRUE" 与
// " true " 都该被当成打开。只有 true 与 1 算数；yes / on / off 一律当成没设
// （开关要么明确打开，要么没打开，"大概是吧"不算）。
func ephemeralJWTSecretAllowed() bool {
	v := strings.TrimSpace(os.Getenv(AllowEphemeralJWTSecretEnv))
	return strings.EqualFold(v, "true") || v == "1"
}
