package kline_studio

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// TickerExtractor pulls 6-digit A-share tickers + exchange codes from free-form
// text. Both forms are accepted:
//
//   - thscode style: "600519.SH", "000001.SZ", "830799.BJ"
//   - spaced style:  "600519 SH", "000001.SZ"
//   - bare style:    "600519" (only matched if followed by an exchange code)
//
// Duplicate (ticker, exchange) pairs are de-duplicated; results preserve the
// order of first occurrence in the input.
type TickerExtractor struct {
	// ThsCodePattern matches "digits.SH/SZ/BJ" with optional whitespace.
	thsCodePattern *regexp.Regexp
	// TickerPattern matches a bare 6-digit ticker followed by SH/SZ/BJ with
	// optional separator (space, slash, dot).
	tickerPattern *regexp.Regexp
}

// NewTickerExtractor returns a configured extractor. The regexes are compiled
// once at construction so callers can run extraction across many texts without
// re-compilation cost.
func NewTickerExtractor() *TickerExtractor {
	return &TickerExtractor{
		thsCodePattern: regexp.MustCompile(`\b(\d{6})\s*[\.\/]?\s*(SH|SZ|BJ)\b`),
		tickerPattern:  regexp.MustCompile(`\b(\d{6})\s+(SH|SZ|BJ)\b`),
	}
}

// Extract returns all unique (ticker, exchange) pairs found in the input.
func (e *TickerExtractor) Extract(text string) []Pick {
	if text == "" {
		return nil
	}
	seen := make(map[string]bool)
	out := make([]Pick, 0, 8)

	add := func(ticker, exchange string) {
		exchange = strings.ToUpper(strings.TrimSpace(exchange))
		if exchange != "SH" && exchange != "SZ" && exchange != "BJ" {
			return
		}
		key := ticker + "." + exchange
		if seen[key] {
			return
		}
		seen[key] = true
		out = append(out, Pick{Ticker: ticker, Exchange: exchange})
	}

	// thscode pattern catches both forms because the optional separator is
	// already in the regex. The bare pattern is checked second to capture
	// pairs where the separator is missing the dot.
	for _, m := range e.thsCodePattern.FindAllStringSubmatch(text, -1) {
		add(m[1], m[2])
	}
	for _, m := range e.tickerPattern.FindAllStringSubmatch(text, -1) {
		add(m[1], m[2])
	}

	return out
}

// ExtractFromTexts runs Extract over every text and returns the merged,
// de-duplicated result. Order follows the input slice, then the position
// inside each text.
func (e *TickerExtractor) ExtractFromTexts(texts []string) []Pick {
	seen := make(map[string]bool)
	out := make([]Pick, 0, 8)
	for _, text := range texts {
		for _, p := range e.Extract(text) {
			key := p.Ticker + "." + p.Exchange
			if seen[key] {
				continue
			}
			seen[key] = true
			out = append(out, p)
		}
	}
	return out
}

// KBExtractResult is the outcome of scanning a knowledge base for tickers and
// pushing them to kline-studio. Returned by ExtractAndPushKB.
type KBExtractResult struct {
	KBID         string `json:"kb_id"`
	KBNamescanned []string `json:"-"`
	Picks        []Pick  `json:"picks"`
	Count        int     `json:"count"`
	URL          string  `json:"url"`
}

// ExtractAndPushKB scans every knowledge entry's Title + Description + Source
// for tickers, dedups, pushes to kline-studio's /api/picks, and returns the
// deep-link URL. If no tickers are found the picks.json file is *not* touched
// (we don't want a KB without any ticker to wipe the current picks list).
func ExtractAndPushKB(ctx context.Context, cfg *Config, kbID string, entries []KBEntry) (*KBExtractResult, error) {
	if cfg == nil {
		cfg = DefaultConfig()
	}
	extractor := NewTickerExtractor()
	texts := make([]string, 0, len(entries)*3)
	for _, e := range entries {
		texts = append(texts, e.Title, e.Description, e.Source)
	}
	picks := extractor.ExtractFromTexts(texts)
	if len(picks) == 0 {
		return &KBExtractResult{KBID: kbID, Picks: picks, Count: 0, URL: strings.TrimRight(cfg.BaseURL, "/") + "/?tab=picks"}, nil
	}

	tool := NewShowTool(cfg)
	args, err := picksToArgs(picks)
	if err != nil {
		return nil, fmt.Errorf("序列化 picks 失败：%w", err)
	}
	res, err := tool.Execute(ctx, args)
	if err != nil {
		return nil, err
	}
	if !res.Success {
		return nil, fmt.Errorf("推送到 kline-studio 失败：%s", res.Error)
	}

	return &KBExtractResult{
		KBID:  kbID,
		Picks: picks,
		Count: len(picks),
		URL:   strings.TrimRight(cfg.BaseURL, "/") + "/?tab=picks",
	}, nil
}

// KBEntry is the minimal shape we need from a knowledge row to scan for
// tickers. Title / Description / Source map to the corresponding columns on
// types.Knowledge.
type KBEntry struct {
	Title       string
	Description string
	Source      string
}

// picksToArgs encodes picks as the JSON tool-arg shape consumed by Execute.
// Uses the thscode form to keep the payload compact.
func picksToArgs(picks []Pick) (json.RawMessage, error) {
	ths := make([]string, 0, len(picks))
	for _, p := range picks {
		ths = append(ths, p.Ticker+"."+p.Exchange)
	}
	payload := map[string]interface{}{"thscode": ths}
	return json.Marshal(payload)
}