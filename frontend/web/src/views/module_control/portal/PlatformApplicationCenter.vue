<template>
  <section class="platform-applications" aria-label="平台应用管理">
    <ElTabs v-model="activeTab">
      <ElTabPane label="全部应用" name="applications" />
      <ElTabPane label="租户开通" name="openings" />
      <ElTabPane label="开通进度" name="provisions" />
    </ElTabs>
    <div class="management-content">
      <ApplicationManagement v-if="activeTab === 'applications'" />
      <TenantApplications v-else :key="activeTab" :ledger="activeTab === 'provisions'" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from "vue";
import ApplicationManagement from "../application/index.vue";
import TenantApplications from "../tenant-application/index.vue";

defineOptions({ name: "PlatformApplicationCenter" });
const activeTab = ref("applications");
</script>

<style scoped lang="scss">
.platform-applications {
  margin-top: 20px;
}

.management-content {
  min-height: 520px;
  height: calc(100vh - 350px);
}
</style>
