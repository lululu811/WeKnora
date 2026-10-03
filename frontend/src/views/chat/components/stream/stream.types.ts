/**
 * Shared types for the agent stream display split.
 *
 * Extracted verbatim from AgentStreamDisplay.vue so the stream sub-components
 * and the parent can agree on the event shape without importing each other.
 * These are type-only declarations: no runtime behaviour, no data flow.
 */

/** Session record backing one assistant turn. */
export interface SessionData {
  id?: string;
  assistant_message_id?: string;
  request_id?: string;
  debugRequest?: Record<string, unknown>;
  isAgentMode?: boolean;
  agentEventStream?: any[];
  knowledge_references?: any[];
  [key: string]: unknown;
}

/**
 * Tool-call fields rendered by the tool call card. The stream merges agent
 * events, persisted history and streaming deltas into one loose shape, so the
 * optionality here is deliberate rather than a copy of a strict schema.
 */
export interface ToolCallEvent {
  type?: string;
  tool_call_id?: string;
  tool_name?: string;
  tool_data?: any;
  arguments?: any;
  output?: any;
  pending?: boolean;
  success?: boolean;
  command_output?: any;
  [key: string]: any;
}

/** Event union as consumed by the stream timeline. */
export interface StreamEvent {
  type?: string;
  event_id?: string;
  content?: string;
  title?: string;
  summary?: string;
  done?: boolean;
  is_fallback?: boolean;
  truncated?: boolean;
  tool_call_id?: string;
  tool_name?: string;
  tool_data?: any;
  arguments?: any;
  output?: any;
  pending?: boolean;
  success?: boolean;
  command_output?: any;
  [key: string]: any;
}

/** One segment of a plan produced by the `todo_write` tool. */
export interface PlanStatusItem {
  icon: string;
  label: string;
  count: number;
  class: string;
}
