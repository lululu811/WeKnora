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
