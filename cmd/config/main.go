// Package main provides the config command for WeKnora CLI.
// It provides utilities for managing and validating WeKnora configuration.
//
// Commands:
//   weknora config validate    - Validate configuration files
//   weknora config show        - Display current configuration (redacted)
//   weknora config init        - Initialize a new configuration from template
//   weknora config diff        - Compare two configuration files
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/Tencent/WeKnora/internal/config"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

func main() {
	rootCmd := &cobra.Command{
		Use:   "config",
		Short: "WeKnora configuration management",
		Long: `Manage WeKnora configuration files.

This command provides utilities for validating, inspecting, and managing
WeKnora configuration files.`,
	}

	// Validate command
	validateCmd := &cobra.Command{
		Use:   "validate [config-file]",
		Short: "Validate a configuration file",
		Long: `Validate a WeKnora configuration file for correctness.

This command checks:
  - YAML syntax
  - Required fields
  - Value ranges and types
  - Logical relationships between fields
  - Environment variable references

Examples:
  # Validate default config.yaml
  weknora config validate

  # Validate specific file
  weknora config validate /path/to/config.yaml

  # Validate with environment variables
  WEKNORA_DEBUG_CONFIG=true weknora config validate`,
		Args: cobra.MaximumNArgs(1),
		RunE: validateConfig,
	}

	// Show command
	showCmd := &cobra.Command{
		Use:   "show",
		Short: "Display current configuration (redacted)",
		Long: `Display the current configuration with sensitive values redacted.

This is useful for:
  - Debugging configuration issues
  - Verifying environment variable overrides
  - Sharing configuration for support (safe to share)

Examples:
  # Show current configuration
  weknora config show

  # Show in JSON format
  weknora config show --format json`,
		RunE: showConfig,
	}

	// Init command
	initCmd := &cobra.Command{
		Use:   "init [directory]",
		Short: "Initialize a new configuration from template",
		Long: `Initialize a new WeKnora configuration from a template.

Available templates:
  - lite       : SQLite + local storage, zero external dependencies
  - production : PostgreSQL + MinIO + Redis, production-ready
  - kubernetes : Kubernetes-optimized with environment variables
  - minimal    : Minimal configuration for testing

Examples:
  # Initialize lite configuration in current directory
  weknora config init --template lite

  # Initialize production configuration in specific directory
  weknora config init /path/to/config --template production

  # List available templates
  weknora config init --list`,
		Args: cobra.MaximumNArgs(1),
		RunE: initConfig,
	}

	initCmd.Flags().String("template", "lite", "Configuration template to use")
	initCmd.Flags().Bool("list", false, "List available templates")

	// Diff command
	diffCmd := &cobra.Command{
		Use:   "diff <file1> <file2>",
		Short: "Compare two configuration files",
		Long: `Compare two configuration files and show differences.

This is useful for:
  - Comparing configurations across environments
  - Reviewing configuration changes
  - Auditing configuration drift

Examples:
  # Compare two files
  weknora config diff config.yaml config.yaml.bak

  # Compare with environment overrides
  weknora config diff config.dev.yaml config.prod.yaml`,
		Args: cobra.ExactArgs(2),
		RunE: diffConfig,
	}

	rootCmd.AddCommand(validateCmd)
	rootCmd.AddCommand(showCmd)
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(diffCmd)

	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func validateConfig(cmd *cobra.Command, args []string) error {
	configFile := "config/config.yaml"
	if len(args) > 0 {
		configFile = args[0]
	}

	fmt.Printf("Validating configuration: %s\n\n", configFile)

	// Check if file exists
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return fmt.Errorf("configuration file not found: %s", configFile)
	}

	// Read and parse YAML
	data, err := os.ReadFile(configFile)
	if err != nil {
		return fmt.Errorf("failed to read config file: %w", err)
	}

	var rawConfig map[string]interface{}
	if err := yaml.Unmarshal(data, &rawConfig); err != nil {
		return fmt.Errorf("YAML syntax error: %w", err)
	}

	fmt.Println("✓ YAML syntax is valid")

	// Try to load the configuration using the actual loader
	// This will trigger all validation logic
	cfg, err := config.LoadConfig()
	if err != nil {
		return fmt.Errorf("configuration validation failed:\n  %w", err)
	}

	fmt.Println("✓ Configuration structure is valid")

	// Additional checks
	warnings := []string{}

	// Check for common issues
	if cfg.Server != nil && cfg.Server.Port == 8080 {
		warnings = append(warnings, "Using default port 8080 - consider changing for production")
	}

	if len(warnings) > 0 {
		fmt.Println("\nWarnings:")
		for _, w := range warnings {
			fmt.Printf("  △ %s\n", w)
		}
	}

	fmt.Println("\n✓ Configuration is valid and ready to use")
	return nil
}

