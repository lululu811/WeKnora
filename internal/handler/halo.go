package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/Tencent/WeKnora/internal/agent/tools/halo"
	"github.com/Tencent/WeKnora/internal/application/access"
	"github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	secutils "github.com/Tencent/WeKnora/internal/utils"
)

// HaloHandler 是**分析栈**的第一个 HTTP 面。
//
// 在此之前 halo / hithink / zettaranc 三个分析栈只经 agent 工具暴露。HTTP 面
// 已有的是另外两类：自选池（/watchlist，用户自己的数据）与浏览器直连
// python-service 的只读行情端点（/api/kline 等）。分析栈两者都不是，所以没有。
//
// 工作台面板需要 HTTP，而 python-service 的 /halo/* 四个端点全部要求 API key
// （require_api_key）—— 那个 key 不能下发到浏览器，所以必须由 Go 侧代理。
// 这也正是 /halo/* 与 /api/kline 那批的本质区别：后者只读且不鉴权，浏览器可以
// 直连（见 frontend/nginx.conf 的白名单）；前者不行。
type HaloHandler struct {
	knowledgeService  interfaces.KnowledgeService
	kbService         interfaces.KnowledgeBaseService
	kbShareService    interfaces.KBShareService
	agentShareService interfaces.AgentShareService
	haloClient        *halo.HTTPClient
}

// NewHaloHandler 构造归档 handler。
func NewHaloHandler(
	knowledgeService interfaces.KnowledgeService,
	kbService interfaces.KnowledgeBaseService,
	kbShareService interfaces.KBShareService,
	agentShareService interfaces.AgentShareService,
	haloClient *halo.HTTPClient,
) *HaloHandler {
	return &HaloHandler{
		knowledgeService:  knowledgeService,
		kbService:         kbService,
		kbShareService:    kbShareService,
		agentShareService: agentShareService,
		haloClient:        haloClient,
	}
}

// haloArchiveRequest 是 POST /halo/archive 的请求体。
type haloArchiveRequest struct {
	KnowledgeBaseID string `json:"knowledge_base_id"`
	Thscode         string `json:"thscode"`
	Period          string `json:"period"`
	ReportType      string `json:"report_type"`
	Scope           string `json:"scope"`
	// IncludeExternal 默认 false，但**可以显式打开**。
	//
	// 两融（东财稳定档）与北向（同花顺）都挂在 external 上，所以不打开时报告里
	// 就没有这两节 —— 这是刻意的默认：external 里还包含 push2his（资金流，易封档，
	// 实测已被 IP 级封禁），归档一次报告不该顺手把请求打到那里去。
	// 需要那两节的调用方显式传 true，并接受一次外网往返。
	IncludeExternal bool `json:"include_external"`
	// Publish 默认 false：归档先落成草稿。
	//
	// 报告里约三成内容是 AI 判断（7 个定性维度及其子项），且自带 30 天有效期
	// （见模板免责声明）。直接 publish 等于把一批会过期的模型判断放进可检索、
	// 可被引用的层。人工过一眼再发布，代价很小。
	Publish bool `json:"publish"`
}

// 归档用的自定义元数据键。CustomMetadata 的约束（见 knowledgeService.UpdateKnowledge）：
// 至多 20 个字段，值只能是字符串/数字/布尔/null —— 所以只能是扁平标量。
//
// 这些字段**会被喂给模型**（types.KnowledgeCustomMetadata 的注释写明是
// "user-authored context safe to expose to models"），因此这里刻意只放
// 「模型读到之后不会误判」的信息：数据截止、口径、哪些维度是 AI 判的、有效期。
// 内部簿记（幂等匹配用）也在其中，但它本身也是模型该知道的事实。
const (
	haloMetaThscode   = "halo_thscode"
	haloMetaPeriod    = "halo_period"
	haloMetaReport    = "halo_report_type"
	haloMetaGenerated = "halo_generated_at"
	haloMetaCutoff    = "halo_data_cutoff"
	haloMetaAssetType = "halo_asset_type"
	haloMetaLLMDims   = "halo_ai_scored_dims"
	haloMetaCaliber   = "halo_caliber"
)

// haloLLMScoredDims 是**由模型给分**的维度，必须显式声明。
//
// 不写这一条，模型读到「护城河 7 分」时无法知道它是算出来的还是判出来的。
// Python 侧 render_markdown 已经把待判分槽位标成 `{{xxx_score}}` 占位符，这里
// 在元数据上再声明一次，是为了让**检索到该文档**的模型也看得到。
const haloLLMScoredDims = "moat,stag,esg,management,shareholder,valuation,risk"

