// 外部测试包（repository_test）而不是 repository：这一组要驱动 service 里的
// 每日作业，而 service 包 import 了 repository，放进 package repository 就成了
// import cycle。三个 watchlist 仓储测试同住这个外测包，于是共用一份迁移拼接
// helper（watchlistTestDDL）。
package repository_test

import (
	"context"
	"math"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/Tencent/WeKnora/internal/watchcond"
)

// stockWatchConditionTestDDL 读**真正会跑的那几条迁移**，不在这里抄 DDL。
//
// 抄一份只能证明抄的那份对；而这两张新表的 GORM 字段约定（列名、NULL 语义、唯一键）
// 必须与迁移逐条对得上。家族内新增迁移自动生效，见 watchlistTestDDL。
func stockWatchConditionTestDDL(t *testing.T) string {
	t.Helper()
	return watchlistTestDDL(t)
}

func setupStockWatchConditionDB(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := "file:stock-watch-cond-" + t.Name() + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.Exec(stockWatchConditionTestDDL(t)).Error)
	return db
}

// stubQuotes is a scripted quote source: the test sets the reading it wants the
// run to see, then flips the date to simulate the next trading day.
type stubQuotes struct {
	readings map[string]watchcond.Reading
	err      error
	calls    int
}

func (s *stubQuotes) Fetch(_ context.Context, symbols []string) (map[string]watchcond.Reading, error) {
	s.calls++
	if s.err != nil {
		return nil, s.err
	}
	out := make(map[string]watchcond.Reading, len(symbols))
	for _, code := range symbols {
		if r, ok := s.readings[code]; ok {
			out[code] = r
		}
	}
	return out, nil
}

type stubNotifier struct {
	enabled  bool
	sent     []string
	attempts int
	err      error
}

func (s *stubNotifier) Enabled() bool { return s.enabled }

func (s *stubNotifier) Send(_ context.Context, message string) (int, error) {
	s.sent = append(s.sent, message)
	if s.err != nil {
		return s.attempts, s.err
	}
	return s.attempts, nil
}

type conditionFixture struct {
	db            *gorm.DB
	repo          interfaces.StockWatchConditionRepository
	notifications interfaces.StockWatchNotificationRepository
	events        interfaces.StockWatchEventsRepository
	svc           interfaces.StockWatchConditionService
	quotes        *stubQuotes
	notifier      *stubNotifier
	job           *service.StockWatchConditionJob
}

const (
	condTestUser   = "user-1"
	condTestTenant = uint64(7)
	condTestSymbol = "600519.SH"
)

func setupConditionFixture(t *testing.T) *conditionFixture {
	t.Helper()
	db := setupStockWatchConditionDB(t)
	repo := repository.NewStockWatchConditionRepository(db)
	notifications := repository.NewStockWatchNotificationRepository(db)
	quotes := &stubQuotes{readings: map[string]watchcond.Reading{}}
	notifier := &stubNotifier{enabled: true, attempts: 1}
	job := service.NewStockWatchConditionJob(repo, notifications, quotes, notifier)
	return &conditionFixture{
		db:            db,
		repo:          repo,
		notifications: notifications,
		events:        repository.NewStockWatchEventsRepository(db),
		svc:           service.NewStockWatchConditionService(repo),
		quotes:        quotes,
		notifier:      notifier,
		job:           job,
	}
}

func (f *conditionFixture) create(t *testing.T, field, op string, value float64) *types.StockWatchCondition {
	t.Helper()
	row, _, err := f.svc.Create(context.Background(), condTestUser, condTestTenant, condTestSymbol, field, op, value)
	require.NoError(t, err)
	return row
}

func (f *conditionFixture) only(t *testing.T) *types.StockWatchCondition {
	t.Helper()
	list, err := f.repo.List(context.Background(), condTestUser, condTestTenant, condTestSymbol)
	require.NoError(t, err)
	require.Len(t, list, 1)
	return list[0]
}

