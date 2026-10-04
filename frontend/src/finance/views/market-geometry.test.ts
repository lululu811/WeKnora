/**
 * `market-geometry.ts` 的回归测试。
 *
 * 这里测的每一条都是"错了页面照样渲染、但含义全错"的坑 —— y 轴方向、昨收虚线位置、
 * 快照条点位分母、炸板率分母。它们的共同点是：不会抛异常、不会白屏，
 * 只会安静地画出一张说谎的图。所以必须跑在 CI 里而不是靠肉眼比对截图。
 */
import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import {
  CHART,
  SPARK,
  areaPath,
  barHeights,
  breadthWidths,
  linePath,
  normalize,
  prevCloseY,
  rangePosition,
  toYi,
  trendOf,
  usableCloses,
} from './market-geometry'

describe('usableCloses', () => {
  it('丢弃 null 点而不是把缺一天画成平盘', () => {
    const got = usableCloses([{ close: 1 }, { close: null }, { close: 3 }])
    assert.deepEqual(got, [1, 3])
  })

  it('丢弃 NaN / Infinity', () => {
    const got = usableCloses([{ close: 1 }, { close: NaN }, { close: Infinity }])
    assert.deepEqual(got, [1])
  })
})

describe('normalize', () => {
  it('映射到 [0,1]，0 是最低、1 是最高', () => {
    assert.deepEqual(normalize([5, 10, 15]), [0, 0.5, 1])
  })

  it('全平返回 0.5 而不是 NaN', () => {
    // min === max 时 (c-min)/span 是 0/0，图会整条消失
    assert.deepEqual(normalize([3, 3, 3]), [0.5, 0.5, 0.5])
  })

  it('空输入返回空', () => {
    assert.deepEqual(normalize([]), [])
  })
})

describe('linePath', () => {
  it('y 轴向下：值越高 y 越小', () => {
    // 这是最容易搞反的一条 —— 反了整条线上下颠倒，但页面照常渲染
    const path = linePath([0, 1], 100, 50, 0)
    const ys = [...path.matchAll(/,(-?[\d.]+)/g)].map((m) => Number(m[1]))
    assert.ok(ys[0] > ys[1], `低值应在下方(y 大)，实际 ${ys}`)
  })

  it('首点用 M 后续用 L', () => {
    const path = linePath([0, 0.5, 1], 100, 50, 0)
    assert.equal((path.match(/M/g) || []).length, 1)
    assert.equal((path.match(/L/g) || []).length, 2)
  })

  it('两点起才画线；单点不画横线', () => {
    assert.equal(linePath([], 100, 50, 0), '')
    // 单点给零长度 path，不给"这段时间没波动"那条会骗人的横线
    assert.equal(linePath([0.5], 100, 50, 0).includes('L'), false)
  })
})

describe('areaPath', () => {
  it('封底到图表下沿', () => {
    const path = areaPath([0, 1], 100, 50, 0)
    assert.ok(path.endsWith('Z'), '应闭合')
    // 末段是 L(width-pad,height) L(pad,height) Z —— 封到图表下沿
    assert.ok(path.includes('L100.0,50'), `应封到 (width, height)：${path}`)
    assert.ok(path.includes('L0,50'), `应封回左下：${path}`)
  })

  it('单点/空不产生面积', () => {
    assert.equal(areaPath([], 100, 50, 0), '')
    assert.equal(areaPath([0.5], 100, 50, 0), '')
  })
})

describe('prevCloseY', () => {
  it('用倒数第二根（昨收），不是最后一根', () => {
    const norm = normalize([10, 20, 30])
    const y = prevCloseY(norm, CHART.height, CHART.pad)
    // 倒数第二 = 20，归一化 0.5 → y = pad + 0.5*(h-2*pad)
    const expected = CHART.pad + 0.5 * (CHART.height - CHART.pad * 2)
    assert.equal(y, Number(expected.toFixed(1)))
  })

  it('只有一个点时没有昨收可画', () => {
    assert.equal(prevCloseY([0.5], CHART.height, CHART.pad), null)
    assert.equal(prevCloseY([], CHART.height, CHART.pad), null)
  })
})

describe('rangePosition', () => {
  it('最低=0%、最高=100%、中间=50%', () => {
    assert.equal(rangePosition(10, 10, 20), 0)
    assert.equal(rangePosition(20, 10, 20), 100)
    assert.equal(rangePosition(15, 10, 20), 50)
  })

  it('越界夹到边界，不让点位跑出轨道外', () => {
    assert.equal(rangePosition(99, 10, 20), 100)
    assert.equal(rangePosition(-5, 10, 20), 0)
  })

  it('一字板（low === high）落中点', () => {
    // 除零会得到 NaN，样式 left:NaN% 整个点位消失
    assert.equal(rangePosition(10, 10, 10), 50)
  })

  it('任一端缺失返回 null（不画点）', () => {
    assert.equal(rangePosition(null, 10, 20), null)
    assert.equal(rangePosition(15, null, 20), null)
    assert.equal(rangePosition(15, 10, null), null)
  })
})

describe('barHeights', () => {
  it('按最大值归一并留 12% 余量，最高那根不到 100', () => {
    const h = barHeights([54, 62, 48, 71, 68])
    const peak = Math.max(...h)
    assert.ok(peak <= 90 && peak > 85, `最高柱应接近但不顶死，实际 ${peak}`)
  })

  it('全 0 时不除零', () => {
    assert.deepEqual(barHeights([0, 0, 0]), [0, 0, 0])
  })

  it('空输入返回空', () => {
    assert.deepEqual(barHeights([]), [])
  })
})

describe('breadthWidths', () => {
  it('三段之和为 100', () => {
    const w = breadthWidths({ up: 3412, flat: 212, down: 1805 })
    assert.equal(Number((w.up + w.flat + w.down).toFixed(0)), 100)
  })

  it('全 null 时不崩，给 0/0/0', () => {
    assert.deepEqual(breadthWidths({ up: null, flat: null, down: null }), { up: 0, flat: 0, down: 0 })
  })

  it('部分缺失按 0 参与，不改写已有段的比例之外的值', () => {
    const w = breadthWidths({ up: 50, flat: null, down: 50 })
    assert.equal(w.up, 50)
    assert.equal(w.down, 50)
    assert.equal(w.flat, 0)
  })

  it('全 0 时不除零', () => {
    assert.deepEqual(breadthWidths({ up: 0, flat: 0, down: 0 }), { up: 0, flat: 0, down: 0 })
  })
})

describe('trendOf', () => {
  it('0 算涨（A股红涨），不是平', () => {
    assert.equal(trendOf(0), 'up')
  })

  it('负值是跌', () => {
    assert.equal(trendOf(-0.01), 'down')
  })

  it('null 不染色', () => {
    // 关键：把"没数据"画成"平"或"涨"都是编造
    assert.equal(trendOf(null), null)
    assert.equal(trendOf(NaN), null)
  })
})

describe('toYi', () => {
  it('元转亿元带正负号', () => {
    assert.equal(toYi(4.82e8), '+4.82')
    assert.equal(toYi(-0.42e8), '−0.42')
  })

  it('null 原样返回', () => {
    assert.equal(toYi(null), null)
  })
})

describe('常量', () => {
  it('viewBox 与样稿一致', () => {
    assert.equal(CHART.width, 260)
    assert.equal(CHART.height, 64)
    assert.equal(SPARK.width, 58)
    assert.equal(SPARK.height, 22)
  })
})