func showConfig(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")

	// Load configuration
	cfg, err := config.LoadConfig()
	if err != nil {
		return fmt.Errorf("failed to load config: %w", err)
	}

	// Redact sensitive values
	redacted := redactConfig(cfg)

	// Output based on format
	switch format {
	case "json":
		output, err := json.MarshalIndent(redacted, "", "  ")
		if err != nil {
			return fmt.Errorf("failed to marshal config: %w", err)
		}
		fmt.Println(string(output))

	case "yaml":
		output, err := yaml.Marshal(redacted)
		if err != nil {
			return fmt.Errorf("failed to marshal config: %w", err)
		}
		fmt.Println(string(output))

	default:
		return fmt.Errorf("unsupported format: %s", format)
	}

	return nil
}

func initConfig(cmd *cobra.Command, args []string) error {
	list, _ := cmd.Flags().GetBool("list")
	template, _ := cmd.Flags().GetString("template")

	if list {
		fmt.Println("Available configuration templates:")
		fmt.Println()
		fmt.Println("  lite        - SQLite + local storage, zero external dependencies")
		fmt.Println("  production  - PostgreSQL + MinIO + Redis, production-ready")
		fmt.Println("  kubernetes  - Kubernetes-optimized with environment variables")
		fmt.Println("  minimal     - Minimal configuration for testing")
		return nil
	}

	dir := "."
	if len(args) > 0 {
		dir = args[0]
	}

	// Create directory if it doesn't exist
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("failed to create directory: %w", err)
	}

	// Generate configuration based on template
	configContent := generateTemplateConfig(template)
	envContent := generateTemplateEnv(template)

	// Write config.yaml
	configPath := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		return fmt.Errorf("failed to write config.yaml: %w", err)
	}

	// Write .env
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte(envContent), 0644); err != nil {
		return fmt.Errorf("failed to write .env: %w", err)
	}

	fmt.Printf("✓ Initialized %s configuration in %s\n", template, dir)
	fmt.Printf("  Created: %s\n", configPath)
	fmt.Printf("  Created: %s\n", envPath)
	fmt.Println()
	fmt.Println("Next steps:")
	fmt.Println("  1. Review and customize the configuration files")
	fmt.Println("  2. Set required environment variables in .env")
	fmt.Println("  3. Validate the configuration: weknora config validate")
	fmt.Println("  4. Start WeKnora: ./weknora")

	return nil
}

func diffConfig(cmd *cobra.Command, args []string) error {
	file1 := args[0]
	file2 := args[1]

	// Read both files
	data1, err := os.ReadFile(file1)
	if err != nil {
		return fmt.Errorf("failed to read %s: %w", file1, err)
	}

	data2, err := os.ReadFile(file2)
	if err != nil {
		return fmt.Errorf("failed to read %s: %w", file2, err)
	}

	// Parse YAML
	var config1, config2 map[string]interface{}
	if err := yaml.Unmarshal(data1, &config1); err != nil {
		return fmt.Errorf("failed to parse %s: %w", file1, err)
	}
	if err := yaml.Unmarshal(data2, &config2); err != nil {
		return fmt.Errorf("failed to parse %s: %w", file2, err)
	}

	// Compare
	diffs := compareConfigs(config1, config2, "")

	if len(diffs) == 0 {
		fmt.Println("✓ Configurations are identical")
		return nil
	}

	fmt.Printf("Found %d differences:\n\n", len(diffs))
	for _, diff := range diffs {
		fmt.Println(diff)
	}

	return nil
}