// ArchiveHaloReport godoc
//
// @Summary      归档 HALO 报告到知识库
// @Description  取该股票的年报评分结果，把预渲染的 markdown 落成知识库中的一条
// @Description  手动知识（默认草稿）。同一 (标的, 报告期) 重复归档为**原地更新**，
// @Description  不产生副本。
// @Tags         HALO
// @Accept       json
// @Produce      json
// @Param        request  body      haloArchiveRequest  true  "归档参数"
// @Success      200      {object}  map[string]interface{}
// @Failure      400      {object}  errors.AppError  "参数错误"
// @Failure      400      {object}  errors.AppError  "该股票没有已落库的年报事实（无可归档内容）"
// @Security     Bearer
// @Security     ApiKeyAuth
// @Router       /halo/archive [post]
func (h *HaloHandler) ArchiveHaloReport(c *gin.Context) {
	ctx := c.Request.Context()

	var req haloArchiveRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(errors.NewBadRequestError(err.Error()))
		return
	}
	thscode := strings.TrimSpace(req.Thscode)
	if thscode == "" {
		c.Error(errors.NewBadRequestError("thscode 不能为空"))
		return
	}

	// 归档是写操作：复用 KB 路由同一套策略（含按 API key 的 KB scope 与角色）。
	grant, err := resolveHandlerKBAccessFor(
		c, req.KnowledgeBaseID, h.kbService, h.kbShareService, h.agentShareService, types.OrgRoleEditor,
	)
	if err != nil {
		c.Error(err)
		return
	}
	if err := access.RequireKBWrite(grant.Context(ctx), grant.KnowledgeBase); err != nil {
		c.Error(kbAccessHTTPError(err))
		return
	}
	ctx = grant.Context(ctx)

	reportType := strings.TrimSpace(req.ReportType)
	if reportType == "" {
		reportType = "annual"
	}
	scope := strings.TrimSpace(req.Scope)
	if scope == "" {
		scope = "consolidated"
	}

	// include_announcements 恒为 true：公告是报告里「消息面」的背景，而它只多
	// 一次巨潮请求（单页，见 _fetch_announcements）。报告里没有公告那一节会让
	// 读者以为这只票近期没有公告，而实际是没取 —— 这两件事必须能区分。
	//
	// include_external 由调用方决定（默认 false）：它包含 push2his 这一易封档，
	// 不该是归档的默认副作用；但两融与北向挂在它上面，所以必须留出显式打开的路径，
	// 否则那两节的渲染代码永远不会被执行。
	score, err := h.haloClient.Score(ctx, halo.ScoreRequest{
		Thscode:              thscode,
		Period:               strings.TrimSpace(req.Period),
		ReportType:           reportType,
		Scope:                scope,
		IncludeExt:           req.IncludeExternal,
		IncludeAnnouncements: true,
	})
	if err != nil {
		logger.ErrorWithFields(ctx, err, map[string]interface{}{"thscode": secutils.SanitizeForLog(thscode)})
		c.Error(errors.NewInternalServerError(err.Error()))
		return
	}

	// 「没数据」不是「归档成功但内容为空」。空骨架入库只会变成一条会骗人的文档，
	// 所以这里如实拒绝，并把 Python 给的原因原样带出去。
	if ok, isBool := score["ok"].(bool); isBool && !ok {
		reason, _ := score["reason"].(string)
		if reason == "" {
			reason = "该股票没有已落库的年报事实，先执行 halo.filing.sync"
		}
		c.Error(errors.NewValidationError(reason))
		return
	}

	markdown, _ := score["markdown"].(string)
	if strings.TrimSpace(markdown) == "" {
		c.Error(errors.NewValidationError("评分结果里没有可归档的 markdown 内容"))
		return
	}

	normalizedThscode, _ := score["thscode"].(string)
	if normalizedThscode == "" {
		normalizedThscode = thscode
	}
	period, _ := score["period"].(string)
	assetType, _ := score["asset_type"].(string)

	meta := haloArchiveMetadata(normalizedThscode, period, reportType, assetType)
	title := haloArchiveTitle(normalizedThscode, period)
	status := types.ManualKnowledgeStatusDraft
	if req.Publish {
		status = types.ManualKnowledgeStatusPublish
	}
	payload := &types.ManualKnowledgePayload{
		Title:   title,
		Content: markdown,
		Status:  status,
		Channel: "halo",
	}

	// 幂等：同一 (标的, 报告期) 已归档过就原地更新，不产生第二份。
	// 用 knowledge ID 更新而不是删了重建，是为了不打断已有引用与链接。
	existing, err := h.findArchivedReport(ctx, req.KnowledgeBaseID, normalizedThscode, period)
	if err != nil {
		logger.ErrorWithFields(ctx, err, map[string]interface{}{"kb_id": req.KnowledgeBaseID})
		c.Error(errors.NewInternalServerError(err.Error()))
		return
	}

	action := "created"
	var knowledge *types.Knowledge
	if existing != nil {
		action = "updated"
		knowledge, err = h.knowledgeService.UpdateManualKnowledge(ctx, existing.ID, payload)
	} else {
		knowledge, err = h.knowledgeService.CreateKnowledgeFromManual(
			ctx, req.KnowledgeBaseID, payload, "halo",
		)
	}
	if err != nil {
		if appErr, ok := errors.IsAppError(err); ok {
			c.Error(appErr)
			return
		}
		logger.ErrorWithFields(ctx, err, map[string]interface{}{
			"kb_id": req.KnowledgeBaseID, "action": action,
		})
		c.Error(errors.NewInternalServerError(err.Error()))
		return
	}

	// 血缘单独写一次：CreateKnowledgeFromManual / UpdateManualKnowledge 都不收
	// 自定义元数据（payload 里没有这个字段），而 UpdateKnowledge 的文档语义就是
	// 「未传字段保持不变」，所以只带 ID + CustomMetadata 是安全的。
	if err := h.attachLineage(ctx, knowledge.ID, meta); err != nil {
		// 正文已经落库，血缘没写上不该让整个归档失败；但要吵，否则下次靠元数据
		// 幂等匹配会找不到这条文档，于是重复归档出第二份。
		logger.ErrorWithFields(ctx, err, map[string]interface{}{
			"knowledge_id": knowledge.ID, "thscode": normalizedThscode,
		})
	}

	logger.Infof(ctx, "HALO 报告已归档 kb=%s thscode=%s period=%s action=%s knowledge_id=%s",
		secutils.SanitizeForLog(req.KnowledgeBaseID), secutils.SanitizeForLog(normalizedThscode),
		secutils.SanitizeForLog(period), action, secutils.SanitizeForLog(knowledge.ID))

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"data": gin.H{
			"knowledge_id": knowledge.ID,
			"title":        title,
			"action":       action,
			"status":       status,
			"thscode":      normalizedThscode,
			"period":       period,
			// 说清后续：手动知识要等解析完成才可检索，这与 MCP 的 ingest 工具
			// 返回的 note 是同一件事。
			"note": "解析与索引异步进行；parse_status 变为 completed 后才可被检索。",
		},
	})
}

