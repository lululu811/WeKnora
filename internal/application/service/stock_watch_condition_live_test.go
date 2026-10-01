package service

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/quoteclient"
	"github.com/Tencent/WeKnora/internal/types"
)

// 真实链路测试：真的去问 python-service 要行情，真的按本地库里那天的价格判穿越。
//
// 为什么值得单开一个：其余测试的 quote 都是构造出来的假读数，只能证明"拿到这些数
// 会这么判"，不能证明"服务端返回的字段名/类型/空值处理对得上"。这一条把中间的
// JSON 边界也覆盖了。
//
// 默认跳过 —— CI 里没有 python-service。要跑就指定一个活的地址：
//
//	WEKNORA_LIVE_QUOTES_URL=http://127.0.0.1:50099 \
//	  go test -count=1 -run TestStockWatchConditionJobLive -v ./internal/application/service/
//
// 断言依赖的是本地库里真实存在的数据（贵州茅台 2026-09-30 收 1258.62），所以库里
// 那天的数据变了这条会红 —— 那是它该有的行为，不是脆弱。
func TestStockWatchConditionJobLive(t *testing.T) {
	base := os.Getenv("WEKNORA_LIVE_QUOTES_URL")
	if base == "" {
		t.Skip("需要真实 python-service：设置 WEKNORA_LIVE_QUOTES_URL 后重跑（例 http://127.0.0.1:50099）")
	}

	db := liveConditionTestDB(t)
	ctx := context.Background()
	const user = "live-user"
	const tenant uint64 = 7

	condRepo := repository.NewStockWatchConditionRepository(db)
	notifier := &recordingNotifier{enabled: true}
	job := NewStockWatchConditionJob(
		condRepo,
		repository.NewStockWatchNotificationRepository(db),
		quoteclient.NewClientWithBase(base, 20*time.Second),
		notifier,
	)

	add := func(code, field, op string, value float64) *types.StockWatchCondition {
		t.Helper()
		row, _, err := condRepo.Create(ctx, &types.StockWatchCondition{
			UserID: user, TenantID: tenant, THSCode: code, Field: field, Op: op, Value: value,
		})
		require.NoError(t, err)
		return row
	}
	above := add("600519.SH", types.StockWatchConditionFieldPrice, types.StockWatchConditionOpAbove, 1000)
	below := add("600519.SH", types.StockWatchConditionFieldPrice, types.StockWatchConditionOpBelow, 1000)
	// 只有 1 根 K 线的票：ma20 为 null，这个条件**无法判定**。
	undecidable := add("001246.SZ", types.StockWatchConditionFieldCloseVsMA20, types.StockWatchConditionOpBelow, 0)

	// ---- 第一轮：只记录，不触发（加入时就已经满足的条件是你本来就知道的事）----
	require.NoError(t, job.RunOnce(ctx))
	assert.Equal(t, 0, countRows(t, db, "stock_watch_events"), "首次评估不得触发")
	assert.Equal(t, 0, countRows(t, db, "stock_watch_notifications"))
	assert.Empty(t, notifier.sent)

	gotAbove := reloadCondition(t, db, above.ID)
	require.NotNil(t, gotAbove.LastSatisfied, "第一轮之后必须有已决定的状态")
	assert.True(t, *gotAbove.LastSatisfied, "1258.62 > 1000 → 满足")
	require.NotNil(t, gotAbove.LastEvalDate)

	gotBelow := reloadCondition(t, db, below.ID)
	require.NotNil(t, gotBelow.LastSatisfied)
	assert.False(t, *gotBelow.LastSatisfied)

	gotUndecidable := reloadCondition(t, db, undecidable.ID)
	assert.Nil(t, gotUndecidable.LastSatisfied, "读数缺失 → 保持未评估，绝不允许写成 false")

	// ---- 模拟"上一个交易日它还不满足"：把水位退回前一天并置为 false ----
	require.NoError(t, db.Exec(
		"UPDATE stock_watch_conditions SET last_satisfied = ?, last_eval_date = ? WHERE id = ?",
		false, "2026-09-29", above.ID).Error)

	// ---- 第二轮：必须恰好触发一次 ----
	require.NoError(t, job.RunOnce(ctx))
	assert.Equal(t, 1, countRows(t, db, "stock_watch_events"), "0→1 的穿越必须写一条事件")
	assert.Equal(t, 1, countRows(t, db, "stock_watch_notifications"))

	// 事件必须带上**判定所属交易日**，而不是只留落库时间：任务在 D+1 早上报告 D 日
	// 收盘，拿 created_at 当"哪一天"会永远差一个交易日。
	var triggerEvent types.StockWatchEvent
	require.NoError(t, db.Where("kind = ?", types.StockWatchEventConditionTriggered).First(&triggerEvent).Error)
	require.NotNil(t, triggerEvent.EvalDate, "触发事件必须带 eval_date")
	assert.Equal(t, "2026-09-30", triggerEvent.EvalDate.String(),
		"应为判定所用的那个交易日（本地库最新交易日）")
	require.Len(t, notifier.sent, 1, "一次运行只推一条聚合消息")
	assert.Contains(t, notifier.sent[0], "600519.SH")
	assert.Contains(t, notifier.sent[0], "1258.62", "消息里要带判定当时的读数")
	assert.Contains(t, notifier.sent[0], "1000", "消息里要带你自己设的门槛")

	// ---- 第三轮：同一天不得重复推 ----
	require.NoError(t, job.RunOnce(ctx))
	assert.Equal(t, 1, countRows(t, db, "stock_watch_events"), "水位必须挡住同一天重复触发")
	assert.Equal(t, 1, countRows(t, db, "stock_watch_notifications"))
	assert.Len(t, notifier.sent, 1)
}

// recordingNotifier 记录被要求发送的消息，不做网络调用。
type recordingNotifier struct {
	enabled bool
	sent    []string
}

func (n *recordingNotifier) Enabled() bool { return n.enabled }

func (n *recordingNotifier) Send(_ context.Context, message string) (int, error) {
	n.sent = append(n.sent, message)
	return 1, nil
}

// liveConditionTestDB 用**真正会跑的**那些迁移建表，而不是在这里抄一份 DDL。
// 按家族通配（`*stock_watch*.up.sql`），所以新增一条家族迁移会自动生效 —— 手写列表
// 的代价是"给共享表加一列"时每个列表都要同步，漏一个就表现为 sibling 测试以
// "no column named ..." 失败。
func liveConditionTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	paths, err := filepath.Glob(filepath.Join("..", "..", "..", "migrations", "sqlite", "*stock_watch*.up.sql"))
	require.NoError(t, err)
	require.NotEmpty(t, paths, "watchlist 家族的迁移文件必须存在")
	sort.Strings(paths)
	for _, path := range paths {
		raw, err := os.ReadFile(path)
		require.NoError(t, err, "迁移文件必须存在：%s", path)
		require.NoError(t, db.Exec(string(raw)).Error, "执行迁移失败：%s", path)
	}
	return db
}

func countRows(t *testing.T, db *gorm.DB, table string) int {
	t.Helper()
	var n int64
	require.NoError(t, db.Table(table).Count(&n).Error)
	return int(n)
}

func reloadCondition(t *testing.T, db *gorm.DB, id string) *types.StockWatchCondition {
	t.Helper()
	var row types.StockWatchCondition
	require.NoError(t, db.Where("id = ?", id).First(&row).Error)
	return &row
}
