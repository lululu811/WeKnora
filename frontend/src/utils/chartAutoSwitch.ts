/**
 * 「回答完成后要不要把右侧 K 线切到这条回答的主标的」的判定。
 *
 * 抽成纯函数是因为这段判定全是守卫条件，而守卫写错的表现是**静默的**：
 * 少一条就会抢用户的图位（界面来回跳，没有任何报错），多一条就永远不联动
 * （功能像没做一样）。放进 .vue 里既测不了也看不清，这里把四条守卫摊开。
 *
 * 与 `stockMentions.pickPrimaryMention` 分工：那个回答「主标的是谁」，
 * 这个回答「现在该不该动图」。两者都可能说"不知道"，此时都什么都不做。
 */

export interface AutoSwitchContext {
  /** 这条回答是否已经完整落盘。 */
  messageCompleted: boolean;
  /** K 线面板当前是否已经打开。 */
  panelOpen: boolean;
  /** 用户本轮手动选过的标的；null 表示还没表态。 */
  userPickedThscode: string | null;
  /** 上一次自动切图是为哪条回答做的。 */
  alreadySwitchedForId: string | null;
  /** 当前这条回答的 id。 */
  messageId: string | null | undefined;
}

/**
 * 是否允许为这条回答自动切图。
 *
 * 四条守卫缺一不可：
 *  1. **回答必须已完成**。流式过程中判定主语会随后续文字反复变化，
 *     图位就会来回跳——这是图位抖动最主要的来源。
 *  2. **面板必须已经打开**。面板关着还去开，等于替用户做了「我要看K线」
 *     这个决定。这正是此前 KLineStudioResult 在 onMounted 里自动开图那个
 *     缺陷，已经被删掉一次，不能再从这条路径回来。
 *  3. **用户没手动选过**。显式选择永远优先于模型的暗示。
 *  4. **每条回答只切一次**。否则同一条回答的多次列表刷新会反复触发。
 */
export function shouldAutoSwitchChart(ctx: AutoSwitchContext): boolean {
  if (!ctx.messageCompleted) return false;
  if (!ctx.panelOpen) return false;
  if (ctx.userPickedThscode) return false;
  if (!ctx.messageId) return false;
  if (ctx.alreadySwitchedForId === ctx.messageId) return false;
  return true;
}

/** 聊天列表里的一行（只列这个判定用得到的字段）。 */
export interface ChatRowLike {
  role?: string;
  id?: string | null;
  assistant_message_id?: string | null;
}

/**
 * 这一行是不是「当前正在生成的那条回答」。
 *
 * 两个字段都要比：列表行的 `id` 在部分路径下是 request id，而
 * `currentAssistantMessageId` 存的是 assistant_message_id
 * （见 useChatStreamHandler 里 `rowId` 的兜底逻辑）。只比一个字段会在另一条
 * 路径下永远不匹配，而那种失效是**静默的**——功能像没做一样，没有任何报错。
 *
 * `currentId` 为空时一律返回 false：会话刚加载时它还没被赋值，此时不该把
 * 历史里的某条回答当成「刚刚完成」。
 */
export function isStreamedAnswer(row: ChatRowLike | null | undefined, currentId: string | null | undefined): boolean {
  if (!row || row.role !== 'assistant') return false;
  if (!currentId) return false;
  return row.id === currentId || row.assistant_message_id === currentId;
}
