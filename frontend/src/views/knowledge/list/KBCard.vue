<template>
  <div
    v-show="visible"
    class="kb-card note-card"
    :class="[mode === 'all-shared' || mode === 'space-shared' ? 'shared-kb-card' : '', {
      uninitialized: !initialized,
      'kb-type-document': (kb.type || 'document') === 'document',
      'kb-type-faq': kb.type === 'faq',
      'highlight-flash': highlighted,
      'is-selected': selected,
    }]"
    :ref="el => { if (highlighted && el) onMountRef(el as HTMLElement) }"
    role="link"
    tabindex="0"
    @keydown.enter.self.prevent="emit('open')"
    @keydown.space.self.prevent="emit('open')"
    @click="emit('open')"
  >
    <!-- 行尾收藏操作，与更多菜单分开。space-shared 形态不下沉收藏星：
         该视图的条目来自「空间共享」清单，收藏态由所属空间统一管理。 -->
    <button
      v-if="mode !== 'space-shared'" type="button" class="kb-favorite-star"
      :class="{ 'is-favorited': favorited }" :aria-label="$t('listSpaceSidebar.favorites')"
      :aria-pressed="favorited" @click.stop="emit('toggle-favorite')"
    >
      <t-icon :name="favorited ? 'star-filled' : 'star'" size="14px" />
    </button>

    <div class="card-header">
      <span class="card-title" :title="kb.name">
        <KbWikiBadge v-if="isWiki" />
        <span class="card-title-text">{{ kb.name }}</span>
      </span>

      <!-- all-mine：菜单常驻，pin 是 per-user 的，只需要 KB 读权限；
           Settings / Delete 是写操作，仍由 canManage 收口。 -->
      <t-popup v-if="showMenu" overlayClassName="card-more-popup" trigger="click" destroy-on-close placement="bottom-right">
        <button type="button" :aria-label="$t('common.expand')" class="more-wrap" @click.stop>
          <img class="more-icon" src="@/assets/img/more.png" alt="" />
        </button>
        <template #content>
          <div class="popup-menu" @click.stop>
            <div class="popup-menu-item" @click.stop="emit('toggle-pin')">
              <t-icon class="menu-icon" :name="kb.is_pinned ? 'pin-filled' : 'pin'" />
              <span>{{ kb.is_pinned ? $t('knowledgeList.pin.unpin') : $t('knowledgeList.pin.pin') }}</span>
            </div>
            <div v-if="canDuplicate" class="popup-menu-item" @click.stop="emit('duplicate')">
              <t-icon class="menu-icon" name="file-copy" />
              <span>{{ $t('knowledgeList.menu.duplicate') }}</span>
            </div>
            <template v-if="canManage">
              <div class="popup-menu-item" @click.stop="emit('settings')">
                <t-icon class="menu-icon" name="setting" />
                <span>{{ $t('knowledgeBase.settings') }}</span>
              </div>
              <div class="popup-menu-item delete" @click.stop="emit('delete')">
                <t-icon class="menu-icon" name="delete" />
                <span>{{ $t('common.delete') }}</span>
              </div>
            </template>
          </div>
        </template>
      </t-popup>

      <!-- mine-mine：更多菜单受控展开，用于和 openMore / currentMoreIndex 联动。 -->
      <t-popup
        v-else-if="showTrackedMenu"
        :visible="menuVisible"
        overlayClassName="card-more-popup"
        trigger="click"
        destroy-on-close
        placement="bottom-right"
        @update:visible="onTrackedMenuVisible"
      >
        <button
          type="button"
          :aria-label="$t('common.expand')"
          class="more-wrap"
          :class="{ 'active-more': menuActive }"
          @click.stop="emit('menu-open')"
        >
          <img class="more-icon" src="@/assets/img/more.png" alt="" />
        </button>
        <template #content>
          <div class="popup-menu" @click.stop>
            <div class="popup-menu-item" @click.stop="emit('toggle-pin')">
              <t-icon class="menu-icon" :name="kb.is_pinned ? 'pin-filled' : 'pin'" />
              <span>{{ kb.is_pinned ? $t('knowledgeList.pin.unpin') : $t('knowledgeList.pin.pin') }}</span>
            </div>
            <div v-if="canDuplicate" class="popup-menu-item" @click.stop="emit('duplicate')">
              <t-icon class="menu-icon" name="file-copy" />
              <span>{{ $t('knowledgeList.menu.duplicate') }}</span>
            </div>
            <template v-if="canManage">
              <div class="popup-menu-item" @click.stop="emit('settings')">
                <t-icon class="menu-icon" name="setting" />
                <span>{{ $t('knowledgeBase.settings') }}</span>
              </div>
              <div class="popup-menu-item delete" @click.stop="emit('delete')">
                <t-icon class="menu-icon" name="delete" />
                <span>{{ $t('common.delete') }}</span>
              </div>
            </template>
          </div>
        </template>
      </t-popup>

      <t-tooltip v-if="showDetailTrigger" :content="$t('knowledgeList.menu.viewDetails')" placement="top">
        <button
          type="button"
          class="shared-detail-trigger"
          :aria-label="$t('knowledgeList.menu.viewDetails')"
          @click.stop="emit('open-detail')"
        >
          <t-icon name="info-circle" size="16px" />
        </button>
      </t-tooltip>
    </div>

    <div class="card-content">
      <div class="card-description" :title="kb.description || $t('knowledgeBase.noDescription')">
        {{ kb.description || $t('knowledgeBase.noDescription') }}
      </div>
    </div>

    <div class="card-bottom">
      <div class="bottom-left">
        <div class="feature-badges">
          <t-tooltip :content="typeLabel" placement="top">
            <div class="feature-badge" :class="typeBadgeClass">
              <t-icon :name="isFaq ? 'chat-bubble-help' : 'file'" size="14px" />
              <span class="badge-count">{{ countText }}</span>
              <t-icon v-if="isMineMode && kb.isProcessing" name="loading" size="12px" class="processing-icon" />
            </div>
          </t-tooltip>
          <t-tooltip v-if="showKnowledgeGraph" :content="$t('knowledgeList.features.knowledgeGraph')" placement="top">
            <div class="feature-badge kg"><t-icon name="relation" size="14px" /></div>
          </t-tooltip>
          <t-tooltip v-if="showMultimodal" :content="$t('knowledgeList.features.multimodal')" placement="top">
            <div class="feature-badge multimodal"><t-icon name="image" size="14px" /></div>
          </t-tooltip>
          <t-tooltip v-if="showQuestionGeneration" :content="$t('knowledgeList.features.questionGeneration')" placement="top">
            <div class="feature-badge question"><t-icon name="help-circle" size="14px" /></div>
          </t-tooltip>
              <t-tooltip v-if="showShare" :content="$t('knowledgeList.sharedToOrgs', { count: kb.share_count ?? 0 })" placement="top">
            <div class="feature-badge shared"><t-icon name="share" size="14px" /></div>
          </t-tooltip>
        </div>
      </div>

      <!-- all-mine / mine-mine：右下角来源徽章 -->
      <div v-if="isMineMode && showOriginBadge" class="bottom-right">
        <ResourceOriginBadge :variant="originVariant" :creator-name="kb.creator_name" />
      </div>
      <!-- all-shared：右下角组织来源；space-shared 保持最简，不显示。 -->
      <div v-else-if="mode === 'all-shared' && kb.org_name" class="bottom-right">
        <t-tooltip :content="kb.org_name" placement="top">
          <div class="org-source">
            <img src="@/assets/img/organization-green.svg" class="org-source-icon" alt="" aria-hidden="true" />
            <span>{{ kb.org_name }}</span>
          </div>
        </t-tooltip>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import KbWikiBadge from '../components/KbWikiBadge.vue'
