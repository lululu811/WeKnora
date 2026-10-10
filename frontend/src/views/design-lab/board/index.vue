<template>
  <div class="bl">
    <header class="bl__bar">
      <h1>大盘工作台 · 新增三块（dev 预览）</h1>
      <p>
        真实数据、真实样式，直接挂三个组件。用于在**没有登录态**时验收它们本身 ——
        大盘 tab 整体需要登录（自选股走 Go 鉴权接口，401 会触发全局跳登录），
        所以「三块组件的对不对」与「它们在大盘 tab 里的位置对不对」是两件事，
        前者在这里验，后者要登录后看。
      </p>
    </header>

    <div class="bl__stack">
      <!--
        技术位置条。这里的 thscodes 是**真实代码**（与 WatchPulse 里自选清单同源），
        所以读到的就是生产数据，不是造出来的样例。
        未覆盖的只有「WatchPulse 是否传对了代码」那一步 —— 那是 props 绑定，
        读代码即可确认，不值得为它单开一个验收入口。
      -->
      <TechnicalStrip :thscodes="SAMPLE_CODES" :labels="SAMPLE_LABELS" />

      <AuctionStrip />
      <SectorBoard />
      <div class="bl__pair">
        <LadderHeat />
        <BasisStrip />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import AuctionStrip from '@/finance/components/market/AuctionStrip.vue'
import SectorBoard from '@/finance/components/market/SectorBoard.vue'
import LadderHeat from '@/finance/components/market/LadderHeat.vue'
import BasisStrip from '@/finance/components/market/BasisStrip.vue'
import TechnicalStrip from '@/finance/components/watchlist/TechnicalStrip.vue'

/** 真实代码，用于验收读数。 */
const SAMPLE_CODES = ['601579.SH', '002897.SZ', '002025.SZ', '002281.SZ', '000021.SZ', '002859.SZ']
const SAMPLE_LABELS: Record<string, string> = {
  '601579.SH': '云锗股份',
  '002897.SZ': '意华股份',
  '002025.SZ': '航天电器',
  '002281.SZ': '光迅科技',
  '000021.SZ': '深科技',
  '002859.SZ': '洁美电子',
}
</script>

<style scoped>
.bl {
  min-height: 100%;
  padding: 20px 24px 48px;
  background: var(--td-bg-color-page);
  color: var(--td-text-color-primary);
}
.bl__bar { margin-bottom: 16px; }
.bl__bar h1 {
  margin: 0 0 6px;
  font-family: var(--app-font-display);
  font-size: var(--app-text-xl);
}
.bl__bar p {
  margin: 0;
  max-width: 720px;
  font-size: var(--app-text-xs);
  line-height: 1.7;
  color: var(--td-text-color-secondary);
}
.bl__stack { display: flex; flex-direction: column; gap: 14px; }
.bl__pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}
@media (max-width: 1200px) {
  .bl__pair { grid-template-columns: minmax(0, 1fr); }
}
</style>
