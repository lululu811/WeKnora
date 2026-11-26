package runtime

import (
	"strings"
	"testing"
)

// secrets_test.go 守住启动期的密钥校验。
//
// 它守的是三件事：公开示例值被拒（那是伪造 token 的口子）、未配置被拒（那是
// 重启即登出与多副本随机掉线的口子）、显式打开的开关能放行（否则开发起不来）。

func TestValidateStartupSecretsRejectsUnset(t *testing.T) {
	t.Setenv("JWT_SECRET", "")
	t.Setenv(AllowEphemeralJWTSecretEnv, "")

	err := ValidateStartupSecrets()
	if err == nil {
		t.Fatal("JWT_SECRET 未设置时竟然通过了")
	}
	if !strings.Contains(err.Error(), "openssl rand") {
		t.Errorf("错误里没有给出怎么修：%v", err)
	}
}

func TestValidateStartupSecretsRejectsPlaceholders(t *testing.T) {
	for _, placeholder := range []string{
		"change-me-in-production", // 配置生成器写进 .env 的那个
		"CHANGE-ME-jwt-secret",
		"weknora-jwt-secret",
		"  change-me  ", // 带空白的也算
	} {
		t.Setenv("JWT_SECRET", placeholder)
		t.Setenv(AllowEphemeralJWTSecretEnv, "")

		err := ValidateStartupSecrets()
		if err == nil {
			t.Errorf("JWT_SECRET=%q（公开示例值）竟然通过了", placeholder)
			continue
		}
		if !strings.Contains(err.Error(), "公开示例值") {
			t.Errorf("JWT_SECRET=%q 的错误没有点明它是公开值：%v", placeholder, err)
		}
	}
}

// TestValidateStartupSecretsRejectsPlaceholderEvenWithEscapeHatch 守住一条：
// 公开示例值**无条件**被拒——开关只放宽"未配置"，不放宽"配了个公开值"。
//
// 那两种情况严重程度不同：未配置是"没配"，公开值是"配了个所有人都知道的值"。
func TestValidateStartupSecretsRejectsPlaceholderEvenWithEscapeHatch(t *testing.T) {
	t.Setenv("JWT_SECRET", "change-me-in-production")
	t.Setenv(AllowEphemeralJWTSecretEnv, "true")

	if err := ValidateStartupSecrets(); err == nil {
		t.Fatal("公开示例值竟然在开关打开时被放行了")
	}
}

func TestValidateStartupSecretsAcceptsRealSecret(t *testing.T) {
	t.Setenv("JWT_SECRET", "s3cr3t-value-that-is-not-public")
	t.Setenv(AllowEphemeralJWTSecretEnv, "")

	if err := ValidateStartupSecrets(); err != nil {
		t.Fatalf("真密钥竟然被拒了：%v", err)
	}
}

func TestValidateStartupSecretsAcceptsEphemeralWhenAllowed(t *testing.T) {
	t.Setenv("JWT_SECRET", "")
	t.Setenv(AllowEphemeralJWTSecretEnv, "true")

	if err := ValidateStartupSecrets(); err != nil {
		t.Fatalf("开关打开时竟然被拒了：%v", err)
	}
}

// TestEphemeralSwitchValuesAreNarrow 说明开关只认两个值。
// 设成 "yes" / "TRUE" / "0" 都当成没设——开关要么明确打开，要么没打开。
func TestEphemeralSwitchValuesAreNarrow(t *testing.T) {
	for _, value := range []string{"", "0", "false", "yes", "on", "off"} {
		t.Setenv("JWT_SECRET", "")
		t.Setenv(AllowEphemeralJWTSecretEnv, value)

		if err := ValidateStartupSecrets(); err == nil {
			t.Errorf("开关值 %q 被当成了打开", value)
		}
	}
	for _, value := range []string{"true", "1", " TRUE "} {
		t.Setenv("JWT_SECRET", "")
		t.Setenv(AllowEphemeralJWTSecretEnv, value)

		if err := ValidateStartupSecrets(); err != nil {
			t.Errorf("开关值 %q 没被当成打开：%v", value, err)
		}
	}
}
