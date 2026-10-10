package datasource

import (
	"strings"
	"testing"

	secutils "github.com/Tencent/WeKnora/internal/utils"
)

func TestValidateConnectorBaseURLBlocksLoopback(t *testing.T) {
	secutils.ResetSSRFWhitelistForTest()
	t.Cleanup(secutils.ResetSSRFWhitelistForTest)

	err := ValidateConnectorBaseURL("http://127.0.0.1:8000")
	if err == nil {
		t.Fatal("expected loopback base_url to be rejected")
	}
}

func TestValidateConnectorBaseURLAllowsPublicHTTPS(t *testing.T) {
	secutils.ResetSSRFWhitelistForTest()
	t.Cleanup(secutils.ResetSSRFWhitelistForTest)

	err := ValidateConnectorBaseURL("https://open.feishu.cn")
	if err == nil {
		return
	}
	// This test's subject IS the URL policy, so the whitelist lever the other
	// tests use is not available: whitelisting would bypass the check being
	// exercised. The policy resolves the hostname, and a developer machine
	// behind a fake-IP/TUN resolver (Surge, clash, some corporate resolvers)
	// answers every public name from 198.18.0.0/15 — RFC 2544 benchmarking
	// space — which the guard is right to refuse. The premise ("this name is
	// public") does not hold on that machine; it holds anywhere else, where
	// this test keeps running unchanged. Same call as
	// internal/utils/security_test.go's TestSSRFSafeURL_AllowPublicDomain.
	if strings.Contains(err.Error(), "DNS resolution failed") ||
		strings.Contains(err.Error(), "resolves to restricted IP") {
		t.Skipf("skip: local resolver rewrote or failed the public name: %v", err)
	}
	t.Fatalf("expected public base_url to pass: %v", err)
}
