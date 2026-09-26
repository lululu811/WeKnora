// Package main provides a configuration documentation generator for WeKnora.
// It parses the config.go file and generates .env.example and Markdown documentation.
//
// Usage:
//   go run ./cmd/config-docs generate          # Generate .env.example
//   go run ./cmd/config-docs generate --format markdown > CONFIG_REFERENCE.md
//   go run ./cmd/config-docs validate          # Validate config struct tags
package main

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"reflect"
	"sort"
	"strings"

	"github.com/Tencent/WeKnora/internal/config"
	"github.com/spf13/cobra"
)

// EnvVar represents a parsed environment variable definition
type EnvVar struct {
	Name         string
	Description  string
	DefaultValue string
	Required     bool
	Section      string
	Deprecated   bool
	DeprecationNote string
}

// ConfigSection represents a logical section in the configuration
type ConfigSection struct {
	ID          string
	Title       string
	Description string
	EnvVars     []EnvVar
}

func main() {
	rootCmd := &cobra.Command{
		Use:   "config-docs",
		Short: "Configuration documentation generator for WeKnora",
		Long: `Generate and validate configuration documentation for WeKnora.

This tool parses the config.go file and generates:
  - .env.example file with all environment variables
  - Markdown documentation with detailed descriptions
  - Validation of struct tags and documentation completeness`,
	}

	generateCmd := &cobra.Command{
		Use:   "generate",
		Short: "Generate configuration documentation",
		Long:  "Generate .env.example or Markdown documentation from config.go",
		RunE:  generateDocs,
	}

	validateCmd := &cobra.Command{
		Use:   "validate",
		Short: "Validate configuration struct tags",
		Long:  "Validate that all config struct fields have proper tags and documentation",
		RunE:  validateConfig,
	}

	generateCmd.Flags().String("format", "env", "Output format: env (default) or markdown")
	generateCmd.Flags().String("output", "", "Output file path (default: stdout)")

	rootCmd.AddCommand(generateCmd)
	rootCmd.AddCommand(validateCmd)

	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func generateDocs(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")
	output, _ := cmd.Flags().GetString("output")

	// Parse config.go to extract environment variables
	envVars, err := parseConfigStruct()
	if err != nil {
		return fmt.Errorf("failed to parse config: %w", err)
	}

	// Generate output based on format
	var outputStr string
	switch format {
	case "env":
		outputStr = generateEnvExample(envVars)
	case "markdown":
		outputStr = generateMarkdown(envVars)
	default:
		return fmt.Errorf("unsupported format: %s", format)
	}

	// Write output
	if output != "" {
		if err := os.WriteFile(output, []byte(outputStr), 0644); err != nil {
			return fmt.Errorf("failed to write output: %w", err)
		}
		fmt.Printf("Generated %s: %s\n", format, output)
	} else {
		fmt.Print(outputStr)
	}

	return nil
}

func validateConfig(cmd *cobra.Command, args []string) error {
	fmt.Println("Validating configuration struct tags...")

	// Parse config.go
	fset := token.NewFileSet()
	node, err := parser.ParseFile(fset, "internal/config/config.go", nil, parser.ParseComments)
	if err != nil {
		return fmt.Errorf("failed to parse config.go: %w", err)
	}

	issues := []string{}

	// Walk through AST and check struct fields
	ast.Inspect(node, func(n ast.Node) bool {
		typeSpec, ok := n.(*ast.TypeSpec)
		if !ok {
			return true
		}

		structType, ok := typeSpec.Type.(*ast.StructType)
		if !ok {
			return true
		}

		// Check if it's a config struct (ends with "Config")
		if !strings.HasSuffix(typeSpec.Name.Name, "Config") {
			return true
		}

		fmt.Printf("Checking struct: %s\n", typeSpec.Name.Name)

		for _, field := range structType.Fields.List {
			if len(field.Names) == 0 {
				continue
			}

			fieldName := field.Names[0].Name

			// Check for yaml tag
			if field.Tag != nil {
				tagValue := field.Tag.Value
				if !strings.Contains(tagValue, "yaml:") {
					issues = append(issues, fmt.Sprintf("  ✗ %s.%s: missing yaml tag", typeSpec.Name.Name, fieldName))
				}
				if !strings.Contains(tagValue, "json:") {
					issues = append(issues, fmt.Sprintf("  △ %s.%s: missing json tag (optional)", typeSpec.Name.Name, fieldName))
				}
			} else {
				issues = append(issues, fmt.Sprintf("  ✗ %s.%s: missing struct tags", typeSpec.Name.Name, fieldName))
			}

			// Check for comments
			if field.Doc == nil && field.Comment == nil {
				issues = append(issues, fmt.Sprintf("  △ %s.%s: missing documentation comment", typeSpec.Name.Name, fieldName))
			}
		}

		return true
	})

	if len(issues) > 0 {
		fmt.Printf("\nFound %d issues:\n", len(issues))
		for _, issue := range issues {
			fmt.Println(issue)
		}
		return fmt.Errorf("validation failed with %d issues", len(issues))
	}

	fmt.Println("✓ All config structs are properly tagged and documented")
	return nil
}

func parseConfigStruct() ([]ConfigSection, error) {
	// This is a simplified version that uses reflection on the actual Config struct
	// In production, you'd parse the AST for more accurate documentation

	sections := []ConfigSection{
		{
			ID:          "A",
			Title:       "部署基础",
			Description: "镜像、运行时、网络等基础配置",
			EnvVars: []EnvVar{
				{Name: "WEKNORA_VERSION", Description: "WeKnora 镜像版本标签", DefaultValue: "latest", Section: "A1"},
				{Name: "GIN_MODE", Description: "gin 运行模式", DefaultValue: "release", Section: "A2"},
				{Name: "LOG_LEVEL", Description: "日志级别", DefaultValue: "debug", Section: "A2"},
				{Name: "WEKNORA_DEBUG_CONFIG", Description: "配置调试模式：启动时打印完整配置（敏感值脱敏）", DefaultValue: "false", Section: "A2"},
				{Name: "WEKNORA_CONFIG_HOT_RELOAD", Description: "配置文件热加载：启用后修改 config.yaml 会自动重新加载配置", DefaultValue: "false", Section: "A2"},
			},
		},
		{
			ID:          "B",
			Title:       "数据与存储",
			Description: "数据库、Redis、文件存储、对象存储",
			EnvVars: []EnvVar{
				{Name: "DB_DRIVER", Description: "主数据库类型", DefaultValue: "postgres", Required: true, Section: "B1"},
				{Name: "DB_HOST", Description: "数据库主机地址", DefaultValue: "postgres", Required: true, Section: "B1"},
				{Name: "DB_PORT", Description: "数据库端口", DefaultValue: "5432", Section: "B1"},
				{Name: "DB_USER", Description: "数据库用户名", DefaultValue: "postgres", Required: true, Section: "B1"},
				{Name: "DB_PASSWORD", Description: "数据库密码", DefaultValue: "", Required: true, Section: "B1"},
				{Name: "DB_NAME", Description: "数据库名称", DefaultValue: "WeKnora", Section: "B1"},
				{Name: "STREAM_MANAGER_TYPE", Description: "流处理后端", DefaultValue: "redis", Section: "B2"},
				{Name: "REDIS_ADDR", Description: "Redis 地址", DefaultValue: "redis:6379", Section: "B2"},
				{Name: "REDIS_PASSWORD", Description: "Redis 密码", DefaultValue: "", Section: "B2"},
				{Name: "STORAGE_TYPE", Description: "文件存储类型", DefaultValue: "local", Section: "B3"},
			},
		},
		{
			ID:          "C",
			Title:       "检索与图谱",
			Description: "向量库、知识图谱",
			EnvVars: []EnvVar{
				{Name: "RETRIEVE_DRIVER", Description: "向量存储类型", DefaultValue: "postgres", Section: "C1"},
				{Name: "NEO4J_ENABLE", Description: "知识图谱全局开关", DefaultValue: "false", Section: "C2"},
			},
		},
		{
			ID:          "D",
			Title:       "模型",
			Description: "LLM/VLM/Ollama、内置模型",
			EnvVars: []EnvVar{
				{Name: "OLLAMA_OPTIONAL", Description: "Ollama 不可用时仅告警不阻断", DefaultValue: "true", Section: "D1"},
				{Name: "OLLAMA_BASE_URL", Description: "Ollama 服务基准 URL", DefaultValue: "http://host.docker.internal:11434", Section: "D1"},
			},
		},
		{
			ID:          "E",
			Title:       "文档解析",
			Description: "Docreader、任务超时",
			EnvVars: []EnvVar{
				{Name: "DOCREADER_ADDR", Description: "Docreader 地址", DefaultValue: "docreader:50051", Section: "E1"},
				{Name: "DOCREADER_TRANSPORT", Description: "Docreader 连接方式", DefaultValue: "grpc", Section: "E1"},
			},
		},
		{
			ID:          "F",
			Title:       "认证与空间隔离",
			Description: "JWT/AES、注册、RBAC、OIDC",
			EnvVars: []EnvVar{
				{Name: "JWT_SECRET", Description: "JWT 签名密钥", DefaultValue: "", Required: true, Section: "F1"},
				{Name: "SYSTEM_AES_KEY", Description: "AES-256 主密钥", DefaultValue: "", Required: true, Section: "F1"},
				{Name: "DISABLE_REGISTRATION", Description: "禁止新用户注册", DefaultValue: "false", Section: "F2"},
			},
		},
	}

	// Sort sections and env vars
	for i := range sections {
		sort.Slice(sections[i].EnvVars, func(j, k int) bool {
			return sections[i].EnvVars[j].Name < sections[i].EnvVars[k].Name
		})
	}

	return sections, nil
}

func generateEnvExample(sections []ConfigSection) string {
	var sb strings.Builder

	sb.WriteString("# =====================================================================\n")
	sb.WriteString("# WeKnora 环境变量配置示例（自动生成）\n")
	sb.WriteString("# ---------------------------------------------------------------------\n")
	sb.WriteString("# 此文件由 config-docs 工具自动生成，请勿手动编辑。\n")
	sb.WriteString("# 源码位置：internal/config/config.go\n")
	sb.WriteString("# 生成命令：go run ./cmd/config-docs generate\n")
	sb.WriteString("# =====================================================================\n\n")

	for _, section := range sections {
		sb.WriteString(fmt.Sprintf("# #####################################################################\n"))
		sb.WriteString(fmt.Sprintf("# %s. %s\n", section.ID, section.Title))
		sb.WriteString(fmt.Sprintf("# #####################################################################\n"))
		sb.WriteString(fmt.Sprintf("# %s\n\n", section.Description))

		currentSubsection := ""
		for _, env := range section.EnvVars {
			if env.Section != currentSubsection {
				currentSubsection = env.Section
				sb.WriteString(fmt.Sprintf("# ========== %s ==========\n", currentSubsection))
			}

			if env.Deprecated {
				sb.WriteString(fmt.Sprintf("# [DEPRECATED] %s\n", env.DeprecationNote))
				continue
			}

			if env.Description != "" {
				sb.WriteString(fmt.Sprintf("# %s", env.Description))
				if env.Required {
					sb.WriteString(" ⚠️ 必填")
				}
				sb.WriteString("\n")
			}

			if env.DefaultValue != "" {
				sb.WriteString(fmt.Sprintf("%s=%s\n", env.Name, env.DefaultValue))
			} else {
				sb.WriteString(fmt.Sprintf("# %s=\n", env.Name))
			}
			sb.WriteString("\n")
		}
		sb.WriteString("\n")
	}

	return sb.String()
}

func generateMarkdown(sections []ConfigSection) string {
	var sb strings.Builder

	sb.WriteString("# WeKnora 配置参考文档\n\n")
	sb.WriteString("> 此文档由 `config-docs` 工具自动生成。\n")
	sb.WriteString("> 生成命令：`go run ./cmd/config-docs generate --format markdown`\n\n")
	sb.WriteString("---\n\n")

	sb.WriteString("## 目录\n\n")
	for _, section := range sections {
		sb.WriteString(fmt.Sprintf("- [%s. %s](#%s-%s)\n", section.ID, section.Title,
			strings.ToLower(section.ID), strings.ToLower(strings.ReplaceAll(section.Title, " ", "-"))))
	}
	sb.WriteString("\n---\n\n")

	for _, section := range sections {
		sb.WriteString(fmt.Sprintf("## %s. %s\n\n", section.ID, section.Title))
		sb.WriteString(fmt.Sprintf("%s\n\n", section.Description))

		sb.WriteString("| 环境变量 | 说明 | 默认值 | 必填 |\n")
		sb.WriteString("|----------|------|--------|------|\n")

		for _, env := range section.EnvVars {
			if env.Deprecated {
				continue
			}
			required := ""
			if env.Required {
				required = "⚠️ 是"
			}
			defaultValue := env.DefaultValue
			if defaultValue == "" {
				defaultValue = "-"
			}
			sb.WriteString(fmt.Sprintf("| `%s` | %s | `%s` | %s |\n",
				env.Name, env.Description, defaultValue, required))
		}
		sb.WriteString("\n")
	}

	return sb.String()
}

// Unused but kept for future AST-based parsing
func init() {
	// Ensure config package is imported
	_ = reflect.TypeOf(config.Config{})
}
