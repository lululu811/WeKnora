package service

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/Tencent/WeKnora/internal/types"
)

// A vault is a directory of exported markdown that the operator wants readable
// in place — an Obsidian library, a docs tree — without any of it being copied
// into WeKnora's own storage. Manual knowledge pasted out of such a tree keeps
// its relative image references ("images/fig.png"), and this file is what turns
// one of those references back into bytes on disk.
//
// The design constraint is that WeKnora must not become a general read primitive
// for the operator's filesystem. Two things enforce that:
//
//   - the client never sends a path. It sends a knowledge id plus a reference
//     relative to that entry's own file, and the server derives the directory
//     from stored metadata. There is no route that takes a caller-supplied
//     directory and reads from it.
//   - the reference is resolved inside that one directory and re-checked after
//     symlink resolution, so ".." and a symlink planted in the vault both fail
//     the same containment check.

const (
	// EnvVaultRoot names the directory every vault_path is relative to.
	EnvVaultRoot = "WEKNORA_VAULT_ROOT"

	// vaultAssetMaxBytes caps a single asset. Exported article figures are
	// raster images; anything approaching this is not one.
	vaultAssetMaxBytes = 32 << 20
)

// vaultAssetExts is an allowlist rather than a denylist. The route is named for
// images and only images should answer to it, so an unexpected extension is a
// miss instead of a leak. SVG is included because WeChat articles use it for
// charts; it is served under a script-blocking CSP because an SVG can carry
// script even though <img> embedding usually neutralises it.
var vaultAssetExts = map[string]string{
	".png":  "image/png",
	".jpg":  "image/jpeg",
	".jpeg": "image/jpeg",
	".gif":  "image/gif",
	".webp": "image/webp",
	".bmp":  "image/bmp",
	".avif": "image/avif",
	".svg":  "image/svg+xml",
}

// ErrVaultNotConfigured means WEKNORA_VAULT_ROOT is unset or empty. Callers
// surface it as "this feature is off", not as a failure of the request.
var ErrVaultNotConfigured = fmt.Errorf("vault root is not configured (set %s)", EnvVaultRoot)

// ErrVaultAssetNotFound means the entry has no vault path, or the reference
// does not resolve to a readable image inside it. The two are deliberately
// indistinguishable: telling them apart would let a caller probe which paths
// exist under the root.
var ErrVaultAssetNotFound = fmt.Errorf("vault asset not found")

// vaultPathMaxLength bounds a stored vault_path. The deepest layout seen in
// practice is <公众号>/<标题>/<标题>.md, so this is generous without being
// unbounded.
const vaultPathMaxLength = 1024

// sanitizeVaultPath validates the path a caller claims an entry was read from.
//
// This is an early, friendly rejection — the authoritative containment check
// is secureJoin at read time, which also follows symlinks. Doing the cheap
// lexical test here as well means an obviously wrong path is refused with a
// clear error at write time instead of producing images that silently never
// resolve later.
func sanitizeVaultPath(raw string) (string, error) {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return "", nil
	}
	if len(trimmed) > vaultPathMaxLength {
		return "", fmt.Errorf("vault_path 过长（最多%d个字符）", vaultPathMaxLength)
	}
	// Callers pass a slash-separated relative path regardless of host OS; the
	// vault on macOS contains article titles with characters that are
	// separators on Windows, so normalise to the host separator before any
	// containment reasoning.
	normalized := filepath.FromSlash(trimmed)
	if filepath.IsAbs(normalized) || strings.HasPrefix(normalized, "/") {
		return "", fmt.Errorf("vault_path 必须是相对于 %s 的路径", EnvVaultRoot)
	}
	if filepath.VolumeName(normalized) != "" {
		return "", fmt.Errorf("vault_path 必须是相对于 %s 的路径", EnvVaultRoot)
	}
	// A leading ".." is the only way out of the root lexically, and Join would
	// silently collapse it, so reject before joining.
	for _, seg := range strings.Split(normalized, string(filepath.Separator)) {
		if seg == ".." {
			return "", fmt.Errorf("vault_path 不能包含 \"..\"")
		}
	}
	return normalized, nil
}

