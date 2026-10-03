import assert from 'node:assert/strict';
import test from 'node:test';

import { rewriteVaultImages, vaultAssetUrl, extractOriginalUrl, extractAccountName } from './vaultAssets.ts';

// 图片地址会被 DOMPurify 剥掉 src 就等于功能失效，而剥离是静默的，线上只会
// 表现为"图片不显示"，很难往回追到 sanitizer。
//
// 这里没有跑真实 DOMPurify：`dompurify` 在 Node 下是工厂函数，没有 window 就
// 没有 addHook（仓库现有的 documentPreviewMarkdown.test.ts 也是用恒等
// sanitizer 绕过的），而项目没有 jsdom/happy-dom 可用，不值得为一条测试
// 引入浏览器模拟。
//
// 真正的风险面只有一个 —— URI 白名单正则认不认这个地址。所以直接测那条正则：
// 它是 DOMPurify 的 ALLOWED_URI_REGEXP，也是唯一会剥掉 src 的判据。
import { domPurifyAllowedUriRegexp } from '../../utils/markdownDomPurify.ts';
import { renderDocumentPreviewMarkdown } from '../../utils/documentPreviewMarkdown.ts';

const KID = 'a1b2c3d4-0000-4000-8000-000000000000';

test('vault URL 通过 DOMPurify 的 URI 白名单', () => {
  const url = vaultAssetUrl(KID, 'images/img_001.jpeg');
  assert.match(url, /^\/api\/v1\/knowledge\//, '必须是同源相对路径');
  assert.ok(
    domPurifyAllowedUriRegexp.test(url),
    `URI 白名单会剥掉 ${url} —— 渲染时图片将没有 src`,
  );
});

test('所有改写产出的地址都通过白名单（含尖括号路径）', () => {
  const src = [
    '![](images/img_001.jpeg)',
    '![带标题](images/img_002.png "caption")',
    '<img src="images/img_003.gif" alt="inline">',
    '![空格](<images/my fig.png>)',
  ].join('\n\n');

  const out = rewriteVaultImages(src, KID);
  // 只取代理地址本身：尖括号形式 `(<url>)` 与带标题形式 `(<url> "caption")`
  // 里直接用括号捕获会把修饰符也吃进来，测的就不是地址本身了。ref 已被
  // percent-encode，所以不会含空格/引号/括号，边界是干净的。
  const urls = [...out.matchAll(/\/api\/v1\/knowledge\/[^)\s"'<>]+\/vault-asset\?ref=[^)\s"'<>]+/g)].map(
    (m) => m[0],
  );
  assert.ok(urls.length >= 4, `应产出 4 个地址，实际 ${urls.length}：${out}`);
  for (const u of urls) {
    assert.ok(domPurifyAllowedUriRegexp.test(u), `白名单不认 ${u}`);
  }
});

// 导出器（wechat-article-to-markdown）产出的 713 篇文章里，带空格的相对路径
// 一个都没有：有空格的 13 处全是远程 URL + 合法 title，尖括号形式 0 处。
// 所以未加尖括号的空格路径不在这批语料里，而按 CommonMark 它本来就不是合法
// destination。显式钉住"原样放行"这个行为，免得以后有人把它当成 bug 顺手
// "修"成正则匹配到下一个空格为止。
test('未加尖括号且带空格的相对路径原样放行（语料中不存在，见下方注释）', () => {
  const src = '![x](images/图 1.png)';
  assert.equal(rewriteVaultImages(src, KID), src);
});

test('改写后的 markdown 渲染出正确的 img（用恒等 sanitizer，仓库既有做法）', () => {
  const src = [
    '# 标题',
    '',
    '![](images/img_001.jpeg)',
    '',
    '![带标题](images/img_002.png "caption")',
    '',
    '<img src="images/img_003.gif" alt="inline">',
    '',
    '![远程](https://mmbiz.qpic.cn/remote.png)',
    '',
    '![内联](data:image/png;base64,AAAA)',
  ].join('\n');

  const out = rewriteVaultImages(src, KID);
  const html = renderDocumentPreviewMarkdown(out, (v) => v);

  for (const ref of ['images/img_001.jpeg', 'images/img_002.png', 'images/img_003.gif']) {
    assert.ok(html.includes(vaultAssetUrl(KID, ref)), `${ref} 改写后在渲染结果中丢失`);
  }
  assert.ok(html.includes('caption'), '图片标题被吞掉了');
  assert.ok(html.includes('https://mmbiz.qpic.cn/remote.png'), '远程图片被误改写');
  assert.ok(html.includes('data:image/png;base64,AAAA'), 'data URI 被误改写');
});

test('不会二次改写已经是代理地址的引用', () => {
  const once = rewriteVaultImages('![](images/a.png)', KID);
  const twice = rewriteVaultImages(once, KID);
  assert.equal(once, twice, '重复改写产生了套娃地址');
});

test('不含图片的正文原样返回', () => {
  const text = '# 只有文字\n\n没有图片。';
  assert.equal(rewriteVaultImages(text, KID), text);
});

test('空输入与空 knowledgeId 不炸', () => {
  assert.equal(rewriteVaultImages('', KID), '');
  assert.equal(rewriteVaultImages('![](images/a.png)', ''), '![](images/a.png)');
});

test('含空格的尖括号路径也能改写', () => {
  const out = rewriteVaultImages('![x](<images/my fig.png>)', KID);
  assert.ok(out.includes(vaultAssetUrl(KID, 'images/my fig.png')));
});

test('query 里的 ref 被正确编码', () => {
  const url = vaultAssetUrl(KID, 'images/图 1.png');
  assert.ok(url.includes('ref=images%2F%E5%9B%BE%201.png'), `未正确编码: ${url}`);
});

test('从导出头部抽取原文链接与公众号名', () => {
  const md = [
    '# 人类体验的消失',
    '',
    '> 公众号: 腾讯研究院',
    '> 发布时间: 2026-09-30 16:30:00',
    '> 原文链接: https://mp.weixin.qq.com/s?__biz=MjM5&mid=2650998935&idx=1',
  ].join('\n');
  assert.equal(extractAccountName(md), '腾讯研究院');
  assert.equal(
    extractOriginalUrl(md),
    'https://mp.weixin.qq.com/s?__biz=MjM5&mid=2650998935&idx=1',
  );
  assert.equal(extractOriginalUrl('没有头部'), '');
  assert.equal(extractAccountName('没有头部'), '');
});
