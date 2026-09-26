// Package kline_studio provides WeKnora agent tools that push picks to
// the kline-studio Zettaranc review terminal and return view URLs.
package kline_studio

import (
	"os"
	"time"
)

// Config holds the connection info for the kline-studio backend.
//
// APIURL  — backend HTTP API base (e.g. http://127.0.0.1:4000); used for POST /api/picks.
// BaseURL — frontend URL surfaced to the user (e.g. http://localhost:5173);
//           the tool appends ?tab=picks so the browser opens the picks tab directly.
type Config struct {
	APIURL  string
	BaseURL string
	Timeout time.Duration
}

// DefaultConfig returns the default configuration for local development.
//
// In docker compose, override via env:
//   KLINE_STUDIO_API_URL  (default http://127.0.0.1:4000)
//   KLINE_STUDIO_BASE_URL (default http://localhost:5173)
func DefaultConfig() *Config {
	apiURL := os.Getenv("KLINE_STUDIO_API_URL")
	if apiURL == "" {
		apiURL = "http://127.0.0.1:4000"
	}
	baseURL := os.Getenv("KLINE_STUDIO_BASE_URL")
	if baseURL == "" {
		baseURL = "http://localhost:5173"
	}
	return &Config{
		APIURL:  apiURL,
		BaseURL: baseURL,
		Timeout: 5 * time.Second,
	}
}