// haloReportRequest 是 POST /halo/report 的请求体。
type haloReportRequest struct {
	Thscode    string `json:"thscode"`
	Period     string `json:"period"`
	ReportType string `json:"report_type"`
	Scope      string `json:"scope"`
	// IncludeAnnouncements 用指针是为了区分「没传」与「显式传 false」。
	// 未传时按 true 处理：面板要展示的就是完整报告，而公告只多一次巨潮请求
	// （单页）。显式传 false 是给「不想为一次预览打外网」的调用方留的出口。
	IncludeAnnouncements *bool `json:"include_announcements"`
	// IncludeExternal 默认 false，理由同归档：external 里有 push2his 易封档，
	// 而两融/北向要它。需要那两节的调用方显式传 true。
	IncludeExternal bool `json:"include_external"`
}

// ReportHaloReport godoc
//
// @Summary      取 HALO 报告（渲染，不落库）
// @Description  取该股票的评分结果与预渲染 markdown，供工作台面板展示。
// @Description  只读：不写知识库，因此不做 KB 访问校验，只依赖路由上的认证。
// @Tags         HALO
// @Accept       json
// @Produce      json
// @Param        request  body      haloReportRequest  true  "查询参数"
// @Success      200      {object}  map[string]interface{}
// @Failure      400      {object}  errors.AppError  "参数错误或该股票无年报事实"
// @Security     Bearer
// @Security     ApiKeyAuth
// @Router       /halo/report [post]
func (h *HaloHandler) ReportHaloReport(c *gin.Context) {
	ctx := c.Request.Context()

	var req haloReportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(errors.NewBadRequestError(err.Error()))
		return
	}
	thscode := strings.TrimSpace(req.Thscode)
	if thscode == "" {
		c.Error(errors.NewBadRequestError("thscode 不能为空"))
		return
	}

	reportType := strings.TrimSpace(req.ReportType)
	if reportType == "" {
		reportType = "annual"
	}
	scope := strings.TrimSpace(req.Scope)
	if scope == "" {
		scope = "consolidated"
	}
	withAnnouncements := true
	if req.IncludeAnnouncements != nil {
		withAnnouncements = *req.IncludeAnnouncements
	}

	// external 恒为 false，与归档一致：预览报告不该把请求打到东财的易封子域。
	data, err := h.haloClient.Score(ctx, halo.ScoreRequest{
		Thscode:              thscode,
		Period:               strings.TrimSpace(req.Period),
		ReportType:           reportType,
		Scope:                scope,
		IncludeExt:           req.IncludeExternal,
		IncludeAnnouncements: withAnnouncements,
	})
	if err != nil {
		logger.ErrorWithFields(ctx, err, map[string]interface{}{"thscode": secutils.SanitizeForLog(thscode)})
		c.Error(errors.NewInternalServerError(err.Error()))
		return
	}

	// 「没数据」是正常情况而不是错误：面板要据此显示「先同步年报」，而不是弹一个
	// 失败提示。所以这里仍返回 200，把 ok/reason 原样交给前端判断 —— 与工具侧
	// 「明确区分没数据和服务挂了」是同一条规矩。归档那条路径必须拒绝（它要写库），
	// 预览这条不必。
	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"data":    data,
	})
}

