package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/Tencent/WeKnora/internal/industry"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/Tencent/WeKnora/internal/watchcond"
)

// StockWatchGradingService scores every diary written for one trading day on
// 18 questions (Q1-Q18) and writes the aggregate back as final_score + rank.
//
// The grading job runs AFTER the diary job: the diary writes verdicts and
// readings; the grading job reads those rows back, asks the model to score
// 18 dimensions, computes a weighted aggregate, and updates the same rows
// with final_score, rank and the per-question JSON. The two jobs never write
// the same column, so they cannot clobber each other even on a same-day rerun.
type StockWatchGradingService struct {
	repo     interfaces.StockWatchDiaryRepository
	modelSvc interfaces.ModelService
	industry industry.Fetcher // nil -> fallback to plain chunking
}

// NewStockWatchGradingService wires the grading writer.
func NewStockWatchGradingService(
	repo interfaces.StockWatchDiaryRepository,
	modelSvc interfaces.ModelService,
	ind industry.Fetcher,
) *StockWatchGradingService {
	return &StockWatchGradingService{repo: repo, modelSvc: modelSvc, industry: ind}
}

// gradingMaxSymbolsPerCall bounds one model call. Scoring 18 questions per
// symbol is heavier than the diary's single verdict, so the chunk is smaller
// (20 vs the diary's 40) to keep the prompt inside the model's working set.
const gradingMaxSymbolsPerCall = 20

// gradingCompletionTokens is the first budget. 18 scores x 20 symbols with
// short reasons fits in 8k; the retry doubles it.
const (
	gradingCompletionTokens = 8192
	gradingRetryTokens      = 16384
)

// ErrStockWatchGradingModelUnavailable mirrors the diary's sentinel.
var ErrStockWatchGradingModelUnavailable = errors.New("no chat model available for the watchlist grading")

// gradingSymbolInput is one symbol as the grading model sees it.
//
// It carries everything the diary had (reading + note + recent verdicts) plus
// the diary's own verdict and reasons, so the grading model can see "the
// observation job said hold with confidence 2 because X" and score Q1-Q18 in
// that light rather than re-deriving the observation from scratch.
type gradingSymbolInput struct {
	THSCode       string            `json:"thscode"`
	Name          string            `json:"name"`
	State         string            `json:"state"`
	Note          string            `json:"note"`
	Reading       watchcond.Reading `json:"reading"`
	RecentVerdict string            `json:"recent_verdict"`
	RecentReasons string            `json:"recent_reasons"`
}

// gradingResult is the model's answer for one symbol.
type gradingResult struct {
	THSCode string         `json:"thscode"`
	Scores  gradingScores  `json:"scores"`
	Reasons gradingReasons `json:"reasons"`
}

// gradingScores holds the 18 question scores. Each is 0-100.
type gradingScores struct {
	Q1  int `json:"q1"`
	Q2  int `json:"q2"`
	Q3  int `json:"q3"`
	Q4  int `json:"q4"`
	Q5  int `json:"q5"`
	Q6  int `json:"q6"`
	Q7  int `json:"q7"`
	Q8  int `json:"q8"`
	Q9  int `json:"q9"`
	Q10 int `json:"q10"`
	Q11 int `json:"q11"`
	Q12 int `json:"q12"`
	Q13 int `json:"q13"`
	Q14 int `json:"q14"`
	Q15 int `json:"q15"`
	Q16 int `json:"q16"`
	Q17 int `json:"q17"`
	Q18 int `json:"q18"`
}

// gradingReasons holds one short sentence per question.
type gradingReasons struct {
	Q1  string `json:"q1"`
	Q2  string `json:"q2"`
	Q3  string `json:"q3"`
	Q4  string `json:"q4"`
	Q5  string `json:"q5"`
	Q6  string `json:"q6"`
	Q7  string `json:"q7"`
	Q8  string `json:"q8"`
	Q9  string `json:"q9"`
	Q10 string `json:"q10"`
	Q11 string `json:"q11"`
	Q12 string `json:"q12"`
	Q13 string `json:"q13"`
	Q14 string `json:"q14"`
	Q15 string `json:"q15"`
	Q16 string `json:"q16"`
	Q17 string `json:"q17"`
	Q18 string `json:"q18"`
}