import ResourceOriginBadge from '@/components/ResourceOriginBadge.vue'
import { isInitialized, isWikiKb, type KB, type KbCardMode } from './kbList.types'

/**
 * 单个知识库卡片（方向 A「午后的工作室」：便签形态）。
 *
 * 拆分前这段模板在 KnowledgeBaseList.vue 的 4 个 v-for 分支里各写了一遍，
 * 只有收藏星、计数兜底、feature badge 组合、来源角标、更多菜单这几处细节不同。
 * 这里用一个 `mode` 把差异显式建模，模板只剩一份。
 *
 * 纯展示组件：不取 store、不发请求，所有写操作通过 emit 上抛给列表页。
 */
const props = withDefaults(
  defineProps<{
    /**
     * 卡片数据。`type` 放宽为 string：空间共享接口（OrganizationSharedKnowledgeBaseItem）
     * 返回的 knowledge_base.type 是普通 string，模板里按 (type || 'document') === 'document'
     * 兜底判断，运行时行为与拆分前一致，因此这里不做收窄，强转会掩盖真实的接口差异。
     */
    kb: Omit<KB, 'type'> & { type?: string; org_name?: string; permission?: string }
    mode: KbCardMode
    /** 分组折叠时为 false（对应原模板上的 v-show）。 */
    visible?: boolean
    favorited?: boolean
    canManage?: boolean
    canDuplicate?: boolean
    /** 来源徽章是否展示（由列表页的 showKbOriginBadge 判定）。 */
    showOriginBadge?: boolean
    originVariant?: 'mine' | 'creator'
    /** 高亮闪烁：从 URL 带 highlightKbId 进来时的一次性定位。 */
    highlighted?: boolean
    /** 选中态：珊瑚 2px 边框。 */
    selected?: boolean
    /** mine-mine 形态的受控更多菜单。 */
    menuVisible?: boolean
    menuActive?: boolean
  }>(),
  {
    visible: true,
    favorited: false,
    canManage: false,
    canDuplicate: false,
    showOriginBadge: false,
    originVariant: 'mine',
    highlighted: false,
    selected: false,
    menuVisible: false,
    menuActive: false,
  },
)

