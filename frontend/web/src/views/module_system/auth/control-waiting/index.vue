<template>
  <main class="control-auth-page">
    <el-result icon="info" title="账号已创建，目标系统尚未授予有效菜单权限">
      <template #sub-title>
        <p>统一登录已经完成，请联系本系统管理员分配本地角色和菜单权限。</p>
        <p>管理员操作路径：系统管理 → 用户管理 → 筛选待授权 → 去授权</p>
        <p>当前用户：{{ displayName }}（账号：{{ account }}）</p>
      </template>
      <template #extra>
        <el-button type="primary" @click="retry">重新检查</el-button>
      </template>
    </el-result>
  </main>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useUserStore } from "@stores";
import { reloadAuthorization } from "./retry";

const router = useRouter();
const userStore = useUserStore();
const displayName = computed(
  () => userStore.basicInfo.name || userStore.basicInfo.username || "当前账号"
);
const account = computed(() => userStore.basicInfo.username || "—");
const retry = () => reloadAuthorization(router.resolve({ path: "/" }).href);
</script>

<style scoped>
.control-auth-page {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  background: var(--el-bg-color-page);
}
</style>
