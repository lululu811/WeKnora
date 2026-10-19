<template>
  <!--
    inline 模式：直接把面板内容渲染出来，不套 t-popup。

    为什么要这个开关：这个组件最早是挂在工具栏上、独立弹出的一层。但现在它被塞进
    「kline.layers」这个 t-dropdown 的 content 里 —— popup 套 popup 会同时展开、
    同 z-index 互相压住，而且 popup 的触发器是 slot，没给 slot 就等于塞了个没有触发器
    的空壳，面板永远不显示（界面上就只剩标题文字，开关全部消失）。

    所以：inline=true 时不渲染 popup，只出内容；默认 false 保留原独立弹层用法。
  -->
  <template v-if="inline">
    <div class="kline-layer-panel kline-layer-panel--inline" role="group" :aria-label="ariaLabel">
      <div class="kline-layer-panel__head">
        <button
          type="button"
          class="kline-layer-option kline-layer-option--master"
          :class="{ active: enabled }"
          role="switch"
          :aria-checked="enabled"
          @click.stop="emit('update:enabled', !enabled)"
        >
          <span class="kline-layer-option__box" aria-hidden="true">
            <span v-if="enabled" class="kline-layer-option__tick" />
          </span>
          <span class="kline-layer-option__label">{{ title }}</span>
        </button>
        <button
          type="button"
          class="kline-layer-panel__bulk"
          :disabled="!enabled || options.length === 0"
          @click.stop="toggleAll"
        >
          {{ allSelected ? '全不选' : '全选' }}
        </button>
      </div>
      <div v-if="options.length === 0" class="kline-layer-panel__empty">
        {{ emptyText }}
      </div>
      <div v-else class="kline-layer-panel__list" :class="{ 'is-muted': !enabled }">
        <section v-for="grp in groups" :key="grp.name || 'other'" class="kline-layer-group">
          <button
            v-if="grp.name"
            type="button"
            class="kline-layer-group__head"
            :aria-expanded="isGroupExpanded(grp)"
            @click.stop="toggleGroup(grp.name || 'other')"
          >
            <t-icon
              name="chevron-down"
              size="12px"
              class="kline-layer-group__caret"
              :class="{ 'is-collapsed': !isGroupExpanded(grp) }"
            />
            <span class="kline-layer-group__name">{{ grp.name }}</span>
            <span class="kline-layer-group__count">{{ groupEnabled(grp) }}/{{ grp.options.length }}</span>
          </button>
          <ul v-show="isGroupExpanded(grp)" class="kline-layer-group__list">
            <li v-for="opt in grp.options" :key="opt.value">
              <button
                type="button"
                class="kline-layer-option"
                :class="{ active: isOptionEnabled(selection, opt.value) }"
                role="checkbox"
                :aria-checked="isOptionEnabled(selection, opt.value)"
                :title="opt.desc || opt.label"
                @click.stop="onToggle(opt.value)"
              >
                <span class="kline-layer-option__box" aria-hidden="true">
                  <span v-if="isOptionEnabled(selection, opt.value)" class="kline-layer-option__tick" />
                </span>
                <span class="kline-layer-option__label">{{ opt.label }}</span>
              </button>
            </li>
          </ul>
        </section>
      </div>
      <div class="kline-layer-panel__hint">{{ hint }}</div>
    </div>
  </template>

  <t-popup
    v-else
    :visible="open"
    @visible-change="onVisibleChange"
    trigger="click"
    placement="bottom-right"
    overlay-class-name="kline-layer-popup"
    :overlay-inner-style="{ padding: 0 }"
  >
    <template #content>
      <div class="kline-layer-panel" role="group" :aria-label="ariaLabel">
        <div class="kline-layer-panel__head">
          <button
            type="button"
            class="kline-layer-option kline-layer-option--master"
            :class="{ active: enabled }"
            role="switch"
            :aria-checked="enabled"
            @click.stop="emit('update:enabled', !enabled)"
          >
            <span class="kline-layer-option__box" aria-hidden="true">
              <span v-if="enabled" class="kline-layer-option__tick" />
            </span>
            <span class="kline-layer-option__label">{{ title }}</span>
          </button>
          <button
            type="button"
            class="kline-layer-panel__bulk"
            :disabled="!enabled"
            @click.stop="toggleAll"
          >
            {{ allSelected ? '全不选' : '全选' }}
          </button>
        </div>

        <div v-if="options.length === 0" class="kline-layer-panel__empty">
          {{ emptyText }}
        </div>

        <div v-else class="kline-layer-panel__list" :class="{ 'is-muted': !enabled }">
          <section v-for="grp in groups" :key="grp.name || 'other'" class="kline-layer-group">
            <div v-if="grp.name" class="kline-layer-group__head">{{ grp.name }}</div>
            <ul class="kline-layer-group__list">
              <li v-for="opt in grp.options" :key="opt.value">
                <button
                  type="button"
                  class="kline-layer-option"
                  :class="{ active: isOptionEnabled(selection, opt.value) }"
                  role="checkbox"
                  :aria-checked="isOptionEnabled(selection, opt.value)"
                  :title="opt.desc || opt.label"
                  @click.stop="onToggle(opt.value)"
                >
                  <span class="kline-layer-option__box" aria-hidden="true">
                    <span v-if="isOptionEnabled(selection, opt.value)" class="kline-layer-option__tick" />
                  </span>
                  <span class="kline-layer-option__label">{{ opt.label }}</span>
                  <span v-if="typeof opt.count === 'number'" class="kline-layer-option__count">
                    {{ opt.count }}
                  </span>
                </button>
              </li>
            </ul>
          </section>
        </div>

        <div class="kline-layer-panel__hint">{{ hint }}</div>
      </div>
    </template>

    <!-- 触发器由父组件提供：插槽在父作用域编译，父组件的 scoped 样式才能生效，
         这样按钮和旁边那几个开关长得一模一样，不必把工具栏样式抄一遍。 -->
    <slot />
  </t-popup>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  enabledCount,
  isAllSelected,
  isOptionEnabled,
  type LayerOption,
  type LayerSelection,
} from './layer-selection'

