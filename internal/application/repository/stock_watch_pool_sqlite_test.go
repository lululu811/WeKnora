// 外部测试包（repository_test）而不是 repository：这一组断言里有一条要盯住
// **服务**对非法状态值的拒绝，而 service 包本身 import 了 repository
// （agent_service.go），放在 package repository 里就成了 import cycle。
// 三个 watchlist 仓储测试同住这个外测包，于是可以共用一份迁移拼接 helper
// （watchlistTestDDL）。
package repository_test

import (
	"context"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// stockWatchPoolTestDDL 读**真正会跑的**那些迁移，而不是在这里抄一份 DDL。
//
// "默认状态是 observing"这条断言的全部意义就在于验证**迁移里的 DEFAULT**：抄一份
// DDL 的测试只能证明抄的那份是对的。家族内新增迁移会自动被 watchlistTestDDL 扫到。
func stockWatchPoolTestDDL(t *testing.T) string {
	t.Helper()
	return watchlistTestDDL(t)
}

func setupStockWatchPoolTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	// 共享缓存的命名内存库：GORM 的连接池会开多条连接，:memory: 每条连接一个
	// 独立库，表就"找不到"了。
	dsn := "file:stock-watch-pool-" + t.Name() + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.Exec(stockWatchPoolTestDDL(t)).Error)
	return db
}

// setupStockWatchPoolFixture 建好整条链路：仓储 + 事件仓储 + 服务。
// 服务也要建，因为"非法状态值被服务拒绝"这条断言盯的是服务的入口，
// 只测仓储会把校验放在错误的层上还浑然不觉。
func setupStockWatchPoolFixture(
	t *testing.T,
) (*gorm.DB, interfaces.StockWatchRepository, interfaces.StockWatchEventsRepository, interfaces.StockWatchService) {
	t.Helper()
	db := setupStockWatchPoolTestDB(t)
	repo := repository.NewStockWatchRepository(db)
	events := repository.NewStockWatchEventsRepository(db)
	return db, repo, events, service.NewStockWatchService(repo, events)
}

const (
	poolTestUser   = "user-1"
	poolTestTenant = uint64(7)
)

// 默认状态必须来自迁移的 DEFAULT，而不是服务显式写入的那份。
// 所以这条用裸 SQL 插一行、连 state 列都不提，再看读回来是什么。
func TestStockWatchPoolStateColumnDefaultsToObserving(t *testing.T) {
	db, repo, _, _ := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	require.NoError(t, db.Exec(
		`INSERT INTO stock_watches (user_id, tenant_id, thscode, name, exchange) VALUES (?, ?, ?, '', '')`,
		poolTestUser, poolTestTenant, "600519.SH",
	).Error)

	list, err := repo.List(ctx, poolTestUser, poolTestTenant)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, types.StockWatchStateObserving, list[0].State,
		"没写 state 的行必须落到迁移的 DEFAULT 'observing'")
	assert.Empty(t, list[0].Note)

	// 服务自己加进来的那一行同样是 observing —— 两条入口不能各说各话。
	row, created, err := service.NewStockWatchService(repo, repository.NewStockWatchEventsRepository(db)).
		Add(ctx, poolTestUser, poolTestTenant, "000001.SZ", "平安银行", "SZ")
	require.NoError(t, err)
	require.True(t, created)
	assert.Equal(t, types.StockWatchStateObserving, row.State)
}

// 新增即一条 added 事件，且事件里写的是真的落库状态。
func TestStockWatchPoolAddWritesAddedEvent(t *testing.T) {
	_, _, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	row, created, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)
	require.True(t, created)
	require.Equal(t, types.StockWatchStateObserving, row.State)

	list, err := events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, types.StockWatchEventAdded, list[0].Kind)
	assert.Equal(t, "600519.SH", list[0].THSCode)
	assert.Empty(t, list[0].FromState, "added 没有来处")
	assert.Equal(t, types.StockWatchStateObserving, list[0].ToState)

	// 重复加入不是新事件：票没有离开过池子。
	_, created, err = svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台-ST", "SH")
	require.NoError(t, err)
	assert.False(t, created)
	list, err = events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	assert.Len(t, list, 1, "重新加入不该再记一条 added")
}

