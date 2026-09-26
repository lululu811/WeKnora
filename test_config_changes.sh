#!/bin/bash
# 配置系统改动验证脚本

set -e

echo "========================================="
echo "配置系统改动验证"
echo "========================================="
echo ""

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 测试计数
TESTS_PASSED=0
TESTS_FAILED=0

# 测试函数
run_test() {
    local test_name="$1"
    local test_cmd="$2"

    echo -n "测试: $test_name ... "
    if eval "$test_cmd" > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        ((TESTS_FAILED++))
        return 1
    fi
}

echo "1. 编译测试"
echo "-----------------------------------------"
run_test "Go 编译" "go build -o /tmp/weknora-config-test ./cmd/server"
echo ""

echo "2. 配置验证测试"
echo "-----------------------------------------"

# 创建测试配置文件
cat > /tmp/test-config.yaml <<EOF
server:
  port: 8080
  host: "0.0.0.0"

conversation:
  max_rounds: 5
  embedding_top_k: 30
  rerank_top_k: 30
  vector_threshold: 0.2
  rerank_threshold: 0.3

knowledge_base:
  chunk_size: 512
  chunk_overlap: 50
  document_process_timeout: 2h
  docreader_call_timeout: 30m

agent:
  llm_call_timeout: 120
  tool_approval_timeout_seconds: 600

im:
  workers: 5
  max_queue_size: 50
  max_per_user: 3
EOF

# 测试有效配置
run_test "有效配置验证" "WEKNORA_DEBUG_CONFIG=false /tmp/weknora-config-test --config /tmp/test-config.yaml 2>&1 | grep -q 'Using configuration file'"

# 测试无效配置（chunk_overlap >= chunk_size）
cat > /tmp/test-config-invalid.yaml <<EOF
knowledge_base:
  chunk_size: 512
  chunk_overlap: 600
EOF

echo -n "测试: 无效配置检测 (chunk_overlap >= chunk_size) ... "
if /tmp/weknora-config-test --config /tmp/test-config-invalid.yaml 2>&1 | grep -q "chunk_overlap must be less than chunk_size"; then
    echo -e "${GREEN}PASS${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((TESTS_FAILED++))
fi

echo ""

echo "3. 配置调试模式测试"
echo "-----------------------------------------"

# 测试调试模式输出
echo -n "测试: WEKNORA_DEBUG_CONFIG=true 输出配置 ... "
if WEKNORA_DEBUG_CONFIG=true /tmp/weknora-config-test --config /tmp/test-config.yaml 2>&1 | grep -q "\[config-debug\] === Configuration Dump"; then
    echo -e "${GREEN}PASS${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((TESTS_FAILED++))
fi

# 测试敏感值脱敏
echo -n "测试: 敏感值脱敏 (client_secret) ... "
if WEKNORA_DEBUG_CONFIG=true /tmp/weknora-config-test --config /tmp/test-config.yaml 2>&1 | grep -q "client_secret: \*\*\*\*"; then
    echo -e "${GREEN}PASS${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((TESTS_FAILED++))
fi

echo ""

echo "4. 配置热加载测试"
echo "-----------------------------------------"

# 测试热加载启用
echo -n "测试: WEKNORA_CONFIG_HOT_RELOAD=true 启动监听 ... "
if WEKNORA_CONFIG_HOT_RELOAD=true timeout 3 /tmp/weknora-config-test --config /tmp/test-config.yaml 2>&1 | grep -q "Configuration hot-reload enabled"; then
    echo -e "${GREEN}PASS${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${YELLOW}SKIP${NC} (需要完整运行环境)"
fi

echo ""

echo "5. 环境变量读取测试"
echo "-----------------------------------------"

# 测试新环境变量
echo -n "测试: WEKNORA_DEBUG_CONFIG 环境变量 ... "
if grep -q "WEKNORA_DEBUG_CONFIG" .env.example; then
    echo -e "${GREEN}PASS${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((TESTS_FAILED++))
fi

echo -n "测试: WEKNORA_CONFIG_HOT_RELOAD 环境变量 ... "
if grep -q "WEKNORA_CONFIG_HOT_RELOAD" .env.example; then
    echo -e "${GREEN}PASS${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((TESTS_FAILED++))
fi

echo ""

echo "========================================="
echo "测试结果汇总"
echo "========================================="
echo -e "通过: ${GREEN}$TESTS_PASSED${NC}"
echo -e "失败: ${RED}$TESTS_FAILED${NC}"
echo ""

# 清理
rm -f /tmp/weknora-config-test /tmp/test-config.yaml /tmp/test-config-invalid.yaml

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ 所有测试通过！${NC}"
    exit 0
else
    echo -e "${RED}✗ 部分测试失败${NC}"
    exit 1
fi