// gradingBatchResponse is the whole call's answer.
type gradingBatchResponse struct {
	Entries []gradingResult `json:"entries"`
}

// gradingSystemPrompt fixes the grading contract with the model.
//
// The 18 questions span four dimensions. The model scores each 0-100 based on
// the available evidence (5 readings + note + recent diary). When evidence is
// insufficient for a dimension, the model should score conservatively (40-50)
// rather than inventing confidence it does not have.
const gradingSystemPrompt = `你在为一个股票观察池做每日评分。每只票你看到：收盘价、涨跌幅、量比、20日均线、关注理由、最近一次观察日记的结论。

对每只票回答 18 个问题，每个问题打 0-100 分。

技术面（Q1-Q8）：
Q1 趋势强度：价格相对 MA20 的方向和偏离幅度
Q2 短期动量：今日涨跌幅的方向和持续性
Q3 量能配合：量比是否支持当前价格走势
Q4 技术形态：综合价格和均线关系的短期形态判断
Q5 趋势稳定：近期价格沿同一方向运动的程度
Q6 量价协调：放量涨/缩量跌的健康度
Q7 均线支撑：MA20 对价格的支撑或压制力度
Q8 技术综合：整体技术面健康度

资金面（Q9-Q12）：
Q9 资金方向：是否有资金持续流入/流出的迹象
Q10 量价关系：量价配合的合理性
Q11 交易活跃：成交量是否在合理范围
Q12 资金综合：资金面整体评价

基本面（Q13-Q15）：
Q13 入池理由：用户当初关注的理由是否仍然成立
Q14 质地：公司基本质地（基于可推断信息）
Q15 变化：近期是否有可观察的基本面变化

综合判断（Q16-Q18）：
Q16 行业风险：所在行业当前面临的风险
Q17 护城河：竞争优势的可持续性
Q18 估值：当前价格相对内在价值的合理性

规则：
- 如果某个维度缺乏证据，给 40-50 分（中性），不要编造信心
- 每个 reason 控制在一句话以内
- entries 必须恰好包含输入中的每一只票
- 只输出 JSON，不要解释文字

输出格式：
{"entries":[{"thscode":"600519.SH","scores":{"q1":75,"q2":60,...,"q18":80},"reasons":{"q1":"价格站上MA20偏离3%","q2":"...","q18":"..."}}]}`

// gradingJSONSchema is passed as ChatOptions.Format.
var gradingJSONSchema = json.RawMessage(`{
  "type": "object",
  "properties": {
    "entries": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "thscode": {"type": "string"},
          "scores": {
            "type": "object",
            "properties": {
              "q1": {"type": "integer"}, "q2": {"type": "integer"},
              "q3": {"type": "integer"}, "q4": {"type": "integer"},
              "q5": {"type": "integer"}, "q6": {"type": "integer"},
              "q7": {"type": "integer"}, "q8": {"type": "integer"},
              "q9": {"type": "integer"}, "q10": {"type": "integer"},
              "q11": {"type": "integer"}, "q12": {"type": "integer"},
              "q13": {"type": "integer"}, "q14": {"type": "integer"},
              "q15": {"type": "integer"}, "q16": {"type": "integer"},
              "q17": {"type": "integer"}, "q18": {"type": "integer"}
            },
            "required": ["q1","q2","q3","q4","q5","q6","q7","q8",
                         "q9","q10","q11","q12","q13","q14","q15",
                         "q16","q17","q18"]
          },
          "reasons": {
            "type": "object",
            "properties": {
              "q1": {"type": "string"}, "q2": {"type": "string"},
              "q3": {"type": "string"}, "q4": {"type": "string"},
              "q5": {"type": "string"}, "q6": {"type": "string"},
              "q7": {"type": "string"}, "q8": {"type": "string"},
              "q9": {"type": "string"}, "q10": {"type": "string"},
              "q11": {"type": "string"}, "q12": {"type": "string"},
              "q13": {"type": "string"}, "q14": {"type": "string"},
              "q15": {"type": "string"}, "q16": {"type": "string"},
              "q17": {"type": "string"}, "q18": {"type": "string"}
            }
          }
        },
        "required": ["thscode", "scores"]
      }
    }
  },
  "required": ["entries"]
}`)

