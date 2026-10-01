-- Migration: 000118_stock_watch_event_eval_date
-- 给条件触发事件补上「判定所属交易日」。
--
-- 为什么必须有这一列：CreatedAt 是**落库时间**，而任务在 D+1 的 08:30 报告 D 日收盘，
-- 两者天然差一个交易日。任何「是不是最新交易日触发的」判断，如果拿 CreatedAt 去比，
-- 在真实运行里永远不会成立（浏览器走查时发现「今日触发」徽章因此从来不亮）。
-- 事件该记的是它**关于哪一天**，不是它什么时候被写进去。
--
-- 状态变更/理由变更事件与交易日无关，这两类保持 NULL。
DO $$ BEGIN RAISE NOTICE '[Migration 000118] Adding eval_date to stock_watch_events'; END $$;

ALTER TABLE stock_watch_events ADD COLUMN IF NOT EXISTS eval_date DATE;

DO $$ BEGIN RAISE NOTICE '[Migration 000118] stock_watch_events.eval_date ready'; END $$;
