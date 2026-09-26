/**
 * 注册所有 Zettaranc 自定义指标
 * 在应用启动时调用一次
 */
import { registerZettarancIndicators } from './zettaranc-indicators';

let registered = false;

export function registerAllIndicators() {
  if (registered) return;
  try {
    registerZettarancIndicators();
    registered = true;
    console.log('[zettaranc] indicators registered');
  } catch (err) {
    // 重复注册会报错，忽略
    console.warn('[zettaranc] register failed:', err);
  }
}