// RunGrading scores diaries for one scope's trading day and writes final_score
// + rank back. Called from the grading job after the diary job has committed.
//
// The flow:
//  1. Load today's diaries for this scope (written by the diary job moments ago).
//  2. Chunk into gradingMaxSymbolsPerCall and call the model.
//  3. Compute final_score per symbol using the weighted formula.
//  4. Rank all symbols by final_score DESC.
//  5. Batch-update diaries with final_score, rank and scores JSON.
func (s *StockWatchGradingService) RunGrading(
	ctx context.Context, scope types.StockWatchScope,
	readings map[string]watchcond.Reading, tradeDate types.DateOnly,
) error {
	// Step 1: load today's diaries.
	diaries, err := s.repo.ListByTradeDate(ctx, scope.UserID, scope.TenantID, tradeDate)
	if err != nil {
		return fmt.Errorf("[WatchlistGrading] list diaries: %w", err)
	}
	if len(diaries) == 0 {
		return nil
	}

	// Load watched symbol metadata (name, state, note) so the grading prompt has
	// the necessary context for Q13 ("入池理由是否仍然成立") and company identification.
	watched, _ := s.repo.ListWatchedByScope(ctx, scope.UserID, scope.TenantID)
	watchMap := make(map[string]*types.StockWatch, len(watched))
	for _, w := range watched {
		if w != nil {
			watchMap[w.THSCode] = w
		}
	}

	// Build grading inputs from diaries + readings.
	inputs := make([]gradingSymbolInput, 0, len(diaries))
	for _, d := range diaries {
		if d == nil {
			continue
		}
		reading, ok := readings[d.THSCode]
		if !ok {
			continue
		}
		in := gradingSymbolInput{
			THSCode: d.THSCode,
			Reading: reading,
		}
		if w, ok := watchMap[d.THSCode]; ok {
			in.Name = w.Name
			in.State = w.State
			in.Note = w.Note
		}
		in.RecentVerdict = d.Verdict
		in.RecentReasons = d.Reasons
		inputs = append(inputs, in)
	}
	if len(inputs) == 0 {
		return nil
	}

	// Resolve model.
	modelID, err := s.resolveModelID(ctx, scope)
	if err != nil {
		return err
	}
	model, err := s.modelSvc.GetChatModel(ctx, modelID)
	if err != nil {
		return fmt.Errorf("%w: get grading model %q: %w",
			ErrStockWatchGradingModelUnavailable, modelID, err)
	}

	// Step 2: group by industry (if available) or chunk, then call.
	groups := s.groupInputs(inputs)
	scored := make(map[string]gradingResult, len(inputs))
	for _, chunk := range groups {
		parsed, err := s.callGradingModel(ctx, model, chunk)
		if err != nil {
			logger.Warnf(ctx, "[WatchlistGrading] model call failed for %d symbol(s): %v",
				len(chunk), err)
			continue
		}
		for _, e := range parsed.Entries {
			scored[e.THSCode] = e
		}
	}

	// Step 3: compute final_score per symbol.
	type scoreRow struct {
		thscode    string
		finalScore float64
		scoresJSON string
	}
	rows := make([]scoreRow, 0, len(inputs))
	for _, in := range inputs {
		result, ok := scored[in.THSCode]
		if !ok {
			continue
		}
		fs := computeFinalScore(result.Scores)
		sj := marshalScoresJSON(result)
		rows = append(rows, scoreRow{
			thscode:    in.THSCode,
			finalScore: fs,
			scoresJSON: sj,
		})
	}

	// Step 4: rank by final_score DESC.
	sort.Slice(rows, func(i, j int) bool {
		return rows[i].finalScore > rows[j].finalScore
	})

	// Step 5: build diary updates.
	updates := make([]*types.StockWatchDiary, 0, len(rows))
	for i, r := range rows {
		rank := i + 1
		for _, d := range diaries {
			if d != nil && d.THSCode == r.thscode {
				d.FinalScore = new(math.Round(r.finalScore*100) / 100)
				d.Rank = new(rank)
				d.Scores = r.scoresJSON
				updates = append(updates, d)
				break
			}
		}
	}

	if err := s.repo.UpdateScores(ctx, updates); err != nil {
		return fmt.Errorf("[WatchlistGrading] update scores: %w", err)
	}
	logger.Infof(ctx, "[WatchlistGrading] scored %d symbols for %s/%d",
		len(updates), scope.UserID, scope.TenantID)
	return nil
}

