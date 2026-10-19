package market

import (
	"encoding/json"
	"testing"
)

func TestDividendCalendarToolMetadata(t *testing.T) {
	tool := NewDividendCalendarTool(nil)
	if tool.Name() != "hithink.finance.market.dividend.calendar" {
		t.Errorf("unexpected name: %s", tool.Name())
	}
	if len(tool.Description()) == 0 {
		t.Errorf("empty description")
	}

	params := tool.Parameters()
	var schema map[string]interface{}
	if err := json.Unmarshal(params, &schema); err != nil {
		t.Fatalf("parameters is not valid JSON: %v", err)
	}

	props, ok := schema["properties"].(map[string]interface{})
	if !ok {
		t.Fatalf("expected properties in schema")
	}

	for _, field := range []string{"thscode", "start_date", "end_date", "min_dividend", "min_bonus", "limit"} {
		if _, exists := props[field]; !exists {
			t.Errorf("missing parameter field: %s", field)
		}
	}
}

func TestDividendYieldScreenerToolMetadata(t *testing.T) {
	tool := NewDividendYieldScreenerTool(nil)
	if tool.Name() != "hithink.finance.market.dividend.yield_screener" {
		t.Errorf("unexpected name: %s", tool.Name())
	}
	if len(tool.Description()) == 0 {
		t.Errorf("empty description")
	}

	params := tool.Parameters()
	var schema map[string]interface{}
	if err := json.Unmarshal(params, &schema); err != nil {
		t.Fatalf("parameters is not valid JSON: %v", err)
	}

	props, ok := schema["properties"].(map[string]interface{})
	if !ok {
		t.Fatalf("expected properties in schema")
	}

	for _, field := range []string{"year", "min_yield_pct", "limit"} {
		if _, exists := props[field]; !exists {
			t.Errorf("missing parameter field: %s", field)
		}
	}
}