const props = withDefaults(
  defineProps<{
    /** 面板标题，例如「形态轮廓」。 */
    title: string
    options: LayerOption[]
    selection: LayerSelection
    /** 空列表时的文案（例如「本图没有识别到形态」）。 */
    emptyText?: string
    /** 面板底部的一行说明。 */
    hint?: string
    /**
     * 图层总开关。
     *
     * 和「全不选」是两件事，刻意分开：「全不选」= 这一层还在，只是没有勾中的项
     * （计数显示 0/4）；「关掉」= 整层不画。合并成一个的话，用户点完「全不选」
     * 就分不清自己到底是关掉了整层、还是只清空了勾选。
     */
    enabled: boolean
    /**
     * 面板是否展开。由父组件持有：
     * 工具栏上并列着两个下拉，各自管自己的 visible 会同时展开、同 z-index 互相压住。
     */
    open: boolean
    /**
     * 内联模式（不套 popup）。
     *
     * 嵌在别的下拉/弹层里时必须开：popup 套 popup 会互相压住，而且没有触发器
     * slot 的 popup 永远不展开 —— 表现是"面板整个消失，只剩标题文字"。
     */
    inline?: boolean
  }>(),
  { emptyText: '暂无可选项', hint: '', inline: false },
)

/**
 * 这里刻意**不**用 `update:selection` 直接回传算好的新状态。
 *
 * 算新状态必须基于"当前选择"，而组件拿到的是 **prop**：prop 要等父组件重渲染
 * 才会更新。连续两次点击（或"全不选"紧跟一次勾选）之间 prop 还没刷新，两次都
 * 从同一个旧快照起算 —— 后一次会把前一次覆盖掉，表现为"点了 23 次只生效 2 次"。
 *
 * 改成回传**意图**，由持有 ref 的父组件计算：ref 是同步的，不存在陈旧快照。
 */
const emit = defineEmits<{
  toggle: [value: string]
  setAll: [value: boolean]
  'update:enabled': [value: boolean]
  'update:open': [value: boolean]
}>()

const ariaLabel = computed(() => props.title)
const allSelected = computed(() => isAllSelected(props.options, props.selection))

