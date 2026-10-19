/**
 * 公众号工作台（chatlog /biz）的接入配置。
 *
 * 之前这个地址写死在 WechatWorkbench.vue 里（`http://127.0.0.1:5030/biz`），
 * 于是换机器、走 docker、远程部署全都得改前端源码。地址与令牌都改由环境变量
 * 给出，缺省值保持本地开发可用。
 *
 *   VITE_CHATLOG_BASE_URL  chatlog 服务地址（默认本地 5030）
 *   VITE_CHATLOG_TOKEN     chatlog 的 auth_token；配了才带上
 *
 * 关于 token：chatlog 绑定非 loopback 地址且未配置 auth_token 时会拒绝启动
 * （checkAddrAuth），而它的鉴权中间件接受 `Authorization: Bearer <token>`
 * 或 `?token=<token>` 两种形式。iframe 加不了 Authorization 头，所以走
 * 查询参数 —— 那是 chatlog 明确支持的形式，不是绕过去的 hack。
 */

/** chatlog 服务地址（去掉尾部斜杠）。 */
export const CHATLOG_BASE_URL = (
  import.meta.env.VITE_CHATLOG_BASE_URL?.trim() || 'http://127.0.0.1:5030'
).replace(/\/+$/, '');

/** chatlog 的 auth_token；为空表示 chatlog 未启用鉴权。 */
export const CHATLOG_TOKEN = import.meta.env.VITE_CHATLOG_TOKEN?.trim() || '';

/**
 * iframe 的 origin。
 *
 * postMessage 的 targetOrigin 必须钉死：传 '*' 意味着这条消息会被投递给
 * 该 iframe 当时指向的任何 origin —— iframe 的 src 是可被导航的，一旦
 * 跳到别处，主题信息就会发到第三方页面。
 */
export const CHATLOG_ORIGIN = (() => {
  try {
    return new URL(CHATLOG_BASE_URL).origin;
  } catch {
    // 地址不是合法 URL 时不猜：返回空串，调用方据此不做跨域投递。
    return '';
  }
})();

/**
 * 公众号工作台的页面地址。
 *
 * `embed=1` 让 chatlog 走嵌入态布局；`theme` 决定首屏配色，避免主题切换
 * 前先闪一下浅色。
 */
export function chatlogBizUrl(theme: 'light' | 'dark'): string {
  const params = new URLSearchParams({ embed: '1', theme });
  if (CHATLOG_TOKEN) {
    params.set('token', CHATLOG_TOKEN);
  }
  return `${CHATLOG_BASE_URL}/biz?${params.toString()}`;
}
