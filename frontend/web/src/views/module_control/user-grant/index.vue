<template>
  <div class="fa-full-height grant-page">
    <ElCard shadow="never" class="grant-selector">
      <div class="grant-selector__copy">
        <h2>用户应用授权</h2>
        <p>选择当前租户已开通的应用，再为租户成员逐一授权或撤权。</p>
      </div>
      <ElSelect
        v-model="selectedOpeningId"
        filterable
        placeholder="选择已开通应用"
        class="opening-select"
        :loading="openingsLoading"
        @change="loadMembers"
      >
        <ElOption
          v-for="opening in openings"
          :key="opening.tenant_application_id"
          :label="opening.application_name"
          :value="opening.tenant_application_id"
        />
      </ElSelect>
    </ElCard>

    <ElCard class="fa-table-card" shadow="hover">
      <ElAlert
        v-if="openingsError || membersError || actionError"
        class="mb-3"
        type="error"
        :title="openingsError || membersError || actionError"
        :closable="false"
        show-icon
      >
        <template #default>
          <ElButton
            v-if="openingsError || membersError"
            link
            type="primary"
            @click="openingsError ? loadOpenings() : loadMembers()"
          >
            重新加载
          </ElButton>
        </template>
      </ElAlert>
      <FaTableHeader :loading="membersLoading" @refresh="refreshMembers">
        <template #left>
          <ElTag v-if="selectedOpening" type="info" effect="plain">
            目标租户：{{ selectedOpening.target_tenant_code }}
          </ElTag>
        </template>
      </FaTableHeader>
      <ElEmpty v-if="!selectedOpeningId && !openingsLoading" description="请先选择一个已开通应用" />
      <FaTable
        v-else
        :loading="membersLoading"
        :data="members"
        :columns="columns"
        empty-text="当前租户暂无可授权成员"
      />
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import ControlAPI, {
  type AvailableTenantApplication,
  type GrantMember,
} from "@/api/module_control";
import { useAuth } from "@/hooks/core/useAuth";
import { confirmAction } from "@/hooks/core/useConfirm";
import type { ColumnOption } from "@/types/component";
import { renderTableOperationCell, type TableOperationAction } from "@utils";
import { ElTag, type TagProps } from "element-plus";
import {
  computed,
  h,
  onActivated,
  onDeactivated,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
} from "vue";

defineOptions({ name: "ControlUserGrant", inheritAttrs: false });

const GRANT_PERMISSION = "module_control:user_grant:update";
const REVOKE_PERMISSION = "module_control:user_grant:delete";
const RETRY_PERMISSION = "module_control:user_grant:retry";
const { hasAuth } = useAuth();
const openings = ref<AvailableTenantApplication[]>([]);
const selectedOpeningId = ref<number>();
const members = ref<GrantMember[]>([]);
const openingsLoading = ref(false);
const membersLoading = ref(false);
const openingsError = ref("");
const membersError = ref("");
const actionError = ref("");
const busyUserIds = reactive(new Set<number>());
const selectedOpening = computed(() =>
  openings.value.find((item) => item.tenant_application_id === selectedOpeningId.value)
);

function memberActions(member: GrantMember): TableOperationAction[] {
  const disabled = busyUserIds.has(member.user_id);
  if (member.sync_status === "failed") {
    const actions: TableOperationAction[] = [];
    if (hasAuth(RETRY_PERMISSION)) {
      actions.push({
        key: "retry",
        label: "重试",
        artType: "edit",
        icon: "ri:refresh-line",
        perm: RETRY_PERMISSION,
        disabled,
        run: () => void retry(member),
      });
    }
    if (member.desired_state === "active" && hasAuth(REVOKE_PERMISSION)) {
      actions.push({
        key: "revoke",
        label: "撤销授权",
        artType: "delete",
        perm: REVOKE_PERMISSION,
        disabled,
        run: () => void revoke(member),
      });
    }
    return actions;
  }
  if (member.desired_state === "active" || member.granted) {
    return hasAuth(REVOKE_PERMISSION)
      ? [
          {
            key: "revoke",
            label: "撤销授权",
            artType: "delete",
            perm: REVOKE_PERMISSION,
            disabled,
            run: () => void revoke(member),
          },
        ]
      : [];
  }
  if (["pending", "processing"].includes(member.sync_status ?? "")) return [];
  return hasAuth(GRANT_PERMISSION)
    ? [
        {
          key: "grant",
          label: "授权",
          artType: "edit",
          icon: "ri:shield-check-line",
          perm: GRANT_PERMISSION,
          disabled,
          run: () => void grant(member),
        },
      ]
    : [];
}

function memberStatus(member: GrantMember): string {
  if (member.sync_status === "failed") return "同步失败";
  if (
    member.desired_state === "inactive" &&
    ["pending", "processing"].includes(member.sync_status ?? "")
  ) {
    return "撤权同步中";
  }
  if (
    member.desired_state === "active" &&
    ["pending", "processing"].includes(member.sync_status ?? "")
  ) {
    return "同步中";
  }
  if (member.launchable) return "访问资格已生效";
  return "未授权";
}

function memberStatusType(member: GrantMember): TagProps["type"] {
  if (member.sync_status === "failed") return "danger";
  if (member.launchable) return "success";
  if (["pending", "processing"].includes(member.sync_status ?? "")) return "warning";
  return "info";
}

