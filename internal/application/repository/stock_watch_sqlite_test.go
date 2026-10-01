package repository_test

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// watchlistTestDDL 拼出 watchlist 家族**全部**的 up 迁移。
//
// 用通配符扫 `*stock_watch*.up.sql`，而不是手写文件名列表。理由是
// stock_watch_events 被本目录多个测试共用：**只要有人给它加一列**（例如 000037 加了
// eval_date），每一个手写列表都要跟着改，漏一个就表现为 sibling 测试以
// "table stock_watch_events has no column named ..." 失败 —— 这件事已经真实发生过两次。
// 通配符让"新增一条家族迁移"自动生效；文件名里的 6 位补零序号保证拼接顺序即执行顺序。
func watchlistTestDDL(t *testing.T) string {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join("..", "..", "..", "migrations", "sqlite", "*stock_watch*.up.sql"))
	require.NoError(t, err)
	require.NotEmpty(t, paths, "watchlist 家族的迁移文件必须存在")
	sort.Strings(paths)
	var ddl strings.Builder
	for _, path := range paths {
		raw, err := os.ReadFile(path)
		require.NoError(t, err, "迁移文件必须存在：%s", path)
		ddl.Write(raw)
		ddl.WriteString("\n")
	}
	return ddl.String()
}

// stockWatchTestDDL 读**真正会跑的**那些迁移，而不是在这里抄一份 CREATE TABLE。
//
// 抄一份的代价是它会在某次改迁移时静默失联：测试继续绿，生产的表却已经和 GORM 的
// 字段约定对不上了（最典型的是 NOT NULL 却没有 DEFAULT 的列，插入时才炸）。
// 同目录的 system_model_catalog_test.go 用的也是这个做法。
func stockWatchTestDDL(t *testing.T) string {
	t.Helper()
	return watchlistTestDDL(t)
}

func setupStockWatchTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	// 共享缓存的命名内存库：GORM 的连接池会开多条连接，:memory: 每条连接一个
	// 独立库，表就"找不到"了。
	dsn := "file:stock-watch-" + t.Name() + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.Exec(stockWatchTestDDL(t)).Error)
	return db
}

func TestStockWatchRepositoryAddListRemove(t *testing.T) {
	db := setupStockWatchTestDB(t)
	repo := repository.NewStockWatchRepository(db)
	ctx := context.Background()

	const user = "user-1"
	const tenant uint64 = 7

	row, created, err := repo.Add(ctx, &types.StockWatch{
		UserID: user, TenantID: tenant, THSCode: "600519.SH", Name: "贵州茅台", Exchange: "SH",
	})
	require.NoError(t, err)
	assert.True(t, created)
	assert.Equal(t, "贵州茅台", row.Name)
	assert.False(t, row.CreatedAt.IsZero(), "created_at 必须由迁移的 DEFAULT 填上")
	assert.False(t, row.UpdatedAt.IsZero(), "updated_at 必须由迁移的 DEFAULT 填上")

	// 重复加入是 upsert，不是第二行；名称被刷新，加入时间不被改写。
	time.Sleep(10 * time.Millisecond)
	again, created, err := repo.Add(ctx, &types.StockWatch{
		UserID: user, TenantID: tenant, THSCode: "600519.SH", Name: "贵州茅台-ST", Exchange: "SH",
	})
	require.NoError(t, err)
	assert.False(t, created, "同一 (user, tenant, thscode) 只应有一行")
	assert.Equal(t, "贵州茅台-ST", again.Name, "重新加入要刷新名称")

	list, err := repo.List(ctx, user, tenant)
	require.NoError(t, err)
	require.Len(t, list, 1)
	assert.Equal(t, row.CreatedAt.UTC(), list[0].CreatedAt.UTC(), "重新加入不该重置加入时间")

	count, err := repo.Count(ctx, user, tenant)
	require.NoError(t, err)
	assert.EqualValues(t, 1, count)

	removed, err := repo.Remove(ctx, user, tenant, "600519.SH")
	require.NoError(t, err)
	assert.True(t, removed)

	// 再删一次不算错：调用方的目标（这行不存在）已经成立。
	removed, err = repo.Remove(ctx, user, tenant, "600519.SH")
	require.NoError(t, err)
	assert.False(t, removed)
}

