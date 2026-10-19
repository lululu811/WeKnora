/**
 * 图层开关的交互回归测试。
 *
 * 背景：四个图层开关曾经全部「点了没反应」，两类原因：
 *
 * 1. **双重翻转**。开关的 onChange 写成 `isX.value = val; toggleX()`，而 toggleX
 *    内部又执行 `isX.value = !isX.value` —— 两次翻转互相抵消，值弹回原处。
 *    九转（toggleTD9）和关键位（toggleLevels）当年就是这么写的。
 *
 * 2. **勾选面板没挂上**。LayerFilterDropdown.vue 与 syncBubbleTypes / syncOutline
 *    都写好了却零调用方，面板上写着「气泡 (28/28)」却点不进去逐项关，只能整层关。
 *
 * 这里把两条都钉死。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const SRC = path.join(process.cwd(), 'src/finance/components/kline/KLineWorkspace.vue');
const source = fs.readFileSync(SRC, 'utf8');

test('图层开关的 onChange 不得再叠加会翻转状态的函数（双重翻转会互相抵消）', () => {
  // 已经不存在这两个「自己会翻转」的函数了
  assert.ok(
    !/const toggleTD9\s*=/.test(source),
    'toggleTD9 应已删除：它在 onChange 里再翻一次会抵消赋值',
  );
  assert.ok(
    !/const toggleLevels\s*=/.test(source),
    'toggleLevels 应已删除：它在 onChange 里再翻一次会抵消赋值',
  );
  // onChange 里只允许「直接赋值」，不再调用 toggle*
  // 开关回调的参数注解有 boolean（onUpdate:enabled）与 any（onChange）两种写法，
  // 这里要收的是「有没有再叠一个会翻转状态的 toggle*」，不是参数注解类型，
  // 所以两种都匹配，否则会漏检一半开关。
  const onChangeBodies = [
    ...source.matchAll(/onChange:\s*\(val:\s*(?:any|boolean)\)\s*=>\s*\{([^}]*)\}/g),
    ...source.matchAll(/'onUpdate:enabled':\s*\(v:\s*boolean\)\s*=>\s*\{([^}]*)\}/g),
  ].map((m) => m[1]);
  assert.ok(onChangeBodies.length >= 3, `应至少有 3 个图层开关，实际 ${onChangeBodies.length} 个`);
  for (const body of onChangeBodies) {
    assert.ok(
      !/toggle[A-Z]/.test(body),
      `onChange 里不应再调用 toggle*()（双重翻转）：${body.trim()}`,
    );
  }
});

test('四个图层开关都要把变化推给绘制层（赋值后必须重绘）', () => {
  // 九转 / 气泡 / 轮廓 / 关键位
  for (const [name, key] of [
    ['九转', 'showTD9'],
    ['气泡', 'showPatterns'],
    ['轮廓', 'patternGeometry'],
  ] as const) {
    assert.ok(
      source.includes(key),
      `${name} 图层应通过 setGlobalOverlayConfig({ ${key} }) 推给绘制层`,
    );
  }
  assert.ok(
    /watch\(isTD9Enabled,[\s\S]{0,200}setGlobalOverlayConfig\(\{ showTD9/.test(source),
    'isTD9Enabled 变化后必须 setGlobalOverlayConfig + repaintOverlay',
  );
  assert.ok(
    /watch\(isLevelsEnabled,[\s\S]{0,200}redrawOverlays/.test(source),
    'isLevelsEnabled 变化后必须 redrawOverlays（关键位是 overlay，要重画）',
  );
});

test('LayerFilterDropdown 必须真的挂进图层下拉，且逐项勾选要接到绘制层', () => {
  assert.ok(
    /h\(LayerFilterDropdown,/.test(source),
    '气泡/轮廓两层的逐项勾选面板必须挂进 layerOptions，否则用户只能整层开关',
  );
  // 两层各挂一个
  const count = (source.match(/h\(LayerFilterDropdown,/g) || []).length;
  assert.ok(count >= 2, `气泡层与轮廓层各应有一个勾选面板，实际 ${count} 个`);

  // 逐项勾选 / 全选要真的调 syncBubbleTypes / syncOutline 并持久化
  assert.ok(
    /const onBubbleToggle[\s\S]{0,300}syncBubbleTypes\(/.test(source),
    '气泡逐项勾选必须调用 syncBubbleTypes 推到绘制层',
  );
  assert.ok(
    /const onOutlineToggle[\s\S]{0,300}syncOutline\(/.test(source),
    '轮廓逐项勾选必须调用 syncOutline 推到绘制层',
  );
  assert.ok(
    /const onBubbleToggle[\s\S]{0,300}saveSelection\(/.test(source),
    '气泡勾选必须持久化（跨切票、跨刷新保留）',
  );
  assert.ok(
    /const onOutlineToggle[\s\S]{0,300}saveSelection\(/.test(source),
    '轮廓勾选必须持久化',
  );
});

test('syncBubbleTypes / syncOutline 必须有调用方（写好了却没人用＝功能没上线）', () => {
  const declared = (source.match(/const syncBubbleTypes\s*=/g) || []).length;
  assert.strictEqual(declared, 1, 'syncBubbleTypes 应只声明一次');
  const calls = (source.match(/syncBubbleTypes\(/g) || []).length;
  assert.ok(calls > declared, 'syncBubbleTypes 必须有调用方');
});

test('勾选面板必须用 inline 模式（不套 popup），否则嵌进 dropdown 后整个不渲染', () => {
  const mounts = [...source.matchAll(/h\(LayerFilterDropdown,\s*\{([\s\S]*?)\n      \}\)/g)].map((m) => m[1]);
  assert.ok(mounts.length >= 2, `气泡/轮廓两层都应挂面板，实际 ${mounts.length} 个`);
  for (const props of mounts) {
    assert.ok(
      /inline:\s*true/.test(props),
      '嵌进 t-dropdown 的面板必须 inline=true：默认是自带 t-popup 的独立弹层，' +
        'popup 套 popup 又没有触发器 slot，结果是面板整个不渲染、只剩标题文字',
    );
  }
});

test('面板样式不能是 scoped —— dropdown 会 teleport 到 body，scoped 样式套不上', () => {
  const comp = fs.readFileSync(
    path.join(process.cwd(), 'src/finance/components/kline/LayerFilterDropdown.vue'),
    'utf8',
  );
  assert.ok(
    !/<style\s+scoped/.test(comp),
    'LayerFilterDropdown 的样式不能是 scoped：t-dropdown 把内容 teleport 到 body，' +
      'scoped 的 data-v 属性选择器匹配不到，表现为"渲染出来但全是浏览器默认样式"',
  );
});

test('t-dropdown 的 max-height 必须调大（默认 300px 会把第二层整个截掉）', () => {
  // TDesign 把菜单高度写成**内联 style**（style="max-height:300px"），CSS 覆盖不了，
  // 只能通过 :max-height prop 传大值。
  const layerDropdown = source.slice(
    source.indexOf(':options="layerOptions"'),
    source.indexOf(':options="drawingOptions"'),
  );
  assert.ok(layerDropdown.length > 0, '应能找到图层下拉的 t-dropdown');
  assert.ok(
    /:max-height="\d{3,}"/.test(layerDropdown),
    '图层下拉必须显式调大 max-height，否则「轮廓」层会被默认 300px 截断',
  );
});

test('内联列表限高必须低于视口，且组内分两列', () => {
  const comp = fs.readFileSync(
    path.join(process.cwd(), 'src/finance/components/kline/LayerFilterDropdown.vue'),
    'utf8',
  );
  // 两层各 460px + 头部，再叠加 dropdown 自身的 560px 上限。
  // 实测：面板最高 568px，视口 771px，底部还剩 200px 余量 ——
  // 之前不限高时实测底部溢出视口 74px，「轮廓」层与「关键位」开关点不到。
  const limit = comp.match(
    /\.kline-layer-panel--inline \.kline-layer-panel__list \{[^}]*max-height:\s*(\d+)px/,
  );
  assert.ok(limit, '内联列表应显式限高（不限高会溢出视口，底部开关点不到）');
  assert.ok(
    Number(limit[1]) <= 500,
    `内联列表限高应 <=500px（实测超出会溢出视口），当前 ${limit[1]}px`,
  );
  assert.ok(
    /grid-template-columns:\s*repeat\(2,/.test(comp),
    '组内应分两列：24 项单列排下来有 720px 高，两列才排得进一屏',
  );
});

test('分组可折叠：项数少的组常驻展开，项多的才折叠', () => {
  const comp = fs.readFileSync(
    path.join(process.cwd(), 'src/finance/components/kline/LayerFilterDropdown.vue'),
    'utf8',
  );
  assert.ok(
    /collapsibleGroups/.test(comp) && /const MAX = 8/.test(comp),
    '应按组项数决定是否可折叠（<=8 项不值得折，省下的高度抵不过多点一次）',
  );
  // 折叠状态存「展开的组」而不是「折叠的组」：新分组默认展开，
  // 不会出现后端新增分组后用户看不见、也没有任何提示。
  assert.ok(
    /const openGroups = ref<Set<string>>/.test(comp),
    '折叠状态应存展开的组（存折叠的会让未来新增分组静默消失）',
  );
});

test('t-switch 必须用组件引用，不能用字符串标签名', () => {
  // h('t-switch', ...) 里的字符串标签名在 render 函数里**不会**被解析成组件：
  // DOM 里会出现 <t-switch modelvalue="true"> 这种未解析标签，开关不显示。
  assert.ok(
    /import \{ Switch as TSwitch \}/.test(source),
    '应从 tdesign-vue-next 显式导入 Switch',
  );
  assert.ok(
    !/h\('t-switch'/.test(source),
    "不能用 h('t-switch', ...) 的字符串标签名，会渲染成未解析标签",
  );
  assert.ok(
    /h\(TSwitch,/.test(source),
    '图层开关应使用 h(TSwitch, ...) 组件引用',
  );
});
