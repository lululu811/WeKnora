package handler

import (
	"errors"
	"io"
	"net/http"
	"os"
	"strconv"

	"github.com/gin-gonic/gin"

	"github.com/Tencent/WeKnora/internal/application/service"
	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/logger"
)

// GetKnowledgeVaultAsset serves one image belonging to a knowledge entry's
// vault file.
//
// The client sends an entry id plus a reference relative to that entry —
// "images/fig.png" — never a path. The directory comes from stored metadata,
// and the service confines resolution to it, so this route cannot be turned
// into a reader for the operator's filesystem. See
// interfaces.KnowledgeService.VaultAsset for the containment rules.
//
// GET /knowledge/:id/vault-asset?ref=images/fig.png
func (h *KnowledgeHandler) GetKnowledgeVaultAsset(c *gin.Context) {
	ctx := c.Request.Context()

	id := c.Param("id")
	if id == "" {
		_ = c.Error(apperrors.NewBadRequestError("缺少知识 ID"))
		return
	}
	ref := c.Query("ref")
	if ref == "" {
		_ = c.Error(apperrors.NewBadRequestError("缺少 ref 参数"))
		return
	}

	knowledge, err := h.kgService.GetKnowledgeByID(ctx, id)
	if err != nil {
		logger.Warnf(ctx, "[VaultAsset] knowledge %s not readable: %v", id, err)
		_ = c.Error(apperrors.NewNotFoundError("知识不存在或不可访问"))
		return
	}
	if !knowledge.IsManual() {
		_ = c.Error(apperrors.NewBadRequestError("该知识不是手工条目，无 vault 资源"))
		return
	}

	absPath, contentType, err := h.kgService.VaultAsset(ctx, knowledge, ref)
	if err != nil {
		if errors.Is(err, service.ErrVaultNotConfigured) {
			// The feature is off, not the request's fault. 501 keeps it
			// distinguishable from a 404 for a genuinely missing image, which
			// the frontend uses to decide whether to offer the resolution at
			// all.
			logger.Warnf(ctx, "[VaultAsset] %s is not set", service.EnvVaultRoot)
			c.JSON(http.StatusNotImplemented, gin.H{"error": "vault 未配置"})
			return
		}
		// Deliberately opaque: "no vault path recorded" and "no such image"
		// collapse into one answer, because distinguishing them would let a
		// caller map the filesystem by response code.
		c.JSON(http.StatusNotFound, gin.H{"error": "资源不存在"})
		return
	}

	file, err := os.Open(absPath)
	if err != nil {
		// Reached only if the file vanished between resolution and open.
		logger.Warnf(ctx, "[VaultAsset] open failed for knowledge %s: %v", id, err)
		c.JSON(http.StatusNotFound, gin.H{"error": "资源不存在"})
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		c.JSON(http.StatusNotFound, gin.H{"error": "资源不存在"})
		return
	}

	// These bytes come from outside WeKnora's own storage and so never passed
	// the upload path's content screening, hence explicit headers.
	// SVG is on the allowlist for charts and can carry script; <img> embedding
	// neutralises it in practice and this is the belt to that braces.
	c.Header("Content-Type", contentType)
	c.Header("Content-Length", strconv.FormatInt(info.Size(), 10))
	c.Header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox")
	c.Header("X-Content-Type-Options", "nosniff")
	c.Header("Cache-Control", "private, max-age=3600")
	c.Header("Content-Disposition", "inline")

	if _, err := io.Copy(c.Writer, file); err != nil {
		// Headers are already flushed, so there is no status left to send —
		// just make sure a truncated body is not silently reported as a
		// complete 200 in the logs.
		logger.Warnf(ctx, "[VaultAsset] streaming failed for knowledge %s: %v", id, err)
	}
}