const emit = defineEmits<{
  open: []
  'toggle-favorite': []
  'toggle-pin': []
  duplicate: []
  settings: []
  delete: []
  'open-detail': []
  'menu-open': []
  'update:menuVisible': [value: boolean]
  'card-ref': [el: HTMLElement]
}>()

const { t } = useI18n()

const isFaq = computed(() => props.kb.type === 'faq')
const isMineMode = computed(() => props.mode === 'all-mine' || props.mode === 'mine-mine')
const initialized = computed(() => isInitialized(props.kb))
const isWiki = computed(() => isWikiKb(props.kb))

const typeLabel = computed(() =>
  isFaq.value ? t('knowledgeEditor.basic.typeFAQ') : t('knowledgeEditor.basic.typeDocument'),
)

const typeBadgeClass = computed(() => ({
  'type-document': (props.kb.type || 'document') === 'document',
  'type-faq': props.kb.type === 'faq',
}))

/**
 * 计数兜底值沿用原模板：自己的卡片 0，共享卡片 '-'（共享来源不保证回填计数）。
 * space-shared 用 `??`（只兜 null），all-shared 用 `||`（连 0 也兜）。
 */
const countText = computed(() => {
  const raw = isFaq.value ? props.kb.chunk_count : props.kb.knowledge_count
  if (props.mode === 'space-shared') return raw ?? '-'
  if (props.mode === 'all-shared') return raw || '-'
  return raw || 0
})

const showMenu = computed(() => props.mode === 'all-mine')
const showTrackedMenu = computed(() => props.mode === 'mine-mine')
const showDetailTrigger = computed(() => props.mode !== 'all-mine' && props.mode !== 'mine-mine')

const showKnowledgeGraph = computed(() => props.mode !== 'space-shared' && !!props.kb.extract_config?.enabled)

/** all-mine 只看 vlm_config；mine-mine / all-shared 额外把非 local 存储算作多模态。 */
const showMultimodal = computed(() => {
  if (props.mode === 'space-shared') return false
  if (props.mode === 'all-mine') return !!props.kb.vlm_config?.enabled
  const provider = props.kb.storage_provider_config?.provider
  return !!props.kb.vlm_config?.enabled || (!!provider && provider !== 'local')
})

const showQuestionGeneration = computed(
  () => props.mode !== 'space-shared' && !!props.kb.question_generation_config?.enabled,
)

/** 共享徽章只在自己的卡片上出现：共享卡片本身就是「已共享」的呈现。 */
const showShare = computed(() => isMineMode.value && (props.kb.share_count ?? 0) > 0)

function onMountRef(el: HTMLElement) {
  emit('card-ref', el)
}

function onTrackedMenuVisible(visible: boolean) {
  emit('update:menuVisible', visible)
}
</script>

<style scoped lang="less">
@import (reference) '@/components/css/resource-card.less';

.kb-card {
  .resource-card();

  &.uninitialized {
    opacity: 0.9;
  }

  .kb-favorite-star { .resource-favorite-button(); }
}

/* 方向 A「午后的工作室」：卡片是一张摊在桌上的便签——纸面、墨字、珊瑚重点标记。
   悬停时纸片被轻轻抬起并落下一层暖光，而不是常见的冷灰投影。 */
