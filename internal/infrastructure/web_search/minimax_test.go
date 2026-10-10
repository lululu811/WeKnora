package web_search

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
)

func TestMiniMaxProviderSearch(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s, want POST", r.Method)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer sk-test" {
			t.Fatalf("Authorization = %q", got)
		}
		var request miniMaxSearchRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.Query != "WeKnora" {
			t.Fatalf("unexpected query: %s", request.Query)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"organic": [
				{"title":"First","link":"https://example.com/1","snippet":"Snippet 1","date":"2026/09/24 13:21:40"},
				{"title":"Second","link":"https://example.com/2","snippet":"Snippet 2","date":""},
				{"title":"Third","link":"https://example.com/3","snippet":"Snippet 3","date":"invalid"}
			],
			"base_resp": {
				"status_code": 0,
				"status_msg": "success"
			}
		}`))
	}))
	defer server.Close()

	provider := &MiniMaxProvider{
		client:  server.Client(),
		baseURL: server.URL,
		apiKey:  "sk-test",
		region:  "cn",
	}

	results, err := provider.Search(context.Background(), " WeKnora ", 2, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 2 {
		t.Fatalf("len(results) = %d, want 2", len(results))
	}
	if results[0].Title != "First" || results[0].URL != "https://example.com/1" || results[0].Snippet != "Snippet 1" {
		t.Fatalf("unexpected first result: %+v", results[0])
	}
	if results[0].Source != "minimax" || results[0].PublishedAt == nil {
		t.Fatalf("expected minimax source and non-nil PublishedAt: %+v", results[0])
	}
	if results[0].Age != "2026/09/24 13:21:40" {
		t.Fatalf("unexpected Age: %s", results[0].Age)
	}
	if results[1].PublishedAt != nil {
		t.Fatalf("second result should have nil PublishedAt: %+v", results[1])
	}
}

func TestMiniMaxProviderSearchDates(t *testing.T) {
	tests := []struct {
		name        string
		dateStr     string
		includeDate bool
		wantParsed  bool
	}{
		{"slash datetime", "2026/09/24 13:21:40", true, true},
		{"slash date", "2026/09/24", true, true},
		{"dash datetime", "2026-09-24 13:21:40", true, true},
		{"dash date", "2026-09-24", true, true},
		{"rfc3339", "2026-09-24T13:21:40Z", true, true},
		{"invalid date", "not-a-date", true, false},
		{"empty date", "", true, false},
		{"includeDate false", "2026/09/24 13:21:40", false, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(miniMaxSearchResponse{
					Organic: []miniMaxOrganicItem{
						{Title: "T", Link: "https://example.com", Snippet: "S", Date: tt.dateStr},
					},
					BaseResp: &miniMaxBaseResp{StatusCode: 0, StatusMsg: "success"},
				})
			}))
			defer server.Close()

			p := &MiniMaxProvider{client: server.Client(), baseURL: server.URL, apiKey: "sk-test", region: "cn"}
			results, err := p.Search(context.Background(), "test", 1, tt.includeDate)
			if err != nil {
				t.Fatal(err)
			}
			if len(results) != 1 {
				t.Fatalf("len(results) = %d, want 1", len(results))
			}
			if tt.wantParsed && results[0].PublishedAt == nil {
				t.Fatalf("expected parsed date for %s", tt.dateStr)
			}
			if !tt.wantParsed && results[0].PublishedAt != nil {
				t.Fatalf("expected nil date for %s, got %v", tt.dateStr, results[0].PublishedAt)
			}
		})
	}
}

func TestMiniMaxProviderValidation(t *testing.T) {
	// Missing API key
	err := ValidateMiniMaxParameters(types.WebSearchProviderParameters{
		APIKey: "",
	})
	if err == nil || !strings.Contains(err.Error(), "API key is required") {
		t.Fatalf("expected API key required error, got %v", err)
	}

	// Valid default region
	err = ValidateMiniMaxParameters(types.WebSearchProviderParameters{
		APIKey: "sk-test",
	})
	if err != nil {
		t.Fatalf("valid parameters rejected: %v", err)
	}

	// Valid global region
	err = ValidateMiniMaxParameters(types.WebSearchProviderParameters{
		APIKey:      "sk-test",
		ExtraConfig: map[string]string{"region": "global"},
	})
	if err != nil {
		t.Fatalf("valid global region rejected: %v", err)
	}

	// Invalid region
	err = ValidateMiniMaxParameters(types.WebSearchProviderParameters{
		APIKey:      "sk-test",
		ExtraConfig: map[string]string{"region": "mars"},
	})
	if err == nil || !strings.Contains(err.Error(), "invalid MiniMax region") {
		t.Fatalf("expected invalid region error, got %v", err)
	}
}

func TestMiniMaxProviderSearchError(t *testing.T) {
	// Test HTTP 401 error
	server401 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"base_resp":{"status_code":1004,"status_msg":"invalid api key"}}`))
	}))
	defer server401.Close()

	p401 := &MiniMaxProvider{client: server401.Client(), baseURL: server401.URL, apiKey: "bad", region: "cn"}
	_, err := p401.Search(context.Background(), "test", 1, false)
	if err == nil || !strings.Contains(err.Error(), "invalid api key") {
		t.Fatalf("expected 401 error with message, got: %v", err)
	}

	// Test API level error in base_resp
	serverBaseErr := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"base_resp":{"status_code":2013,"status_msg":"insufficient balance"}}`))
	}))
	defer serverBaseErr.Close()

	pBaseErr := &MiniMaxProvider{client: serverBaseErr.Client(), baseURL: serverBaseErr.URL, apiKey: "key", region: "cn"}
	_, err = pBaseErr.Search(context.Background(), "test", 1, false)
	if err == nil || !strings.Contains(err.Error(), "insufficient balance") {
		t.Fatalf("expected base_resp error, got: %v", err)
	}
}

func TestMiniMaxProviderEmptyQuery(t *testing.T) {
	p := &MiniMaxProvider{client: http.DefaultClient, baseURL: "http://example.com", apiKey: "key", region: "cn"}
	_, err := p.Search(context.Background(), "   ", 1, false)
	if err == nil || !strings.Contains(err.Error(), "query is empty") {
		t.Fatalf("expected empty query error, got: %v", err)
	}
}

func TestMiniMaxProviderRegions(t *testing.T) {
	// Test cn URL
	pCN, err := NewMiniMaxProvider(types.WebSearchProviderParameters{
		APIKey:      "sk-test",
		ExtraConfig: map[string]string{"region": "cn"},
	})
	if err != nil {
		t.Fatal(err)
	}
	mCN := pCN.(*MiniMaxProvider)
	if mCN.baseURL != defaultMiniMaxSearchURL {
		t.Fatalf("expected %s, got %s", defaultMiniMaxSearchURL, mCN.baseURL)
	}

	// Test global URL
	pGlobal, err := NewMiniMaxProvider(types.WebSearchProviderParameters{
		APIKey:      "sk-test",
		ExtraConfig: map[string]string{"region": "global"},
	})
	if err != nil {
		t.Fatal(err)
	}
	mGlobal := pGlobal.(*MiniMaxProvider)
	if mGlobal.baseURL != globalMiniMaxSearchURL {
		t.Fatalf("expected %s, got %s", globalMiniMaxSearchURL, mGlobal.baseURL)
	}
}

func TestMiniMaxRegistry(t *testing.T) {
	r := NewRegistry()
	r.Register("minimax", NewMiniMaxProvider)
	provider, err := r.CreateProvider("minimax", types.WebSearchProviderParameters{
		APIKey: "sk-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	if provider.Name() != "minimax" {
		t.Fatalf("expected minimax, got %s", provider.Name())
	}
}