// 双击不能造出第二条：同一个 (thscode, field, op, value) 是幂等的。
func TestStockWatchConditionCreateIsIdempotent(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()

	first, created, err := f.svc.Create(ctx, condTestUser, condTestTenant, condTestSymbol, "price", "below", 1235)
	require.NoError(t, err)
	require.True(t, created)
	require.NotEmpty(t, first.ID, "id 由仓储生成，长度要能放进 varchar(36)")
	require.Len(t, first.ID, 36)
	require.Nil(t, first.LastSatisfied, "新条件从未评估，必须是 NULL 而不是 false")
	require.Nil(t, first.LastEvalDate)

	second, created, err := f.svc.Create(ctx, condTestUser, condTestTenant, condTestSymbol, "price", "below", 1235)
	require.NoError(t, err)
	assert.False(t, created, "重复添加必须是已存在的那条")
	assert.Equal(t, first.ID, second.ID)

	list, err := f.repo.List(ctx, condTestUser, condTestTenant, condTestSymbol)
	require.NoError(t, err)
	assert.Len(t, list, 1, "唯一键必须挡住第二条")

	// 不同的阈值是另一条；同一个阈值在另一个空间也是另一条。
	_, created, err = f.svc.Create(ctx, condTestUser, condTestTenant, condTestSymbol, "price", "below", 1200)
	require.NoError(t, err)
	assert.True(t, created)
	_, created, err = f.svc.Create(ctx, condTestUser, 99, condTestSymbol, "price", "below", 1235)
	require.NoError(t, err)
	assert.True(t, created, "换空间必须换一份条件")
}

// 白名单与有限数校验发生在**服务**层，且被拒绝时不落库。
func TestStockWatchConditionValidationRejections(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()

	cases := []struct {
		name  string
		field string
		op    string
		value float64
		want  error
	}{
		{"unknown field", "pe_ratio", "above", 15, service.ErrStockWatchConditionInvalidField},
		{"unknown op", "price", "around", 15, service.ErrStockWatchConditionInvalidOp},
		{"NaN value", "price", "above", math.NaN(), service.ErrStockWatchConditionInvalidValue},
		{"+Inf value", "price", "above", math.Inf(1), service.ErrStockWatchConditionInvalidValue},
		{"-Inf value", "price", "below", math.Inf(-1), service.ErrStockWatchConditionInvalidValue},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := f.svc.Create(ctx, condTestUser, condTestTenant, condTestSymbol, tc.field, tc.op, tc.value)
			require.Error(t, err)
			assert.ErrorIs(t, err, tc.want)
		})
	}

	// 非法 thscode 同样被正常化的那条路挡下（与跟踪池共用 normaliseCode）。
	_, _, err := f.svc.Create(ctx, condTestUser, condTestTenant, "not-a-code", "price", "above", 1)
	assert.ErrorIs(t, err, service.ErrStockWatchInvalidCode)

	list, err := f.repo.List(ctx, condTestUser, condTestTenant, condTestSymbol)
	require.NoError(t, err)
	assert.Empty(t, list, "被拒绝的添加不该留下任何行")
}

