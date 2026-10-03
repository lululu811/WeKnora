package service

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/types"
)

// vaultFixture builds a temp vault shaped like the real export
// (<公众号>/<文章>/<文章>.md + 同级 images/).
func vaultFixture(t *testing.T) (root, vaultPath string) {
	t.Helper()
	root = t.TempDir()
	artDir := filepath.Join(root, "某公众号", "某文章")
	require.NoError(t, os.MkdirAll(filepath.Join(artDir, "images"), 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(artDir, "images", "fig.png"), []byte("PNGDATA"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(artDir, "images", "logo.svg"), []byte("<svg/>"), 0o644))
	// A sibling document that must stay unreachable.
	outside := filepath.Join(root, "other", "secret.png")
	require.NoError(t, os.MkdirAll(filepath.Dir(outside), 0o755))
	require.NoError(t, os.WriteFile(outside, []byte("SECRET"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(artDir, "notes.md"), []byte("x"), 0o644))

	t.Setenv(EnvVaultRoot, root)
	return root, filepath.Join("某公众号", "某文章", "某文章.md")
}

func TestBuildVaultImageResult(t *testing.T) {
	_, vaultPath := vaultFixture(t)

	md := `# 标题

![](images/fig.png)
![带标题](images/logo.svg "caption")
<img src="images/fig.png" alt="inline">
![远程](https://mmbiz.qpic.cn/remote.png)
![非图片](../notes.md)
![穿越](../../other/secret.png)
`

	result, err := buildVaultImageResult(md, vaultPath)
	require.NoError(t, err)
	require.NotNil(t, result, "应解析出图片")

	byRef := map[string]types.ImageRef{}
	for _, r := range result.ImageRefs {
		byRef[r.OriginalRef] = r
	}

	require.Len(t, result.ImageRefs, 2, "只应收 fig.png 与 logo.svg（去重后 2 个）")
	require.Equal(t, "PNGDATA", string(byRef["images/fig.png"].ImageData))
	require.Equal(t, "image/png", byRef["images/fig.png"].MimeType)
	require.Equal(t, "fig.png", byRef["images/fig.png"].Filename)
	require.Equal(t, "image/svg+xml", byRef["images/logo.svg"].MimeType)

	_, hasRemote := byRef["https://mmbiz.qpic.cn/remote.png"]
	require.False(t, hasRemote, "远程 URL 不该由 vault 解析")
	_, hasNotes := byRef["../notes.md"]
	require.False(t, hasNotes, "非图片扩展名应跳过")
	_, hasSecret := byRef["../../other/secret.png"]
	require.False(t, hasSecret, "穿越到同级的文件必须被拒绝")
}

// The whole point of routing through ResolveAndStore rather than writing a
// second store: the markdown that comes back has its targets rewritten to
// provider:// URLs and the images are bound into image_info, which is what the
// multimodal engine consumes.
func TestVaultImagesFeedResolveAndStore(t *testing.T) {
	_, vaultPath := vaultFixture(t)
	md := "# 标题\n\n![](images/fig.png)\n"

	result, err := buildVaultImageResult(md, vaultPath)
	require.NoError(t, err)
	require.NotNil(t, result)
	// ResolveAndStore 的重写依赖 result.ImageRefs 里的 OriginalRef 与正文
	// 里的写法逐字一致，否则 refMap 查不到、静默跳过。
	require.Equal(t, "images/fig.png", result.ImageRefs[0].OriginalRef)
	require.Contains(t, result.MarkdownContent, "![](images/fig.png)")
}

func TestBuildVaultImageResultSkips(t *testing.T) {
	_, vaultPath := vaultFixture(t)

	t.Run("no vault configured", func(t *testing.T) {
		t.Setenv(EnvVaultRoot, "  ")
		result, err := buildVaultImageResult("![](images/fig.png)", vaultPath)
		require.NoError(t, err)
		require.Nil(t, result, "vault 未配置时必须是无操作")
	})

	t.Run("no images in markdown", func(t *testing.T) {
		_, vaultPath := vaultFixture(t)
		result, err := buildVaultImageResult("# 纯文字\n\n没有图片。", vaultPath)
		require.NoError(t, err)
		require.Nil(t, result)
	})

	t.Run("empty vault path", func(t *testing.T) {
		vaultFixture(t) // 只需要它把 EnvVaultRoot 设好
		result, err := buildVaultImageResult("![](images/fig.png)", "")
		require.NoError(t, err)
		require.Nil(t, result)
	})

	t.Run("poisoned vault path cannot escape the root", func(t *testing.T) {
		_, _ = vaultFixture(t)
		result, err := buildVaultImageResult("![](images/fig.png)", "../../../../../../etc/passwd")
		require.Error(t, err, "越界的 vault_path 应当报错而不是静默读到别处")
		require.Nil(t, result)
	})
}

func TestManualVaultPath(t *testing.T) {
	require.Empty(t, manualVaultPath(nil))
	require.Empty(t, manualVaultPath(&types.Knowledge{Type: "file", FileType: "pdf"}),
		"非手工条目没有 vault 概念")

	k := &types.Knowledge{Type: types.KnowledgeTypeManual, FileType: types.KnowledgeTypeManual}
	require.Empty(t, manualVaultPath(k), "无 metadata 时应为空而不是 panic")
}