// 一次状态迁移 = 恰好一条 state_changed 事件，from/to 如实。
func TestStockWatchPoolStateChangeWritesExactlyOneEvent(t *testing.T) {
	_, _, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)

	row, err := svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		State: new(types.StockWatchStateHolding),
	})
	require.NoError(t, err)
	require.NotNil(t, row)
	assert.Equal(t, types.StockWatchStateHolding, row.State)

	all, err := events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	require.Len(t, all, 2, "added + state_changed")

	// 最新的在前。
	assert.Equal(t, types.StockWatchEventAdded, all[1].Kind)
	assert.Equal(t, types.StockWatchEventStateChanged, all[0].Kind)
	assert.Equal(t, types.StockWatchStateObserving, all[0].FromState)
	assert.Equal(t, types.StockWatchStateHolding, all[0].ToState)

	// 重复 PUT 同一个状态：不是迁移，也就不该再记一条。
	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		State: new(types.StockWatchStateHolding),
	})
	require.NoError(t, err)
	all, err = events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	assert.Len(t, all, 2, "把状态写成它已有的值不算一次状态变更")
}

// 备注变更记一条 note_changed，并且事件里存的是**当时的快照**，不是引用。
func TestStockWatchPoolNoteChangeWritesSnapshotEvent(t *testing.T) {
	_, repo, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)

	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		Note: new("等回踩 55 日线"),
	})
	require.NoError(t, err)

	all, err := events.List(ctx, poolTestUser, poolTestTenant, "600519.SH", 50)
	require.NoError(t, err)
	require.Len(t, all, 2)
	assert.Equal(t, types.StockWatchEventNoteChanged, all[0].Kind)
	assert.Equal(t, "等回踩 55 日线", all[0].Note)
	assert.Empty(t, all[0].FromState)
	assert.Empty(t, all[0].ToState)

	// 再改一次备注：旧事件里的那句话不能被改写 —— 那正是"历史"的全部价值。
	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		Note: new("已破位，不追"),
	})
	require.NoError(t, err)
	all, err = events.List(ctx, poolTestUser, poolTestTenant, "600519.SH", 50)
	require.NoError(t, err)
	require.Len(t, all, 3)
	assert.Equal(t, "已破位，不追", all[0].Note)
	assert.Equal(t, "等回踩 55 日线", all[1].Note, "旧事件的备注必须是快照")

	// 行上是新值。
	list, err := repo.List(ctx, poolTestUser, poolTestTenant)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, "已破位，不追", list[0].Note)
}

// 未知状态值在**服务**层被哨兵错误挡下，且不落库、不记事件。
func TestStockWatchPoolRejectsUnknownState(t *testing.T) {
	_, repo, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)

	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		State: new("bogus"),
	})
	require.Error(t, err)
	assert.ErrorIs(t, err, service.ErrStockWatchInvalidState)

	list, err := repo.List(ctx, poolTestUser, poolTestTenant)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, types.StockWatchStateObserving, list[0].State, "被拒绝的更新不该动到行")

	all, err := events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	assert.Len(t, all, 1, "被拒绝的更新不该留下事件")
}

// 状态机里不存在的迁移（dropped → holding）同样被挡下：UI 只画合法的那几条，
// 但 UI 不是保护 —— 一个停在旧版本的页面照样能把请求发出来。
func TestStockWatchPoolRejectsIllegalTransition(t *testing.T) {
	_, repo, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)
	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		State: new(types.StockWatchStateDropped),
	})
	require.NoError(t, err)

	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		State: new(types.StockWatchStateHolding),
	})
	require.Error(t, err)
	assert.ErrorIs(t, err, types.ErrStockWatchIllegalTransition)

	list, err := repo.List(ctx, poolTestUser, poolTestTenant)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, types.StockWatchStateDropped, list[0].State)

	all, err := events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	assert.Len(t, all, 2, "added + 那次成功的 dropped，非法迁移没有第三条")
}

