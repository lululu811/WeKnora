package service

import (
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

// A manual entry pasted out of a reader keeps the upstream URL, so the reader
// can link back and a re-import can dedupe on it. The validation therefore has
// to accept real article URLs — including the query-heavy ones WeChat hands out
// — while refusing anything the reader would render as a bogus link.
func TestSanitizeManualSource_Accepts(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "wechat article url",
			in:   "https://mp.weixin.qq.com/s?__biz=MjM5OTE0ODA2MQ==&mid=2650998935&idx=1&sn=e95b",
			want: "https://mp.weixin.qq.com/s?__biz=MjM5OTE0ODA2MQ==&mid=2650998935&idx=1&sn=e95b",
		},
		{
			name: "http is allowed",
			in:   "http://example.com/a",
			want: "http://example.com/a",
		},
		{
			name: "surrounding whitespace trimmed",
			in:   "  https://example.com/a \n",
			want: "https://example.com/a",
		},
		{
			name: "scheme case preserved while still matching",
			in:   "HTTPS://Example.com/A",
			want: "HTTPS://Example.com/A",
		},
		{
			name: "empty means no provenance recorded",
			in:   "",
			want: "",
		},
		{
			name: "whitespace only means the same",
			in:   "   ",
			want: "",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := sanitizeManualSource(tc.in)
			require.NoError(t, err)
			require.Equal(t, tc.want, got)
		})
	}
}

// utils.IsValidURL gates URLs the *server* fetches, so it also admits
// resource:// and the storage:// family. This field is a link the reader
// clicks, so those must not slip through — neither must a bare scheme, a
// javascript: payload, or an oversized value that would fail on column write.
func TestSanitizeManualSource_Rejects(t *testing.T) {
	cases := []struct {
		name string
		in   string
	}{
		{"javascript scheme", "javascript:alert(1)"},
		{"data scheme", "data:text/html,<script>"},
		{"file scheme", "file:///etc/passwd"},
		{"resource scheme is server-internal", "resource://files/abc123"},
		{"storage scheme is server-internal", "storage://bucket/key"},
		{"scheme with no host", "https://"},
		{"bare scheme only", "http://"},
		{"no scheme at all", "mp.weixin.qq.com/s?x=1"},
		{"control characters", "https://example.com/\x00evil"},
		{"exceeds column width", "https://example.com/" + strings.Repeat("a", 2100)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := sanitizeManualSource(tc.in)
			require.Error(t, err)
			require.Empty(t, got)
		})
	}
}

// The regression this guards: an edit used to reset Source to the literal
// "manual", so fixing a typo in an imported article silently destroyed the
// only field linking back to the original.
func TestResolveManualSource(t *testing.T) {
	const url = "https://mp.weixin.qq.com/s?__biz=MjM5&mid=2650998935"

	t.Run("edit preserves an existing url", func(t *testing.T) {
		require.Equal(t, url, resolveManualSource(url, ""))
	})

	t.Run("incoming value wins so a bad link can be corrected", func(t *testing.T) {
		require.Equal(t, "https://other.example/b", resolveManualSource(url, "https://other.example/b"))
	})

	t.Run("legacy marker is not treated as provenance", func(t *testing.T) {
		require.Equal(t, types.KnowledgeTypeManual,
			resolveManualSource(types.KnowledgeTypeManual, ""))
	})

	t.Run("empty row stays empty", func(t *testing.T) {
		require.Equal(t, types.KnowledgeTypeManual, resolveManualSource("", ""))
	})

	t.Run("marker can be upgraded to a real url", func(t *testing.T) {
		require.Equal(t, url, resolveManualSource(types.KnowledgeTypeManual, url))
	})
}