const columns: ColumnOption<GrantMember>[] = [
  { type: "globalIndex", label: "序号", width: 64 },
  { prop: "name", label: "姓名", minWidth: 130, showOverflowTooltip: true },
  { prop: "username", label: "用户名", minWidth: 140, showOverflowTooltip: true },
  { prop: "mobile", label: "手机号", minWidth: 140, showOverflowTooltip: true },
  { prop: "email", label: "邮箱", minWidth: 200, showOverflowTooltip: true },
  {
    prop: "sync_status",
    label: "授权状态",
    width: 126,
    formatter: (row) =>
      h(ElTag, { type: memberStatusType(row), effect: "plain" }, () => memberStatus(row)),
  },
  { prop: "error", label: "同步说明", minWidth: 180, showOverflowTooltip: true },
  {
    prop: "operation",
    label: "操作",
    width: 180,
    align: "right",
    formatter: (row) => renderTableOperationCell(memberActions(row)),
  },
];

async function loadOpenings() {
  openingsLoading.value = true;
  openingsError.value = "";
  try {
    const response = await ControlAPI.listAvailableTenantApplications();
    openings.value = response.data.data ?? [];
    const stillAvailable = openings.value.some(
      (item) => item.tenant_application_id === selectedOpeningId.value
    );
    if (!stillAvailable) {
      selectedOpeningId.value = openings.value[0]?.tenant_application_id;
    }
    await loadMembers();
  } catch {
    openingsError.value = "已开通产品加载失败，请重试。";
  } finally {
    openingsLoading.value = false;
  }
}

let syncRefreshTimer: ReturnType<typeof setTimeout> | undefined;
let pageActive = true;
function stopSyncRefresh() {
  if (syncRefreshTimer) clearTimeout(syncRefreshTimer);
  syncRefreshTimer = undefined;
}
function scheduleSyncRefresh() {
  stopSyncRefresh();
  if (
    pageActive &&
    members.value.some((member) => ["pending", "processing"].includes(member.sync_status ?? ""))
  ) {
    syncRefreshTimer = setTimeout(() => void loadMembers(), 3000);
  }
}
onActivated(() => {
  pageActive = true;
  scheduleSyncRefresh();
});
onDeactivated(() => {
  pageActive = false;
  stopSyncRefresh();
});
onBeforeUnmount(() => {
  pageActive = false;
  stopSyncRefresh();
});

async function loadMembers() {
  stopSyncRefresh();
  if (!selectedOpeningId.value) {
    members.value = [];
    return;
  }
  membersLoading.value = true;
  membersError.value = "";
  try {
    const response = await ControlAPI.listGrantMembers(selectedOpeningId.value);
    members.value = response.data.data ?? [];
  } catch {
    membersError.value = "成员授权状态加载失败，请重试。";
  } finally {
    membersLoading.value = false;
    scheduleSyncRefresh();
  }
}

async function refreshMembers() {
  if (selectedOpeningId.value) await loadMembers();
  else await loadOpenings();
}

async function grant(member: GrantMember) {
  if (!selectedOpeningId.value || busyUserIds.has(member.user_id)) return;
  busyUserIds.add(member.user_id);
  actionError.value = "";
  try {
    await ControlAPI.grantUser(selectedOpeningId.value, member.user_id);
    await loadMembers();
  } catch {
    actionError.value = `「${member.name || member.username}」授权提交失败，请稍后重试。`;
  } finally {
    busyUserIds.delete(member.user_id);
  }
}

async function revoke(member: GrantMember) {
  if (!selectedOpeningId.value || busyUserIds.has(member.user_id)) return;
  busyUserIds.add(member.user_id);
  try {
    await confirmAction(
      `确认撤销「${member.name || member.username}」的应用访问权限吗？`,
      "撤销授权"
    );
  } catch {
    busyUserIds.delete(member.user_id);
    return;
  }
  actionError.value = "";
  try {
    await ControlAPI.revokeUser(selectedOpeningId.value, member.user_id);
    await loadMembers();
  } catch {
    actionError.value = `「${member.name || member.username}」撤权提交失败，请稍后重试。`;
  } finally {
    busyUserIds.delete(member.user_id);
  }
}

async function retry(member: GrantMember) {
  if (
    !selectedOpeningId.value ||
    member.sync_status !== "failed" ||
    busyUserIds.has(member.user_id)
  )
    return;
  busyUserIds.add(member.user_id);
  actionError.value = "";
  try {
    await ControlAPI.retryUserGrant(selectedOpeningId.value, member.user_id);
    await loadMembers();
  } catch {
    actionError.value = `「${member.name || member.username}」重试提交失败，请稍后重试。`;
  } finally {
    busyUserIds.delete(member.user_id);
  }
}

onMounted(() => void loadOpenings());
defineExpose({
  grant,
  revoke,
  retry,
  memberActions,
  busyUserIds,
  actionError,
  loadOpenings,
  loadMembers,
});
</script>

<style scoped lang="scss">
.grant-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.grant-selector {
  :deep(.el-card__body) {
    display: flex;
    gap: 28px;
    align-items: center;
    justify-content: space-between;
  }
}

.grant-selector__copy {
  min-width: 0;

  h2 {
    margin: 4px 0 6px;
    font-size: 20px;
    color: var(--el-text-color-primary);
  }

  p {
    margin: 0;
    color: var(--el-text-color-secondary);
  }
}

.opening-select {
  width: min(420px, 45vw);
}

@media (width <= 720px) {
  .grant-selector :deep(.el-card__body) {
    flex-direction: column;
    align-items: stretch;
  }

  .opening-select {
    width: 100%;
  }
}
</style>
