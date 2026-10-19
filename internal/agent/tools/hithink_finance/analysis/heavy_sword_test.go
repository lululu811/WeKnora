package analysis

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestAnalyzeDualAbove5(t *testing.T) {
	tool := &VolumeAnalysisTool{}

	// Case 1: insufficient rows
	res := tool.analyzeDualAbove5([]marketRow{{Close: 10, Vol: 100}})
	assert.Equal(t, "unknown", res["state"])

	// Case 2: BOTH_UP_5
	rows := []marketRow{
		{Close: 11.0, Open: 10.0, Vol: 1500, MA5: 10.0},
		{Close: 10.0, Open: 10.0, Vol: 1000, MA5: 10.0},
		{Close: 10.0, Open: 10.0, Vol: 1000, MA5: 10.0},
		{Close: 10.0, Open: 10.0, Vol: 1000, MA5: 10.0},
		{Close: 10.0, Open: 10.0, Vol: 1000, MA5: 10.0},
	}
	res = tool.analyzeDualAbove5(rows)
	assert.Equal(t, "BOTH_UP_5", res["state"])
	assert.Equal(t, "双上五", res["label"])
	assert.Equal(t, "BUY_OR_HOLD", res["action"])

	// Case 3: YIN_VOL_UP_5 (阴线且放量)
	rows[0] = marketRow{Close: 9.5, Open: 10.5, Vol: 1500, MA5: 10.0}
	res = tool.analyzeDualAbove5(rows)
	assert.Equal(t, "YIN_VOL_UP_5", res["state"])
	assert.Equal(t, "阴量上五", res["label"])
	assert.Equal(t, "PROBE_BUY_LEFT", res["action"])

	// Case 4: BOTH_DOWN_5 (量价齐破)
	rows[0] = marketRow{Close: 9.0, Open: 9.5, Vol: 500, MA5: 10.0}
	res = tool.analyzeDualAbove5(rows)
	assert.Equal(t, "BOTH_DOWN_5", res["state"])
	assert.Equal(t, "双下五", res["label"])
	assert.Equal(t, "EXIT_DEFENSE", res["action"])
}

func TestAnalyzeMA518System(t *testing.T) {
	// Case 1: insufficient rows
	res := analyzeMA518System([]marketRow{{Close: 10}})
	assert.Equal(t, "unknown", res["status"])

	// Case 2: 25 rows, uptrend
	rows := make([]marketRow, 25)
	for i := 0; i < 25; i++ {
		// Newest first (i=0 is highest close)
		rows[i] = marketRow{
			Date:  "2026-05-01",
			Close: 25.0 - float64(i)*0.5,
		}
	}
	res = analyzeMA518System(rows)
	require.NotNil(t, res["ma5"])
	require.NotNil(t, res["ma18"])
	assert.True(t, res["is_golden"].(bool))
	assert.Contains(t, res["phase_semantic"].(string), "金叉")

	// Case 3: death cross with warning
	deathRows := make([]marketRow, 25)
	for i := 0; i < 25; i++ {
		// Newest first (i=0 is lowest close)
		deathRows[i] = marketRow{
			Date:  "2026-05-01",
			Close: 10.0 + float64(i)*0.8,
		}
	}
	resDeath := analyzeMA518System(deathRows)
	assert.False(t, resDeath["is_golden"].(bool))
	assert.NotEmpty(t, resDeath["risk_warning"])
	assert.Contains(t, resDeath["risk_warning"].(string), "严禁抄底")
}
