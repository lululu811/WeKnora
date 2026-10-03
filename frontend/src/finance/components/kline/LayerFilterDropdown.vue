<template>
  <t-popup
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
            @click.stop="$emit('update:enabled', !enabled)"
          >
            <span class="kline-layer-option__box" aria-hidden="true">
              <t-icon v-if="enabled" name="check" size="12px" />
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
          <section v-for="grp in groups" :key="grp.name" class="kline-layer-group">
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
                    <t-icon v-if="isOptionEnabled(selection, opt.value)" name="check" size="12px" />
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
import { computed, watch } from 'vue'

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
  }>(),
  { emptyText: '暂无可选项', hint: '' },
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
const onCount = computed(() => enabledCount(props.options, props.selection))

// 面板收起时若「一项都没勾」，把按钮的激活态交回父组件判断；
// 这里只负责把 (已勾/总数) 暴露出去。
defineExpose({ onCount })

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

<style scoped lang="less">
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
    background: rgba(59, 130, 246, 0.12);
  }
}

.kline-layer-group__head {
  padding: 6px 10px 2px;
  font-weight: 600;
  opacity: 0.55;
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
  padding: 5px 10px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  font-size: var(--app-text-sm);

  &:hover {
    background: rgba(59, 130, 246, 0.1);
  }

  &:not(.active) {
    opacity: 0.5;
  }
}

.kline-layer-option--master {
  flex: 1;
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
  border-radius: var(--app-radius-xs);
}

.kline-layer-option.active .kline-layer-option__box {
  background: var(--td-brand-color);
  border-color: var(--td-brand-color);
  color: #fff;
}

.kline-layer-option__label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
