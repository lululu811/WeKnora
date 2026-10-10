package service

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestComputeFinalScore_AllPerfect(t *testing.T) {
	s := gradingScores{
		Q1: 100, Q2: 100, Q3: 100, Q4: 100,
		Q5: 100, Q6: 100, Q7: 100, Q8: 100,
		Q9: 100, Q10: 100, Q11: 100, Q12: 100,
		Q13: 100, Q14: 100, Q15: 100,
		Q16: 100, Q17: 100, Q18: 100,
	}
	assert.InDelta(t, 100.0, computeFinalScore(s), 0.01)
}

func TestComputeFinalScore_AllZero(t *testing.T) {
	s := gradingScores{}
	assert.InDelta(t, 0.0, computeFinalScore(s), 0.01)
}

func TestComputeFinalScore_Weights(t *testing.T) {
	// Tech+fund flow (Q1-Q12) all 80, fundamental (Q13-Q15) all 60,
	// LLM judge (Q16-Q18) all 40.
	// Expected: 80*0.50 + 60*0.25 + 40*0.25 = 40 + 15 + 10 = 65
	s := gradingScores{
		Q1: 80, Q2: 80, Q3: 80, Q4: 80,
		Q5: 80, Q6: 80, Q7: 80, Q8: 80,
		Q9: 80, Q10: 80, Q11: 80, Q12: 80,
		Q13: 60, Q14: 60, Q15: 60,
		Q16: 40, Q17: 40, Q18: 40,
	}
	assert.InDelta(t, 65.0, computeFinalScore(s), 0.01)
}

func TestComputeFinalScore_MixedDimensions(t *testing.T) {
	// Tech+flow avg = 90, fundamental avg = 50, LLM avg = 70
	// Expected: 90*0.50 + 50*0.25 + 70*0.25 = 45 + 12.5 + 17.5 = 75
	s := gradingScores{
		Q1: 90, Q2: 90, Q3: 90, Q4: 90,
		Q5: 90, Q6: 90, Q7: 90, Q8: 90,
		Q9: 90, Q10: 90, Q11: 90, Q12: 90,
		Q13: 50, Q14: 50, Q15: 50,
		Q16: 70, Q17: 70, Q18: 70,
	}
	assert.InDelta(t, 75.0, computeFinalScore(s), 0.01)
}

func TestAvgInt(t *testing.T) {
	assert.InDelta(t, 50.0, avgInt(50), 0.01)
	assert.InDelta(t, 75.0, avgInt(50, 100), 0.01)
	assert.InDelta(t, 60.0, avgInt(40, 60, 80), 0.01)
	assert.InDelta(t, 0.0, avgInt(), 0.01)
}
