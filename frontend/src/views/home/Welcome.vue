<template>
  <div class="welcome-page">
    <div class="welcome-container">
      <!-- 问候卡 -->
      <section class="greeting-card">
        <div class="greeting-text">
          <h1 class="greeting-title">{{ greetingText }}，{{ userName }}</h1>
          <p class="greeting-subtitle">让今天的知识，成为明天的智慧</p>
        </div>
        <div class="greeting-date">
          <span class="date-text">{{ currentDate }}</span>
        </div>
      </section>

      <!-- 今日统计 -->
      <section class="stats-grid">
        <div class="stat-card">
          <div class="stat-icon">💬</div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.conversations }}</div>
            <div class="stat-label">今日对话</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon">📚</div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.knowledgeBases }}</div>
            <div class="stat-label">知识库</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon">📄</div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.documents }}</div>
            <div class="stat-label">文档总数</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon">✨</div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.insights }}</div>
            <div class="stat-label">知识洞察</div>
          </div>
        </div>
      </section>

      <!-- 快捷入口 -->
      <section class="quick-actions">
        <h2 class="section-title">开始探索</h2>
        <div class="actions-grid">
          <button class="action-card primary" @click="$router.push('/platform/creatChat')">
            <div class="action-icon">🗨️</div>
            <div class="action-text">
              <div class="action-title">新对话</div>
              <div class="action-desc">与 AI 助手开始新的知识探索</div>
            </div>
          </button>
          <button class="action-card" @click="$router.push('/platform/knowledge-bases')">
            <div class="action-icon">📚</div>
            <div class="action-text">
              <div class="action-title">浏览知识库</div>
              <div class="action-desc">管理和探索你的知识资产</div>
            </div>
          </button>
          <button class="action-card" @click="$router.push('/platform/agents')">
            <div class="action-icon">🤖</div>
            <div class="action-text">
              <div class="action-title">智能体</div>
              <div class="action-desc">定制化 AI 助手满足特定需求</div>
            </div>
          </button>
        </div>
      </section>

      <!-- 今日一句 -->
      <section class="daily-quote">
        <div class="quote-mark">❝</div>
        <p class="quote-text">{{ dailyQuote.text }}</p>
        <p class="quote-author">—— {{ dailyQuote.author }}</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

// 问候语（根据时间）
const greetingText = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

// 用户名
const userName = computed(() => {
  return (authStore.user as any)?.name || (authStore.user as any)?.username || authStore.user?.email || '小陈'
})

// 当前日期
const currentDate = computed(() => {
  const now = new Date()
  const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
  const month = now.getMonth() + 1
  const day = now.getDate()
  const weekday = weekdays[now.getDay()]
  return `${month}月${day}日 ${weekday}`
})

// 统计数据（从各 store 聚合，这里用占位值）
const stats = ref({
  conversations: 0,
  knowledgeBases: 0,
  documents: 0,
  insights: 0,
})

// 今日一句（知识/学习相关名言）
const quotes = [
  { text: '知识就是力量。', author: '培根' },
  { text: '学而不思则罔，思而不学则殆。', author: '孔子' },
  { text: '读书破万卷，下笔如有神。', author: '杜甫' },
  { text: '知识是一种快乐，而好奇则是知识的萌芽。', author: '赫胥黎' },
  { text: '活到老，学到老。', author: '朱熹' },
  { text: '书山有路勤为径，学海无涯苦作舟。', author: '韩愈' },
  { text: '不积跬步，无以至千里；不积小流，无以成江海。', author: '荀子' },
]

const dailyQuote = computed(() => {
  // 根据日期选择名言，保证同一天看到同一条
  const dayOfYear = Math.floor((Date.now() - new Date(new Date().getFullYear(), 0, 0).getTime()) / 86400000)
  return quotes[dayOfYear % quotes.length]
})

onMounted(() => {
  // TODO: 从各 store 加载真实统计数据
  // stats.value.conversations = ...
  // stats.value.knowledgeBases = ...
})
</script>

<style lang="less" scoped>
.welcome-page {
  min-height: 100%;
  background: var(--td-bg-color-page);
  padding: var(--app-space-8) var(--app-space-6);
  overflow-y: auto;
}

.welcome-container {
  max-width: 960px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--app-space-8);
}