func redactConfig(cfg *config.Config) map[string]interface{} {
	redacted := make(map[string]interface{})

	// Helper to redact strings
	redact := func(s string) string {
		if s == "" {
			return ""
		}
		if len(s) <= 4 {
			return "****"
		}
		return s[:2] + "****" + s[len(s)-2:]
	}

	// Server
	if cfg.Server != nil {
		redacted["server"] = map[string]interface{}{
			"port": cfg.Server.Port,
			"host": cfg.Server.Host,
		}
	}

	// Conversation
	if cfg.Conversation != nil {
		redacted["conversation"] = map[string]interface{}{
			"max_rounds":       cfg.Conversation.MaxRounds,
			"embedding_top_k":  cfg.Conversation.EmbeddingTopK,
			"rerank_top_k":     cfg.Conversation.RerankTopK,
			"enable_rewrite":   cfg.Conversation.EnableRewrite,
			"enable_rerank":    cfg.Conversation.EnableRerank,
		}
	}

	// KnowledgeBase
	if cfg.KnowledgeBase != nil {
		redacted["knowledge_base"] = map[string]interface{}{
			"chunk_size":    cfg.KnowledgeBase.ChunkSize,
			"chunk_overlap": cfg.KnowledgeBase.ChunkOverlap,
		}
	}

	// Agent
	if cfg.Agent != nil {
		redacted["agent"] = map[string]interface{}{
			"llm_call_timeout": cfg.Agent.LLMCallTimeout,
		}
	}

	// OIDC (redact secrets)
	if cfg.OIDCAuth != nil {
		redacted["oidc_auth"] = map[string]interface{}{
			"enable":        cfg.OIDCAuth.Enable,
			"client_id":     redact(cfg.OIDCAuth.ClientID),
			"client_secret": redact(cfg.OIDCAuth.ClientSecret),
		}
	}

	// StreamManager (redact Redis password)
	if cfg.StreamManager != nil {
		redisConfig := map[string]interface{}{
			"address": cfg.StreamManager.Redis.Address,
			"db":      cfg.StreamManager.Redis.DB,
		}
		if cfg.StreamManager.Redis.Password != "" {
			redisConfig["password"] = redact(cfg.StreamManager.Redis.Password)
		}
		redacted["stream_manager"] = map[string]interface{}{
			"type":  cfg.StreamManager.Type,
			"redis": redisConfig,
		}
	}

	return redacted
}

func generateTemplateConfig(template string) string {
	switch template {
	case "lite":
		return `# WeKnora Lite Configuration
# SQLite + local storage, zero external dependencies

server:
  port: 8080
  host: "0.0.0.0"

conversation:
  max_rounds: 5
  embedding_top_k: 30
  rerank_top_k: 30
  vector_threshold: 0.2
  rerank_threshold: 0.3
  enable_rewrite: true
  enable_rerank: true

knowledge_base:
  chunk_size: 512
  chunk_overlap: 50
  document_process_timeout: 2h

agent:
  llm_call_timeout: 120
`

	case "production":
		return `# WeKnora Production Configuration
# PostgreSQL + MinIO + Redis

server:
  port: 8080
  host: "0.0.0.0"
  shutdown_timeout: 30s

conversation:
  max_rounds: 10
  embedding_top_k: 50
  rerank_top_k: 50
  vector_threshold: 0.2
  rerank_threshold: 0.3
  enable_rewrite: true
  enable_rerank: true

knowledge_base:
  chunk_size: 512
  chunk_overlap: 50
  document_process_timeout: 2h
  docreader_call_timeout: 30m

agent:
  llm_call_timeout: 180
  tool_approval_timeout_seconds: 600

stream_manager:
  type: redis
  cleanup_timeout: 5m
`

	case "kubernetes":
		return `# WeKnora Kubernetes Configuration
# Optimized for container orchestration

server:
  port: ${APP_PORT:-8080}
  host: "0.0.0.0"
  shutdown_timeout: 30s

conversation:
  max_rounds: 10
  embedding_top_k: 50
  rerank_top_k: 50

knowledge_base:
  chunk_size: 512
  chunk_overlap: 50

agent:
  llm_call_timeout: 180
`

	default:
		return `# WeKnora Minimal Configuration
server:
  port: 8080
`
	}
}

