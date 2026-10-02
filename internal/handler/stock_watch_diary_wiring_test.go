package handler

import (
	"testing"

	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/stretchr/testify/require"
	"go.uber.org/dig"
)

// TestStockWatchDiaryDIResolution resolves the diary's dependency subgraph.
//
// Why this is needed at all: dig resolves types at Invoke time, so a
// constructor whose parameters do not line up with a registered provider
// compiles cleanly and fails on the first boot of the day. The container's own
// TestRouterWiring is the end-to-end guard, but it cannot run in every
// environment (this package's tests are the ones that can), so the diary's own
// constructors are pinned here where they will actually execute.
//
// This test earned its keep immediately. Two earlier versions of the container
// wiring registered a decorator that took a handler and returned that same
// handler type. Both are dependency cycles, and `go build` reported nothing
// wrong with either one — dig rejects them at Invoke, which is boot. The
// lesson is the reason this file exists: for a container, compiling is not
// wiring.
func TestStockWatchDiaryDIResolution(t *testing.T) {
	c := dig.New()

	require.NoError(t, c.Provide(func() interfaces.StockWatchService { return nil }))
	require.NoError(t, c.Provide(func() interfaces.StockWatchConditionService { return nil }))
	require.NoError(t, c.Provide(func() *service.StockWatchDiaryService { return nil }))

	// The diary handler as the container registers it: its own provider, never
	// a decorator on the watch handler.
	require.NoError(t, c.Provide(NewStockWatchDiaryHandler))
	require.NoError(t, c.Provide(NewStockWatchHandler))

	var diary *StockWatchDiaryHandler
	require.NoError(t, c.Invoke(func(h *StockWatchDiaryHandler) { diary = h }),
		"the diary handler must resolve from the providers the container registers")
	require.NotNil(t, diary)

	var watch *StockWatchHandler
	require.NoError(t, c.Invoke(func(h *StockWatchHandler) { watch = h }),
		"the watch handler must still resolve alongside the diary")
	require.NotNil(t, watch)
}

// TestStockWatchDiaryJobAttachment pins the second attachment: the diary is
// added to the existing 08:30 condition job by mutating it inside an Invoke.
//
// The shape is asserted rather than re-derived. A provider taking
// *StockWatchConditionJob and returning *StockWatchConditionJob is the same
// cycle the handler adapter had, and this test is the thing that would have
// caught it in the container package.
func TestStockWatchDiaryJobAttachment(t *testing.T) {
	c := dig.New()
	require.NoError(t, c.Provide(func() *service.StockWatchConditionJob { return nil }))
	require.NoError(t, c.Provide(func() *service.StockWatchDiaryService { return nil }))
	require.NoError(t, c.Provide(func() interfaces.StockWatchDiaryRepository { return nil }))

	// The container's Invoke, verbatim in shape: an in-place mutation, whose
	// only requirement is that every dependency resolves. The providers return
	// typed nils on purpose — what is under test is the GRAPH, not the values,
	// and a non-nil value would need a database.
	resolved := false
	require.NoError(t, c.Invoke(func(
		job *service.StockWatchConditionJob,
		diaries *service.StockWatchDiaryService,
		diaryRepo interfaces.StockWatchDiaryRepository,
	) {
		resolved = true
		job.WithDiary(diaries, diaryRepo)
	}))
	require.True(t, resolved, "the Invoke must actually run once every dependency resolves")

	// A nil job must be a no-op rather than a panic: the container's own
	// startStockWatchConditionJob tolerates a nil job for the same reason.
	require.NotPanics(t, func() {
		var nilJob *service.StockWatchConditionJob
		nilJob.WithDiary(nil, nil)
	})
}
