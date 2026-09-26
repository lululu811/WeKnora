/**
 * 调试工具 - 列出 KLineChartPro 实例的所有属性和方法
 */
export function debugChartPro(chartPro: any): void {
  if (!chartPro) {
    console.log('[debug] chartPro is null');
    return;
  }

  console.group('[debug] KLineChartPro instance');

  // 列出所有属性
  const props = Object.getOwnPropertyNames(chartPro);
  console.log('Own properties:', props);

  // 列出原型链上的方法
  const proto = Object.getPrototypeOf(chartPro);
  console.log('Prototype:', proto?.constructor?.name);
  const protoMethods = Object.getOwnPropertyNames(proto);
  console.log('Prototype methods:', protoMethods);

  // 查找可能的 chart 实例
  const candidates = ['chart', '_chart', 'klineChart', '_instance', 'instance'];
  for (const key of candidates) {
    if (chartPro[key]) {
      console.log(`chartPro.${key}:`, typeof chartPro[key], chartPro[key]);
    }
  }

  console.groupEnd();
}