// 一次完整的三天回放：首次只记录、0→1 才触发、同一天不重复触发。
func TestStockWatchConditionJobFiresOnlyOnTheCrossing(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()
	f.create(t, "price", "below", 1235)

	// Day 1: 1300 在线之上 → 记录 false，不触发，也不推送。
	f.quotes.readings[condTestSymbol] = watchcond.Reading{
		Name: "贵州茅台", Date: "2026-10-02", Close: new(1300.0),
	}
	require.NoError(t, f.job.RunOnce(ctx))
	cond := f.only(t)
	require.NotNil(t, cond.LastSatisfied)
	assert.False(t, *cond.LastSatisfied)
	require.NotNil(t, cond.LastEvalDate)
	assert.Equal(t, "2026-10-02", cond.LastEvalDate.String())
	assert.Empty(t, f.notifier.sent, "没有穿越就没有推送")

	// Day 2: 新交易日、跌破线 → 0→1，触发一次并推一条。
	f.quotes.readings[condTestSymbol] = watchcond.Reading{
		Name: "贵州茅台", Date: "2026-10-03", Close: new(1200.0),
	}
	require.NoError(t, f.job.RunOnce(ctx))
	cond = f.only(t)
	require.NotNil(t, cond.LastSatisfied)
	assert.True(t, *cond.LastSatisfied)
	assert.Equal(t, "2026-10-03", cond.LastEvalDate.String())

	events, err := f.events.List(ctx, condTestUser, condTestTenant, condTestSymbol, 50)
	require.NoError(t, err)
	require.Len(t, events, 1, "0→1 恰好一条 condition_triggered")
	assert.Equal(t, types.StockWatchEventConditionTriggered, events[0].Kind)
	assert.Contains(t, events[0].Note, "跌破 1235")
	assert.Empty(t, events[0].FromState)
	assert.Empty(t, events[0].ToState)

	// 注意：作业绝不写跟踪池的 state —— `triggered` 只能由人来标。
	poolRows, err := repository.NewStockWatchRepository(f.db).List(ctx, condTestUser, condTestTenant)
	require.NoError(t, err)
	assert.Empty(t, poolRows)

	require.Len(t, f.notifier.sent, 1, "整轮一条聚合消息")
	assert.Contains(t, f.notifier.sent[0], "600519.SH 贵州茅台")

	// Day 2 再跑一次（同一天、同一份数据）：水位挡住，不重复触发。
	require.NoError(t, f.job.RunOnce(ctx))
	events, err = f.events.List(ctx, condTestUser, condTestTenant, condTestSymbol, 50)
	require.NoError(t, err)
	assert.Len(t, events, 1, "同一个交易日不能二次触发")
	assert.Len(t, f.notifier.sent, 1)

	// 通知审计：3 次运行里只有真正推送的那一次写了 ok=true 的行。
	var notifications []*types.StockWatchNotification
	require.NoError(t, f.db.Where("user_id = ? AND tenant_id = ?", condTestUser, condTestTenant).
		Order("id ASC").Find(&notifications).Error)
	require.Len(t, notifications, 1)
	assert.True(t, notifications[0].OK)
	assert.Equal(t, types.StockWatchNotificationConditionTriggered, notifications[0].Kind)
	assert.Equal(t, 1, notifications[0].Attempts)
	assert.Contains(t, notifications[0].Payload, "价格跌破 1235")
}

// 首次评估即已满足 → 只记录、不触发；推送到下一个交易日才可能发生。
func TestStockWatchConditionJobFirstEvaluationRecordsWithoutFiring(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()
	f.create(t, "price", "below", 1235)

	f.quotes.readings[condTestSymbol] = watchcond.Reading{
		Name: "贵州茅台", Date: "2026-10-02", Close: new(1200.0), // already below
	}
	require.NoError(t, f.job.RunOnce(ctx))

	cond := f.only(t)
	require.NotNil(t, cond.LastSatisfied)
	assert.True(t, *cond.LastSatisfied, "首次评估要记录真实状态")
	events, err := f.events.List(ctx, condTestUser, condTestTenant, condTestSymbol, 50)
	require.NoError(t, err)
	assert.Empty(t, events, "加进来时就已经满足的条件不该推送")
	assert.Empty(t, f.notifier.sent)
}

// 读数为 null（历史不足）→ 判不了：不写状态、不推水位，等数据到了再判。
func TestStockWatchConditionJobUndecidableKeepsWaiting(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()
	f.create(t, "price", "below", 1235)
	// 先人为设置成"已判定为 false，水位停在 10-01"，模拟一个老条件。
	require.NoError(t, f.db.Model(&types.StockWatchCondition{}).
		Where("user_id = ? AND tenant_id = ?", condTestUser, condTestTenant).
		Updates(map[string]interface{}{"last_satisfied": false, "last_eval_date": "2026-10-01"}).Error)

	// 新交易日但 close 为 null → 无法判定。
	f.quotes.readings[condTestSymbol] = watchcond.Reading{Date: "2026-10-02", Close: nil}
	require.NoError(t, f.job.RunOnce(ctx))
	cond := f.only(t)
	require.NotNil(t, cond.LastEvalDate)
	assert.Equal(t, "2026-10-01", cond.LastEvalDate.String(), "判不了就不能推进水位")
	require.NotNil(t, cond.LastSatisfied)
	assert.False(t, *cond.LastSatisfied)
	events, err := f.events.List(ctx, condTestUser, condTestTenant, condTestSymbol, 50)
	require.NoError(t, err)
	assert.Empty(t, events)

	// 数据补齐后（同一交易日），条件仍应被判定并触发。
	f.quotes.readings[condTestSymbol] = watchcond.Reading{Date: "2026-10-02", Close: new(1200.0)}
	require.NoError(t, f.job.RunOnce(ctx))
	events, err = f.events.List(ctx, condTestUser, condTestTenant, condTestSymbol, 50)
	require.NoError(t, err)
	assert.Len(t, events, 1, "补上读数后这一次必须触发")
}

