package service

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestWithinDir(t *testing.T) {
	base := filepath.Join(string(filepath.Separator), "vault", "root")

	require.True(t, withinDir(base, base), "a directory contains itself")
	require.True(t, withinDir(base, filepath.Join(base, "a", "b.png")))
	require.True(t, withinDir(base, filepath.Join(base, "..foo")), `a sibling named "..foo" is inside`)

	require.False(t, withinDir(base, filepath.Join(base, "..")), `".." escapes`)
	require.False(t, withinDir(base, filepath.Join(base, "..", "sibling")), `a parent-relative path escapes`)
	require.False(t, withinDir(base, filepath.Join(base, "a", "..", "..", "elsewhere")))
	require.False(t, withinDir(base, filepath.Join(string(filepath.Separator), "etc", "passwd")))
}

// The containment helper is the whole security argument, so it gets exercised
// against the traversal shapes a caller could actually send.
func TestResolveVaultAssetRejectsEscapes(t *testing.T) {
	dir := t.TempDir()
	articleDir := filepath.Join(dir, "腾讯研究院", "某篇文章")
	require.NoError(t, os.MkdirAll(filepath.Join(articleDir, "images"), 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(articleDir, "images", "fig.png"), []byte("x"), 0o644))
	require.NoError(t, os.MkdirAll(filepath.Join(dir, "other"), 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "other", "secret.png"), []byte("x"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "notes.md"), []byte("x"), 0o644))

	t.Setenv(EnvVaultRoot, dir)
	vaultPath := filepath.Join("腾讯研究院", "某篇文章", "某篇文章.md")

	t.Run("resolves a reference inside the article directory", func(t *testing.T) {
		// vaultRoot() hands back a symlink-free path, and on macOS
		// t.TempDir() lives under /var -> /private/var. Build the expectation
		// from the root the code actually uses, otherwise this assertion
		// compares two spellings of one directory and fails for a reason that
		// has nothing to do with the behaviour under test.
		root, err := vaultRoot()
		require.NoError(t, err)

		got, contentType, err := resolveVaultAsset(vaultPath, "images/fig.png")
		require.NoError(t, err)
		require.Equal(t, filepath.Join(root, "腾讯研究院", "某篇文章", "images", "fig.png"), got)
		require.Equal(t, "image/png", contentType)
	})

	t.Run("traversal out of the vault is refused", func(t *testing.T) {
		for _, ref := range []string{
			"../other/secret.png",
			"../../other/secret.png",
			"images/../../other/secret.png",
		} {
			_, _, err := resolveVaultAsset(vaultPath, ref)
			require.ErrorIs(t, err, ErrVaultAssetNotFound, ref)
		}
	})

	t.Run("a sibling article's image is refused", func(t *testing.T) {
		// Contained by the vault root but not by the entry's own directory.
		_, _, err := resolveVaultAsset(vaultPath, "../../other/secret.png")
		require.ErrorIs(t, err, ErrVaultAssetNotFound)
	})

	t.Run("absolute and volume paths are refused", func(t *testing.T) {
		for _, ref := range []string{"/etc/passwd", "notes.md"} {
			_, _, err := resolveVaultAsset(vaultPath, ref)
			require.ErrorIs(t, err, ErrVaultAssetNotFound, ref)
		}
	})

	t.Run("non-image extensions are refused", func(t *testing.T) {
		_, _, err := resolveVaultAsset(vaultPath, "../notes.md")
		require.ErrorIs(t, err, ErrVaultAssetNotFound)
	})

	t.Run("a poisoned vault_path cannot park the directory outside the root", func(t *testing.T) {
		_, _, err := resolveVaultAsset("../../../../etc/passwd.md", "passwd")
		require.ErrorIs(t, err, ErrVaultAssetNotFound)
	})

	t.Run("a symlink out of the vault is refused", func(t *testing.T) {
		outside := t.TempDir()
		require.NoError(t, os.WriteFile(filepath.Join(outside, "leak.png"), []byte("x"), 0o644))
		require.NoError(t, os.Symlink(outside, filepath.Join(articleDir, "images", "escape")))

		// Lexically this is "images/escape/leak.png" — well inside the article
		// directory — so only the post-symlink containment check catches it.
		_, _, err := resolveVaultAsset(vaultPath, "images/escape/leak.png")
		require.ErrorIs(t, err, ErrVaultAssetNotFound)
	})
}

func TestResolveVaultAssetNotConfigured(t *testing.T) {
	t.Setenv(EnvVaultRoot, "   ")
	_, _, err := resolveVaultAsset("a/b.md", "images/fig.png")
	require.ErrorIs(t, err, ErrVaultNotConfigured)
	require.False(t, VaultEnabled())

	t.Setenv(EnvVaultRoot, t.TempDir())
	require.True(t, VaultEnabled())
}
