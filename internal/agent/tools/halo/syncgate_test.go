package halo

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestSyncGateRejectsSameSymbol(t *testing.T) {
	g := newSyncGate(2)
	release, err := g.acquire(context.Background(), "600519")
	if err != nil {
		t.Fatalf("首次获取应成功：%v", err)
	}
	defer release()

	// 同一标的第二次必须立刻失败，且错误信息要说清是「已在同步」而不是「限流」。
	if _, err := g.acquire(context.Background(), "600519"); err == nil {
		t.Fatal("同一标的重复同步应当被拒绝")
	} else if !strings.Contains(err.Error(), "正在同步中") {
		t.Fatalf("错误信息应说明已在同步中，实际：%v", err)
	}
}

func TestSyncGateNormalizesSymbol(t *testing.T) {
	g := newSyncGate(2)
	release, err := g.acquire(context.Background(), " 600519.sh ")
	if err != nil {
		t.Fatalf("首次获取应成功：%v", err)
	}
	defer release()

	// 大小写与空白不同但指向同一只票，不该被当成两个标的放行 —— 否则互斥形同虚设。
	if _, err := g.acquire(context.Background(), "600519.SH"); err == nil {
		t.Fatal("大小写/空白归一后应视为同一标的并拒绝")
	}
}

func TestSyncGateAllowsDistinctSymbolsUpToCap(t *testing.T) {
	g := newSyncGate(2)
	r1, err := g.acquire(context.Background(), "600519")
	if err != nil {
		t.Fatalf("第一个标的应放行：%v", err)
	}
	defer r1()
	r2, err := g.acquire(context.Background(), "000001")
	if err != nil {
		t.Fatalf("闸门未满时第二个标的应放行：%v", err)
	}
	defer r2()

	// 第三个要排队：acquire 阻塞，所以用已取消的 ctx 立刻验证它确实在等。
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := g.acquire(ctx, "300033"); err == nil {
		t.Fatal("超出全局并发上限时不应立即放行")
	}
}

func TestSyncGateReleasesSlotOnCancel(t *testing.T) {
	g := newSyncGate(1)
	release, err := g.acquire(context.Background(), "600519")
	if err != nil {
		t.Fatalf("首次获取应成功：%v", err)
	}
	release()

	// release 之后再取必须成功；若 release 没有把名额还回来，这里会永久阻塞。
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	release2, err := g.acquire(ctx, "600519")
	if err != nil {
		t.Fatalf("release 后应能重新获取：%v", err)
	}
	release2()
}

func TestSyncGateReleaseIsIdempotent(t *testing.T) {
	g := newSyncGate(1)
	release, err := g.acquire(context.Background(), "600519")
	if err != nil {
		t.Fatalf("首次获取应成功：%v", err)
	}
	release()

	// B 合法占用容量为 1 的唯一名额。此时闸门是满的。
	releaseB, err := g.acquire(context.Background(), "000001")
	if err != nil {
		t.Fatalf("release 后 B 应能占用名额：%v", err)
	}
	defer releaseB()

	// A 的第二次 release 若不是幂等的（无 sync.Once），就会把 B 的名额也抽走，
	// 造成并发数超过上限。所以这里再 release 一次 A，然后验证闸门**仍然**是满的。
	release()
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	if _, err := g.acquire(ctx, "300033"); err == nil {
		t.Fatal("重复 release 不应抽走他人占用的名额，否则并发会超过上限")
	}
}

func TestSyncGateRejectsEmptySymbol(t *testing.T) {
	g := newSyncGate(2)
	if _, err := g.acquire(context.Background(), "   "); err == nil {
		t.Fatal("空标的应被拒绝")
	}
}
