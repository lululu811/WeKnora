package service

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/industry"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/Tencent/WeKnora/internal/watchcond"
)

// TestGradingService_GroupInputs_ByIndustry verifies industry-based grouping.
func TestGradingService_GroupInputs_ByIndustry(t *testing.T) {
	svc := &StockWatchGradingService{
		industry: &fakeIndustryFetcher{
			data: map[string]industry.Industry{
				"600519.SH": {Level1: "食品饮料", Level2: "白酒"},
				"000858.SZ": {Level1: "食品饮料", Level2: "白酒"},
				"601318.SH": {Level1: "非银金融", Level2: "保险"},
				"600036.SH": {Level1: "银行", Level2: "股份制银行"},
			},
		},
	}

	inputs := []gradingSymbolInput{
		{THSCode: "600519.SH"},
		{THSCode: "000858.SZ"},
		{THSCode: "601318.SH"},
		{THSCode: "600036.SH"},
	}

	groups := svc.groupInputs(inputs)

	// 3 groups: 白酒(2), 保险(1), 股份制银行(1)
	assert.Len(t, groups, 3)
	assert.Len(t, groups[0], 2)
	assert.Equal(t, "600519.SH", groups[0][0].THSCode)
	assert.Equal(t, "000858.SZ", groups[0][1].THSCode)
	assert.Len(t, groups[1], 1)
	assert.Equal(t, "601318.SH", groups[1][0].THSCode)
	assert.Len(t, groups[2], 1)
	assert.Equal(t, "600036.SH", groups[2][0].THSCode)
}

// TestGradingService_GroupInputs_Fallback verifies fallback to chunking.
func TestGradingService_GroupInputs_Fallback(t *testing.T) {
	svc := &StockWatchGradingService{
		industry: &fakeIndustryFetcher{err: assert.AnError},
	}

	inputs := make([]gradingSymbolInput, 50)
	for i := range inputs {
		inputs[i].THSCode = "000000.SH"
	}

	groups := svc.groupInputs(inputs)
	assert.Len(t, groups, 3)
	assert.Len(t, groups[0], 20)
	assert.Len(t, groups[1], 20)
	assert.Len(t, groups[2], 10)
}

// TestGradingService_GroupInputs_NilFetcher verifies plain chunking.
func TestGradingService_GroupInputs_NilFetcher(t *testing.T) {
	svc := &StockWatchGradingService{industry: nil}

	inputs := make([]gradingSymbolInput, 45)
	for i := range inputs {
		inputs[i].THSCode = "000000.SH"
	}

	groups := svc.groupInputs(inputs)
	assert.Len(t, groups, 3)
	assert.Len(t, groups[0], 20)
	assert.Len(t, groups[1], 20)
	assert.Len(t, groups[2], 5)
}

// TestGradingService_MarshalScoresJSON verifies the stored JSON format.
func TestGradingService_MarshalScoresJSON(t *testing.T) {
	r := gradingResult{
		THSCode: "600519.SH",
		Scores: gradingScores{
			Q1: 80, Q2: 70, Q3: 60, Q4: 90,
			Q5: 75, Q6: 85, Q7: 65, Q8: 95,
			Q9: 70, Q10: 80, Q11: 75, Q12: 85,
			Q13: 90, Q14: 85, Q15: 80,
			Q16: 70, Q17: 75, Q18: 65,
		},
		Reasons: gradingReasons{
			Q1:  "价格站上MA20",
			Q16: "行业景气度下行",
		},
	}

	jsonStr := marshalScoresJSON(r)
	require.NotEmpty(t, jsonStr)

	var parsed map[string]json.RawMessage
	require.NoError(t, json.Unmarshal([]byte(jsonStr), &parsed))

	var scores gradingScores
	require.NoError(t, json.Unmarshal(parsed["scores"], &scores))
	assert.Equal(t, 80, scores.Q1)
	assert.Equal(t, 65, scores.Q18)

	var reasons gradingReasons
	require.NoError(t, json.Unmarshal(parsed["reasons"], &reasons))
	assert.Equal(t, "价格站上MA20", reasons.Q1)
}

// TestGradingService_RunGrading_EmptyDiaries verifies no-op on empty.
func TestGradingService_RunGrading_EmptyDiaries(t *testing.T) {
	svc := &StockWatchGradingService{
		repo: &fakeDiaryRepoForGrading{},
	}

	err := svc.RunGrading(
		context.Background(),
		types.StockWatchScope{UserID: "u1", TenantID: 1},
		map[string]watchcond.Reading{},
		types.DateOnly{},
	)
	assert.NoError(t, err)
}

// --- fakes ---

type fakeIndustryFetcher struct {
	data map[string]industry.Industry
	err  error
}

func (f *fakeIndustryFetcher) Fetch(_ context.Context, thscodes []string) (map[string]industry.Industry, error) {
	if f.err != nil {
		return nil, f.err
	}
	out := make(map[string]industry.Industry)
	for _, code := range thscodes {
		if e, ok := f.data[code]; ok {
			out[code] = e
		}
	}
	return out, nil
}

type fakeDiaryRepoForGrading struct {
	interfaces.StockWatchDiaryRepository // embed to satisfy interface
}

func (f *fakeDiaryRepoForGrading) ListByTradeDate(_ context.Context, _ string, _ uint64, _ types.DateOnly) ([]*types.StockWatchDiary, error) {
	return nil, nil
}
func (f *fakeDiaryRepoForGrading) UpdateScores(_ context.Context, _ []*types.StockWatchDiary) error {
	return nil
}
