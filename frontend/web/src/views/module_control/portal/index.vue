<template>
  <div class="portal-page">
    <header class="portal-heading">
      <div>
        <h1>应用中心</h1>
        <p>{{ headingDescription }}</p>
      </div>
      <ElButton
        v-if="portalMode !== 'platform'"
        :loading="loading"
        icon="Refresh"
        plain
        @click="loadApplications"
        >刷新</ElButton
      >
    </header>

    <ElAlert
      v-if="portalMode !== 'tenant'"
      :title="contextAlertTitle"
      :description="contextAlertDescription"
      :type="portalMode === 'managed' ? 'warning' : 'info'"
      show-icon
      :closable="false"
      class="portal-alert portal-context-alert"
    />

    <PlatformApplicationCenter v-if="portalMode === 'platform'" />

    <template v-else>
      <ElAlert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
        class="portal-alert"
      />

      <div v-loading="loading" class="application-grid" aria-live="polite">
        <button
          v-for="app in applications"
          :key="app.code"
          v-auth="'module_control:portal:launch'"
          type="button"
          class="application-card"
          :disabled="!app.launchable || launchingCode === app.code"
          @click="launch(app)"
        >
          <span class="application-card__topline">
            <ElAvatar :size="42" shape="square" :src="app.icon || undefined">
              {{ app.name.slice(0, 1) }}
            </ElAvatar>
            <ElTag :type="applicationTagType(app)" effect="plain" size="small">
              {{ applicationTag(app) }}
            </ElTag>
          </span>
          <span class="application-card__body">
            <strong>{{ app.name }}</strong>
            <span>{{ app.description || "该应用暂未填写说明。" }}</span>
          </span>
          <span class="application-card__action">
            <span>{{ applicationAction(app) }}</span>
            <FaSvgIcon icon="ri:arrow-right-line" />
          </span>
        </button>
      </div>

      <ElEmpty
        v-if="!loading && !errorMessage && applications.length === 0"
        :description="emptyDescription"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import PlatformApplicationCenter from "./PlatformApplicationCenter.vue";
import ControlAPI, { type PortalApplication } from "@/api/module_control";
import { useUserStore } from "@/store/modules/user.store";
import { computed, onMounted, ref } from "vue";

defineOptions({ name: "ControlPortal", inheritAttrs: false });

const applications = ref<PortalApplication[]>([]);
const loading = ref(false);
const launchingCode = ref("");
const errorMessage = ref("");
const userStore = useUserStore();

type PortalMode = "platform" | "managed" | "tenant";
const PLATFORM_TENANT_ID = 1;

const activeTenant = computed(() => userStore.currentTenant);
const activeTenantName = computed(() => activeTenant.value?.name || "当前租户");
const sessionTenantId = computed(() => {
  const value = userStore.info?.session_tenant_id ?? activeTenant.value?.id;
  const numericValue = Number(value);
  return Number.isFinite(numericValue) && numericValue > 0 ? numericValue : null;
});
const portalMode = computed<PortalMode>(() => {
  if (!userStore.info?.is_superuser) return "tenant";
  return sessionTenantId.value === PLATFORM_TENANT_ID ? "platform" : "managed";
});
const headingDescription = computed(() => {
  if (portalMode.value === "managed") {
    return `当前显示「${activeTenantName.value}」已开通的应用，进入后将以平台代管身份操作。`;
  }
  if (portalMode.value === "platform") {
    return "查看和配置全部已登记应用，管理租户开通及同步进度。";
  }
  return "这里只显示当前租户已开通且已向你授权的应用。";
});
const contextAlertTitle = computed(() =>
  portalMode.value === "managed" ? `平台代管模式 · ${activeTenantName.value}` : "平台管理视图"
);
const contextAlertDescription = computed(() =>
  portalMode.value === "managed"
    ? `你正在代管「${activeTenantName.value}」。这不会改变该租户用户的个人应用授权。`
    : "管理权限不等于业务访问身份。进入产品时，请先选择目标业务租户。"
);
function applicationTag(app: PortalApplication) {
  if (app.sync_status === "failed") return "同步失败";
  if (app.desired_state === "inactive" && app.sync_status !== "succeeded") return "撤权同步中";
  if (app.sync_status === "pending" || app.sync_status === "processing") return "同步中";
  if (app.launchable) return portalMode.value === "managed" ? "租户已开通" : "访问资格已生效";
  return "未授权";
}

