<template>
  <main class="personal-workspace">
    <header class="workspace-header">
      <div class="brand">
        <FaLogo :size="32" /><span>{{ AppConfig.systemInfo.name }}</span>
      </div>
      <ElButton @click="userStore.logout()">退出登录</ElButton>
    </header>
    <section class="workspace-content">
      <h1>个人工作区</h1>
      <p class="intro">{{ displayName }}，欢迎回来。你已登录，可以使用以下基础功能。</p>
      <div class="workspace-grid">
        <ElCard shadow="never">
          <template #header>我的资料</template>
          <div class="profile-row">
            <span>姓名</span><span>{{ displayName }}</span>
          </div>
          <div class="profile-row">
            <span>账号</span><span>{{ account }}</span>
          </div>
          <div class="profile-row">
            <span>所属租户</span><span>{{ userStore.currentTenant?.name || "—" }}</span>
          </div>
          <div class="profile-row">
            <span>登录方式</span><span>{{ isFederated ? "统一登录" : "本地账号" }}</span>
          </div>
          <p v-if="isFederated" class="footnote">统一账号资料由中控维护，本产品不会另建密码。</p>
          <ElForm v-else class="name-form" @submit.prevent="saveName">
            <ElFormItem label="姓名"
              ><ElInput v-model="nameDraft" maxlength="50" placeholder="输入你的姓名"
            /></ElFormItem>
            <ElButton
              type="primary"
              :loading="saving"
              :disabled="!nameDraft.trim()"
              @click="saveName"
              >保存我的资料</ElButton
            >
          </ElForm>
        </ElCard>
        <ElCard shadow="never">
          <template #header>可用功能</template>
          <ElTag type="success">基础访问已开通</ElTag>
          <div class="service">
            <strong>个人资料</strong>
            <p>查看自己的账号信息，安全退出当前会话。</p>
          </div>
          <div class="service">
            <strong>业务功能</strong>
            <p>业务功能按岗位开放，开放后即可进入。当前基础工作区可以正常使用。</p>
          </div>
          <ElButton type="primary" :loading="loading" :disabled="loading" @click="retry"
            >刷新可用功能</ElButton
          >
          <p v-if="feedback" class="feedback" :class="{ error: hasError }">{{ feedback }}</p>
        </ElCard>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import UserAPI from "@/api/module_system/user";
import AppConfig from "@/config";
import { useRouter } from "vue-router";
import { useUserStore } from "@stores";
import { rebuildDynamicRoutesFromCurrentUser, resetDynamicRoutesSync } from "@/router/beforeEach";
import { createAuthorizationReloader } from "./retry";

const router = useRouter();
const userStore = useUserStore();
const loading = ref(false);
const feedback = ref("");
const saving = ref(false);
const nameDraft = ref("");
const isFederated = computed(() => userStore.basicInfo.auth_source === "federated");
watch(
  () => userStore.basicInfo.name,
  (name) => {
    nameDraft.value = name || "";
  },
  { immediate: true }
);
onMounted(async () => {
  try {
    await userStore.getUserInfo();
  } catch {
    feedback.value = "资料暂时未能刷新，请稍后重试。";
  }
});
async function saveName() {
  if (isFederated.value || !nameDraft.value.trim() || saving.value) return;
  saving.value = true;
  try {
    await UserAPI.updateCurrentUserInfo({ name: nameDraft.value.trim() });
    await userStore.getUserInfo();
  } finally {
    saving.value = false;
  }
}
const hasError = ref(false);
const displayName = computed(
  () => userStore.basicInfo.name || userStore.basicInfo.username || "当前账号"
);
const account = computed(() => userStore.basicInfo.username || "—");

const recheck = createAuthorizationReloader({
  userStore: {
    getUserInfo: () => userStore.getUserInfo(),
    get routeList() {
      return userStore.routeList;
    },
    clearAuthorizationSnapshot: () => userStore.clearAuthorizationSnapshot(),
  },
  routerUtils: { resetDynamicRoutesSync, rebuildDynamicRoutesFromCurrentUser },
  router,
});

async function retry() {
  if (loading.value) return;
  loading.value = true;
  feedback.value = "";
  hasError.value = false;
  try {
    const result = await recheck();
    if (result.status === "pending") {
      feedback.value = "当前可使用个人工作区；有新的业务功能开放时，会在这里更新。";
    }
  } catch (error) {
    console.error("[ControlSSO] 重新检查授权失败", error);
    feedback.value = "可用功能暂时未能刷新，请稍后重试。";
    hasError.value = true;
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.personal-workspace {
  min-height: 100vh;
  background: var(--el-bg-color-page);
  padding: 28px;
  color: var(--el-text-color-primary);
}
.workspace-header,
.workspace-content {
  max-width: 960px;
  margin: 0 auto;
}
.workspace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 36px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-weight: 600;
}
h1 {
  font-size: 28px;
  margin: 0 0 12px;
}
.intro {
  color: var(--el-text-color-secondary);
  margin-bottom: 28px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
.profile-row {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  overflow-wrap: anywhere;
}
.profile-row span:first-child {
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
}
.service {
  margin: 18px 0;
}
.service p,
.footnote {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
.feedback {
  margin-top: 20px;
}
.feedback.error {
  color: var(--el-color-danger);
}
.name-form {
  margin-top: 20px;
}
@media (max-width: 640px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
  .personal-workspace {
    padding: 20px;
  }
}
</style>
