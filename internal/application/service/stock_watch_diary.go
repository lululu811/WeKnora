package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/Tencent/WeKnora/internal/watchcond"
)

// ErrStockWatchDiaryModelUnavailable is returned when no chat model can be
// resolved for the diary run. It is a sentinel rather than a plain error so
// the job can tell "this workspace has no model configured" — a setup problem
// the user can fix — apart from "the model call failed", which is transient.
var ErrStockWatchDiaryModelUnavailable = errors.New("no chat model available for the watchlist diary")

// StockWatchDiaryService writes one observation per tracked symbol per trading
// day, and reads them back for the pool page.
//
// What this service deliberately does NOT do: touch stock_watches.state. The
// state machine is the human's position, and a model has no standing to move
// it — see types.StockWatch. The service produces an opinion; the handler that
// adopts it is a separate, human-initiated call.
type StockWatchDiaryService struct {
	repo     interfaces.StockWatchDiaryRepository
	watches  interfaces.StockWatchRepository
	modelSvc interfaces.ModelService
}

// NewStockWatchDiaryService wires the diary writer.
func NewStockWatchDiaryService(
	repo interfaces.StockWatchDiaryRepository,
	watches interfaces.StockWatchRepository,
	modelSvc interfaces.ModelService,
) *StockWatchDiaryService {
	return &StockWatchDiaryService{repo: repo, watches: watches, modelSvc: modelSvc}
}

// Diary run bounds.
const (
	// diaryHistoryDays is how many prior verdicts each symbol contributes as
	// context. Small on purpose: the only thing history buys is "yesterday I
	// said tighten, today the reading is unchanged", so the model does not
	// contradict itself day to day. A longer window costs tokens and invites
	// the model to narrate a trend it cannot see in five scalar readings.
	diaryHistoryDays = types.StockWatchDiaryHistoryDays

	// diaryMaxSymbolsPerCall bounds one model call. Past this the prompt stops
	// being a list a model can hold in its head and starts being a bulk
	// transform, which is where the JSON starts coming back malformed.
	// A scope larger than this is split across calls rather than truncated:
	// silently dropping the tail would leave those symbols with no diary and
	// nothing recording why.
	diaryMaxSymbolsPerCall = 40

	// diaryCompletionTokens is the first budget for one call. Sized for
	// diaryMaxSymbolsPerCall symbols at a short body each; the retry below
	// doubles it if the response was cut off.
	diaryCompletionTokens = 4096

	// diaryRetryTokens is the larger budget used when the first attempt came
	// back truncated.
	diaryRetryTokens = 8192
)

// diarySymbolInput is one symbol as the model sees it.
//
// Readings is the watchcond.Reading struct itself rather than a re-derived
// shape: those five fields are the entire evidence base, and a parallel
// definition would be one more place for the two to drift.
type diarySymbolInput struct {
	THSCode string             `json:"thscode"`
	Name    string             `json:"name"`
	State   string             `json:"state"`
	Note    string             `json:"note"`
	Reading watchcond.Reading  `json:"reading"`
	Recent  []diaryPriorStance `json:"recent"`
}

// diaryPriorStance is one earlier verdict, reduced to what is worth spending
// a token on: what it said, and the reading it said it about.
type diaryPriorStance struct {
	TradeDate string `json:"date"`
	Verdict   string `json:"verdict"`
	Close     string `json:"close"`
}

// diaryVerdict is the model's answer for one symbol.
//
// Verdict is a plain string and is validated AFTER parsing, against
// types.VerdictsForState — a model that answers "keep" for a symbol nobody
// holds is recorded as "none", not stored as a contradiction. Validating in
// Go rather than trusting an enum in the JSON schema is what makes that
// possible: a JSON schema would reject the response outright and take the
// whole batch with it.
type diaryVerdict struct {
	THSCode    string `json:"thscode"`
	Verdict    string `json:"verdict"`
	Confidence int    `json:"confidence"`
	Reasons    string `json:"reasons"`
	Body       string `json:"body"`
}

// diaryBatchResponse is the whole call's answer.
type diaryBatchResponse struct {
	Entries []diaryVerdict `json:"entries"`
}