// vaultRoot returns the configured vault root, or ErrVaultNotConfigured.
//
// The root is resolved to an absolute, symlink-free path once here so that
// every later containment check compares like with like.
func vaultRoot() (string, error) {
	raw := strings.TrimSpace(os.Getenv(EnvVaultRoot))
	if raw == "" {
		return "", ErrVaultNotConfigured
	}
	abs, err := filepath.Abs(raw)
	if err != nil {
		return "", fmt.Errorf("resolve %s: %w", EnvVaultRoot, err)
	}
	// A symlinked root is normal (a dotfiles link into a dotfiles repo). Real
	// path is what containment has to be measured against, otherwise a link
	// would look like an escape from the literal path the operator configured.
	real, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return "", fmt.Errorf("resolve %s: %w", EnvVaultRoot, err)
	}
	return real, nil
}

// VaultEnabled reports whether a vault root is configured. The frontend uses
// this to decide whether to offer relative-image resolution at all.
func VaultEnabled() bool {
	_, err := vaultRoot()
	return err == nil
}

// resolveVaultAsset turns a reference relative to a knowledge entry's own
// vault file into an absolute path to a readable image.
//
// vaultPath is the entry's stored path relative to the root; ref is the
// caller's reference such as "images/fig.png". Both are untrusted in the sense
// that vaultPath came from an API payload at write time, so neither is trusted
// for containment on its own.
func resolveVaultAsset(vaultPath, ref string) (string, string, error) {
	root, err := vaultRoot()
	if err != nil {
		return "", "", err
	}

	// The entry's own directory. vaultPath is resolved and re-contained
	// against the root first, so a poisoned vaultPath cannot park the
	// directory outside the vault.
	entryAbs, err := secureJoin(root, vaultPath)
	if err != nil {
		return "", "", ErrVaultAssetNotFound
	}
	dir := filepath.Dir(entryAbs)

	ref = strings.TrimSpace(ref)
	if ref == "" {
		return "", "", ErrVaultAssetNotFound
	}
	// A reference is relative by construction. Reject the absolute forms up
	// front rather than letting filepath.Join quietly discard dir.
	if filepath.IsAbs(ref) || strings.HasPrefix(ref, "/") || strings.HasPrefix(ref, "\\") {
		return "", "", ErrVaultAssetNotFound
	}
	// Windows-style drive letters and UNC paths are absolute to filepath on
	// some platforms even when they do not start with a separator.
	if vol := filepath.VolumeName(ref); vol != "" {
		return "", "", ErrVaultAssetNotFound
	}

	target, err := secureJoin(dir, ref)
	if err != nil {
		return "", "", ErrVaultAssetNotFound
	}
	// Containment against the entry's directory, not just the root: one
	// article must not be able to read a sibling's figures.
	if !withinDir(dir, target) {
		return "", "", ErrVaultAssetNotFound
	}

	ext := strings.ToLower(filepath.Ext(target))
	contentType, ok := vaultAssetExts[ext]
	if !ok {
		return "", "", ErrVaultAssetNotFound
	}

	info, err := os.Stat(target)
	if err != nil || !info.Mode().IsRegular() {
		return "", "", ErrVaultAssetNotFound
	}
	if info.Size() > vaultAssetMaxBytes {
		return "", "", ErrVaultAssetNotFound
	}

	return target, contentType, nil
}

