<!--
  连板梯队（大盘工作台 · 大盘 tab）。

  涨停家数只说"今天热不热"，梯队说"热度在往上垒还是在塌"。
  今天 2 板 16 家、7 板以上 9 家，和每天都停在 2 板，是两种完全不同的市场 ——
  后者意味着打板资金没有接力意愿。

  画成 档位 × 交易日 的热力矩阵：横着看是这一天的高度分布（梯形是否完整），
  竖着看是某个高度最近在升温还是退潮。分档说明写在标题右侧而不是藏进文档，
  因为「7+ 板那 9 家」这个数字脱离"7 板以上"这个含义是没有意义的。
-->
<template>
  <article class="lad">
    <header class="lad__head">
      <div class="lad__title-row">
        <span class="bas-title">{{ $t('marketDashboard.ladder.title') }}</span>
        <span class="lad__caption">{{ $t('marketDashboard.ladder.caption', { n: daysShown }) }}</span>
      </div>
    </header>

    <p v-if="failed" class="lad__empty">{{ $t('marketDashboard.ladder.failed') }}</p>
    <p v-else-if="loading" class="lad__empty">{{ $t('common.loading') }}</p>
    <p v-else-if="!matrix.length" class="lad__empty">{{ $t('marketDashboard.noData') }}</p>

    <div v-else class="lad__wrap">
      <div class="lad__grid" :style="{ gridTemplateColumns: `44px repeat(${daysShown}, minmax(0, 1fr))` }">
        <span class="lad__corner" />
        <span v-for="d in matrix" :key="`h-${d}`" class="lad__col-head">{{ shortDate(d) }}</span>

        <template v-for="lv in LEVELS" :key="lv.key">
          <span class="lad__row-head">{{ $t(lv.labelKey) }}</span>
          <span v-for="d in matrix" :key="`${lv.key}-${d}`" class="lad__cell"
            :class="cellClass(countOf(d, lv.key))" :title="cellTitle(d, lv.key)">
            <template v-if="countOf(d, lv.key) !== null">{{ countOf(d, lv.key) }}</template>
          </span>
        </template>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getLimitUpLadder, type LadderResponse } from '@/finance/api/market'

const { t } = useI18n()

const data = ref<LadderResponse | null>(null)
const loading = ref(true)
const failed = ref(false)

const LEVELS = [
  { key: 'two_board', labelKey: 'marketDashboard.ladder.level.two' },
  { key: 'three_board', labelKey: 'marketDashboard.ladder.level.three' },
  { key: 'four_board', labelKey: 'marketDashboard.ladder.level.four' },
  { key: 'five_board', labelKey: 'marketDashboard.ladder.level.five' },
  { key: 'six_board', labelKey: 'marketDashboard.ladder.level.six' },
  { key: 'seven_over', labelKey: 'marketDashboard.ladder.level.seven' },
] as const

/** 横向最多画这么多列 —— 再多就分不清单格是几了。 */
const MAX_COLS = 20

async function load() {
  loading.value = true
  failed.value = false
  try {
    data.value = await getLimitUpLadder(30)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

const matrix = computed(() =>
  (data.value?.days ?? []).slice(0, MAX_COLS).map((d) => d.trade_date as string),
)
const daysShown = computed(() => matrix.value.length)

/** 某天某档的家数。缺这一行 = 那天该档为 0（真实的 0，不是缺失）。 */
function countOf(date: string, level: string): number | null {
  const day = (data.value?.days ?? []).find((d) => d.trade_date === date)
  if (!day) return null
  const hit = day.levels.find((l) => l.board_level === level)
  return hit ? hit.board_num : 0
}

function cellTitle(date: string, level: string): string {
  return `${date} · ${LEVELS.find((l) => l.key === level)?.labelKey ?? level}: ${countOf(date, level) ?? 0}`
}

/** 着色按**同一档位内**的横向分位，而不是绝对家数：
 * 「2 板 16 家」看着吓人，但 2 板天天有十几家；「7+ 板 9 家」才是真异常。
 * 跨档位共用一套色标会让低档永远最亮，恰好把最该看的信号盖掉。 */
function cellClass(v: number | null): string {
  if (v === null) return 'is-void'
  if (v === 0) return 'is-zero'
  return v >= 8 ? 'is-hi' : v >= 4 ? 'is-mid' : 'is-lo'
}

function shortDate(d: string): string {
  return d ? d.slice(5) : ''
}
</script>

<style scoped lang="less">
.lad {
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--td-radius-medium);
  padding: 12px 14px;
}

.lad__head { margin-bottom: 10px; }
.lad__title-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.bas-title { font-family: var(--app-font-display); font-size: var(--app-text-md); }
.lad__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

.lad__empty {
  margin: 0; padding: 14px 0;
  color: var(--td-text-color-placeholder); font-size: var(--app-text-sm);
}

.lad__wrap { overflow-x: auto; }
.lad__grid { display: grid; gap: 2px; min-width: 520px; align-items: center; }

.lad__corner { }

.lad__col-head,
.lad__row-head {
  font-size: var(--app-text-2xs, 10px);
  color: var(--td-text-color-placeholder);
  text-align: center;
}

.lad__row-head { text-align: left; white-space: nowrap; }

/* 格子：用品牌色的透明度阶梯表示强度，而不是涨跌红绿 ——
   红绿在这里会被读成"涨/跌"，而热力强度没有方向。 */
.lad__cell {
  display: flex; align-items: center; justify-content: center;
  height: 22px; border-radius: 3px;
  font-family: var(--app-font-family-mono);
  font-size: var(--app-text-2xs, 10px);
  font-variant-numeric: tabular-nums;
}

.lad__cell.is-void { background: transparent; }
.lad__cell.is-zero { background: var(--td-bg-color-container-hover); color: var(--td-text-color-placeholder); }
.lad__cell.is-lo { background: color-mix(in srgb, var(--td-brand-color) 14%, transparent); color: var(--td-text-color-primary); }
.lad__cell.is-mid { background: color-mix(in srgb, var(--td-brand-color) 34%, transparent); color: var(--td-text-color-primary); }
.lad__cell.is-hi { background: color-mix(in srgb, var(--td-brand-color) 62%, transparent); color: #fff; font-weight: 600; }
</style>