// groupInputs splits inputs into industry-based groups when the industry
// fetcher is available, falling back to fixed-size chunks otherwise.
//
// Industry grouping reduces LLM calls: ~300 industry groups vs ~500 chunks
// of 20. Each group's prompt includes the industry name so the model can
// score Q16 (industry risk) with shared context rather than repeating it.
func (s *StockWatchGradingService) groupInputs(inputs []gradingSymbolInput) [][]gradingSymbolInput {
	if s.industry == nil || len(inputs) == 0 {
		return chunkInputs(inputs, gradingMaxSymbolsPerCall)
	}
	thscodes := make([]string, len(inputs))
	for i, in := range inputs {
		thscodes[i] = in.THSCode
	}
	industries, err := s.industry.Fetch(context.Background(), thscodes)
	if err != nil {
		logger.Warnf(context.Background(), "[WatchlistGrading] industry fetch failed, falling back to chunks: %v", err)
		return chunkInputs(inputs, gradingMaxSymbolsPerCall)
	}
	groups := make(map[string][]gradingSymbolInput)
	var order []string
	for _, in := range inputs {
		ind := industries[in.THSCode]
		key := ind.Level2
		if key == "" {
			key = ind.Level1
		}
		if key == "" {
			key = "_unknown"
		}
		if _, exists := groups[key]; !exists {
			order = append(order, key)
		}
		groups[key] = append(groups[key], in)
	}
	var result [][]gradingSymbolInput
	for _, key := range order {
		g := groups[key]
		result = append(result, chunkInputs(g, gradingMaxSymbolsPerCall)...)
	}
	return result
}

func chunkInputs(inputs []gradingSymbolInput, size int) [][]gradingSymbolInput {
	if len(inputs) == 0 {
		return nil
	}
	var out [][]gradingSymbolInput
	for start := 0; start < len(inputs); start += size {
		end := min(start+size, len(inputs))
		out = append(out, inputs[start:end])
	}
	return out
}

// computeFinalScore applies the weighted formula:
//
//	技术+资金 (Q1-Q12 avg) x 50%
//	+ 基本面 (Q13-Q15 avg) x 25%
//	+ LLM判断 (Q16-Q18 avg) x 25%
//
// Each sub-average is 0-100. The result is 0-100.
func computeFinalScore(s gradingScores) float64 {
	techFundFlow := avgInt(
		s.Q1, s.Q2, s.Q3, s.Q4, s.Q5, s.Q6, s.Q7, s.Q8,
		s.Q9, s.Q10, s.Q11, s.Q12,
	)
	fundamental := avgInt(s.Q13, s.Q14, s.Q15)
	llmJudge := avgInt(s.Q16, s.Q17, s.Q18)
	return techFundFlow*0.50 + fundamental*0.25 + llmJudge*0.25
}

