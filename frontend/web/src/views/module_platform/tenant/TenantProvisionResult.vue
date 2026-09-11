<template>
  <section v-if="provisions.length" class="provision-result">
    <h3>产品开通状态</h3>
    <div class="provision-result__list">
      <article v-for="item in provisions" :key="item.id" class="provision-result__item">
        <div>
          <strong>{{ applicationLabel(item.application_id) }}</strong>
          <span>{{ item.desired_target_tenant_code }}</span>
        </div>
        <ElTag :type="statusMeta[item.status].type">{{ statusMeta[item.status].label }}</ElTag>
        <span>尝试 {{ item.attempt_count }}/{{ item.max_attempts }}</span>
        <span class="provision-result__error">{{ item.last_error_message || "-" }}</span>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { TenantProvisionListItem, TenantProvisionStatus } from "@/api/module_control";

defineOptions({ name: "TenantProvisionResult" });

const props = withDefaults(
  defineProps<{
    provisions?: TenantProvisionListItem[];
    applicationNames?: Record<number, string>;
  }>(),
  { provisions: () => [], applicationNames: () => ({}) }
);

const statusMeta: Record<
  TenantProvisionStatus,
  { label: string; type: "info" | "warning" | "success" | "danger" }
> = {
  pending: { label: "等待开通", type: "info" },
  processing: { label: "开通中", type: "warning" },
  succeeded: { label: "已开通", type: "success" },
  failed: { label: "开通失败", type: "danger" },
};

function applicationLabel(applicationId: number) {
  return props.applicationNames[applicationId] ?? `应用 #${applicationId}`;
}
</script>

<style scoped lang="scss">
.provision-result {
  h3 {
    margin: 0 0 12px;
    font-size: 15px;
  }
}

.provision-result__list {
  overflow: hidden;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--el-border-radius-base);
}

.provision-result__item {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) 100px 90px minmax(120px, 1fr);
  gap: 12px;
  align-items: center;
  padding: 12px;

  & + & {
    border-top: 1px solid var(--el-border-color-lighter);
  }

  div {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  span {
    color: var(--el-text-color-secondary);
  }
}

.provision-result__error {
  overflow-wrap: anywhere;
}
</style>
