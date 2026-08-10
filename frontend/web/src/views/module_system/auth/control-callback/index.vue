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
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { Loading } from "@element-plus/icons-vue";
import AuthAPI from "@/api/module_system/auth";
import { useUserStore } from "@stores";

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
const exchangeStarted = ref(false);

onMounted(async () => {
  if (exchangeStarted.value) return;
  exchangeStarted.value = true;

  const rawCode = route.query.code;
  const code = typeof rawCode === "string" ? rawCode.trim() : "";
  if (!code) {
    ElMessage.error("统一登录启动码无效");
    await router.replace({ name: "Login" });
    return;
  }

  try {
    const response = await AuthAPI.controlExchange(code);
    const tokens = response.data.data;
    await userStore.establishSession(tokens);

    if (userStore.routeList.length === 0 && userStore.prems.length === 0) {
      await router.replace({ name: "ControlSsoWaiting" });
      return;
    }
    await router.replace("/");
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
