package service

import (
	"strings"
	"sync"
	"testing"
)

// jwt_secret_test.go 守住"公开示例值不会被拿去签 token"。
//
// 这一层是上一层的最后一道：utils.PlaceholderSecrets 与 runtime 的启动期
// 校验都可能在某次重构里被绕开，而这里守的是**签 token 的那一刻**——
// getJwtSecret 拿到的到底是什么。
//
// 曾经的漏洞：配置生成器写进 .env 的是 "change-me-in-production"，它不在
// 旧的占位符名单里，于是被拿去真的签 token——而那个值写在仓库里，任何知道
// 它的人都能伪造任意用户的 token，包括管理员。

func TestGetJwtSecretIgnoresPlaceholders(t *testing.T) {
	for _, placeholder := range []string{
		"change-me-in-production",
		"CHANGE-ME-jwt-secret",
		"weknora-jwt-secret",
		"change-me",
	} {
		t.Setenv("JWT_SECRET", placeholder)
		resetJwtSecretCacheForTest()

		got := getJwtSecret()
		if got == placeholder {
			t.Errorf("JWT_SECRET=%q 被当成了真密钥——公开值等于没有密钥", placeholder)
		}
		if strings.TrimSpace(got) == "" {
			t.Errorf("JWT_SECRET=%q 时拿到了空的密钥", placeholder)
		}
	}
}

func TestGetJwtSecretUsesConfiguredValue(t *testing.T) {
	const real = "a-real-secret-value-for-tests"
	t.Setenv("JWT_SECRET", real)
	resetJwtSecretCacheForTest()

	if got := getJwtSecret(); got != real {
		t.Errorf("配好的密钥没被用上：%q", got)
	}
}

// resetJwtSecretCacheForTest 重置 getJwtSecret 的一次性缓存。
//
// getJwtSecret 用 sync.Once 缓存密钥，测试里换 JWT_SECRET 之后缓存不会失效，
// 于是第一个用例的结果会漏给后面每一个。这个助手只在测试里用。
func resetJwtSecretCacheForTest() {
	jwtSecretOnce = sync.Once{}
	jwtSecret = ""
}
