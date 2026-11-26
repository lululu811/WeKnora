package utils

import "testing"

// secrets_test.go 守住公开示例值清单。
//
// 这份清单是"公开密钥"与"真密钥"的分界线。曾经有三份各写各的，于是配置
// 生成器写的 "change-me-in-production" 不在名单里——它被拿去真的签 token
// 了，而那个值写在仓库里。

func TestIsPlaceholderSecret(t *testing.T) {
	placeholders := []string{
		"change-me-in-production",
		"CHANGE-ME-jwt-secret",
		"weknora-jwt-secret",
		"change-me",
		"changeme",
		"your-secret-key",
		"  change-me-in-production  ", // 带空白
		"CHANGE-ME-IN-PRODUCTION",     // 大小写不同
	}
	for _, v := range placeholders {
		if !IsPlaceholderSecret(v) {
			t.Errorf("%q 竟然没被认成公开示例值", v)
		}
	}

	real := []string{
		"",
		"a-real-secret",
		"change-me-in-production-and-then-some",
		"my-change-me-secret",
	}
	for _, v := range real {
		if IsPlaceholderSecret(v) {
			t.Errorf("%q 竟然被当成了公开示例值", v)
		}
	}
}

// TestPlaceholderListCoversGeneratedValues 守住清单与生成器之间的一致性。
//
// 配置生成器写进 .env 的值必须都在清单里——否则它会被拿去真的签 token。
// 这里把生成器源码里出现的示例值抠出来比对，避免"改了生成器忘了改清单"。
func TestPlaceholderListCoversGeneratedValues(t *testing.T) {
	// 生成器（cmd/config/main.go）曾经写下的三个值。
	generated := []string{"change-me-in-production", "change-me-32-bytes-long!!", "change-me"}
	for _, v := range generated {
		if !IsPlaceholderSecret(v) {
			t.Errorf("配置生成器写过的 %q 不在公开示例值清单里——它会被当成真密钥用", v)
		}
	}
}
