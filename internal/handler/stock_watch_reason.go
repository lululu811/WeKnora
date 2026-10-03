package handler

import (
	"context"
	stderrors "errors"
	"net/http"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/gin-gonic/gin"

	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// DistillWatchReasonRequest is the body of POST /api/v1/watchlist/reason.
type DistillWatchReasonRequest struct {
	// Thscode identifies the symbol the reason is about.
	Thscode string `json:"thscode" binding:"required"`
	// Name is the display name, when known. Purely cosmetic in the prompt.
	Name string `json:"name"`
	// Conversation is the assistant answer the reason should be distilled from.
	Conversation string `json:"conversation" binding:"required"`
}

// StockWatchReasonHandler distills a one-sentence "why am I tracking this"
// out of a whole conversation.
//
// It is a separate handler from StockWatchHandler on purpose. That one is pure
// CRUD over rows the user owns: every method is a scoped query or an upsert, it
// never calls a model, and it never fails for a reason the user can act on. This
// one is a best-effort text-generation side job — a model call that can be slow,
// rate-limited, or entirely unavailable. Mixing the two would force the CRUD
// endpoints to inherit a dependency they never need and an error mode that would
// make "add to watchlist" fail for a reason that has nothing to do with the
// watchlist.
//
// The contract is deliberately lossy: on any failure this returns 200 with an
// empty reason rather than an error. The caller already has a mechanical
// fallback (frontend/finance/utils/stockMentions.ts extractTrackingReason), and
// that fallback — while it produces mediocre prose — always works. A watchlist
// row that says a little is strictly better than a failed request that leaves
// the symbol untracked.
type StockWatchReasonHandler struct {
	models interfaces.ModelService
}

func NewStockWatchReasonHandler(models interfaces.ModelService) *StockWatchReasonHandler {
	return &StockWatchReasonHandler{models: models}
}

// reasonInputBudget caps how much of the conversation is sent. The useful
// signal is the conclusion at the end, not the model's opening throat-clearing,
// and every token here is paid for on the user's model quota at the exact moment
// they are clicking "add to watchlist".
const reasonInputBudget = 4000

// reasonOutputBudget is the DB column width for stock_watches.note (varchar(200)).
// Kept in step with the frontend's extractTrackingReason default so neither side
// is the one that silently truncates.
const reasonOutputBudget = 200

// reasonSystemPrompt is the whole contract. The failure mode it exists to
// prevent is the one mechanical extraction cannot fix: a chat answer that ends
// with a summary list ("⭐ 万科A —— 地产板块龙头，放量突破") yields a note that
// restates the signal instead of giving a reason, and a lead-in sentence
// ("好，数据回来了，给你掰开了揉碎了聊。") yields a note that is pure filler.
// Both read as plausible notes, which is exactly why they are hard to notice.
const reasonSystemPrompt = `你把一段股票分析对话，压缩成一句话的关注理由。

这条理由会存进用户的自选池备注栏，几个月后回看，要能回答「当初为什么跟它」。

硬性要求：
- 一句话，不超过 60 个字，以句号结尾。
- 写「为什么值得跟踪」，不要写「它现在是什么」。
  ✗ 「地产板块龙头，放量突破」——这是复述信号，不是理由。
  ✗ 「4 连板，热股榜第一」——同上。
  ✓ 「地产政策预期未兑现，回调不破年线就是布局位」——这才有信息量。
- 不要复述行情数字（涨幅、连板数、榜单排名），除非它本身构成理由。
- 不要用 emoji，不要 markdown，不要股票代码和名称（界面已经显示）。
- 不要写「建议关注」「值得关注」这类没有信息量的套话。
- 对话里如果找不到这只票的具体理由，就回答「无」，不要编。`

// DistillWatchReason handles POST /api/v1/watchlist/reason.
func (h *StockWatchReasonHandler) DistillWatchReason(c *gin.Context) {
	ctx := c.Request.Context()

	var req DistillWatchReasonRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(apperrors.NewBadRequestError("invalid request body"))
		return
	}
	if !types.IsValidStockWatchCode(req.Thscode) {
		c.Error(apperrors.NewBadRequestError("invalid thscode"))
		return
	}

	reason, err := h.distill(ctx, req)
	if err != nil {
		// Logged, not surfaced: the caller falls back to mechanical extraction
		// and the user has a working note either way. A toast saying "模型调用
		// 失败" for a field the user never asked to fill would be noise.
		logger.WarnWithFields(ctx, logger.Fields{
			"thscode": req.Thscode,
			"error":   err.Error(),
		}, "watch reason distillation failed")
		c.JSON(http.StatusOK, gin.H{"success": true, "data": gin.H{"reason": "", "source": "unavailable"}})
		return
	}
	if reason == "" {
		c.JSON(http.StatusOK, gin.H{"success": true, "data": gin.H{"reason": "", "source": "none"}})
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": gin.H{"reason": reason, "source": "llm"}})
}

// distill performs the one-shot model call and normalises its output.
func (h *StockWatchReasonHandler) distill(
	ctx context.Context, req DistillWatchReasonRequest,
) (string, error) {
	conv := req.Conversation
	if utf8.RuneCountInString(conv) > reasonInputBudget {
		runes := []rune(conv)
		conv = string(runes[len(runes)-reasonInputBudget:])
	}
	if strings.TrimSpace(conv) == "" {
		return "", nil
	}

	model, err := h.defaultChatModel(ctx)
	if err != nil {
		return "", err
	}

	subject := req.Name
	if subject == "" {
		subject = req.Thscode
	}
	user := "以下是一段股票分析对话。请针对其中提到的「" + subject + "」，写一句关注理由。\n\n" +
		"---- 对话开始 ----\n" + conv + "\n---- 对话结束 ----"

	resp, err := model.Chat(ctx, []chat.Message{
		{Role: "system", Content: reasonSystemPrompt},
		{Role: "user", Content: user},
	}, &chat.ChatOptions{
		Temperature: 0.2,
		MaxTokens:   200,
	})
	if err != nil {
		return "", err
	}
	if resp == nil {
		return "", nil
	}
	return normaliseReason(resp.Content), nil
}

// defaultChatModel picks the tenant's knowledge-QA model — the same one the
// chat surface uses, so distillation costs the user no model they haven't
// already configured.
func (h *StockWatchReasonHandler) defaultChatModel(ctx context.Context) (chat.Chat, error) {
	models, err := h.models.ListModels(ctx)
	if err != nil {
		return nil, err
	}
	id := ""
	for _, m := range models {
		if m == nil {
			continue
		}
		if m.Type == types.ModelTypeKnowledgeQA && m.Status == types.ModelStatusActive {
			id = m.ID
			break
		}
	}
	if id == "" {
		return nil, stderrors.New("no active knowledge-QA model configured for this tenant")
	}
	return h.models.GetChatModel(ctx, id)
}

// normaliseReason turns model output into something safe to store in a
// varchar(200) column: no markdown, no emoji, no leading bullets, no stray
// quotes, truncated on a rune boundary.
func normaliseReason(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	s = stripChartAnchors(s)
	// Trim only clears the ends. Bold/italic markers also show up in the
	// middle of a sentence ("**政策预期未兑现**，年线不破"), so they need their
	// own pass — a note column has no renderer to interpret them.
	s = strings.ReplaceAll(s, "**", "")
	s = strings.ReplaceAll(s, "`", "")
	s = strings.Trim(s, "*_ \t\n\r")
	s = strings.TrimPrefix(s, "- ")
	s = strings.TrimPrefix(s, "• ")
	// Drop a leading "理由：" / "Reason:" style label.
	for _, p := range []string{"理由：", "理由:", "原因：", "原因:"} {
		s = strings.TrimPrefix(s, p)
	}
	// Unwrap surrounding quotes the model may have added.
	s = strings.Trim(s, "“”\"'")

	// The model may ignore "one sentence" and write a paragraph. Keep the
	// first sentence that actually carries content.
	if i := strings.IndexAny(s, "\n"); i > 0 {
		s = s[:i]
	}
	s = strings.TrimSpace(s)
	if s == "" || s == "无" || s == "无。" {
		return ""
	}

	runes := []rune(s)
	if len(runes) <= reasonOutputBudget {
		return s
	}
	// Cut at the last sentence boundary inside the budget so the note never
	// ends mid-clause; the column would do it anyway, just less gracefully.
	tail := string(runes[:reasonOutputBudget])
	cut := strings.LastIndexAny(tail, "。；;，,")
	if cut > reasonOutputBudget/2 {
		return strings.TrimSpace(tail[:cut+1])
	}
	return strings.TrimSpace(tail)
}

// chartAnchorRe matches the K-line chart anchor tags the agent system prompt
// teaches the model to emit (config/prompt_templates/agent_system_prompt.yaml:
// `<anchor kind="level" value="72.4" label="第一目标"/>`). The chat UI renders
// them as numbered markers on the chart; a note column must never contain one.
//
// It was a real bug, not a hypothetical: an extracted note reached the database
// carrying a raw `<anchor kind="level" value="31.81" .../>`, and varchar(200)
// then cut it mid-tag. The frontend now strips these before saving, but the
// model that generates reasons here was never told to avoid them, and a prompt
// is not a guarantee — so the gate holds on both sides.
var chartAnchorRe = regexp.MustCompile(`@?\s*<anchor\b([^>]*?)/?>`)

// stripChartAnchors replaces each anchor with readable text: the label when
// there is one, else the value, else the date range. Prefixed with "@" it falls
// back to the bare value, because "当前收盘价@31.81" reads naturally where
// "当前收盘价@当前价31.81" does not.
func stripChartAnchors(s string) string {
	return chartAnchorRe.ReplaceAllStringFunc(s, func(m string) string {
		atPrefix := strings.HasPrefix(strings.TrimSpace(m), "@")
		attrs := map[string]string{}
		for _, kv := range anchorAttrRe.FindAllStringSubmatch(m, -1) {
			attrs[kv[1]] = kv[2]
		}
		if atPrefix {
			if v := attrs["value"]; v != "" {
				return "@" + v
			}
			if v := attrs["from"]; v != "" {
				return "@" + v
			}
			return ""
		}
		if v := attrs["label"]; v != "" {
			return v
		}
		if v := attrs["value"]; v != "" {
			return v
		}
		if from, to := attrs["from"], attrs["to"]; from != "" && to != "" {
			return from + " ~ " + to
		}
		return attrs["from"]
	})
}

var anchorAttrRe = regexp.MustCompile(`(\w+)\s*=\s*"([^"]*)"`)
