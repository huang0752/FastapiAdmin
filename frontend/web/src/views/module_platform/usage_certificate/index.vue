<template>
  <div class="fa-full-height certificate-registry">
    <ElCard shadow="hover">
      <ElForm inline @submit.prevent>
        <ElFormItem label="关键词"><ElInput v-model="keyword" clearable placeholder="租户名称 / 编码 / 证明编号" @keyup.enter="load" /></ElFormItem>
        <ElFormItem label="当前状态"><ElSelect v-model="validity" clearable style="width: 140px"><ElOption label="当前有效" :value="true" /><ElOption label="当前无效" :value="false" /></ElSelect></ElFormItem>
        <ElButton type="primary" :loading="loading" @click="load">查询</ElButton>
      </ElForm>
      <ElTable :data="rows" v-loading="loading">
        <ElTableColumn prop="enterprise_name" label="租户" min-width="180" />
        <ElTableColumn prop="tenant_code" label="租户编码" width="140" />
        <ElTableColumn prop="certificate_no" label="证明编号" min-width="220" />
        <ElTableColumn label="软件" min-width="180"><template #default="{ row }">{{ row.system_name }} · {{ row.system_version }}</template></ElTableColumn>
        <ElTableColumn label="授权期限" min-width="260"><template #default="{ row }">{{ row.start_time }} — {{ row.end_time }}</template></ElTableColumn>
        <ElTableColumn label="状态" width="110"><template #default="{ row }"><ElTag :type="row.currently_valid ? 'success' : 'danger'">{{ row.status_label }}</ElTag></template></ElTableColumn>
        <ElTableColumn label="操作" width="190" fixed="right"><template #default="{ row }"><ElButton link type="primary" @click="preview(row)">预览</ElButton><ElButton link type="primary" @click="download(row)">下载 PDF</ElButton></template></ElTableColumn>
      </ElTable>
      <ElPagination v-model:current-page="pageNo" v-model:page-size="pageSize" :total="total" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="load" />
    </ElCard>
    <ElDialog v-model="previewVisible" title="软件使用证明" width="900px"><iframe v-if="previewHtml" class="preview-frame" sandbox="allow-same-origin" :srcdoc="previewHtml" title="软件使用证明预览" /></ElDialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import UsageCertificateAPI, { saveCertificateBlob, type UsageCertificateItem } from "@/api/module_platform/usage_certificate";

defineOptions({ name: "UsageCertificateRegistry" });
const rows = ref<UsageCertificateItem[]>([]);
const loading = ref(false);
const keyword = ref("");
const validity = ref<boolean | undefined>();
const pageNo = ref(1);
const pageSize = ref(20);
const total = ref(0);
const previewVisible = ref(false);
const previewHtml = ref("");

async function load() {
  loading.value = true;
  try {
    const { data: res } = await UsageCertificateAPI.platformList({ page_no: pageNo.value, page_size: pageSize.value, keyword: keyword.value || undefined, currently_valid: validity.value });
    rows.value = res?.data?.items || [];
    total.value = res?.data?.total || 0;
  } finally { loading.value = false; }
}
async function preview(row: UsageCertificateItem) {
  const { data: res } = await UsageCertificateAPI.platformPreview(row.tenant_id);
  previewHtml.value = res?.data?.html || "";
  previewVisible.value = true;
}
async function download(row: UsageCertificateItem) {
  const response = await UsageCertificateAPI.platformDownload(row.tenant_id);
  saveCertificateBlob(response.data, `企业软件使用证明-${row.enterprise_name}.pdf`);
}
onMounted(load);
</script>

<style scoped>.certificate-registry { padding: 16px; }.preview-frame { width: 100%; height: 72vh; border: 0; background: #eef2f5; }.el-pagination { margin-top: 16px; justify-content: flex-end; }</style>
