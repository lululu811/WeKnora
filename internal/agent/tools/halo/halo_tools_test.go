package halo

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolNamesAndSchema(t *testing.T) {
	client := NewHTTPClient("")
	tools := []struct {
		tool interface {
			Name() string
			Parameters() json.RawMessage
			Description() string
		}
		want string
	}{
		{NewSyncTool(client), "halo.filing.sync"},
		{NewQueryTool(client), "halo.filing.query"},
	}

	for _, tc := range tools {
		t.Run(tc.want, func(t *testing.T) {
			assert.Equal(t, tc.want, tc.tool.Name())

			var schema struct {
				Type       string                 `json:"type"`
				Properties map[string]interface{} `json:"properties"`
				Required   []string               `json:"required"`
			}
			require.NoError(t, json.Unmarshal(tc.tool.Parameters(), &schema))
			assert.Equal(t, "object", schema.Type)
			assert.Contains(t, schema.Properties, "thscode")
			assert.Contains(t, schema.Required, "thscode", "thscode 必须是必填，否则会打到巨潮的无效请求")
		})
	}
}

// 工具描述是模型唯一的说明书。HALO 的价值主张是「数据不编造」，而模型
// 只有读到「存疑数字不能当依据 / 取不到就标缺失」才会照做，所以这几条
// 措辞是行为约束而不是文案，删掉等于撤掉护栏。
func TestDescriptionsCarryDataIntegrityRules(t *testing.T) {
	client := NewHTTPClient("")

	syncDesc := NewSyncTool(client).Description()
	for _, must := range []string{
		"巨潮",              // 权威源
		"对账",              // 校验机制
		"不会",              // 明确不会用本地库顶替
		"disputed",        // 状态语义
		"employees_total", // 补的是哪个缺口
	} {
		assert.Contains(t, syncDesc, must, "sync 描述缺少关键约束：%s", must)
	}

	queryDesc := NewQueryTool(client).Description()
	for _, must := range []string{
		"consolidated", // 合并 vs 母公司口径
		"不能",           // 存疑不可用
		"估算",           // 禁止补值
	} {
		assert.Contains(t, queryDesc, must, "query 描述缺少关键约束：%s", must)
	}
}

func TestSyncRejectsEmptyThscode(t *testing.T) {
	client := NewHTTPClient("http://127.0.0.1:1") // 不会真的发请求
	res, err := NewSyncTool(client).Execute(context.Background(), json.RawMessage(`{}`))
	require.NoError(t, err, "参数错误应作为工具结果返回，不是 Go error")
	assert.False(t, res.Success)
	assert.Contains(t, res.Error, "thscode")
}

func TestQueryRejectsEmptyThscode(t *testing.T) {
	client := NewHTTPClient("http://127.0.0.1:1")
	res, err := NewQueryTool(client).Execute(context.Background(), json.RawMessage(`{"thscode":""}`))
	require.NoError(t, err)
	assert.False(t, res.Success)
	assert.Contains(t, res.Error, "thscode")
}

func TestSyncDefaultsReportTypeToAnnual(t *testing.T) {
	var got SyncRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.NoError(t, json.NewDecoder(r.Body).Decode(&got))
		w.Write([]byte(`{"thscode":"600519","report_type":"annual"}`))
	}))
	defer srv.Close()

	res, err := NewSyncTool(NewHTTPClient(srv.URL)).Execute(
		context.Background(), json.RawMessage(`{"thscode":"600519"}`))
	require.NoError(t, err)
	require.True(t, res.Success)
	assert.Equal(t, "annual", got.ReportType,
		"未指定 report_type 时应默认年报——它是唯一含员工人数的口径")
}

func TestSyncSurfacesUpstreamErrors(t *testing.T) {
	cases := []struct {
		status int
		body   string
		expect string
	}{
		{http.StatusNotFound, `{"detail":"巨潮无该股票年报"}`, "未找到对应披露文件"},
		{http.StatusServiceUnavailable, `{"detail":"financials 数据源不可用"}`, "数据源不可用"},
		{http.StatusBadRequest, `boom`, "HALO 请求失败"},
	}

	for _, tc := range cases {
		t.Run(http.StatusText(tc.status), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			res, err := NewSyncTool(NewHTTPClient(srv.URL)).Execute(
				context.Background(), json.RawMessage(`{"thscode":"600519"}`))
			require.NoError(t, err)
			assert.False(t, res.Success)
			assert.True(t, strings.Contains(res.Error, tc.expect),
				"错误信息应说明发生了什么，实际：%s", res.Error)
		})
	}
}

func TestQuerySendsOnlyVerifiedByDefault(t *testing.T) {
	var got QueryRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.NoError(t, json.NewDecoder(r.Body).Decode(&got))
		w.Write([]byte(`{"thscode":"600519","facts":[]}`))
	}))
	defer srv.Close()

	res, err := NewQueryTool(NewHTTPClient(srv.URL)).Execute(
		context.Background(), json.RawMessage(`{"thscode":"600519"}`))
	require.NoError(t, err)
	require.True(t, res.Success)
	assert.Nil(t, got.OnlyVerified,
		"客户端不下发该参数时由服务端默认 only_verified=true；"+
			"若客户端默认下发 false，未经交叉验证的数字会流进评分")
}
