/**
 * 主图多指标的 isStack 语义回归测试。
 *
 * 背景：klinecharts 的 IndicatorStore.addInstance 里有这么一句 ——
 *
 *     if (!isStack) { paneInstances = [] }
 *
 * 也就是说 createIndicator(name, false, {id: 'candle_pane'}) 不是「往主图加一个」，
 * 而是「**清空主图再放这一个**」。主图循环里逐个 add 且传 false 时，前一个会被后一个
 * 抹掉，最终只活下来最后一个：Z_MAIN（黄白线 + BBI）被紧随其后的 Z_DONCHIAN
 * （唐奇安通道）整组顶掉，图上就只剩唐奇安，双线+BBI 消失。
 *
 * 这里把「多主图指标必须共存」钉死，防止将来再被改成 false。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { coreChartOptions } from './core-chart';
import path from 'node:path';

const SRC = path.join(process.cwd(), 'src/finance/components/kline/core-chart.ts');
const source = fs.readFileSync(SRC, 'utf8');

/** 复刻 klinecharts IndicatorStore 的实例管理（只保留与本 bug 相关的分支）。 */
class FakeIndicatorStore {
  private _instances = new Map<string, Array<{ name: string }>>();

  addInstance(name: string, paneId: string, isStack: boolean): Promise<boolean> {
    let paneInstances = this._instances.get(paneId);
    if (paneInstances?.some((i) => i.name === name)) {
      return Promise.reject(new Error('Duplicate indicators.'));
    }
    if (!paneInstances) paneInstances = [];
    if (!isStack) paneInstances = [];
    paneInstances.push({ name });
    this._instances.set(paneId, paneInstances);
    return Promise.resolve(true);
  }

  getInstances(paneId: string): Array<{ name: string }> {
    return this._instances.get(paneId) ?? [];
  }
}

test('klinecharts 的 !isStack 会清空整个 pane —— 这正是双线+BBI 消失的机制', async () => {
  const store = new FakeIndicatorStore();
  await store.addInstance('Z_MAIN', 'candle_pane', false);
  await store.addInstance('Z_DONCHIAN', 'candle_pane', false);
  assert.deepEqual(
    store.getInstances('candle_pane').map((i) => i.name),
    ['Z_DONCHIAN'],
    '传 false 时后建的会顶掉先建的',
  );

  const stacked = new FakeIndicatorStore();
  await stacked.addInstance('Z_MAIN', 'candle_pane', true);
  await stacked.addInstance('Z_DONCHIAN', 'candle_pane', true);
  assert.deepEqual(
    stacked.getInstances('candle_pane').map((i) => i.name),
    ['Z_MAIN', 'Z_DONCHIAN'],
    '传 true 才能让多个主图指标共存',
  );
});

test('createCoreChart 建主图指标时 isStack 必须是 true', () => {
  // 建图路径：主图循环里 createIndicator 的第二个参数
  const initBlock = source.slice(
    source.indexOf('const mainNames = options.mainIndicators'),
    source.indexOf('// 副图：`createIndicator(name, true)`'),
  );
  assert.ok(initBlock.length > 0, '应能找到建图时的主图指标循环');
  assert.ok(
    /createIndicator\(name,\s*true,\s*\{\s*id:\s*'candle_pane'\s*\}\)/.test(initBlock),
    'createCoreChart 里主图 createIndicator 必须传 isStack=true',
  );
  assert.ok(
    !/createIndicator\([^)]*,\s*false,\s*\{\s*id:\s*'candle_pane'/.test(source),
    'core-chart.ts 任何地方都不应再出现 candle_pane + isStack=false',
  );
});

test('swapIndicators 重建主图时 isStack 也必须是 true', () => {
  const swapBlock = source.slice(
    source.indexOf('export function swapIndicators'),
    source.indexOf('// 副图：差集增删'),
  );
  assert.ok(swapBlock.length > 0, '应能找到 swapIndicators 的主图重建段');
  assert.ok(
    /createIndicator\(name,\s*true,\s*\{\s*id:\s*'candle_pane'\s*\}\)/.test(swapBlock),
    'swapIndicators 里主图 createIndicator 必须传 isStack=true，否则切指标时又只剩最后一个',
  );
});

test('主图为空时仍然要有兜底指标（MA），且不能只建一个就丢掉其余', () => {
  assert.ok(
    /options\.mainIndicators\.length > 0 \? options\.mainIndicators : \['MA'\]/.test(source),
    'mainIndicators 为空时应回落到 MA',
  );
  assert.ok(
    !/createIndicator\(options\.mainIndicators\[0\] \?\? 'MA'/.test(source),
    "不应再用 '只建第一个 + 循环 add' 的写法（第二个会把第一个清掉）",
  );
});

// ── 两个外壳共用同一份建图选项 ─────────────────────────────────────────────
//
// 重工作台（KLineWorkspace）与自选股侧栏里的轻量图（WatchKLineChart）都要
// 建一张 klinecharts 实例。同一只票在两处画出来，X 轴、时区、locale 得
// 长得一样 —— 否则两处的读数没法互相印证，出了分歧也没法判断谁对。
//
// 这两条断言盯的是「别再各写一份」：谁把 options 抄回自己文件里，测试就红。
test('两个外壳都从 core-chart 取建图选项，不各自抄一份', () => {
  const workspace = fs.readFileSync(
    path.join(process.cwd(), 'src/finance/components/kline/KLineWorkspace.vue'), 'utf8');
  const light = fs.readFileSync(
    path.join(process.cwd(), 'src/finance/components/watchlist/WatchKLineChart.vue'), 'utf8');

  for (const [name, src] of [['KLineWorkspace.vue', workspace], ['WatchKLineChart.vue', light]] as const) {
    assert.ok(
      !/init\([^)]*locale:\s*'zh-CN'/.test(src),
      `${name} 里还留着抄写的 init options —— locale/时区/X 轴格式应当只有一份`);
  }
  // 重的那个走 createCoreChart（它内部用 coreChartOptions），
  // 轻的那个自己建实例，所以直接调 coreChartOptions。
  assert.ok(
    /createCoreChart\(/.test(workspace),
    'KLineWorkspace.vue 应当走 createCoreChart(...) 建图');
  assert.ok(
    /coreChartOptions\(/.test(light),
    'WatchKLineChart.vue 应当用 coreChartOptions(...) 建图，不要自己再抄一份 options');
});

test('coreChartOptions 的 X 轴按粒度显示日期，与两处原先的实现一致', () => {
  const options = coreChartOptions({} as never);
  const format = (options.customApi as { formatDate: Function }).formatDate;
  // 周K/月K 的时间戳精确到某一天 00:00，默认格式会把同一周的 7 根 K 线
  // 标成同一个完整时刻，窄面板下必然重叠糊成一片。
  assert.equal(format(new Intl.DateTimeFormat(), Date.UTC(2026, 9, 20), '', 2), '2026-10-20');
  assert.equal(format(new Intl.DateTimeFormat(), Date.UTC(2026, 8, 1), '', 1), '2026-09-01');
  assert.equal(options.locale, 'zh-CN');
  assert.equal(options.timezone, 'Asia/Shanghai');
});