function applicationTagType(app: PortalApplication) {
  if (app.sync_status === "failed") return "danger" as const;
  if (app.launchable) return "success" as const;
  if (app.sync_status === "pending" || app.sync_status === "processing") return "warning" as const;
  return "info" as const;
}

function applicationAction(app: PortalApplication) {
  if (launchingCode.value === app.code) return "正在进入…";
  return app.launchable ? "进入应用" : applicationTag(app);
}
const emptyDescription = computed(() => {
  if (portalMode.value === "managed") return `该租户「${activeTenantName.value}」尚未开通可用应用`;
  if (portalMode.value === "platform") return "当前为平台租户，请先切换到业务租户";
  return "当前没有可访问的应用，请联系租户管理员开通并授权";
});

async function loadApplications() {
  if (portalMode.value === "platform") return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const response = await ControlAPI.listMyApplications();
    applications.value = response.data.data ?? [];
  } catch {
    errorMessage.value = "应用目录加载失败，请稍后重试。";
  } finally {
    loading.value = false;
  }
}

async function launch(
  app: PortalApplication,
  navigate: (url: string) => void = (url) => window.location.assign(url)
) {
  if (launchingCode.value || !app.launchable) return;
  launchingCode.value = app.code;
  try {
    const response = await ControlAPI.launchApplication(app.code);
    const data = response.data.data;
    if (!data?.redirect_url) throw new Error("中控未返回应用跳转地址");
    navigate(data.redirect_url);
  } catch {
    errorMessage.value = `暂时无法进入「${app.name}」，请稍后重试。`;
    launchingCode.value = "";
  }
}

onMounted(() => void loadApplications());

defineExpose({ launch });
</script>

<style scoped lang="scss">
.portal-page {
  min-height: 100%;
  padding: 4px;
}

.portal-heading {
  display: flex;
  gap: 24px;
  align-items: flex-end;
  justify-content: space-between;
  padding: 8px 2px 24px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  h1 {
    margin: 5px 0 7px;
    font-size: 28px;
    line-height: 1.2;
    color: var(--el-text-color-primary);
  }

  p {
    margin: 0;
    color: var(--el-text-color-secondary);
  }
}

.portal-alert {
  margin-top: 18px;
}

.application-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  min-height: 120px;
  padding-top: 20px;
}

.application-card {
  display: flex;
  flex-direction: column;
  min-height: 220px;
  padding: 20px;
  overflow: hidden;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--el-border-radius-base);
  box-shadow: var(--el-box-shadow-light);
  transition:
    border-color 0.18s ease,
    transform 0.18s ease,
    box-shadow 0.18s ease;

  &:hover:not(:disabled) {
    border-color: var(--el-color-primary-light-5);
    box-shadow: var(--el-box-shadow);
    transform: translateY(-2px);
  }

  &:focus-visible {
    outline: 2px solid var(--el-color-primary);
    outline-offset: 2px;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.72;
  }
}

.application-card__topline,
.application-card__action {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.application-card__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  margin-top: 22px;

  strong {
    font-size: 18px;
    color: var(--el-text-color-primary);
  }

  > span {
    display: -webkit-box;
    margin-top: 10px;
    overflow: hidden;
    -webkit-line-clamp: 2;
    line-height: 1.6;
    color: var(--el-text-color-secondary);
    -webkit-box-orient: vertical;
  }
}

.application-card__action {
  padding-top: 16px;
  font-weight: 600;
  color: var(--el-color-primary);
  border-top: 1px solid var(--el-border-color-extra-light);
}

@media (width <= 640px) {
  .portal-heading {
    flex-direction: column;
    align-items: flex-start;
  }

  .application-grid {
    grid-template-columns: 1fr;
  }
}
</style>
