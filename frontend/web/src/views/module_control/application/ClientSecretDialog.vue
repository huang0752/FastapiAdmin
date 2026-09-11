<template>
  <FaDialog
    v-model="visible"
    title="保存客户端凭据"
    width="560px"
    :show-confirm="false"
    @cancel="close"
  >
    <ElAlert
      title="客户端密钥仅显示这一次。关闭前请复制并保存到目标系统的安全配置中。"
      type="warning"
      :closable="false"
      show-icon
    />
    <div class="secret-fields">
      <div class="secret-field">
        <span>Client ID</span>
        <ElInput :model-value="secret.client_id" readonly>
          <template #append>
            <ElButton @click="copyValue(secret.client_id, 'Client ID')">复制</ElButton>
          </template>
        </ElInput>
      </div>
      <div class="secret-field">
        <span>Client Secret</span>
        <ElInput :model-value="secret.client_secret" type="password" readonly show-password>
          <template #append>
            <ElButton @click="copyValue(secret.client_secret, 'Client Secret')">复制</ElButton>
          </template>
        </ElInput>
      </div>
    </div>
  </FaDialog>
</template>

<script setup lang="ts">
import type { ClientSecretResult } from "@/api/module_control";
import { ElMessage } from "element-plus";
import { onBeforeUnmount, reactive, ref, watch } from "vue";

defineOptions({ name: "ControlClientSecretDialog" });

const visible = ref(false);
const secret = reactive<ClientSecretResult>({ client_id: "", client_secret: "" });

function clearSecret() {
  secret.client_id = "";
  secret.client_secret = "";
}

function show(result: ClientSecretResult) {
  clearSecret();
  Object.assign(secret, result);
  visible.value = true;
}

function close() {
  visible.value = false;
}

async function copyValue(value: string, label: string) {
  if (!value) return;
  await navigator.clipboard.writeText(value);
  ElMessage.success(`${label} 已复制`);
}

watch(visible, (value) => {
  if (!value) clearSecret();
});
onBeforeUnmount(() => clearSecret());

defineExpose({ show, close, secretSnapshot: () => ({ ...secret }) });
</script>

<style scoped lang="scss">
.secret-fields {
  display: grid;
  gap: 18px;
  margin-top: 20px;
}

.secret-field {
  display: grid;
  gap: 8px;

  > span {
    font-size: 13px;
    font-weight: 600;
    color: var(--el-text-color-regular);
  }
}
</style>