func avgInt(vs ...int) float64 {
	if len(vs) == 0 {
		return 0
	}
	sum := 0
	for _, v := range vs {
		sum += v
	}
	return float64(sum) / float64(len(vs))
}

// marshalScoresJSON produces the JSON stored in the scores column.
func marshalScoresJSON(r gradingResult) string {
	merged := map[string]any{
		"scores":  r.Scores,
		"reasons": r.Reasons,
	}
	b, err := json.Marshal(merged)
	if err != nil {
		return ""
	}
	return string(b)
}

// callGradingModel makes one model call and parses the answer.
func (s *StockWatchGradingService) callGradingModel(
	ctx context.Context, model chat.Chat, inputs []gradingSymbolInput,
) (*gradingBatchResponse, error) {
	prompt := buildGradingUserPrompt(inputs)
	thinking := false
	messages := []chat.Message{
		{Role: "system", Content: gradingSystemPrompt},
		{Role: "user", Content: prompt},
	}
	response, err := model.Chat(ctx, messages, &chat.ChatOptions{
		Temperature:         0,
		MaxCompletionTokens: gradingCompletionTokens,
		Thinking:            &thinking,
		Format:              gradingJSONSchema,
	})
	if err != nil {
		return nil, fmt.Errorf("grading model call: %w", err)
	}
	if isGradingTruncated(response) {
		logger.Warnf(ctx, "[WatchlistGrading] response truncated, retrying with %d tokens",
			gradingRetryTokens)
		response, err = model.Chat(ctx, messages, &chat.ChatOptions{
			Temperature:         0,
			MaxCompletionTokens: gradingRetryTokens,
			Thinking:            &thinking,
			Format:              gradingJSONSchema,
		})
		if err != nil {
			return nil, fmt.Errorf("grading model retry: %w", err)
		}
	}
	return parseGradingResponse(response)
}

func isGradingTruncated(response *types.ChatResponse) bool {
	if response == nil {
		return true
	}
	if strings.TrimSpace(response.Content) == "" {
		return true
	}
	return response.FinishReason == "length"
}

func parseGradingResponse(response *types.ChatResponse) (*gradingBatchResponse, error) {
	if response == nil {
		return nil, errors.New("no response")
	}
	content := strings.TrimSpace(response.Content)
	if content == "" {
		return nil, errors.New("empty response")
	}
	// Strip code fences.
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
		return nil, errors.New("no JSON object in grading response")
	}
	var parsed gradingBatchResponse
	if err := json.Unmarshal([]byte(content[start:end+1]), &parsed); err != nil {
		return nil, fmt.Errorf("parse grading response: %w", err)
	}
	return &parsed, nil
}

func buildGradingUserPrompt(inputs []gradingSymbolInput) string {
	b, err := json.Marshal(inputs)
	if err != nil {
		return "[]"
	}
	var sb strings.Builder
	sb.WriteString("以下是需要评分的 ")
	sb.WriteString(fmt.Sprintf("%d", len(inputs)))
	sb.WriteString(" 只票。请为每一只打出 Q1-Q18 的分数，thscode 必须原样回填。\n")
	sb.Write(b)
	return sb.String()
}

// resolveModelID picks the model for grading. Mirrors the diary's resolver.
func (s *StockWatchGradingService) resolveModelID(
	ctx context.Context, scope types.StockWatchScope,
) (string, error) {
	if s.modelSvc == nil {
		return "", ErrStockWatchGradingModelUnavailable
	}
	models, err := s.modelSvc.ListModels(ctx)
	if err != nil {
		return "", fmt.Errorf("%w: list models: %w",
			ErrStockWatchGradingModelUnavailable, err)
	}
	for _, m := range models {
		if m == nil || m.Type != types.ModelTypeKnowledgeQA {
			continue
		}
		if m.Status != "" && m.Status != types.ModelStatusActive {
			continue
		}
		logger.Infof(ctx, "[WatchlistGrading] using workspace model %s", m.ID)
		return m.ID, nil
	}
	return "", ErrStockWatchGradingModelUnavailable
}
