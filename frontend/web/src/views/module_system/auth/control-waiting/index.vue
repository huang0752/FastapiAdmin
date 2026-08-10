<template>
  <main class="control-auth-page">
    <el-result icon="info" title="账号已创建，等待管理员授权">
      <template #sub-title>
        <p>统一登录已经完成，但当前账号尚未分配角色或菜单权限。</p>
        <p>请联系本系统管理员完成授权后再刷新页面。</p>
      </template>
      <template #extra>
        <el-button type="primary" @click="retry">重新检查</el-button>
      </template>
    </el-result>
  </main>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";

const router = useRouter();
const retry = () => router.replace("/");
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