/** 按 group 归并，保持 options 的原始顺序；没有 group 的归入「其他」。 */
const groups = computed(() => {
  const out: Array<{ name: string; options: LayerOption[] }> = []
  const index = new Map<string, { name: string; options: LayerOption[] }>()
  for (const opt of props.options) {
    const name = opt.group || ''
    let bucket = index.get(name)
    if (!bucket) {
      bucket = { name, options: [] }
      index.set(name, bucket)
      out.push(bucket)
    }
    bucket.options.push(opt)
  }
  return out
})
// 面板标题右侧的「已选/总数」。命名避开导入的 enabledCount 函数。
const enabledCountLabel = computed(() => enabledCount(props.options, props.selection))

/**
 * 分组折叠状态。
 *
 * 存**展开**的组名，而不是存折叠的 —— 与 layer-selection.ts「存关掉的那些」同一条
 * 原则：以后后端新增一个分组（名字此前没人表过态），它默认展开，用户一眼就能看到，
 * 不会出现"新分组静默不见了"。
 *
 * `collapsibleGroups` 让父组件决定哪些组可折叠：项少的组（如「战法」4 项）永远展开，
 * 项多的组（如「K线」24 项）默认折叠。不给这个列表的话，所有分组都可折叠，
 * 只有一个 4 项的组也去点箭头才看得到内容，纯属多余动作。
 */
const openGroups = ref<Set<string>>(new Set())
const collapsibleGroups = computed(() => {
  // 8 项以内不值得折叠：折起来省下的高度抵不过多点一次的麻烦。
  const MAX = 8
  return new Set(props.options.filter((o) => o.group && countInGroup(o.group) > MAX)
    .map((o) => o.group as string))
})

function countInGroup(group: string): number {
  return props.options.filter((o) => o.group === group).length
}

function isGroupExpanded(grp: { name: string; options: LayerOption[] }): boolean {
  if (!collapsibleGroups.value.has(grp.name)) return true
  return openGroups.value.has(grp.name)
}

function toggleGroup(name: string): void {
  if (!collapsibleGroups.value.has(name)) return
  const next = new Set(openGroups.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  openGroups.value = next
}

/** 该组已勾选数 —— 折叠时也看得见"这一组开了几个"，不必展开才知道。 */
function groupEnabled(grp: { options: LayerOption[] }): number {
  return grp.options.filter((o) => isOptionEnabled(props.selection, o.value)).length
}

// 面板收起时若「一项都没勾」，把按钮的激活态交回父组件判断；
// 这里只负责把 (已勾/总数) 暴露出去。
defineExpose({ onCount: enabledCountLabel })

function onVisibleChange(v: boolean) {
  emit('update:open', v)
}

function onToggle(value: string) {
  emit('toggle', value)
}

function toggleAll() {
  emit('setAll', !allSelected.value)
}

// 切换标的导致可选项整批换掉时，面板若还开着，内容会当着用户的面跳变。
// 收起面板，让用户重新打开时看到的是新一批形态。
watch(
  () => props.options.map((o) => o.value).join('\u0000'),
  () => {
    if (props.open) emit('update:open', false)
  },
)

</script>

<!--
  刻意用**全局**样式而非 scoped。
  这个面板以内联模式被塞进「kline.layers」的 t-dropdown 里，而 TDesign 的 dropdown
  会把内容 teleport 到 body —— scoped 的属性选择器只认本组件模板里的节点，
  teleport 出去的内容拿不到 data-v 属性，结果就是"渲染出来了但完全没有样式"。
  类名统一用 kline-layer- 前缀，不会污染其他组件。
-->
<style lang="less">
.kline-layer-panel {
  min-width: 190px;
  max-width: 280px;
  padding: 6px 0 0;
  font-size: var(--app-text-sm);
}

.kline-layer-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 10px 6px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.kline-layer-panel__title {
  font-weight: 600;
  opacity: 0.85;
}

.kline-layer-panel__bulk {
  border: none;

  &:disabled {
    opacity: 0.4;
    cursor: default;
  }

  background: transparent;
  color: var(--td-brand-color);
  cursor: pointer;
  font-size: var(--app-text-xs);
  padding: 2px 4px;
  border-radius: var(--app-radius-xs);

  &:hover {
    background: color-mix(in srgb, var(--td-brand-color) 12%, transparent);
  }
}

.kline-layer-group__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.kline-layer-panel__list {
  &.is-muted {
    opacity: 0.45;
  }

  list-style: none;
  margin: 4px 0;
  padding: 0;
  max-height: 260px;
  overflow-y: auto;
}

.kline-layer-option {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 4px 10px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  font-size: var(--app-text-sm);

  &:hover {
    background: color-mix(in srgb, var(--td-brand-color) 10%, transparent);
  }

  &:not(.active) {
    opacity: 0.55;
  }
}

.kline-layer-option--master {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  padding-left: 10px;

  &:not(.active) {
    opacity: 0.55;
  }
}

.kline-layer-option__box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
  border: 1px solid currentColor;
  border-radius: 3px;
  opacity: 0.55;
}