.note-card {
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-lg);
  min-height: 140px; // 容纳「标题 + 一行描述 + 元信息行」三段
  box-shadow: var(--td-shadow-1); // 暖光，静止时几乎不可见
  transition:
    transform var(--app-motion-base) cubic-bezier(0.16, 1, 0.3, 1),
    box-shadow var(--app-motion-base) ease,
    border-color var(--app-motion-base) ease;

  &:hover {
    transform: translateY(-2px);
    border-color: color-mix(in srgb, var(--td-brand-color) 40%, var(--td-component-stroke));
    box-shadow: var(--td-shadow-2);
  }

  &:active { // 陷回桌面，给出物理触感
    transform: translateY(0) scale(0.98);
    box-shadow: var(--td-shadow-1);
  }

  &:focus-visible {
    outline: 2px solid var(--td-brand-color);
    outline-offset: -2px;
  }

  &.is-selected { // 珊瑚 2px 实边，与 focus 环区分
    border: 2px solid var(--td-brand-color);
    box-shadow: var(--td-shadow-2);
  }

  .card-title-text {
    font-family: var(--app-font-display);
    font-weight: 600;
    color: var(--td-text-color-primary);
  }

  .card-description {
    display: -webkit-box; // 收成一行，纸面上不该有大段文字
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    overflow: hidden;
    color: var(--td-text-color-secondary);
  }
}

.shared-kb-card {
  position: relative;

  .org-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: var(--app-text-sm);
    border-color: color-mix(in srgb, var(--td-brand-color) 15%, transparent);
    color: var(--td-brand-color);
    background: color-mix(in srgb, var(--td-brand-color) 4%, transparent);
    font-weight: 500;
    padding: 2px 8px;
    border-radius: var(--app-radius-xs);
    max-width: fit-content;
  }
}

.bottom-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.bottom-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;

  .card-time {
    font-size: var(--app-text-sm);
    color: var(--td-text-color-placeholder);
  }
}

.feature-badge {
  .resource-feature-badge();

  &.type-document,
  &.type-faq {
    background: var(--td-bg-color-secondarycontainer);
    color: var(--td-text-color-secondary);
    width: auto;
    padding: 0 6px;
    gap: 3px;

    &:hover {
      background: var(--td-bg-color-container-hover);
    }

    .badge-count {
      font-size: var(--app-text-xs);
      font-weight: 500;
    }

    .processing-icon {
      animation: wk-spin 1s linear infinite;
    }
  }

  &.kg {
    background: color-mix(in srgb, var(--app-accent-purple) 8%, transparent);
    color: var(--td-brand-color);

    &:hover {
      background: color-mix(in srgb, var(--app-accent-purple) 12%, transparent);
    }
  }

  &.multimodal {
    background: color-mix(in srgb, var(--td-warning-color) 8%, transparent);
    color: var(--td-warning-color);

    &:hover {
      background: color-mix(in srgb, var(--td-warning-color) 12%, transparent);
    }
  }

  &.question {
    background: color-mix(in srgb, var(--td-success-color) 8%, transparent);
    color: var(--td-success-color);

    &:hover {
      background: color-mix(in srgb, var(--td-success-color) 12%, transparent);
    }
  }

  &.shared {
    background: color-mix(in srgb, var(--td-brand-color) 8%, transparent);
    color: var(--td-brand-color);

    &:hover {
      background: color-mix(in srgb, var(--td-brand-color) 12%, transparent);
    }
  }
}

.shared-detail-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: none;
  border-radius: var(--app-radius-sm);
  background: transparent;
  color: var(--td-brand-color);
  font-size: var(--app-text-md);
  font-family: var(--app-font-family);
  cursor: pointer;
  transition: background var(--app-motion-base) ease, color var(--app-motion-base) ease;

  .t-icon {
    flex-shrink: 0;
  }

  &:hover {
    background: color-mix(in srgb, var(--td-brand-color) 8%, transparent);
  }
}

@keyframes highlightFlash {
  0%,
  100% {
    border-color: var(--td-brand-color);
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--td-brand-color) 40%, transparent);
    transform: scale(1);
  }
  50% {
    box-shadow: 0 0 0 8px color-mix(in srgb, var(--td-brand-color) 0%, transparent);
    transform: scale(1.02);
  }
}

.kb-card.highlight-flash {
  animation: highlightFlash 0.6s ease-in-out 3;
  border-color: var(--td-brand-color) !important;
  box-shadow: 0 0 12px color-mix(in srgb, var(--td-brand-color) 30%, transparent) !important;
}
</style>