// 行情拉取失败 → 在任何状态写入之前中止；绝不半途推进水位。
func TestStockWatchConditionJobAbortsBeforeAnyWriteOnQuoteFailure(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()
	f.create(t, "price", "below", 1235)
	require.NoError(t, f.db.Model(&types.StockWatchCondition{}).
		Where("user_id = ? AND tenant_id = ?", condTestUser, condTestTenant).
		Updates(map[string]interface{}{"last_satisfied": false, "last_eval_date": "2026-10-01"}).Error)

	f.quotes.err = assert.AnError
	require.Error(t, f.job.RunOnce(ctx))

	cond := f.only(t)
	require.NotNil(t, cond.LastEvalDate)
	assert.Equal(t, "2026-10-01", cond.LastEvalDate.String(), "拉取失败不能推进水位")
	events, err := f.events.List(ctx, condTestUser, condTestTenant, condTestSymbol, 50)
	require.NoError(t, err)
	assert.Empty(t, events)
}

// webhook 未配置：照常写触发事件（页面是主面），审计行说明"没推"。
func TestStockWatchConditionJobWithoutWebhookStillWritesEvents(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()
	f.notifier.enabled = false
	f.create(t, "price", "below", 1235)
	require.NoError(t, f.db.Model(&types.StockWatchCondition{}).
		Where("user_id = ? AND tenant_id = ?", condTestUser, condTestTenant).
		Updates(map[string]interface{}{"last_satisfied": false, "last_eval_date": "2026-10-01"}).Error)

	f.quotes.readings[condTestSymbol] = watchcond.Reading{Date: "2026-10-02", Close: new(1200.0)}
	require.NoError(t, f.job.RunOnce(ctx))

	events, err := f.events.List(ctx, condTestUser, condTestTenant, condTestSymbol, 50)
	require.NoError(t, err)
	assert.Len(t, events, 1, "没有 webhook 也必须留下触发事件")
	assert.Empty(t, f.notifier.sent)

	var notifications []*types.StockWatchNotification
	require.NoError(t, f.db.Where("user_id = ? AND tenant_id = ?", condTestUser, condTestTenant).
		Find(&notifications).Error)
	require.Len(t, notifications, 1)
	assert.False(t, notifications[0].OK)
	assert.Zero(t, notifications[0].Attempts, "没配置 = 没有发起任何尝试")
	assert.Contains(t, notifications[0].Error, "webhook")
}

// 每日作业的 cron 能起来、能停，且重复 Start/Stop 不出错 —— 这是容器接线走的那条路。
func TestStockWatchConditionJobStartsAndStops(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()
	require.NoError(t, f.job.Start(ctx))
	require.NoError(t, f.job.Start(ctx), "重复 Start 必须是幂等的")
	f.job.StopWithin(time.Second)
	f.job.StopWithin(time.Second)
}

// 删除按 (user, tenant, thscode, id) 全量限定：换一只票的路径删不掉。
func TestStockWatchConditionRemoveIsScoped(t *testing.T) {
	f := setupConditionFixture(t)
	ctx := context.Background()
	row := f.create(t, "price", "below", 1235)

	removed, err := f.svc.Remove(ctx, condTestUser, condTestTenant, "000001.SZ", row.ID)
	require.NoError(t, err)
	assert.False(t, removed, "路径里的票与条件不属于同一只时不能删")

	removed, err = f.svc.Remove(ctx, condTestUser, condTestTenant, condTestSymbol, row.ID)
	require.NoError(t, err)
	assert.True(t, removed)

	// 再删一次不算错。
	removed, err = f.svc.Remove(ctx, condTestUser, condTestTenant, condTestSymbol, row.ID)
	require.NoError(t, err)
	assert.False(t, removed)
}
