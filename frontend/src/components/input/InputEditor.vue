<script setup lang="ts">
/**
 * 文本编辑区：图片预览条 + 已选知识库/文件 chip + 自增高 textarea。
 *
 * 纯展示层：所有状态与事件都来自父组件（Input-field.vue）。这里刻意不持有任何
 * 业务状态，只把原生事件原样向上抛，并把 t-textarea 的组件实例回写进父组件的
 * elRef，让父组件的自动增高 / 焦点恢复逻辑继续拿到原来的那个实例。
 */
import { ref, watch, type PropType } from 'vue'
import type { MentionItem } from '@/types/mention'
import type { ElRef, MentionChipHelpers, TranslateFn, UploadedImage } from './input.types'

/** 父组件的 allSelectedItems 在 MentionItem 基础上多了组织名等展示字段。 */
type ChipItem = MentionItem & { org_name?: string }

const props = defineProps({
  /** v-model: 文本内容 */
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  /** 图片附件的本地预览 */
  images: { type: Array as PropType<UploadedImage[]>, default: () => [] },
  /** 已选中的知识库 / 文件 / 技能等提及项 */
  selectedItems: { type: Array as PropType<ChipItem[]>, default: () => [] },
  /** 父组件持有的 textarea 组件实例引用（由本组件回写） */
  elRef: { type: Object as PropType<ElRef>, default: null },
  getMentionChipClass: { type: Function as PropType<MentionChipHelpers['getMentionChipClass']>, required: true },
  getMentionIcon: { type: Function as PropType<MentionChipHelpers['getMentionIcon']>, required: true },
  getImgSrc: { type: Function as PropType<MentionChipHelpers['getImgSrc']>, required: true },
  t: { type: Function as PropType<TranslateFn>, required: true },
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  // 父组件的 onKeydown / onInput 签名各不相同（keydown 收 (val, {e})，input 收
  // string | InputEvent），本组件只做透传，因此事件载荷用 any 原样上抛。
  (e: 'keydown', ...payload: any[]): void
  (e: 'input', payload: any): void
  (e: 'compositionstart'): void
  (e: 'compositionend', payload: any): void
  (e: 'paste', payload: any): void
  (e: 'remove-image', index: number): void
  (e: 'remove-item', item: ChipItem): void
}>()

const textareaRef = ref<any>(null)

// 把 t-textarea 的组件实例交还给父组件：拆分前的 ref 指向的就是这个对象，
// 因此父脚本里所有 textareaRef.value?.xxx 的调用保持原有行为。
watch(
  textareaRef,
  (instance) => {
    if (props.elRef) props.elRef.value = instance
  },
  { immediate: true, flush: 'post' },
)
</script>

<template>
  <div class="input-editor">
    <!-- 图片预览区域 -->
    <div v-if="images.length > 0" class="image-preview-bar">
      <div v-for="(img, idx) in images" :key="idx" class="image-preview-item">
        <img :src="img.preview" class="image-preview-thumb" />
        <span class="image-preview-remove" @click="emit('remove-image', idx)">×</span>
      </div>
    </div>

    <!-- 选中的知识库和文件标签（显示在输入框内顶部） -->
    <div v-if="selectedItems.length > 0" class="selected-tags-inline">
      <span
        v-for="item in selectedItems"
        :key="`${item.type}:${item.id}`"
        class="mention-chip"
        :class="[getMentionChipClass(item), { 'mention-chip--agent': item.isAgentConfigured }]"
      >
        <span class="mention-chip__icon-wrap" :class="{ 'has-org': item.org_name }">
          <span class="mention-chip__icon">
            <t-icon v-if="item.type === 'kb'" :name="item.kbType === 'faq' ? 'chat-bubble-help' : 'folder'" />
            <t-icon v-else :name="getMentionIcon(item)" />
          </span>
          <span v-if="item.org_name" class="mention-chip__org-badge">
            <img
              :src="getImgSrc(item.type === 'file' ? 'organization-grey.svg' : 'organization-green.svg')"
              class="mention-chip__org-img"
              alt=""
              aria-hidden="true"
            />
          </span>
        </span>
        <span class="mention-chip__name" :title="item.name">{{ item.name }}</span>
        <span
          class="mention-chip__remove"
          :aria-label="t('common.remove')"
          @click.stop="emit('remove-item', item)"
        >×</span>
      </span>
    </div>

    <!-- 实际输入框：事件全部透传给父组件，逻辑仍只存在于父组件 -->
    <t-textarea
      ref="textareaRef"
      :model-value="modelValue"
      :placeholder="placeholder"
      name="description"
      :autosize="true"
      @update:model-value="(value: string) => emit('update:modelValue', value)"
      @keydown="(e: any) => emit('keydown', e)"
      @input="(e: any) => emit('input', e)"
      @compositionstart="() => emit('compositionstart')"
      @compositionend="(e: any) => emit('compositionend', e)"
      @paste="(e: any) => emit('paste', e)"
    />
  </div>
</template>

<style scoped lang="less">
/* 图片预览条与 mention chip 的样式随编辑区一起搬过来，作用域不变 */
@import './css/input-editor.less';

/* 只作为布局透传壳：不产生盒子，保住拆分前的兄弟顺序与尺寸计算 */
.input-editor {
  display: contents;
}
</style>