// secureJoin joins base and rel, then refuses the result unless it really is
// inside base after symlinks are followed.
func secureJoin(base, rel string) (string, error) {
	if base == "" {
		return "", ErrVaultAssetNotFound
	}
	// filepath.Join already cleans, so "a/../../etc" collapses before the
	// containment test rather than after it.
	joined := filepath.Join(base, rel)

	// Containment on the lexical path first. This is what rejects "..".
	if !withinDir(base, joined) {
		return "", ErrVaultAssetNotFound
	}

	// Then again on the real path. A symlink inside the vault pointing at
	// /etc passes the lexical test and fails this one.
	realBase, err := filepath.EvalSymlinks(base)
	if err != nil {
		return "", err
	}
	realTarget, err := resolveExistingAncestor(joined)
	if err != nil {
		return "", err
	}
	if !withinDir(realBase, realTarget) {
		return "", ErrVaultAssetNotFound
	}
	return joined, nil
}

// resolveExistingAncestor resolves symlinks for as much of path as actually
// exists, then re-appends whatever is left.
//
// A plain EvalSymlinks would demand the leaf exist, which is the wrong
// requirement here: vault_path names the markdown a knowledge entry was read
// from, and that file is allowed to have been re-exported or deleted since.
// Requiring it would make every image in the article break because of an
// unrelated edit to the .md. Callers stat the resolved path themselves, so a
// missing leaf is caught there — with the right error.
func resolveExistingAncestor(path string) (string, error) {
	remainder := ""
	current := path
	for {
		if real, err := filepath.EvalSymlinks(current); err == nil {
			if remainder == "" {
				return real, nil
			}
			return filepath.Join(real, remainder), nil
		}
		parent := filepath.Dir(current)
		if parent == current {
			// Reached the root without finding anything that resolves.
			return "", ErrVaultAssetNotFound
		}
		remainder = filepath.Join(filepath.Base(current), remainder)
		current = parent
	}
}

