<template>
  <main class="control-auth-page">
    <el-card class="control-auth-card" shadow="never">
      <template v-if="status === 'processing'">
        <el-icon class="is-loading" :size="36"><Loading /></el-icon>
        <h1>正在完成统一登录</h1>
        <p>请稍候，系统正在验证身份并建立本地会话。</p>
      </template>
      <template v-else>
        <h1>统一登录失败</h1>
        <p>{{ failureMessage }}</p>
        <div class="actions">
          <el-button type="primary" @click="startSession">重新尝试</el-button>
          <el-button @click="leaveFailedCallback">
            {{ sourceReturnUrl ? "返回来源页" : "返回登录" }}
          </el-button>
        </div>
      </template>
    </el-card>
  </main>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Loading } from "@element-plus/icons-vue";
import type { JWTOut } from "@/api/module_system/auth";
import { useUserStore } from "@stores";
import { consumeControlCodeExchange, exchangeControlCodeOnce } from "./control-exchange";
import { resolveSourceReturnUrl } from "./control-return";
import { persistControlExchange, resumeControlExchange } from "./control-session";

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
const status = ref<"processing" | "failed">("processing");
const failureMessage = ref("");
const callbackTimeoutMs = 15_000;
const sourceReturnUrl = resolveSourceReturnUrl(document.referrer, window.location.origin);
let activeAttempt: Promise<void> | null = null;
let activeGeneration = 0;

async function completeSession(tokens: JWTOut, generation: number) {
  await userStore.establishSession(tokens, undefined, [], {
    isCurrent: () => generation === activeGeneration,
    restoreSavedTenant: false,
  });
  if (generation !== activeGeneration) return;

  if (userStore.routeList.length === 0) {
    await router.replace({ name: "ControlSsoWaiting" });
    return;
  }
  await router.replace("/");
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("统一登录处理超时")), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

async function establishControlSession(code: string, generation: number) {
  let tokens = resumeControlExchange(code);
  if (!tokens) {
    tokens = (await exchangeControlCodeOnce(code)).data.data;
    if (generation !== activeGeneration) return;
    // 先落盘兑换结果，再请求用户/菜单；开发服务器意外重载时可直接恢复。
    persistControlExchange(code, tokens);
  }
  if (generation !== activeGeneration) return;
  await completeSession(tokens, generation);
  if (generation !== activeGeneration) return;
  consumeControlCodeExchange(code);
}

function startSession(): Promise<void> {
  if (activeAttempt) return activeAttempt;
  status.value = "processing";
  failureMessage.value = "";
  const rawCode = route.query.code;
  const code = typeof rawCode === "string" ? rawCode.trim() : "";
  const generation = ++activeGeneration;

  const sessionPromise = code
    ? establishControlSession(code, generation)
    : Promise.reject(new Error("统一登录启动码无效"));
  const attempt = withTimeout(sessionPromise, callbackTimeoutMs)
    .catch((error) => {
      console.error("[ControlSSO] 统一登录失败", error);
      if (generation === activeGeneration) activeGeneration += 1;
      status.value = "failed";
      failureMessage.value =
        error instanceof Error && error.message.includes("超时")
          ? `处理超过 15 秒，请重新尝试或${sourceReturnUrl ? "返回来源页" : "返回登录"}。`
          : `会话建立失败，请重新尝试或${sourceReturnUrl ? "返回来源页" : "返回登录"}。`;
    })
    .finally(() => {
      if (activeAttempt === attempt) activeAttempt = null;
    });
  activeAttempt = attempt;
  return attempt;
}

function leaveFailedCallback() {
  if (sourceReturnUrl) {
    window.location.assign(sourceReturnUrl);
    return;
  }
  void router.replace({ name: "Login" });
}

onMounted(startSession);
onBeforeUnmount(() => {
  activeGeneration += 1;
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

.actions {
  display: flex;
  justify-content: center;
  margin-top: 24px;
}
</style>