/* 问候卡 */
.greeting-card {
  background: var(--td-bg-color-container);
  border-radius: var(--app-radius-xl);
  padding: var(--app-space-10) var(--app-space-8);
  box-shadow: var(--td-shadow-1);
  display: flex;
  justify-content: space-between;
  align-items: center;
  /* 温润现代：左侧品牌色渐变条 */
  border-left: 4px solid var(--td-brand-color);
  background: linear-gradient(135deg,
    var(--td-bg-color-container) 0%,
    var(--app-brand-subtle, rgba(45, 106, 100, 0.04)) 100%);
}

.greeting-text {
  flex: 1;
}

.greeting-title {
  font-family: var(--app-font-display);
  font-size: 32px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  margin: 0 0 var(--app-space-2) 0;
  letter-spacing: 0.02em;
}

.greeting-subtitle {
  font-size: var(--app-text-base);
  color: var(--td-text-color-secondary);
  margin: 0;
}

.greeting-date {
  text-align: right;
}

.date-text {
  font-family: var(--app-font-display);
  font-size: var(--app-text-lg);
  color: var(--td-text-color-secondary);
  letter-spacing: 0.05em;
}

/* 统计网格 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--app-space-4);
}

.stat-card {
  background: var(--td-bg-color-container);
  border-radius: var(--app-radius-lg);
  padding: var(--app-space-5);
  box-shadow: var(--td-shadow-1);
  display: flex;
  align-items: center;
  gap: var(--app-space-4);
  transition: all var(--app-motion-base);

  &:hover {
    box-shadow: var(--td-shadow-2);
    transform: translateY(-2px);
  }
}

.stat-icon {
  font-size: 32px;
  line-height: 1;
}

.stat-content {
  flex: 1;
}

.stat-value {
  font-family: var(--app-font-display);
  font-size: 28px;
  font-weight: 600;
  color: var(--td-brand-color);
  line-height: 1.2;
}

.stat-label {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  margin-top: var(--app-space-1);
}

/* 快捷入口 */
.section-title {
  font-family: var(--app-font-display);
  font-size: var(--app-text-xl);
  font-weight: 600;
  color: var(--td-text-color-primary);
  margin: 0 0 var(--app-space-4) 0;
  letter-spacing: 0.02em;
}

.actions-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--app-space-4);
}

.action-card {
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-lg);
  padding: var(--app-space-5);
  display: flex;
  align-items: center;
  gap: var(--app-space-4);
  cursor: pointer;
  transition: all var(--app-motion-base);
  text-align: left;

  &:hover {
    border-color: var(--td-brand-color);
    box-shadow: var(--td-shadow-1);
    transform: translateY(-1px);
  }

  &.primary {
    background: linear-gradient(135deg,
      var(--td-brand-color) 0%,
      var(--td-brand-color-hover) 100%);
    border-color: transparent;
    color: var(--td-text-color-anti);

    .action-title,
    .action-desc {
      color: var(--td-text-color-anti);
    }

    &:hover {
      box-shadow: var(--td-shadow-2);
    }
  }
}

.action-icon {
  font-size: 28px;
  line-height: 1;
}

.action-text {
  flex: 1;
}

.action-title {
  font-size: var(--app-text-base);
  font-weight: 600;
  color: var(--td-text-color-primary);
  margin-bottom: 2px;
}

.action-desc {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

/* 今日一句 */
.daily-quote {
  background: var(--td-bg-color-container);
  border-radius: var(--app-radius-lg);
  padding: var(--app-space-6);
  box-shadow: var(--td-shadow-1);
  position: relative;
  text-align: center;
  /* 温润现代：赭石点缀 */
  border-top: 2px solid var(--app-accent-ochre, #B8855E);
}

.quote-mark {
  font-family: var(--app-font-display);
  font-size: 48px;
  color: var(--app-accent-ochre, #B8855E);
  line-height: 1;
  margin-bottom: var(--app-space-2);
  opacity: 0.4;
}

.quote-text {
  font-family: var(--app-font-display);
  font-size: var(--app-text-lg);
  color: var(--td-text-color-primary);
  line-height: 1.7;
  margin: 0 0 var(--app-space-3) 0;
  letter-spacing: 0.05em;
}

.quote-author {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  margin: 0;
  letter-spacing: 0.08em;
}

/* 响应式 */
@media (max-width: 768px) {
  .welcome-page {
    padding: var(--app-space-4);
  }

  .greeting-card {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--app-space-3);
    padding: var(--app-space-6);
  }

  .greeting-date {
    text-align: left;
  }

  .greeting-title {
    font-size: 24px;
  }

  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .actions-grid {
    grid-template-columns: 1fr;
  }
}
</style>
