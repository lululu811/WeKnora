<template>
    <div class="sidebar-user-area">
        <!-- 批量管理底部操作条：固定在侧栏底部、用户头像上方 -->
        <div v-if="batchMode && !collapsed" class="batch-inline-footer">
            <div class="batch-footer-left">
                <t-checkbox :checked="isAllBatchSelected" :indeterminate="isBatchIndeterminate"
                    @change="$emit('toggle-select-all', $event)">
                    {{ t('batchManage.selectAll') }}
                </t-checkbox>
            </div>
            <div class="batch-footer-right">
                <t-button size="small" variant="text" @click="$emit('exit-batch')">
                    {{ t('batchManage.cancel') }}
                </t-button>
                <t-button size="small" theme="danger" variant="base" :disabled="selectedCount === 0"
                    :loading="batchDeleting" @click="$emit('delete-selected')">
                    {{ t('batchManage.delete') }}{{ selectedCount > 0 ? `(${batchDisplayCount})` : '' }}
                </t-button>
            </div>
        </div>

        <!-- 用户区：圆形头像 + 珊瑚描边 -->
        <div class="menu_bottom">
            <UserMenu />
        </div>
    </div>
</template>

<script setup lang="ts">
/**
 * SidebarUserArea —— 侧栏底部用户区。
 *
 * 只负责渲染：批量操作条 + UserMenu 触发器。批量状态的读写全部通过
 * props/emit 回传给 menu.vue，数据流与拆分前完全一致。
 */
import { useI18n } from 'vue-i18n';
import UserMenu from '@/components/UserMenu.vue';

defineProps<{
  /** 批量管理进行中。 */
  batchMode: boolean;
  /** 侧栏折叠态（折叠时不显示批量操作条）。 */
  collapsed: boolean;
  /** 全选状态。 */
  isAllBatchSelected: boolean;
  /** 半选状态。 */
  isBatchIndeterminate: boolean;
  /** 删除按钮上展示的数量（全选时是总数，否则是已选数）。 */
  batchDisplayCount: number;
  /** 已选会话数，0 时禁用删除按钮。 */
  selectedCount: number;
  /** 删除请求进行中。 */
  batchDeleting: boolean;
}>();

defineEmits<{
  (e: 'toggle-select-all', checked: boolean): void;
  (e: 'exit-batch'): void;
  (e: 'delete-selected'): void;
}>();

const { t } = useI18n();
</script>