// 行更新失败时不能留下事件。
//
// 用 SQLite 触发器把 UPDATE 打成 ABORT，而不是造一个"看起来会失败"的 patch：
// 触发器失败的位置正好在事务内部，是这条不变量能被证伪的地方。
func TestStockWatchPoolNoEventWhenRowUpdateFails(t *testing.T) {
	db, repo, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)

	require.NoError(t, db.Exec(`
		CREATE TRIGGER fail_stock_watch_update BEFORE UPDATE ON stock_watches
		BEGIN SELECT RAISE(ABORT, 'boom'); END;
	`).Error)

	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		State: new(types.StockWatchStateHolding),
	})
	require.Error(t, err, "触发器必须让这次更新失败，否则这条测试什么也没测到")

	list, err := repo.List(ctx, poolTestUser, poolTestTenant)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, types.StockWatchStateObserving, list[0].State)

	all, err := events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	assert.Len(t, all, 1, "行没改成，事件也不该存在")
	assert.Equal(t, types.StockWatchEventAdded, all[0].Kind)
}

// 反过来也要成立：事件写不进去时，行更新必须跟着回滚。
// 只测一个方向的话，"同一个事务"这句话有一半是没有证据的。
func TestStockWatchPoolRowUpdateRollsBackWhenEventInsertFails(t *testing.T) {
	db, repo, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)

	require.NoError(t, db.Exec(`
		CREATE TRIGGER fail_stock_watch_event_insert BEFORE INSERT ON stock_watch_events
		BEGIN SELECT RAISE(ABORT, 'boom'); END;
	`).Error)

	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		State: new(types.StockWatchStateHolding),
	})
	require.Error(t, err, "触发器必须让事件写入失败，否则这条测试什么也没测到")

	list, err := repo.List(ctx, poolTestUser, poolTestTenant)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, types.StockWatchStateObserving, list[0].State,
		"事件没写成，行更新必须回滚 —— 否则池子变了却没有留下任何痕迹")

	all, err := events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	require.Len(t, all, 1)
	assert.Equal(t, types.StockWatchEventAdded, all[0].Kind)
}

// 事件流按 (user, tenant) 隔离，且能按 thscode 收窄。
func TestStockWatchPoolEventsAreScopedAndFilterable(t *testing.T) {
	_, _, events, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)
	_, _, err = svc.Add(ctx, poolTestUser, poolTestTenant, "000001.SZ", "平安银行", "SZ")
	require.NoError(t, err)
	// 同一个人、另一个空间：另一份池子，另一段历史。
	_, _, err = svc.Add(ctx, poolTestUser, 99, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)

	pool, err := events.List(ctx, poolTestUser, poolTestTenant, "", 50)
	require.NoError(t, err)
	assert.Len(t, pool, 2, "换空间必须换历史")

	one, err := events.List(ctx, poolTestUser, poolTestTenant, "600519.SH", 50)
	require.NoError(t, err)
	require.Len(t, one, 1)
	assert.Equal(t, "600519.SH", one[0].THSCode)

	other, err := events.List(ctx, poolTestUser, 99, "", 50)
	require.NoError(t, err)
	assert.Len(t, other, 1)
}

// 备注长度上限在服务层挡下，不让 varchar(200) 的截断/报错成为用户体验。
func TestStockWatchPoolRejectsOverlongNote(t *testing.T) {
	_, _, _, svc := setupStockWatchPoolFixture(t)
	ctx := context.Background()

	_, _, err := svc.Add(ctx, poolTestUser, poolTestTenant, "600519.SH", "贵州茅台", "SH")
	require.NoError(t, err)

	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		Note: new(strings.Repeat("备", types.MaxStockWatchNoteLen+1)),
	})
	require.Error(t, err)
	assert.ErrorIs(t, err, service.ErrStockWatchNoteTooLong)

	// 恰好到上限是允许的 —— 边界值不能只测越界那一侧。
	_, err = svc.Update(ctx, poolTestUser, poolTestTenant, "600519.SH", interfaces.StockWatchPatch{
		Note: new(strings.Repeat("备", types.MaxStockWatchNoteLen)),
	})
	require.NoError(t, err)
}