// diarySystemPrompt fixes the job's contract with the model.
//
// The shape of this prompt follows internal/application/service/memory: state
// the task, state the output shape, and — the part that matters — offer the
// model an honest way to decline. `none` exists because a model asked to
// classify readings it does not have will produce a confident buy anyway, and
// the only defence is to have made "I cannot tell" a legal answer in advance.
const diarySystemPrompt = `你在为一个人的自选股池写每日观察日记。每一只票你只能看到 5 个读数：收盘价、涨跌幅、量比、20日均线、行情日期。

重要：这个池子不记录成本价和盈亏。所以"继续持股"的意思是"当初关注它的理由还站得住吗"，不是"要不要止损"。你不知道用户的成本，永远不要提到止损、止盈、回本、赚了多少。

对每只票，判断它属于哪一种：
- 状态是 observing（未持仓）：buy（关注理由成立且像是买点）/ hold（继续观察）/ sell（关注理由已消失）
- 状态是 holding（已持仓）：keep（持有理由未变）/ tighten（理由变弱但未失效）/ exit（持有理由已失效）

关键：如果读数不足——没有收盘价、MA20 缺失、量比缺失、或只有一根K线导致涨跌幅为 null——一律用 none。你无法判断的时候必须说 none，不要猜。编一个买入或卖出结论比说"数据不足"有害得多。

同时看每只票的 note（用户当初写下的关注理由）和 recent（你前几天说过什么）。你要回答的是"用户当初因为 X 关注它，今天 X 变了没有"，而不是复述均线交叉。

每个读数都是原文，不要自己计算或推算任何数字。

只输出 JSON，不要任何解释文字或代码块以外的内容：
{"entries":[{"thscode":"600519.SH","verdict":"hold","confidence":3,"reasons":"一句话依据","body":"两三句观察"}]}

confidence 是 0-5 的整数。entries 里必须恰好包含输入中的每一只票，一只都不能少、不能多。`

// diaryJSONSchema is passed as ChatOptions.Format so providers that support
// json_object get an object rather than prose.
//
// It is NOT sufficient on its own: openaicompletions only sends
// response_format when the vendor's compat settings say SupportsResponseFormat,
// and that is per-vendor. The tolerant parser below is what makes the call
// work on the vendors where it is silently dropped, which is why both exist.
var diaryJSONSchema = json.RawMessage(`{
  "type": "object",
  "properties": {
    "entries": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "thscode": {"type": "string"},
          "verdict": {"type": "string"},
          "confidence": {"type": "integer"},
          "reasons": {"type": "string"},
          "body": {"type": "string"}
        },
        "required": ["thscode", "verdict", "confidence", "reasons", "body"]
      }
    }
  },
  "required": ["entries"]
}`)