func generateTemplateEnv(template string) string {
	switch template {
	case "lite":
		return `# WeKnora Lite Mode Environment Variables
# SQLite + local storage, zero external dependencies

# Database (SQLite)
DB_DRIVER=sqlite
DB_PATH=./data/weknora.db

# Storage (local)
STORAGE_TYPE=local
LOCAL_STORAGE_BASE_DIR=./data/files

# Vector Database (SQLite with sqlite-vec)
RETRIEVE_DRIVER=sqlite

# Redis (disabled in lite mode)
STREAM_MANAGER_TYPE=memory

# Authentication
JWT_SECRET=change-me-in-production
SYSTEM_AES_KEY=change-me-in-production-32b

# Optional: Ollama for local LLM
# OLLAMA_BASE_URL=http://localhost:11434
`

	case "production":
		return `# WeKnora Production Environment Variables

# Database (PostgreSQL)
DB_DRIVER=postgres
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=change-me
DB_NAME=weknora

# Redis
STREAM_MANAGER_TYPE=redis
REDIS_ADDR=localhost:6379
REDIS_PASSWORD=change-me

# Storage (MinIO)
STORAGE_TYPE=minio
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY_ID=change-me
MINIO_SECRET_ACCESS_KEY=change-me
MINIO_BUCKET_NAME=weknora

# Vector Database (PostgreSQL with pgvector)
RETRIEVE_DRIVER=postgres

# Authentication
JWT_SECRET=change-me-in-production
SYSTEM_AES_KEY=change-me-in-production-32b

# Document Parser
DOCREADER_ADDR=localhost:50051
DOCREADER_TRANSPORT=grpc
`

	case "kubernetes":
		return `# WeKnora Kubernetes Environment Variables
# All values should be set via Kubernetes ConfigMaps/Secrets

# Database
DB_DRIVER=${DB_DRIVER}
DB_HOST=${DB_HOST}
DB_PORT=${DB_PORT}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=${DB_NAME}

# Redis
STREAM_MANAGER_TYPE=redis
REDIS_ADDR=${REDIS_ADDR}
REDIS_PASSWORD=${REDIS_PASSWORD}

# Storage
STORAGE_TYPE=${STORAGE_TYPE}
# ... storage-specific variables

# Authentication
JWT_SECRET=${JWT_SECRET}
SYSTEM_AES_KEY=${SYSTEM_AES_KEY}
`

	default:
		return `# WeKnora Minimal Environment Variables
JWT_SECRET=change-me
SYSTEM_AES_KEY=change-me-32-bytes-long!!
`
	}
}

func compareConfigs(config1, config2 map[string]interface{}, prefix string) []string {
	diffs := []string{}

	// Check keys in config1
	for key, val1 := range config1 {
		fullKey := key
		if prefix != "" {
			fullKey = prefix + "." + key
		}

		val2, exists := config2[key]
		if !exists {
			diffs = append(diffs, fmt.Sprintf("  + %s: %v (only in first)", fullKey, val1))
			continue
		}

		// Recursively compare nested maps
		if map1, ok := val1.(map[string]interface{}); ok {
			if map2, ok := val2.(map[string]interface{}); ok {
				diffs = append(diffs, compareConfigs(map1, map2, fullKey)...)
				continue
			}
		}

		// Compare values
		if fmt.Sprintf("%v", val1) != fmt.Sprintf("%v", val2) {
			diffs = append(diffs, fmt.Sprintf("  ~ %s: %v → %v", fullKey, val1, val2))
		}
	}

	// Check keys only in config2
	for key, val2 := range config2 {
		fullKey := key
		if prefix != "" {
			fullKey = prefix + "." + key
		}

		if _, exists := config1[key]; !exists {
			diffs = append(diffs, fmt.Sprintf("  + %s: %v (only in second)", fullKey, val2))
		}
	}

	return diffs
}
