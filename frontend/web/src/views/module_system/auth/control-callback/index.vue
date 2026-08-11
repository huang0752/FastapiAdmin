<template>
  <main class="control-auth-page">
    <el-card class="control-auth-card" shadow="never">
      <el-icon class="is-loading" :size="36"><Loading /></el-icon>
      <h1>正在完成统一登录</h1>
      <p>请稍候，系统正在验证身份并建立本地会话。</p>
    </el-card>
  </main>
</template>

<script setup lang="ts">
import { onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { Loading } from "@element-plus/icons-vue";
import type { JWTOut } from "@/api/module_system/auth";
import { useUserStore } from "@stores";
import { Auth } from "@utils";
import { exchangeControlCodeOnce } from "./control-exchange";

const CONTROL_SSO_EXCHANGED_CODE_KEY = "control_sso_exchanged_code";
const route = useRoute();
const router = useRouter();
const userStore = useUserStore();

async function completeSession(tokens: JWTOut) {
  await userStore.establishSession(tokens);

  if (userStore.routeList.length === 0 && userStore.prems.length === 0) {
    await router.replace({ name: "ControlSsoWaiting" });
    return;
  }
  await router.replace("/");
}

onMounted(async () => {
  const rawCode = route.query.code;
  const code = typeof rawCode === "string" ? rawCode.trim() : "";
  if (!code) {
    ElMessage.error("统一登录启动码无效");
    await router.replace({ name: "Login" });
    return;
  }

  try {
    const storedAccessToken = Auth.getAccessToken();
    const canResume =
      sessionStorage.getItem(CONTROL_SSO_EXCHANGED_CODE_KEY) === code && !!storedAccessToken;
    const tokens: JWTOut = canResume
      ? {
          access_token: storedAccessToken,
          refresh_token: Auth.getRefreshToken(),
          token_type: "bearer",
          expires_in: 0,
        }
      : (await exchangeControlCodeOnce(code)).data.data;

    if (!canResume) {
      sessionStorage.setItem(CONTROL_SSO_EXCHANGED_CODE_KEY, code);
    }
    await completeSession(tokens);
  } catch (error) {
    console.error("[ControlSSO] 统一登录失败", error);
    ElMessage.error("统一登录失败，请返回中控台重试");
    await router.replace({ name: "Login" });
  }
});
</script>

<style scoped>
.control-auth-page {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  background: var(--el-bg-color-page);
}

.control-auth-card {
  width: min(460px, 100%);
  text-align: center;
}

h1 {
  margin: 20px 0 8px;
  font-size: 22px;
}

p {
  margin: 0;
  color: var(--el-text-color-secondary);
}
</style>