// RunOnceForScope writes one diary per tracked symbol in one scope.
//
// The whole scope is one transaction on the write side (repo.UpsertMany) and
// any number of model calls on the read side, chunked by
// diaryMaxSymbolsPerCall. Per-entry failures are contained: one malformed
// symbol is recorded as a "none" diary rather than discarding the batch,
// because losing twenty good observations to one bad row is the worse outcome
// and the user cannot tell the difference between "no verdict" and "no diary".
func (s *StockWatchDiaryService) RunOnceForScope(
	ctx context.Context, scope types.StockWatchScope, readings map[string]watchcond.Reading,
) error {
	watched, err := s.repo.ListWatchedByScope(ctx, scope.UserID, scope.TenantID)
	if err != nil {
		return fmt.Errorf("list watched symbols: %w", err)
	}
	if len(watched) == 0 {
		return nil
	}

	// Build the inputs before touching the model: a symbol whose reading is
	// entirely absent is decided here, in Go, and never costs a token. "No
	// data" is not something to ask a model about.
	inputs := make([]diarySymbolInput, 0, len(watched))
	for _, w := range watched {
		if w == nil {
			continue
		}
		reading, ok := readings[w.THSCode]
		if !ok {
			continue
		}
		tradeDate, err := types.ParseDateOnly(reading.Date)
		if err != nil {
			// A reading with an unparseable date has no trading day to file
			// under. Skipping it is the only honest option: filing it under
			// today would be inventing an observation date.
			logger.Warnf(ctx, "[WatchlistDiary] %s: unparseable reading date %q, skipping",
				w.THSCode, reading.Date)
			continue
		}
		inputs = append(inputs, diarySymbolInput{
			THSCode: w.THSCode,
			Name:    w.Name,
			State:   w.State,
			Note:    w.Note,
			Reading: reading,
			Recent:  s.priorStances(ctx, scope, w.THSCode, tradeDate),
		})
	}
	if len(inputs) == 0 {
		return nil
	}

	modelID, err := s.resolveModelID(ctx, scope)
	if err != nil {
		return err
	}
	model, err := s.modelSvc.GetChatModel(ctx, modelID)
	if err != nil {
		return fmt.Errorf("%w: get diary model %q: %w", ErrStockWatchDiaryModelUnavailable, modelID, err)
	}

	answers := make(map[string]diaryVerdict, len(inputs))
	for start := 0; start < len(inputs); start += diaryMaxSymbolsPerCall {
		end := start + diaryMaxSymbolsPerCall
		if end > len(inputs) {
			end = len(inputs)
		}
		chunk := inputs[start:end]
		parsed, err := s.callDiaryModel(ctx, model, chunk)
		if err != nil {
			// One chunk failing does not mean the scope failed: the symbols
			// in the other chunks already have honest answers waiting to be
			// written. Report it and keep going.
			logger.Warnf(ctx, "[WatchlistDiary] %s/%d: model call failed for %d symbol(s): %v",
				scope.UserID, scope.TenantID, len(chunk), err)
			continue
		}
		for _, v := range parsed.Entries {
			answers[v.THSCode] = v
		}
	}

	diaries := make([]*types.StockWatchDiary, 0, len(inputs))
	for _, in := range inputs {
		tradeDate, _ := types.ParseDateOnly(in.Reading.Date)
		row := &types.StockWatchDiary{
			UserID:    scope.UserID,
			TenantID:  scope.TenantID,
			THSCode:   in.THSCode,
			TradeDate: tradeDate,
			Verdict:   types.StockWatchDiaryVerdictNone,
			ModelID:   modelID,
			Readings:  readingsJSON(in.Reading),
		}
		if a, ok := answers[in.THSCode]; ok {
			row.Verdict, row.Confidence, row.Reasons, row.Body = applyVerdict(in.State, a)
		}
		// A "none" with no body is still a row: its presence is what
		// distinguishes "the job ran and had nothing to say" from "the job
		// never ran", which is the same distinction
		// stock_watch_events.eval_date exists to preserve.
		if row.Body == "" {
			row.Body = noVerdictBody(in)
		}
		diaries = append(diaries, row)
	}
	if err := s.repo.UpsertMany(ctx, diaries); err != nil {
		return fmt.Errorf("upsert diaries: %w", err)
	}
	return nil
}

// applyVerdict validates a model answer against the row's state and clamps the
// values it hands back.
//
// Returning the stored triple rather than mutating in place keeps the
// validation in one place: an out-of-set verdict, a confidence outside 0-5 and
// a missing thscode all collapse to the same honest answer, "none".
func applyVerdict(state string, a diaryVerdict) (verdict string, confidence int, reasons, body string) {
	if !types.IsStockWatchVerdictForState(state, a.Verdict) {
		return types.StockWatchDiaryVerdictNone, 0, a.Reasons, a.Body
	}
	confidence = a.Confidence
	if confidence < 0 {
		confidence = 0
	}
	if confidence > 5 {
		confidence = 5
	}
	return a.Verdict, confidence, a.Reasons, a.Body
}

// noVerdictBody is the text shown for a symbol the model did not answer for or
// could not answer about. It states the reason rather than staying silent: an
// empty body next to a row would read as a bug.
func noVerdictBody(in diarySymbolInput) string {
	if !readingIsDecidable(in.Reading) {
		return "读数不足，未作判断。"
	}
	return "本次未得出结论。"
}

// readingIsDecidable reports whether the five readings support a judgement.
//
// MA20 and volume_ratio are both required because they are the two a user is
// most likely to have set a threshold on (see the condition fields), so a
// verdict without them would be answering a question the user never asked.
// A nil change_pct is a legitimately young listing and does not block a
// verdict on the other three.
func readingIsDecidable(r watchcond.Reading) bool {
	return r.Close != nil && r.MA20 != nil && r.VolumeRatio != nil
}

// readingsJSON is the persisted evidence snapshot.
func readingsJSON(r watchcond.Reading) string {
	b, err := json.Marshal(r)
	if err != nil {
		return ""
	}
	return string(b)
}

