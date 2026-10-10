package file

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/utils"
)

func TestParseOssFilePath(t *testing.T) {
	tests := []struct {
		name        string
		input       string
		wantBucket  string
		wantKey     string
		wantErr     bool
		errContains string
	}{
		{
			name:       "valid path with nested key",
			input:      "oss://my-bucket/123/exports/abc123.csv",
			wantBucket: "my-bucket",
			wantKey:    "123/exports/abc123.csv",
		},
		{
			name:       "valid path with simple key",
			input:      "oss://test-bucket/key",
			wantBucket: "test-bucket",
			wantKey:    "key",
		},
		{
			name:       "valid path with deep nesting",
			input:      "oss://bucket/prefix/tenant/exports/uuid.png",
			wantBucket: "bucket",
			wantKey:    "prefix/tenant/exports/uuid.png",
		},
		{
			name:        "invalid scheme",
			input:       "s3://bucket/key",
			wantErr:     true,
			errContains: "invalid OSS file path",
		},
		{
			name:        "empty path",
			input:       "",
			wantErr:     true,
			errContains: "invalid OSS file path",
		},
		{
			name:        "bucket only no key",
			input:       "oss://bucket/",
			wantErr:     true,
			errContains: "invalid OSS file path",
		},
		{
			name:        "scheme only",
			input:       "oss://",
			wantErr:     true,
			errContains: "invalid OSS file path",
		},
		{
			name:        "no slash after bucket",
			input:       "oss://bucket",
			wantErr:     true,
			errContains: "invalid OSS file path",
		},
		{
			name:        "empty bucket name",
			input:       "oss:///some-key",
			wantErr:     true,
			errContains: "invalid OSS file path",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			bucket, key, err := parseOssFilePath(tt.input)
			if tt.wantErr {
				if err == nil {
					t.Errorf("parseOssFilePath(%q) expected error, got bucket=%q key=%q", tt.input, bucket, key)
				}
				if tt.errContains != "" && !strings.Contains(err.Error(), tt.errContains) {
					t.Errorf("parseOssFilePath(%q) error = %v, want containing %q", tt.input, err, tt.errContains)
				}
				return
			}
			if err != nil {
				t.Errorf("parseOssFilePath(%q) unexpected error: %v", tt.input, err)
				return
			}
			if bucket != tt.wantBucket {
				t.Errorf("parseOssFilePath(%q) bucket = %q, want %q", tt.input, bucket, tt.wantBucket)
			}
			if key != tt.wantKey {
				t.Errorf("parseOssFilePath(%q) key = %q, want %q", tt.input, key, tt.wantKey)
			}
		})
	}
}

// whitelistOSSHostsForTest pins the OSS fixture endpoints in the SSRF
// whitelist for the duration of a test.
//
// Why: newOSSClient runs the endpoint through ValidateURLForSSRF, which
// resolves the hostname. On a developer machine behind a fake-IP/TUN resolver
// (Surge, clash, some corporate resolvers) every public name answers from
// 198.18.0.0/15 — RFC 2544 benchmarking space — and the guard correctly
// refuses it, so these tests failed on the local DNS rather than on the OSS
// client they mean to exercise. A whitelisted host returns before resolution
// (utils.ValidateURLForSSRF), which is the same lever an operator has.
//
// TestNewOSSClientRejectsUnsafeEndpoint deliberately does NOT call this: the
// loopback endpoint it asserts on must stay outside the whitelist.
func whitelistOSSHostsForTest(t *testing.T) {
	t.Helper()
	utils.SetSSRFWhitelistFromRaw("oss-cn-hangzhou.aliyuncs.com,example.com")
	t.Cleanup(func() { utils.SetSSRFWhitelistFromRaw("") })
}

func TestNewOSSClient(t *testing.T) {
	whitelistOSSHostsForTest(t)
	tests := []struct {
		name      string
		endpoint  string
		region    string
		accessKey string
		secretKey string
		wantErr   bool
	}{
		{
			name:      "valid parameters create client",
			endpoint:  "https://oss-cn-hangzhou.aliyuncs.com",
			region:    "cn-hangzhou",
			accessKey: "test-access-key",
			secretKey: "test-secret-key",
			wantErr:   false,
		},
		{
			name:      "custom endpoint",
			endpoint:  "https://example.com",
			region:    "cn-shanghai",
			accessKey: "ak",
			secretKey: "sk",
			wantErr:   false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			client, err := newOSSClient(tt.endpoint, tt.region, tt.accessKey, tt.secretKey)
			if tt.wantErr {
				if err == nil {
					t.Error("expected error but got nil")
				}
				return
			}
			if err != nil {
				t.Errorf("newOSSClient() unexpected error: %v", err)
				return
			}
			if client == nil {
				t.Error("expected non-nil client")
			}
		})
	}
}

func TestNewOSSClientRejectsUnsafeEndpoint(t *testing.T) {
	if _, err := newOSSClient("http://127.0.0.1:9000", "cn-hangzhou", "ak", "sk"); err == nil {
		t.Fatal("expected loopback OSS endpoint to be rejected")
	}
}

func TestCheckOssConnectivity_InvalidEndpoint(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Should fail with an invalid/unreachable endpoint
	err := CheckOssConnectivity(ctx,
		"https://invalid-oss-endpoint-that-does-not-exist.local",
		"cn-hangzhou",
		"invalid-access-key",
		"invalid-secret-key",
		"nonexistent-bucket",
	)

	if err == nil {
		t.Error("CheckOssConnectivity with invalid endpoint should return an error")
	}
}

func TestOssEnsureBucket_NonExistent(t *testing.T) {
	whitelistOSSHostsForTest(t)
	client, err := newOSSClient(
		"https://oss-cn-hangzhou.aliyuncs.com",
		"cn-hangzhou",
		"test-invalid-key",
		"test-invalid-secret",
	)
	if err != nil {
		t.Fatalf("newOSSClient() error: %v", err)
	}

	// Bucket that definitely doesn't exist - should return error
	err = ossEnsureBucket(client, "this-bucket-definitely-does-not-exist-12345")
	if err == nil {
		t.Error("ossEnsureBucket with non-existent bucket should return an error")
	}
}

func TestOssEnsureBucket_CreateFails(t *testing.T) {
	whitelistOSSHostsForTest(t)
	client, err := newOSSClient(
		"https://oss-cn-hangzhou.aliyuncs.com",
		"cn-hangzhou",
		"test-invalid-key",
		"test-invalid-secret",
	)
	if err != nil {
		t.Fatalf("newOSSClient() error: %v", err)
	}

	// Use a bucket that does not exist so IsBucketExist returns false and the
	// create path is exercised; with invalid credentials PutBucket then fails.
	// A common name like "test-bucket" already exists globally on OSS, which
	// would short-circuit at IsBucketExist and make this assertion flaky.
	err = ossEnsureBucket(client, "weknora-nonexistent-bucket-create-fails-12345")
	if err == nil {
		t.Error("ossEnsureBucket with invalid credentials should return an error")
	}
}
