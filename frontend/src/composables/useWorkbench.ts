import { ref, computed, watch } from 'vue';
import { listWorkbenches, WORKBENCH_SHARED, type WorkbenchDef } from '@/api/workspace';
import { listAgents, type CustomAgent } from '@/api/agent';
import { useSettingsStore } from '@/stores/settings';

/**
 * 工作台词表（部署级静态数据）。
 *
 * 这里用模块级单例缓存是**有意为之**，与 `useAgentWorkspace` 里被移除的那个
 * 模块级单例有本质区别：
 *   - 那个存的是 **per-session 的可变 UI 状态**（谁打开的面板、选了哪只股票），
 *     多实例必须互相隔离，所以只能走 provide/inject；
 *   - 这个存的是 **部署期内不可变的静态词表**，全局只有一份是正确的，
 *     缓存它反而避免了每个 chat 页面各拉一次。
 *
 * 若将来工作台变成租户级可配置，这里才需要改成按租户缓存。
 */
const workbenches = ref<WorkbenchDef[]>([]);
const loaded = ref(false);
let inFlight: Promise<void> | null = null;

const byId = computed(() => {
  const map = new Map<string, WorkbenchDef>();
  for (const w of workbenches.value) map.set(w.id, w);
  return map;
});

/** 加载工作台词表，重复调用共享同一个 in-flight 请求。 */
export function ensureWorkbenchesLoaded(): Promise<void> {
  if (loaded.value) return Promise.resolve();
  if (inFlight) return inFlight;

  inFlight = listWorkbenches()
    .then((res) => {
      workbenches.value = res?.data ?? [];
      loaded.value = true;
    })
    .catch((err) => {
      // 词表拿不到时保持空表：所有 workbench 解析为「无工作台」，
      // 即不渲染任何面板——保守降级，不会把未知的组件名渲染出来。
      console.warn('[workbench] failed to load workbench registry, degrading to "no workbench"', err);
    })
    .finally(() => {
      inFlight = null;
    });

  return inFlight;
}

export function useWorkbenches() {
  return {
    workbenches: computed(() => workbenches.value),
    ready: computed(() => loaded.value),
  };
}

/** 该 agent tag 是否表示 shared agent。 */
export function isSharedWorkbench(rawWorkbench: string | undefined | null): boolean {
  return rawWorkbench === WORKBENCH_SHARED;
}

/**
 * 归一化 agent 上存储的 workbench tag。
 *
 * 与后端 `types.ResolveWorkbench` 语义一致：未注册值降级为空字符串（无工作台），
 * 并返回 downgraded 标记供调用方告警。agent 的 tag 存在 JSONB 里，可能被手改、
 * 被直接调 API 写入或拼错——若原样透传，结果是一个永久空白且无任何报错的侧栏。
 */
export function resolveWorkbench(rawWorkbench: string | undefined | null): {
  id: string;
  downgraded: boolean;
} {
  const raw = (rawWorkbench ?? '').trim();
  if (!raw || raw === WORKBENCH_SHARED) return { id: raw, downgraded: false };
  if (byId.value.has(raw)) return { id: raw, downgraded: false };
  if (raw) {
    console.warn(`[workbench] unknown workbench "${raw}" on agent, degrading to "no workbench"`);
  }
  return { id: '', downgraded: true };
}

/**
 * 返回某工作台允许渲染的组件 key 集合。
 * 无工作台 / 未注册 / shared 均返回空数组 —— shared 是跨工作台的通用 agent，
 * 本身不决定面板。
 */
export function componentsForWorkbench(rawWorkbench: string | undefined | null): string[] {
  const { id } = resolveWorkbench(rawWorkbench);
  if (!id) return [];
  return byId.value.get(id)?.components ?? [];
}

// ---------------------------------------------------------------------------
// 当前 agent → 工作台
// ---------------------------------------------------------------------------

/**
 * agent 列表同样是部署/租户内相对静态的数据，按 id 缓存一份即可。
 * 只用来读 `config.workbench` 这一个字段，不做深比较、不做失效策略——
 * agent 编辑后刷新页面即生效，符合当前「设置存 localStorage」的心智模型。
 */
const agentsById = ref<Record<string, CustomAgent>>({});
let agentsInFlight: Promise<void> | null = null;

function ensureAgentsLoaded(): Promise<void> {
  if (Object.keys(agentsById.value).length > 0) return Promise.resolve();
  if (agentsInFlight) return agentsInFlight;

  agentsInFlight = listAgents()
    .then((res) => {
      const map: Record<string, CustomAgent> = {};
      for (const a of res?.data ?? []) map[a.id] = a;
      agentsById.value = map;
    })
    .catch((err) => {
      // 拿不到 agent 列表时按「无工作台」处理：不渲染面板。
      console.warn('[workbench] failed to load agents, degrading to "no workbench"', err);
    })
    .finally(() => {
      agentsInFlight = null;
    });

  return agentsInFlight;
}

/**
 * 当前选中 agent 的工作台组件集合。
 *
 * 会话工作台由「会话绑定的 agent」推导；MVP 阶段用当前选中的 agent 近似，
 * 待 sessions.agent_id 物化列落地后（设计文档阶段⑤）改为从会话取值。
 */
export function useCurrentWorkbenchComponents() {
  const settings = useSettingsStore();
  const agentId = computed(() => settings.selectedAgentId || '');

  const rawWorkbench = computed(() => agentsById.value[agentId.value]?.config?.workbench ?? '');
  const resolved = computed(() => resolveWorkbench(rawWorkbench.value));
  const allowedComponents = computed(() => {
    const { id } = resolved.value;
    if (!id) return [] as string[];
    return byId.value.get(id)?.components ?? [];
  });

  watch(
    agentId,
    () => {
      void ensureWorkbenchesLoaded();
      void ensureAgentsLoaded();
    },
    { immediate: true },
  );

  return {
    workbenchId: computed(() => resolved.value.id),
    /** 该 workbench 是否被 agent 声明（false = 拼错/未注册/无工作台） */
    workbenchKnown: computed(() => !resolved.value.downgraded),
    isShared: computed(() => isSharedWorkbench(rawWorkbench.value)),
    allowedComponents,
    /** 工作台是否允许渲染某个组件。未注册组件一律 false。 */
    allows: (component: string) => allowedComponents.value.includes(component),
  };
}
