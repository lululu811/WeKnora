<!--
  技术位置条（「今天谁在动」页顶部）。

  这个页原本只回答"谁在动"—— 放量几倍、涨跌几个点。它答不了**为什么**。
  同样是涨 4%：在 250 日线上方 3% 和在下方 12%，是两件完全不同的事 ——
  前者可能是延续，后者是反弹。这条把后者补上。

  只取**最新日**的少量指标，不画序列 —— 序列在 K 线工作台那边已经有了，
  这里重复画只会让同一份数据有两个视觉出口。序列/多周期对比属于那边。

  取的是哪几个指标、为什么是这几个：见 `finance_panel/technicals.py`
  模块头的 `_FIELDS` 注释（短中长期均线 + RSI + MACD 柱 + 布林位置）。

  口径提醒：indicators 表在 2024-05-06 前后由**两套实现**算出
  （zettaranc_migrate → pandas_ta|talib）。本组件只读最新日，永远落在后者，
  所以不受影响；但真要做长回看，那才是要注意的时候。
-->
<template>
  <section v-if="rows.length" class="tec">
    <header class="tec__head">
      <span class="tec__title">{{ $t('watchPulse.technicals.title') }}</span>
      <span class="tec__caption">
        {{ $t('watchPulse.technicals.caption', { date: data?.as_of ?? '—' }) }}
      </span>
    </header>

    <div class="tec__grid">
      <div v-for="r in rows" :key="r.it.thscode" class="tec__cell">
        <span class="tec__name" :title="r.it.thscode">{{ r.label }}</span>

        <!-- 均线排列：一句话结论。多头/空头用涨跌色，纠缠用中性灰。 -->
        <span class="tec__badge" :class="alignmentClass(r.it.ma_alignment)">
          {{ $t(`watchPulse.technicals.align.${r.it.ma_alignment ?? 'na'}`) }}
        </span>

        <span class="tec__nums">
          <!--
            每格显示的是**该均线自身的值**，颜色表示「收盘是否在它上方」。
            曾经写成显示收盘价 —— 视觉上"数字是红的"仍然成立（因为收盘确实
            在均线上方），但标签写着 MA20 而数字其实是收盘价，只有把两个数
            摆在一起才看得出来。这是那种类型检查和单测都抓不到的错。
          -->
          <span class="tec__num" :class="flagClass(r.it.above_sma20)">
            {{ $t('watchPulse.technicals.sma20') }} {{ fmtNum(r.it.sma20) }}
          </span>
          <span class="tec__num" :class="flagClass(r.it.above_sma60)">
            {{ $t('watchPulse.technicals.sma60') }} {{ fmtNum(r.it.sma60) }}
          </span>
          <span class="tec__num" :class="flagClass(r.it.above_sma250)">
            {{ $t('watchPulse.technicals.sma250') }} {{ fmtNum(r.it.sma250) }}
          </span>
        </span>

        <!-- RSI 分档：超买用红（A股红=涨，这里是"涨太多了"的意思，语义一致） -->
        <span class="tec__num" :class="rsiClass(r.it.rsi_zone)">
          RSI {{ fmtNum(r.it.rsi14, 1) }}
        </span>

        <!-- 布林位置：可超出 0~1（跌破下轨为负、突破上轨 >1），那正是最该看的两种情况。
             不着色 —— 它是"在波动区间的哪一段"，没有涨跌方向，红绿会误导。 -->
        <span class="tec__num">{{ $t('watchPulse.technicals.bb') }} {{ fmtBb(r.it.bb_position) }}</span>
      </div>
    </div>

    <p v-if="data?.missing?.length" class="tec__foot">
      {{ $t('watchPulse.technicals.missing', { codes: data.missing.join('、') }) }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchTechnicals, type TechnicalItem, type TechnicalsResponse } from '@/finance/api/market'
import { fmtNum, DASH } from '@/finance/utils/format'

const props = defineProps<{
  /** 自选清单代码。变化时重新取（清空时清空，不发请求）。 */
  thscodes: string[]
  /** 代码 → 展示名。自选清单带的名字优先，缺了才退回代码。 */
  labels?: Record<string, string>
}>()

const { t } = useI18n()
const data = ref<TechnicalsResponse | null>(null)
const loading = ref(false)

async function load(codes: string[]) {
  if (!codes.length) {
    data.value = null
    return
  }
  loading.value = true
  try {
    data.value = await fetchTechnicals(codes)
  } catch {
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.thscodes, (v) => void load(v ?? []), { immediate: true, deep: false })

const rows = computed(() => {
  const items = data.value?.items ?? {}
  const codes = props.thscodes ?? []
  return codes
    .filter((c) => items[c])
    .map((c) => ({ it: items[c], label: props.labels?.[c] || c }))
})

function alignmentClass(a: TechnicalItem['ma_alignment']): string {
  return a === 'bull' ? 'is-up' : a === 'bear' ? 'is-down' : 'is-flat'
}

/** 收盘在均线上方 → 红，下方 → 绿，null → 中性（**不是**当成在下方）。 */
function flagClass(above: boolean | null): string {
  if (above === null) return 'is-flat'
  return above ? 'is-up' : 'is-down'
}

function rsiClass(zone: TechnicalItem['rsi_zone']): string {
  return zone === 'overbought' ? 'is-up' : zone === 'oversold' ? 'is-down' : 'is-flat'
}

function fmtBb(p: number | null): string {
  return p === null ? DASH : `${(p * 100).toFixed(0)}%`
}
</script>

<style scoped lang="less">
.tec {
  --md-up: #dc2626;
  --md-down: #047857;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--td-radius-medium);
  padding: 12px 14px;
}

:global(:root[theme-mode='dark']) .tec {
  --md-up: #f87171;
  --md-down: #34d399;
}

.is-up { color: var(--md-up); }
.is-down { color: var(--md-down); }
.is-flat { color: var(--td-text-color-placeholder); }

.tec__head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.tec__title { font-family: var(--app-font-display); font-size: var(--app-text-md); }
.tec__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

/*
  自选通常 7~20 只，所以用 auto-fill 而不是固定列数 ——
  3 只和 20 只都要排得下，且不留大片空白。
*/
.tec__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 8px;
}

.tec__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: var(--td-radius-small);
  background: var(--td-bg-color-container-hover);
}

.tec__name { font-size: var(--app-text-sm); color: var(--td-text-color-primary); }

.tec__badge { font-size: var(--app-text-xs); }

.tec__nums { display: flex; gap: 10px; flex-wrap: wrap; }

.tec__num {
  font-family: var(--app-font-family-mono);
  font-size: var(--app-text-2xs, 10px);
  font-variant-numeric: tabular-nums;
}

.tec__foot { margin: 8px 0 0; font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }
</style>