// priorStances loads the recent verdicts for one symbol, as context.
func (s *StockWatchDiaryService) priorStances(
	ctx context.Context, scope types.StockWatchScope, thscode string, before types.DateOnly,
) []diaryPriorStance {
	rows, err := s.repo.RecentBefore(ctx, scope.UserID, scope.TenantID, thscode, before, diaryHistoryDays)
	if err != nil {
		// Missing history degrades the prompt, it does not invalidate it.
		logger.Warnf(ctx, "[WatchlistDiary] %s: load prior stances: %v", thscode, err)
		return nil
	}
	out := make([]diaryPriorStance, 0, len(rows))
	for _, r := range rows {
		if r == nil {
			continue
		}
		stance := diaryPriorStance{
			TradeDate: r.TradeDate.String(),
			Verdict:   r.Verdict,
		}
		if r.Readings != "" {
			var reading watchcond.Reading
			if err := json.Unmarshal([]byte(r.Readings), &reading); err == nil && reading.Close != nil {
				stance.Close = fmt.Sprintf("%.2f", *reading.Close)
			}
		}
		out = append(out, stance)
	}
	return out
}

// callDiaryModel makes one model call and parses the answer.
//
// Temperature 0 and Thinking false for the reasons memory/extract.go records:
// a reasoning model spends the whole completion budget deliberating and
// returns an empty string, which here would silently produce zero diaries.
func (s *StockWatchDiaryService) callDiaryModel(
	ctx context.Context, model chat.Chat, inputs []diarySymbolInput,
) (*diaryBatchResponse, error) {
	prompt := buildDiaryUserPrompt(inputs)
	thinking := false
	messages := []chat.Message{
		{Role: "system", Content: diarySystemPrompt},
		{Role: "user", Content: prompt},
	}
	response, err := model.Chat(ctx, messages, &chat.ChatOptions{
		Temperature:         0,
		MaxCompletionTokens: diaryCompletionTokens,
		Thinking:            &thinking,
		Format:              diaryJSONSchema,
	})
	if err != nil {
		return nil, fmt.Errorf("diary model call: %w", err)
	}
	if isDiaryTruncated(response) {
		logger.Warnf(ctx, "[WatchlistDiary] response hit the token ceiling, retrying with %d tokens",
			diaryRetryTokens)
		response, err = model.Chat(ctx, messages, &chat.ChatOptions{
			Temperature:         0,
			MaxCompletionTokens: diaryRetryTokens,
			Thinking:            &thinking,
			Format:              diaryJSONSchema,
		})
		if err != nil {
			return nil, fmt.Errorf("diary model retry: %w", err)
		}
	}
	return parseDiaryResponse(response)
}

// isDiaryTruncated reports whether a response ran out of room before saying
// anything usable.
//
// An empty body counts as truncation even without a finish reason, because
// some providers report neither — and an empty string is exactly what a
// reasoning model returns after spending the budget thinking.
func isDiaryTruncated(response *types.ChatResponse) bool {
	if response == nil {
		return true
	}
	if strings.TrimSpace(response.Content) == "" {
		return true
	}
	return response.FinishReason == "length"
}

// parseDiaryResponse tolerates the wrappers models put around JSON: a fenced
// code block, and prose before or after the object.
//
// Every one of these is observed in practice, and the alternative — treating
// a well-formed answer wrapped in a fence as a failure — would throw away
// good output over formatting the model was never told it could not use.
func parseDiaryResponse(response *types.ChatResponse) (*diaryBatchResponse, error) {
	if response == nil {
		return nil, errors.New("no response")
	}
	content := strings.TrimSpace(response.Content)
	if content == "" {
		return nil, errors.New("empty response")
	}
	if fence := strings.Index(content, "```"); fence >= 0 {
		rest := content[fence+3:]
		if newline := strings.Index(rest, "\n"); newline >= 0 {
			rest = rest[newline+1:]
		}
		if end := strings.Index(rest, "```"); end >= 0 {
			rest = rest[:end]
		}
		content = strings.TrimSpace(rest)
	}
	start := strings.Index(content, "{")
	end := strings.LastIndex(content, "}")
	if start < 0 || end <= start {
		return nil, errors.New("no JSON object in response")
	}
	var parsed diaryBatchResponse
	if err := json.Unmarshal([]byte(content[start:end+1]), &parsed); err != nil {
		return nil, fmt.Errorf("parse diary response: %w", err)
	}
	return &parsed, nil
}

// buildDiaryUserPrompt renders the inputs as JSON.
//
// JSON rather than prose because these are five scalar readings per symbol and
// a templated sentence makes null and zero indistinguishable to the model —
// and "there is no reading" versus "the reading is zero" is precisely the
// distinction the `none` verdict depends on.
func buildDiaryUserPrompt(inputs []diarySymbolInput) string {
	b, err := json.Marshal(inputs)
	if err != nil {
		return "[]"
	}
	var sb strings.Builder
	sb.WriteString("以下是池子里的 ")
	sb.WriteString(fmt.Sprintf("%d", len(inputs)))
	sb.WriteString(" 只票。请为每一只写一条观察，thscode 必须原样回填。\n")
	sb.Write(b)
	return sb.String()
}