func TestStockWatchRepositoryOrderAndTenantScope(t *testing.T) {
	db := setupStockWatchTestDB(t)
	repo := repository.NewStockWatchRepository(db)
	ctx := context.Background()

	const user = "user-1"
	const tenant uint64 = 7

	add := func(tenantID uint64, code string, sortOrder int) {
		t.Helper()
		_, _, err := repo.Add(ctx, &types.StockWatch{
			UserID: user, TenantID: tenantID, THSCode: code, SortOrder: sortOrder,
		})
		require.NoError(t, err)
	}

	// 默认 sort_order 全是 0：顺序应退化成加入顺序（created_at）。
	add(tenant, "600519.SH", 0)
	time.Sleep(10 * time.Millisecond)
	add(tenant, "000001.SZ", 0)
	time.Sleep(10 * time.Millisecond)
	// 置顶：负数排到最前。
	add(tenant, "300750.SZ", -1)
	// 另一个空间：同一个人、同一只票，但必须是另一行、另一份清单。
	add(99, "600519.SH", 0)

	list, err := repo.List(ctx, user, tenant)
	require.NoError(t, err)
	require.Len(t, list, 3, "换空间必须换清单")
	assert.Equal(t, []string{"300750.SZ", "600519.SH", "000001.SZ"},
		[]string{list[0].THSCode, list[1].THSCode, list[2].THSCode})

	other, err := repo.List(ctx, user, 99)
	require.NoError(t, err)
	require.Len(t, other, 1)
	assert.Equal(t, "600519.SH", other[0].THSCode)

	otherCount, err := repo.Count(ctx, user, 99)
	require.NoError(t, err)
	assert.EqualValues(t, 1, otherCount)
}

func TestStockWatchRepositoryUpdatePatchesOnlyTheTargetRow(t *testing.T) {
	db := setupStockWatchTestDB(t)
	repo := repository.NewStockWatchRepository(db)
	ctx := context.Background()

	const user = "user-1"
	const tenant uint64 = 7

	for _, code := range []string{"600519.SH", "000001.SZ"} {
		_, _, err := repo.Add(ctx, &types.StockWatch{UserID: user, TenantID: tenant, THSCode: code, Name: code})
		require.NoError(t, err)
	}

	name := "贵州茅台"
	order := -5
	updated, err := repo.Update(ctx, user, tenant, "600519.SH", interfaces.StockWatchPatch{
		Name: &name, SortOrder: &order,
	})
	require.NoError(t, err)
	require.NotNil(t, updated)
	assert.Equal(t, "贵州茅台", updated.Name)
	assert.Equal(t, -5, updated.SortOrder)

	// 邻居行不受影响 —— 这是 Model(&rec) 按复合主键定位 UPDATE 的那条路径，
	// 写成无条件 Updates 就会把它一起改掉。
	rows, err := repo.List(ctx, user, tenant)
	require.NoError(t, err)
	require.Len(t, rows, 2)
	// 被置顶的那行排到最前，名称与排序都是更新后的值。
	assert.Equal(t, "贵州茅台", rows[0].Name)
	assert.Equal(t, -5, rows[0].SortOrder)
	// 邻居行原封不动 —— 这条断言盯的是 "Model(&rec).Updates" 按复合主键定位
	// 的那条路径，写成无条件的 Updates 就会把它一起改掉。
	assert.Equal(t, "000001.SZ", rows[1].Name)
	assert.Equal(t, 0, rows[1].SortOrder)

	// 空 patch 是"什么都不改"，不是清空字段。
	untouched, err := repo.Update(ctx, user, tenant, "600519.SH", interfaces.StockWatchPatch{})
	require.NoError(t, err)
	require.NotNil(t, untouched)
	assert.Equal(t, "贵州茅台", untouched.Name)

	// 不在清单里的代码：返回 (nil, nil)，让 handler 自己决定是 404 还是忽略。
	missing, err := repo.Update(ctx, user, tenant, "999999.SH", interfaces.StockWatchPatch{Name: &name})
	require.NoError(t, err)
	assert.Nil(t, missing)
}