// withinDir reports whether target is base itself or sits underneath it.
// Both arguments must already be cleaned absolute paths.
func withinDir(base, target string) bool {
	if base == target {
		return true
	}
	rel, err := filepath.Rel(base, target)
	if err != nil {
		return false
	}
	if rel == "." || filepath.IsAbs(rel) {
		return rel == "."
	}
	// The only way to be outside is a leading "..". Checking the separator
	// matters: a file genuinely named "..foo" is inside.
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

// VaultAsset resolves a relative reference against the vault file a manual
// knowledge entry was read from. See interfaces.KnowledgeService.VaultAsset.
func (s *knowledgeService) VaultAsset(
	_ context.Context, knowledge *types.Knowledge, ref string,
) (string, string, error) {
	if knowledge == nil || !knowledge.IsManual() {
		return "", "", ErrVaultAssetNotFound
	}
	meta, err := knowledge.ManualMetadata()
	if err != nil || meta == nil || meta.VaultPath == "" {
		return "", "", ErrVaultAssetNotFound
	}
	return resolveVaultAsset(meta.VaultPath, ref)
}

// vaultImageMimes maps the extensions the exporter emits to their content type.
// The allowlist matches vaultAssetExts: only what the asset route can serve is
// worth resolving, and resolving a .md or .pdf sibling would be pointless work.
var vaultImageMimes = map[string]string{
	".png":  "image/png",
	".jpg":  "image/jpeg",
	".jpeg": "image/jpeg",
	".gif":  "image/gif",
	".webp": "image/webp",
	".bmp":  "image/bmp",
	".avif": "image/avif",
	".svg":  "image/svg+xml",
}

// vaultImageRefsMaxBytes caps a single image read. A finance article's figures
// are raster screenshots; anything past this is not one, and reading it would
// turn a metadata operation into an OOM.
const vaultImageRefsMaxBytes = 32 << 20

// markdownImageTargetRe matches the *destination* of a markdown image, tolerating
// the two valid spellings the exporter emits: bare and angle-bracketed.
// Group 1 is the raw target with any <> stripped.
var markdownImageTargetRe = regexp.MustCompile(`!\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)`)

// htmlImageSrcRe matches src="..." in a bare <img> tag, which some exports emit
// alongside markdown syntax.
var htmlImageSrcRe = regexp.MustCompile(`(?i)<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']`)

// buildVaultImageResult collects the article's sibling image files into a
// ReadResult that ImageResolver.ResolveAndStore already knows how to consume.
//
// The manual ingestion path only ever called ResolveDataURIImages and
// ResolveRemoteImages, neither of which recognises a relative reference like
// `images/fig.png`. That is fine for *reading* — the render-time vault-asset
// proxy serves those bytes — but it leaves chunks with an empty image_info, so
// the multimodal engine has nothing to OCR. Filling ImageRefs here reuses the
// exact machinery the docreader path uses, rather than adding a second way to
// store images.
//
// Returns nil when the entry has no vault path or no resolvable images, which
// is the common case and must stay a no-op.
func buildVaultImageResult(markdown, vaultPath string) (*types.ReadResult, error) {
	if markdown == "" || vaultPath == "" {
		return nil, nil
	}
	if _, err := vaultRoot(); err != nil {
		// Vault not configured. Not an error: a manual entry without a vault
		// location is perfectly normal.
		return nil, nil
	}

	// Collect candidate refs in document order, de-duplicated.
	seen := map[string]bool{}
	var candidates []string
	add := func(raw string) {
		ref := strings.TrimSpace(raw)
		ref = strings.TrimPrefix(ref, "<")
		ref = strings.TrimSuffix(ref, ">")
		if ref == "" || seen[ref] {
			return
		}
		seen[ref] = true
		candidates = append(candidates, ref)
	}
	for _, m := range markdownImageTargetRe.FindAllStringSubmatch(markdown, -1) {
		add(m[1])
	}
	for _, m := range htmlImageSrcRe.FindAllStringSubmatch(markdown, -1) {
		add(m[1])
	}
	if len(candidates) == 0 {
		return nil, nil
	}

	entryAbs, err := secureJoin(mustVaultRoot(), vaultPath)
	if err != nil {
		return nil, err
	}
	dir := filepath.Dir(entryAbs)

	refs := make([]types.ImageRef, 0, len(candidates))
	for _, ref := range candidates {
		// Absolute URLs and provider:// handles are somebody else's job —
		// ResolveAndStore skips them too, and reading them here would be
		// both wasteful and a traversal hazard.
		if filepath.IsAbs(ref) || strings.Contains(ref, "://") {
			continue
		}
		mime, ok := vaultImageMimes[strings.ToLower(filepath.Ext(ref))]
		if !ok {
			continue
		}
		abs, err := secureJoin(dir, ref)
		if err != nil {
			// Unreadable reference: leave the markdown alone for this one.
			continue
		}
		if !withinDir(dir, abs) {
			continue
		}
		info, err := os.Stat(abs)
		if err != nil || !info.Mode().IsRegular() || info.Size() > vaultImageRefsMaxBytes {
			continue
		}
		data, err := os.ReadFile(abs)
		if err != nil {
			continue
		}
		refs = append(refs, types.ImageRef{
			Filename:    filepath.Base(ref),
			OriginalRef: ref,
			MimeType:    mime,
			ImageData:   data,
			// Vault figures are content, not chrome. IsOriginal also exempts
			// them from the icon/size filter, which is what we want for a
			// chart that happens to be small.
			IsOriginal: true,
		})
	}

	if len(refs) == 0 {
		return nil, nil
	}
	return &types.ReadResult{MarkdownContent: markdown, ImageRefs: refs}, nil
}

// mustVaultRoot is vaultRoot for callers that have already established that a
// vault exists.
func mustVaultRoot() string {
	root, _ := vaultRoot()
	return root
}

// manualVaultPath returns the vault path recorded on a manual knowledge entry,
// or "" when it has none (hand-typed content) or the metadata is unreadable.
// A malformed entry must degrade to "no vault" rather than fail ingestion.
func manualVaultPath(k *types.Knowledge) string {
	if k == nil || !k.IsManual() {
		return ""
	}
	meta, err := k.ManualMetadata()
	if err != nil || meta == nil {
		return ""
	}
	return strings.TrimSpace(meta.VaultPath)
}