// haloArchiveTitle 是确定性的标题。
//
// 确定性有两个用处：人一眼能看出是哪只票哪一期；幂等匹配时它可以作为元数据
// 之外的兜底判据（元数据写入失败时仍能靠标题认出重复）。
func haloArchiveTitle(thscode, period string) string {
	if period == "" {
		return "HALO " + thscode
	}
	return "HALO " + thscode + " " + period
}

// haloArchiveMetadata 组装给模型看的血缘。
func haloArchiveMetadata(thscode, period, reportType, assetType string) map[string]any {
	meta := map[string]any{
		haloMetaThscode:   thscode,
		haloMetaPeriod:    period,
		haloMetaReport:    reportType,
		haloMetaGenerated: time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		haloMetaLLMDims:   haloLLMScoredDims,
		haloMetaCaliber: "数值由 python-service 的 HALO 评分内核计算，事实来自巨潮年报 PDF " +
			"与本地 hithink 库；报告期见 halo_period，不要与其他口径混用。",
	}
	if assetType != "" {
		meta[haloMetaAssetType] = assetType
	}
	return meta
}

// attachLineage 把血缘写进 CustomMetadata。
func (h *HaloHandler) attachLineage(ctx context.Context, knowledgeID string, meta map[string]any) error {
	raw, err := json.Marshal(meta)
	if err != nil {
		return err
	}
	return h.knowledgeService.UpdateKnowledge(ctx, &types.Knowledge{
		ID:             knowledgeID,
		CustomMetadata: types.JSON(raw),
	})
}

// findArchivedReport 在同一 KB 里找该 (标的, 报告期) 已归档的报告，找不到返回 nil。
//
// 按 CustomMetadata 匹配而不是按标题：标题是给人看的、可能被人改过；元数据是
// 归档自己写的。标题作为兜底判据保留在调用方，这里不重复。
func (h *HaloHandler) findArchivedReport(
	ctx context.Context, kbID, thscode, period string,
) (*types.Knowledge, error) {
	items, err := h.knowledgeService.ListKnowledgeByKnowledgeBaseID(ctx, kbID)
	if err != nil {
		return nil, err
	}
	for _, item := range items {
		if item == nil || len(item.CustomMetadata) == 0 {
			continue
		}
		var meta map[string]any
		if err := json.Unmarshal(item.CustomMetadata, &meta); err != nil {
			// 别人的自定义元数据不是 JSON 对象，不该让归档失败。
			continue
		}
		gotThscode, _ := meta[haloMetaThscode].(string)
		gotPeriod, _ := meta[haloMetaPeriod].(string)
		if gotThscode == thscode && gotPeriod == period {
			return item, nil
		}
	}
	return nil, nil
}
