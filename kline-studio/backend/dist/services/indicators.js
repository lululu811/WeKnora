/**
 * 技术指标计算（纯 TypeScript 实现，无外部依赖）
 *
 * 用于 Z 哥交易体系的形态识别
 */
/**
 * 简单移动平均 (SMA)
 */
export function calcSMA(data, length) {
    const result = [];
    for (let i = 0; i < data.length; i++) {
        if (i < length - 1) {
            result.push(null);
        }
        else {
            const sum = data.slice(i - length + 1, i + 1).reduce((a, b) => a + b, 0);
            result.push(sum / length);
        }
    }
    return result;
}
/**
 * 指数移动平均 (EMA)
 */
export function calcEMA(data, length) {
    const result = [];
    const multiplier = 2 / (length + 1);
    for (let i = 0; i < data.length; i++) {
        if (i === 0) {
            result.push(data[0]);
        }
        else {
            const prev = result[i - 1] ?? data[i];
            result.push((data[i] - prev) * multiplier + prev);
        }
    }
    return result;
}
/**
 * BBI 指标 (四均线平均) - Z 哥牵牛绳
 * BBI = (MA3 + MA6 + MA12 + MA24) / 4
 */
export function calcBBI(close) {
    const ma3 = calcSMA(close, 3);
    const ma6 = calcSMA(close, 6);
    const ma12 = calcSMA(close, 12);
    const ma24 = calcSMA(close, 24);
    return close.map((_, i) => {
        const v3 = ma3[i];
        const v6 = ma6[i];
        const v12 = ma12[i];
        const v24 = ma24[i];
        if (v3 === null || v6 === null || v12 === null || v24 === null) {
            return null;
        }
        return (v3 + v6 + v12 + v24) / 4;
    });
}
/**
 * MACD 指标
 * @returns { dif, dea, histogram }
 */
export function calcMACD(close, fast = 12, slow = 26, signal = 9) {
    const emaFast = calcEMA(close, fast);
    const emaSlow = calcEMA(close, slow);
    const dif = close.map((_, i) => {
        const f = emaFast[i];
        const s = emaSlow[i];
        if (f === null || s === null)
            return null;
        return f - s;
    });
    // DEA = EMA(DIF, signal)
    const difValues = dif.map(v => v ?? 0);
    const dea = calcEMA(difValues, signal);
    const histogram = close.map((_, i) => {
        const d = dif[i];
        const e = dea[i];
        if (d === null || e === null)
            return null;
        return 2 * (d - e);
    });
    return { dif, dea, histogram };
}
/**
 * KDJ 指标 - B1 信号核心（J 值<13 为买入信号）
 * @returns { k, d, j }
 */
export function calcKDJ(high, low, close, n = 9, m1 = 3, m2 = 3) {
    const k = [];
    const d = [];
    const j = [];
    for (let i = 0; i < close.length; i++) {
        if (i < n - 1) {
            k.push(null);
            d.push(null);
            j.push(null);
            continue;
        }
        // RSV = (Close - Low_n) / (High_n - Low_n) * 100
        const lowN = Math.min(...low.slice(i - n + 1, i + 1));
        const highN = Math.max(...high.slice(i - n + 1, i + 1));
        const rsv = highN === lowN ? 50 : ((close[i] - lowN) / (highN - lowN)) * 100;
        // K = SMA(RSV, m1)  这里用 EMA 近似
        const prevK = k[i - 1] ?? 50;
        const currK = (rsv * (1 / m1)) + (prevK * (1 - 1 / m1));
        k.push(currK);
        // D = SMA(K, m2)
        const prevD = d[i - 1] ?? 50;
        const currD = (currK * (1 / m2)) + (prevD * (1 - 1 / m2));
        d.push(currD);
        // J = 3K - 2D
        j.push(3 * currK - 2 * currD);
    }
    return { k, d, j };
}
/**
 * RSI 指标
 */
export function calcRSI(close, length = 14) {
    const result = [];
    const gains = [];
    const losses = [];
    for (let i = 0; i < close.length; i++) {
        if (i === 0) {
            result.push(null);
            continue;
        }
        const change = close[i] - close[i - 1];
        gains.push(change > 0 ? change : 0);
        losses.push(change < 0 ? -change : 0);
        if (i < length) {
            result.push(null);
        }
        else {
            const avgGain = gains.slice(i - length, i).reduce((a, b) => a + b, 0) / length;
            const avgLoss = losses.slice(i - length, i).reduce((a, b) => a + b, 0) / length;
            if (avgLoss === 0) {
                result.push(100);
            }
            else {
                const rs = avgGain / avgLoss;
                result.push(100 - 100 / (1 + rs));
            }
        }
    }
    return result;
}
/**
 * 成交量移动平均
 */
export function calcVolumeMA(volume, length) {
    return calcSMA(volume, length);
}
