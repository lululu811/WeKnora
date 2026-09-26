/**
 * Z 哥交易体系形态识别器（TypeScript 实现）
 *
 * 识别 B1/B2/B3/S1、关键K、暴力K 等经典形态
 *
 * 从 kline-service/app/core/annotator.py 翻译而来
 */
import { calcKDJ } from './indicators.js';
export class ZettarancAnnotator {
    /**
     * 检测 B1 建仓波信号
     *
     * Z 哥定义:
     * - 建仓波：底部横盘后，连续中大阳线放量上升（不涨停）
     * - B1 信号：建仓波后的第一次回调低点，KDJ 的 J 值 < 13
     * - 两个 30% 原则：涨幅 ≈ 30%，累计换手率 ≈ 30%
     */
    detectB1(bars) {
        const patterns = [];
        if (bars.length < 30)
            return patterns;
        const close = bars.map(b => b.close);
        const high = bars.map(b => b.high);
        const low = bars.map(b => b.low);
        const volume = bars.map(b => b.volume);
        // 计算 KDJ
        const kdj = calcKDJ(high, low, close);
        const jValues = kdj.j;
        // 识别大阳线（2.5% - 9.5%，不涨停）
        const isBigYang = bars.map(bar => {
            const bodyPct = ((bar.close - bar.open) / bar.open) * 100;
            return bar.close > bar.open && bodyPct > 2.5 && bodyPct < 9.5;
        });
        // 滑动窗口检测
        for (let i = 25; i < bars.length - 3; i++) {
            const windowStart = Math.max(0, i - 25);
            const windowEnd = i - 5;
            // 统计窗口内大阳线数量
            let yangCount = 0;
            for (let j = windowStart; j <= windowEnd; j++) {
                if (isBigYang[j])
                    yangCount++;
            }
            if (yangCount >= 3) {
                const waveGain = ((bars[windowEnd].close - bars[windowStart].open) / bars[windowStart].open) * 100;
                if (waveGain >= 20 && waveGain <= 50) {
                    const jVal = jValues[i];
                    if (jVal !== null && jVal < 13) {
                        // 检查回调缩量
                        const pullbackVol = volume.slice(i - 5, i).reduce((a, b) => a + b, 0) / 5;
                        const waveVol = volume.slice(windowStart, windowEnd + 1).reduce((a, b) => a + b, 0) / (windowEnd - windowStart + 1);
                        if (pullbackVol < waveVol * 0.7) {
                            patterns.push({
                                type: 'b1',
                                date: bars[i].date,
                                price: bars[i].low,
                                text: `B1 (J=${jVal.toFixed(1)})`,
                                confidence: Math.min(0.95, 0.5 + (13 - jVal) / 30),
                                metadata: {
                                    wave_gain: Math.round(waveGain * 100) / 100,
                                    j_value: Math.round(jVal * 100) / 100,
                                    yang_count: yangCount,
                                },
                            });
                        }
                    }
                }
            }
        }
        return patterns;
    }
    /**
     * 检测关键K
     * Z 哥定义：位置关键 + 量能匹配的 K 线
     */
    detectKeyK(bars) {
        const patterns = [];
        if (bars.length < 20)
            return patterns;
        const volume = bars.map(b => b.volume);
        // 计算 20 日均量
        const volMA = [];
        for (let i = 0; i < volume.length; i++) {
            if (i < 19) {
                volMA.push(null);
            }
            else {
                const sum = volume.slice(i - 19, i + 1).reduce((a, b) => a + b, 0);
                volMA.push(sum / 20);
            }
        }
        for (let i = 20; i < bars.length; i++) {
            const bar = bars[i];
            const body = Math.abs(bar.close - bar.open);
            const upperShadow = bar.high - Math.max(bar.close, bar.open);
            const lowerShadow = Math.min(bar.close, bar.open) - bar.low;
            // 十字星：实体 < 上下影线之和的 30%
            const isDoji = body < (upperShadow + lowerShadow) * 0.3;
            // 缩量：成交量 < 20 日均量的 70%
            const ma = volMA[i];
            const lowVolume = ma !== null && bar.volume < ma * 0.7;
            if (isDoji && lowVolume) {
                patterns.push({
                    type: 'key_k',
                    date: bar.date,
                    price: bar.close,
                    text: '关键K (十字星)',
                    confidence: 0.6,
                    metadata: {
                        volume_ratio: ma !== null ? Math.round((bar.volume / ma) * 100) / 100 : 0,
                    },
                });
            }
        }
        return patterns;
    }
    /**
     * 检测 S1 卖出信号
     * Z 哥定义：高位放量阶段性顶部预警
     */
    detectS1(bars) {
        const patterns = [];
        if (bars.length < 60)
            return patterns;
        const volume = bars.map(b => b.volume);
        // 计算 20 日均量
        const volMA = [];
        for (let i = 0; i < volume.length; i++) {
            if (i < 19) {
                volMA.push(null);
            }
            else {
                const sum = volume.slice(i - 19, i + 1).reduce((a, b) => a + b, 0);
                volMA.push(sum / 20);
            }
        }
        for (let i = 60; i < bars.length; i++) {
            const bar = bars[i];
            // 60 日新高区域（> 95%）
            const high60 = Math.max(...bars.slice(i - 59, i + 1).map(b => b.high));
            const isHigh = bar.high > high60 * 0.95;
            // 放量：成交量 > 20 日均量 × 2
            const ma = volMA[i];
            const bigVolume = ma !== null && bar.volume > ma * 2;
            // 长上影线（> 实体 × 1.5）
            const body = Math.abs(bar.close - bar.open);
            const upperShadow = bar.high - Math.max(bar.close, bar.open);
            const longUpper = upperShadow > body * 1.5;
            if (isHigh && bigVolume && longUpper) {
                patterns.push({
                    type: 's1',
                    date: bar.date,
                    price: bar.high,
                    text: 'S1 (高位放量)',
                    confidence: 0.75,
                    metadata: {
                        volume_ratio: ma !== null ? Math.round((bar.volume / ma) * 100) / 100 : 0,
                    },
                });
            }
        }
        return patterns;
    }
    /**
     * 检测暴力K
     * Z 哥定义：底部突兀 + 倍量/天量的破坏性长阳/长阴
     */
    detectViolentK(bars) {
        const patterns = [];
        if (bars.length < 60)
            return patterns;
        const volume = bars.map(b => b.volume);
        // 计算 20 日均量
        const volMA = [];
        for (let i = 0; i < volume.length; i++) {
            if (i < 19) {
                volMA.push(null);
            }
            else {
                const sum = volume.slice(i - 19, i + 1).reduce((a, b) => a + b, 0);
                volMA.push(sum / 20);
            }
        }
        for (let i = 60; i < bars.length; i++) {
            const bar = bars[i];
            const bodyPct = Math.abs(bar.close - bar.open) / bar.open * 100;
            const ma = volMA[i];
            const doubleVolume = ma !== null && bar.volume > ma * 2;
            // 低位判定
            const low60 = Math.min(...bars.slice(i - 59, i + 1).map(b => b.low));
            const isLow = bar.low < low60 * 1.15;
            if (bodyPct > 5 && doubleVolume && isLow) {
                const direction = bar.close > bar.open ? '阳' : '阴';
                patterns.push({
                    type: 'violent_k',
                    date: bar.date,
                    price: bar.close,
                    text: `暴力K (${direction}, ${bodyPct.toFixed(1)}%)`,
                    confidence: 0.8,
                    metadata: {
                        body_pct: Math.round(bodyPct * 100) / 100,
                        volume_ratio: ma !== null ? Math.round((bar.volume / ma) * 100) / 100 : 0,
                    },
                });
            }
        }
        return patterns;
    }
    /**
     * 运行所有检测器并返回标注结果
     */
    annotateAll(bars, patternTypes = ['b1', 'key_k', 's1', 'violent_k']) {
        const allPatterns = [];
        const dispatch = {
            b1: this.detectB1.bind(this),
            key_k: this.detectKeyK.bind(this),
            s1: this.detectS1.bind(this),
            violent_k: this.detectViolentK.bind(this),
        };
        for (const ptype of patternTypes) {
            if (dispatch[ptype]) {
                const patterns = dispatch[ptype](bars);
                allPatterns.push(...patterns);
            }
        }
        // 按日期排序
        allPatterns.sort((a, b) => a.date.localeCompare(b.date));
        return allPatterns;
    }
}
// 导出单例
export const annotator = new ZettarancAnnotator();
