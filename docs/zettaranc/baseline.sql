-- Z哥 工具调用基线
--
-- 用途：每次改 prompt / 工具之后重跑一次，看两个缺陷计数有没有变。
-- 数据源：messages.agent_steps（JSONB，已建 GIN 索引），无需任何新埋点。
--
-- 两个指标（都不受问题复杂度影响，只数「错」）：
--   A 无效调用     result.success = false
--   C 静默空转     result.success = true 但 result.output 为空
--
-- C 长期必须是 0。非零即回归——模型收到空字符串却以为调用成功。
-- 2026-09-29 修掉 analysis.trend 只填 Data 不填 Output 之前，C 恒为 0 只是
-- 因为该工具从未被调用，不代表它是安全的。
--
-- 注意：2026-09-26 及更早的记录来自旧的 Python CLI 实现（错误信息里有
-- `chdir .../zettaranc-skill: no such file`），失败率不代表当前行为。
-- 对比时只取迁移到 python-service 之后的区间，或直接看按天分布确认干净区间。

\echo '=== 总体 ==='
WITH calls AS (
  SELECT m.id AS msg_id,
         COALESCE((tc->'result'->>'success')::boolean, false) AS ok,
         COALESCE(NULLIF(tc->'result'->>'output', ''), '') AS out
  FROM messages m,
       LATERAL jsonb_array_elements(m.agent_steps) st,
       LATERAL jsonb_array_elements(st->'tool_calls') tc
  WHERE m.agent_steps IS NOT NULL AND m.agent_steps <> '[]'::jsonb
)
SELECT count(DISTINCT msg_id)                       AS 消息数,
       count(*)                                     AS 工具调用,
       count(*) FILTER (WHERE NOT ok)               AS A_无效调用,
       round(100.0 * count(*) FILTER (WHERE NOT ok) / count(*), 1) AS A_失败率,
       count(*) FILTER (WHERE ok AND out = '')      AS C_静默空转
FROM calls;

\echo ''
\echo '=== 按工具（失败数降序）==='
WITH calls AS (
  SELECT (tc->>'name') AS tool,
         COALESCE((tc->'result'->>'success')::boolean, false) AS ok,
         COALESCE(NULLIF(tc->'result'->>'output', ''), '') AS out
  FROM messages m,
       LATERAL jsonb_array_elements(m.agent_steps) st,
       LATERAL jsonb_array_elements(st->'tool_calls') tc
  WHERE m.agent_steps IS NOT NULL AND m.agent_steps <> '[]'::jsonb
)
SELECT tool,
       count(*)                                     AS 调用,
       count(*) FILTER (WHERE NOT ok)               AS 失败,
       count(*) FILTER (WHERE ok AND out = '')      AS 空转,
       round(100.0 * count(*) FILTER (WHERE NOT ok) / count(*), 1) AS 失败率
FROM calls
GROUP BY tool
ORDER BY 失败 DESC, 调用 DESC;

\echo ''
\echo '=== 按天（用于识别被旧实现污染的区间）==='
WITH calls AS (
  SELECT date_trunc('day', m.created_at)::date AS d,
         COALESCE((tc->'result'->>'success')::boolean, false) AS ok
  FROM messages m,
       LATERAL jsonb_array_elements(m.agent_steps) st,
       LATERAL jsonb_array_elements(st->'tool_calls') tc
  WHERE m.agent_steps IS NOT NULL AND m.agent_steps <> '[]'::jsonb
)
SELECT d,
       count(*)                                     AS 调用,
       count(*) FILTER (WHERE NOT ok)               AS 失败,
       round(100.0 * count(*) FILTER (WHERE NOT ok) / count(*), 1) AS 失败率
FROM calls
GROUP BY d
ORDER BY d;

\echo ''
\echo '=== 近期失败样本（最多 10 条，用于人工判读）==='
SELECT m.created_at::date AS 日期,
       (tc->>'name')       AS 工具,
       left(COALESCE(tc->'result'->>'error', ''), 160) AS 错误
FROM messages m,
     LATERAL jsonb_array_elements(m.agent_steps) st,
     LATERAL jsonb_array_elements(st->'tool_calls') tc
WHERE NOT COALESCE((tc->'result'->>'success')::boolean, false)
ORDER BY m.created_at DESC
LIMIT 10;
