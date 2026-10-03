/**
 * workbenchFormat — 方向 A「午后的工作室」工作台首屏的展示层格式化。
 *
 * 只做纯函数（无 Vue 依赖），便于单测与复用：会话卡片的最后一条消息预览
 * 与相对时间。预览走"先剥结构再截断"，避免把 markdown 语法当正文显示。
 */

/** 剥离 markdown / HTML 结构，只留可读正文。 */
export function stripMarkdownToPreview(raw: string, maxLength = 80): string {
    if (!raw) return '';
    let text = String(raw);
    // 代码围栏整块丢弃（工具输出的 JSON 对读者没有预览价值）
    text = text.replace(/```[\s\S]*?```/g, ' ');
    // 图片 / 链接：链接只留文字
    text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
    text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
    // 标题、引用、列表、项目符号
    text = text.replace(/^\s{0,3}#{1,6}\s+/gm, '');
    text = text.replace(/^\s{0,3}>\s?/gm, '');
    text = text.replace(/^\s{0,3}[-*+]\s+/gm, '');
    text = text.replace(/^\s{0,3}\d+\.\s+/gm, '');
    // 行内强调、删除线、行内代码
    text = text.replace(/(\*\*|__)(.*?)\1/g, '$2');
    text = text.replace(/(\*|_)(.*?)\1/g, '$2');
    text = text.replace(/~~(.*?)~~/g, '$1');
    text = text.replace(/`([^`]*)`/g, '$1');
    // 残留标签
    text = text.replace(/<[^>]+>/g, ' ');
    // 表格分隔行
    text = text.replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, ' ');

    text = text.replace(/\s+/g, ' ').trim();
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength).trimEnd()}…`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** 极简 t 函数签名：只用来取"分钟/小时/天"这几个 key。 */
type TFn = (key: string) => string;

/**
 * 相对时间：今天显示"x 分钟前"，昨天显示"昨天"，更早显示日期。
 * 非法时间返回空串，卡片不显示时间而不是显示 Invalid Date。
 */
export function toRelativeTime(iso: string, t: TFn, now: number = Date.now()): string {
    if (!iso) return '';
    const ts = new Date(iso).getTime();
    if (Number.isNaN(ts)) return '';

    const diff = now - ts;
    if (diff < 0) return '';
    if (diff < HOUR) {
        const m = Math.max(1, Math.round(diff / MINUTE));
        return t('createChat.workbench.minutesAgo').replace('{n}', String(m));
    }
    if (diff < DAY) {
        const h = Math.round(diff / HOUR);
        return t('createChat.workbench.hoursAgo').replace('{n}', String(h));
    }
    if (diff < 2 * DAY) return t('createChat.workbench.yesterday');
    if (diff < 7 * DAY) {
        const d = Math.round(diff / DAY);
        return t('createChat.workbench.daysAgo').replace('{n}', String(d));
    }
    const d = new Date(ts);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${d.getFullYear()}-${mm}-${dd}`;
}
