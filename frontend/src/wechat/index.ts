/**
 * 公众号工作台模块入口。
 *
 * 与 `@/finance` 同构：主应用只 `import ... from '@/wechat'`，不碰内部路径。
 * 这里承担两件事 —— 副作用注册菜单项/路由，以及对外 re-export。
 */

import { registerModule } from '@/modules/registry';

// 图标随模块自带，不去改 menu.vue 的内置图标表。
import wechatIcon from './assets/wechat.svg';

registerModule({
  id: 'wechat',
  path: 'wechat',
  titleKey: 'menu.wechat',
  icon: 'wechat',
  iconSrc: wechatIcon,
  insertAfter: 'knowledge-bases',
  routeName: 'wechat',
  routeComponent: () => import('./views/WechatWorkbench.vue'),
});

export { default as WechatWorkbench } from './views/WechatWorkbench.vue';
export { useWechatWorkbench } from './composables/useWechatWorkbench';
export { useDocumentChat } from './composables/useDocumentChat';
export * from './utils/vaultAssets';
export * from './api';