// resolveModelID picks the model that writes diaries for one scope.
//
// The chain mirrors memory/extract.go's workspaceChatModelID rather than
// inventing a new one, and for the same reason: without an explicitly
// configured model there is no record of which model the workspace wants used
// for background work, and silently picking one is only acceptable if it is
// visible afterwards — which is why the choice is logged and stamped onto
// every row.
//
// ctx MUST already carry the tenant. modelService.ListModels calls
// types.MustTenantIDFromContext, which panics rather than returning an error —
// so the job injects the scope's tenant into the context before reaching here.
func (s *StockWatchDiaryService) resolveModelID(ctx context.Context, scope types.StockWatchScope) (string, error) {
	if s.modelSvc == nil {
		return "", ErrStockWatchDiaryModelUnavailable
	}
	models, err := s.modelSvc.ListModels(ctx)
	if err != nil {
		return "", fmt.Errorf("%w: list models: %w", ErrStockWatchDiaryModelUnavailable, err)
	}
	for _, m := range models {
		if m == nil || m.Type != types.ModelTypeKnowledgeQA {
			continue
		}
		if m.Status != "" && m.Status != types.ModelStatusActive {
			continue
		}
		logger.Infof(ctx, "[WatchlistDiary] no diary model configured, using workspace model %s", m.ID)
		return m.ID, nil
	}
	return "", ErrStockWatchDiaryModelUnavailable
}

// ListBySymbol reads one symbol's diary history for the drawer.
func (s *StockWatchDiaryService) ListBySymbol(
	ctx context.Context, userID string, tenantID uint64, thscode string, limit int,
) ([]*types.StockWatchDiary, error) {
	if limit > types.MaxStockWatchDiaryLimit {
		limit = types.MaxStockWatchDiaryLimit
	}
	return s.repo.ListBySymbol(ctx, userID, tenantID, thscode, limit)
}

// Get returns one diary, or (nil, nil) when that trading day has none.
func (s *StockWatchDiaryService) Get(
	ctx context.Context, userID string, tenantID uint64, thscode string, tradeDate types.DateOnly,
) (*types.StockWatchDiary, error) {
	return s.repo.Get(ctx, userID, tenantID, thscode, tradeDate)
}

// TopRanked returns the top N scored diaries for a given trading day,
// enriched with stock names from the user's watch list.
func (s *StockWatchDiaryService) TopRanked(
	ctx context.Context, userID string, tenantID uint64, tradeDate types.DateOnly, limit int,
) ([]*types.StockWatchDiary, error) {
	list, err := s.repo.TopRanked(ctx, userID, tenantID, tradeDate, limit)
	if err != nil {
		return nil, err
	}
	if len(list) > 0 && s.watches != nil {
		watched, err := s.watches.List(ctx, userID, tenantID)
		if err == nil {
			names := make(map[string]string, len(watched))
			for _, w := range watched {
				if w != nil {
					names[w.THSCode] = w.Name
				}
			}
			for _, d := range list {
				if d != nil && d.Name == "" {
					d.Name = names[d.THSCode]
				}
			}
		}
	}
	return list, nil
}

// RecordVerdictIgnored writes the event that says the user saw a verdict and
// declined it.
//
// This is the only place in the diary feature that records a NON-action, and it
// is the reason it exists. Without it, "the model says buy every morning and
// the human never acts" is indistinguishable in the data from "the model says
// nothing", and a prompt that is systematically wrong looks identical to a
// prompt that is being correctly ignored. A decline is evidence; discarding it
// is throwing away the only signal that would tell you the model is wrong.
func (s *StockWatchDiaryService) RecordVerdictIgnored(
	ctx context.Context, userID string, tenantID uint64, thscode string,
	tradeDate types.DateOnly, verdict string,
) error {
	note := "忽略 " + tradeDate.String() + " 的观察建议：" + verdict
	return s.watches.RecordEvent(ctx, &types.StockWatchEvent{
		UserID:   userID,
		TenantID: tenantID,
		Kind:     types.StockWatchEventVerdictIgnored,
		THSCode:  thscode,
		Note:     note,
		EvalDate: &tradeDate,
	})
}
