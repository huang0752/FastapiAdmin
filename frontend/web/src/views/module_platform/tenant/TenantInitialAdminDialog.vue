<template>
  <FaDialog
    :model-value="modelValue"
    title="租户创建成功"
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :before-close="beforeClose"
    @update:model-value="handleVisibilityUpdate"
    @closed="emit('closed')"
  >
    <div class="tenant-credentials">
      <ElAlert
        title="初始管理员密码仅显示一次，关闭后无法再次查看，请立即安全保存。"
        type="warning"
        :closable="false"
        show-icon
      />

      <dl class="tenant-credentials__list">
        <div class="tenant-credentials__row">
          <dt>租户</dt>
          <dd>{{ tenantName }}</dd>
        </div>
        <div class="tenant-credentials__row">
          <dt>登录账号</dt>
          <dd>
            <code>{{ credentialSnapshot.username }}</code>
            <ElButton
              data-test="copy-username"
              link
              type="primary"
              @click="copyText(credentialSnapshot.username)"
            >
              复制账号
            </ElButton>
          </dd>
        </div>
        <div class="tenant-credentials__row">
          <dt>临时密码</dt>
          <dd>
            <code>{{ credentialSnapshot.password }}</code>
            <ElButton
              data-test="copy-password"
              link
              type="primary"
              @click="copyText(credentialSnapshot.password)"
            >
              复制密码
            </ElButton>
          </dd>
        </div>
      </dl>

      <ElButton data-test="copy-all" type="primary" plain @click="copyText(handoffText)">
        复制完整交付信息
      </ElButton>

      <TenantProvisionResult :provisions="provisions" :application-names="applicationNames" />

      <ElCheckbox v-model="acknowledged" class="tenant-credentials__acknowledgement">
        我已安全保存账号和临时密码
      </ElCheckbox>
    </div>

    <template #footer>
      <ElButton data-test="finish" type="primary" :disabled="!acknowledged" @click="finish">
        已保存，关闭
      </ElButton>
    </template>
  </FaDialog>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { TenantInitialAdmin } from "@/api/module_platform/tenant";
import type { TenantProvisionListItem } from "@/api/module_control";
import TenantProvisionResult from "./TenantProvisionResult.vue";

defineOptions({ name: "TenantInitialAdminDialog" });

interface Props {
  modelValue: boolean;
  tenantName: string;
  credentials: TenantInitialAdmin;
  provisions?: TenantProvisionListItem[];
  applicationNames?: Record<number, string>;
}

const props = withDefaults(defineProps<Props>(), {
  provisions: () => [],
  applicationNames: () => ({}),
});

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  closed: [];
}>();

const acknowledged = ref(false);
const credentialSnapshot = ref<TenantInitialAdmin>({ username: "", password: "" });

const handoffText = computed(
  () =>
    `租户：${props.tenantName}\n登录账号：${credentialSnapshot.value.username}\n临时密码：${credentialSnapshot.value.password}\n请首次登录后立即修改密码。`
);

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      acknowledged.value = false;
      credentialSnapshot.value = { ...props.credentials };
    } else {
      clearCredentialSnapshot();
    }
  },
  { immediate: true }
);

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    ElMessage.success("复制成功");
  } catch {
    ElMessage.warning("复制失败，请手动复制");
  }
}

function beforeClose(done: () => void) {
  if (acknowledged.value) {
    done();
    return;
  }
  ElMessage.warning("请先确认已安全保存账号和临时密码");
}

function handleVisibilityUpdate(value: boolean) {
  if (value || acknowledged.value) emit("update:modelValue", value);
}

function finish() {
  if (!acknowledged.value) return;
  emit("update:modelValue", false);
  clearCredentialSnapshot();
}

function clearCredentialSnapshot() {
  credentialSnapshot.value = { username: "", password: "" };
}

defineExpose({ credentialSnapshot: () => ({ ...credentialSnapshot.value }) });
onBeforeUnmount(clearCredentialSnapshot);
</script>

<style scoped lang="scss">
.tenant-credentials {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.tenant-credentials__list {
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--el-border-radius-base);
}

.tenant-credentials__row {
  display: grid;
  grid-template-columns: 112px 1fr;

  & + & {
    border-top: 1px solid var(--el-border-color-light);
  }

  dt,
  dd {
    display: flex;
    gap: 12px;
    align-items: center;
    min-height: 52px;
    padding: 10px 16px;
    margin: 0;
  }

  dt {
    color: var(--el-text-color-secondary);
    background: var(--el-fill-color-light);
  }

  dd {
    justify-content: space-between;
    min-width: 0;
  }

  code {
    font-size: 15px;
    color: var(--el-text-color-primary);
    overflow-wrap: anywhere;
  }
}

.tenant-credentials__acknowledgement {
  align-self: flex-start;
}
</style>