// 勾号用 CSS 画（两条边框旋转），不依赖 t-icon：
// 内联模式（inline）下这个面板被塞进 t-dropdown 的 content 里，图标组件的
// 注入时机和 teleport 容易对不上，表现为"框在、勾没画出来"。
.kline-layer-option__tick {
  width: 8px;
  height: 4px;
  border-left: 2px solid #fff;
  border-bottom: 2px solid #fff;
  transform: rotate(-45deg) translate(0.5px, -1px);
}

.kline-layer-option.active .kline-layer-option__box {
  background: var(--td-brand-color);
  border-color: var(--td-brand-color);
  opacity: 1;
}

.kline-layer-option__label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

// 内联模式：嵌在 t-dropdown 里。TDesign 的 dropdown 有 overlay 默认最大高度，
// 加上气泡 8 项 + 轮廓 5 项会顶到视口外 —— 两层各限高并独立滚动。
.kline-layer-panel--inline {
  min-width: 0;
  padding: 4px 0 2px;
  width: 100%;
}

.kline-layer-panel--inline .kline-layer-panel__head {
  padding: 2px 10px 4px;
  border-bottom: none;
}

// 两层之间的分隔线，避免糊成一片。
.kline-layer-panel--inline + .kline-layer-panel--inline {
  border-top: 1px solid var(--td-component-stroke);
  margin-top: 4px;
}

// 两层一起全展开时最高约 36 项（气泡 28 + 轮廓 6），纵向一定放不下。
// 所以：宽度给到 400px 并让每组内的列表分两列，横向用起来；
// 面板自身限高 460px（低于常见视口的 2/3），超出内部滚动，
// 保证底部的「关键位」开关永远在视口内、点得到。
.kline-layer-panel--inline {
  min-width: 0;
  width: 100%;
  padding: 4px 0 2px;
}

.kline-layer-panel--inline .kline-layer-panel__list {
  max-height: 460px;
  padding: 2px 0;
  overflow-y: auto;
}

.kline-layer-panel--inline .kline-layer-panel__head {
  padding: 2px 10px 6px;
  border-bottom: none;
}

// 两层之间的分隔线，避免糊成一片。
.kline-layer-panel--inline + .kline-layer-panel--inline {
  border-top: 1px solid var(--td-component-stroke);
  margin-top: 4px;
}

.kline-layer-panel__master {
  padding: 2px 0;
}

// 分组标题升级为可点的按钮行：加箭头、加「已选/总数」，
// 让它一眼区别于下面同宽的勾选行（之前两者字号字重几乎一样，分不清谁可点）。
.kline-layer-group__head {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 5px 10px 3px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  font-weight: 600;
  text-align: left;

  &:hover {
    color: var(--td-text-color-primary);
  }
}

.kline-layer-group__caret {
  flex: 0 0 12px;
  transition: transform var(--app-motion-fast) ease;

  // 折叠时箭头朝右（TDesign 的 chevron-down 默认朝下）。
  &.is-collapsed {
    transform: rotate(-90deg);
  }
}

.kline-layer-group__name {
  flex: 1;
}

.kline-layer-group__count {
  font-family: monospace;
  font-weight: 400;
  opacity: 0.7;
}

// 组内多列：24 项的单列排下来有 720px，分两列后只占一半高度。
.kline-layer-panel--inline .kline-layer-group__list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 2px;
}

.kline-layer-option__count {
  font-family: monospace;
  font-size: var(--app-text-2xs);
  opacity: 0.7;
}

.kline-layer-panel__empty {
  padding: 10px;
  opacity: 0.6;
}

.kline-layer-panel__hint {
  padding: 6px 10px 8px;
  border-top: 1px solid var(--td-component-stroke);
  font-size: var(--app-text-xs);
  opacity: 0.6;
  line-height: 1.4;
}
</style>
