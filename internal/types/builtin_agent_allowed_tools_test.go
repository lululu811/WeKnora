package types

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// MergeBuiltinAllowedTools 的行为锁死。
//
// 这条函数是「出厂白名单扩充后，老租户也能用上新工具」的全部机制，所以它的
// 语义必须精确：并集、保序、去重，且不改动入参。
func TestMergeBuiltinAllowedTools(t *testing.T) {
	t.Run("出厂新增的工具被补进来", func(t *testing.T) {
		got := MergeBuiltinAllowedTools([]string{"a"}, []string{"a", "b"})
		require.Equal(t, []string{"a", "b"}, got)
	})

	t.Run("租户自己加的工具保住", func(t *testing.T) {
		got := MergeBuiltinAllowedTools([]string{"tenant_only"}, []string{"factory"})
		require.Equal(t, []string{"tenant_only", "factory"}, got)
	})

	t.Run("已存的顺序不动，出厂新增的追加在后", func(t *testing.T) {
		got := MergeBuiltinAllowedTools([]string{"b", "a"}, []string{"a", "c"})
		require.Equal(t, []string{"b", "a", "c"}, got)
	})

	t.Run("重复的只留一次", func(t *testing.T) {
		got := MergeBuiltinAllowedTools([]string{"a", "a"}, []string{"a", "b", "b"})
		require.Equal(t, []string{"a", "b"}, got)
	})

	t.Run("出厂为空则原样返回", func(t *testing.T) {
		stored := []string{"a"}
		got := MergeBuiltinAllowedTools(stored, nil)
		require.Equal(t, stored, got)
	})

	t.Run("空名被丢掉", func(t *testing.T) {
		got := MergeBuiltinAllowedTools([]string{"a", ""}, []string{"", "b"})
		require.Equal(t, []string{"a", "b"}, got)
	})

	t.Run("不改入参", func(t *testing.T) {
		stored := []string{"a"}
		builtin := []string{"b"}
		MergeBuiltinAllowedTools(stored, builtin)
		require.Equal(t, []string{"a"}, stored)
		require.Equal(t, []string{"b"}, builtin)
	})
}
