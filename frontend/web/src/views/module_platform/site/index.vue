<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="searchItems"
      :is-expand="false"
      :show-expand="true"
      :show-reset="true"
      :show-search="true"
      :disabled-search="false"
      :default-expanded="false"
      @search="handleSearch"
      @reset="handleReset"
    />

    <ElCard
      shadow="hover"
      class="fa-table-card"
      :style="{ 'margin-top': showSearchBar ? '12px' : '0' }"
    >
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <FaTableHeaderLeft
            :remove-ids="selectedIds"
            :perm-create="['module_platform:site:create']"
            :perm-delete="['module_platform:site:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="openCreate"
            @delete="handleBatchDelete"
          />
        </template>
      </FaTableHeader>

      <FaTable
        ref="tableRef"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @selection-change="onTableSelectionChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <FaDialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="860px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :confirm-loading="submitLoading"
      @cancel="closeDialog"
      @confirm="dialogMode === 'detail' ? closeDialog() : submitForm()"
    >
      <ElForm
        ref="formRef"
        :model="formData"
        :rules="formRules"
        label-width="110px"
        label-position="right"
        :disabled="dialogMode === 'detail'"
      >
        <ElTabs v-model="activeTab">
          <ElTabPane label="基础信息" name="basic">
            <ElRow :gutter="20">
              <ElCol :span="12">
                <ElFormItem label="站点名称" prop="name">
                  <ElInput v-model="formData.name" maxlength="100" placeholder="请输入站点名称" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="站点编码" prop="code">
                  <ElInput
                    v-model="formData.code"
                    maxlength="64"
                    placeholder="字母、数字或下划线"
                  />
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="状态" prop="status">
                  <ElRadioGroup v-model="formData.status">
                    <ElRadio :value="0">启用</ElRadio>
                    <ElRadio :value="1">停用</ElRadio>
                  </ElRadioGroup>
                </ElFormItem>
              </ElCol>
            </ElRow>
          </ElTabPane>

          <ElTabPane label="域名配置" name="domains">
            <ElAlert
              title="请求 Host 会匹配下列域名；每个站点必须且只能设置一个主域名。"
              type="info"
              :closable="false"
              class="mb-4"
            />
            <div v-for="(domain, index) in formData.domains" :key="index" class="domain-row">
              <ElFormItem
                :label="`域名 ${index + 1}`"
                :prop="`domains.${index}.host`"
                :rules="[{ required: true, message: '请输入域名', trigger: 'blur' }]"
                class="domain-host"
              >
                <ElInput v-model="domain.host" placeholder="例如 carbon.example.com" />
              </ElFormItem>
              <ElRadio
                :model-value="domain.is_primary"
                :value="true"
                @change="setPrimaryDomain(index)"
              >
                主域名
              </ElRadio>
              <ElButton
                v-if="dialogMode !== 'detail'"
                type="danger"
                link
                :disabled="formData.domains.length === 1"
                @click="removeDomain(index)"
              >
                删除
              </ElButton>
            </div>
            <ElButton v-if="dialogMode !== 'detail'" type="primary" plain @click="addDomain">
              添加域名
            </ElButton>
          </ElTabPane>

          <ElTabPane label="品牌配置" name="brand">
            <ElRow :gutter="20">
              <ElCol :span="12">
                <ElFormItem label="站点 Logo" prop="logo_url">
                  <ElInput v-model="formData.logo_url" placeholder="Logo URL" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="网站图标" prop="favicon">
                  <ElInput v-model="formData.favicon" placeholder="Favicon URL" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="24">
                <ElFormItem label="登录背景" prop="login_bg">
                  <ElInput v-model="formData.login_bg" placeholder="登录背景图片 URL" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="版权信息" prop="copyright">
                  <ElInput v-model="formData.copyright" maxlength="255" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="备案号" prop="keep_record">
                  <ElInput v-model="formData.keep_record" maxlength="100" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="24">
                <ElFormItem label="帮助文档" prop="help_doc">
                  <ElInput v-model="formData.help_doc" placeholder="帮助文档 URL" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="隐私政策" prop="privacy">
                  <ElInput v-model="formData.privacy" placeholder="隐私政策 URL" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="服务条款" prop="clause">
                  <ElInput v-model="formData.clause" placeholder="服务条款 URL" />
                </ElFormItem>
              </ElCol>
            </ElRow>
          </ElTabPane>
        </ElTabs>
      </ElForm>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import type { FormInstance, FormRules } from "element-plus";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import SiteAPI, {
  type SiteCreateForm,
  type SiteDomain,
  type SiteTable,
  validateSiteDomains,
} from "@/api/module_platform/site";
import { useAuth } from "@/hooks/core/useAuth";
import { confirmBatchDelete, confirmDelete, confirmToggleStatus } from "@/hooks/core/useConfirm";
import { useTable } from "@/hooks/core/useTable";
import { useTableSelection } from "@/hooks/core/useTableSelection";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";

defineOptions({ name: "Site", inheritAttrs: false });

type DialogMode = "create" | "update" | "detail";
type SiteSearchForm = { name?: string; code?: string; status?: number };
type SiteEditorForm = SiteCreateForm & { id?: number };

const { hasAuth } = useAuth();
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchForm = ref<SiteSearchForm>({});
const searchItems = computed<SearchFormItem[]>(() => [
  { label: "站点名称", key: "name", type: "input", placeholder: "请输入站点名称", span: 6 },
  { label: "站点编码", key: "code", type: "input", placeholder: "请输入站点编码", span: 6 },
  {
    label: "状态",
    key: "status",
    type: "select",
    span: 6,
    props: {
      placeholder: "请选择状态",
      clearable: true,
      options: [
        { label: "启用", value: 0 },
        { label: "停用", value: 1 },
      ],
    },
  },
]);

const tableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<SiteTable>();
const createLoading = ref(false);

const operationContext = {
  detail: (id: number) => void openDialog("detail", id),
  update: (id: number) => void openDialog("update", id),
  remove: (id: number) => void deleteRow(id),
  toggle: (row: SiteTable) => void toggleStatus(row),
};

function siteActions(row: SiteTable): TableOperationAction[] {
  const actions: TableOperationAction[] = [
    {
      key: "detail",
      label: "详情",
      artType: "view",
      perm: "module_platform:site:query",
      run: () => operationContext.detail(row.id!),
    },
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      perm: "module_platform:site:update",
      run: () => operationContext.update(row.id!),
    },
    {
      key: "toggle",
      label: row.status === 0 ? "停用" : "启用",
      artType: "more",
      icon: "ri:toggle-line",
      perm: "module_platform:site:update",
      run: () => operationContext.toggle(row),
    },
    {
      key: "delete",
      label: "删除",
      artType: "delete",
      perm: "module_platform:site:delete",
      run: () => operationContext.remove(row.id!),
    },
  ];
  return actions.filter((item) => item.perm && hasAuth(item.perm));
}

const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  getData,
  replaceSearchParams,
  resetSearchParams,
  handleSizeChange,
  handleCurrentChange,
  refreshData,
  refreshCreate,
  refreshUpdate,
  refreshRemove,
} = useTable({
  core: {
    apiFn: SiteAPI.listSites,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: resolveStatusColumns<SiteTable>(() => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "name", label: "站点名称", minWidth: 140, showOverflowTooltip: true },
      { prop: "site_code", label: "站点编码", minWidth: 120, showOverflowTooltip: true },
      {
        prop: "domains",
        label: "域名",
        minWidth: 240,
        showOverflowTooltip: true,
        formatter: (row: SiteTable) =>
          row.domains
            .map((item) => `${item.host}${item.is_primary ? "（主）" : ""}`)
            .join("、"),
      },
      {
        prop: "status",
        label: "状态",
        width: 80,
        status: {
          0: { type: "success", text: "启用" },
          1: { type: "danger", text: "停用" },
        },
      },
      { prop: "updated_time", label: "更新时间", width: 160 },
      {
        prop: "operation",
        label: "操作",
        width: 220,
        fixed: "right",
        align: "right",
        formatter: (row: SiteTable) => renderTableOperationCell(siteActions(row)),
      },
    ]),
  },
});

function initialForm(): SiteEditorForm {
  return {
    code: "",
    name: "",
    domains: [{ host: "", is_primary: true }],
    logo_url: "",
    favicon: "",
    login_bg: "",
    copyright: "",
    keep_record: "",
    help_doc: "",
    privacy: "",
    clause: "",
    status: 0,
  };
}

const dialogVisible = ref(false);
const dialogMode = ref<DialogMode>("create");
const activeTab = ref("basic");
const submitLoading = ref(false);
const formRef = ref<FormInstance>();
const formData = ref<SiteEditorForm>(initialForm());
const dialogTitle = computed(() =>
  dialogMode.value === "create"
    ? "新增站点"
    : dialogMode.value === "update"
      ? "编辑站点"
      : "站点详情"
);
const formRules: FormRules<SiteEditorForm> = {
  name: [{ required: true, message: "请输入站点名称", trigger: "blur" }],
  code: [
    { required: true, message: "请输入站点编码", trigger: "blur" },
    {
      pattern: /^[a-zA-Z0-9_]+$/,
      message: "站点编码仅允许字母、数字和下划线",
      trigger: "blur",
    },
  ],
};

async function openDialog(mode: DialogMode, id?: number) {
  dialogMode.value = mode;
  activeTab.value = "basic";
  formData.value = initialForm();
  dialogVisible.value = true;
  if (id === undefined) return;
  submitLoading.value = true;
  try {
    const response = await SiteAPI.detailSite(id);
    const site = response.data.data;
    if (site) {
      formData.value = {
        id: site.id,
        code: site.site_code,
        name: site.name,
        domains: site.domains.map((item) => ({ ...item })),
        logo_url: site.logo_url ?? "",
        favicon: site.favicon ?? "",
        login_bg: site.login_bg ?? "",
        copyright: site.copyright ?? "",
        keep_record: site.keep_record ?? "",
        help_doc: site.help_doc ?? "",
        privacy: site.privacy ?? "",
        clause: site.clause ?? "",
        status: site.status,
      };
    }
  } finally {
    submitLoading.value = false;
  }
}

async function openCreate() {
  createLoading.value = true;
  try {
    await openDialog("create");
  } finally {
    createLoading.value = false;
  }
}

function closeDialog() {
  dialogVisible.value = false;
  formRef.value?.clearValidate();
  formData.value = initialForm();
}

function addDomain() {
  formData.value.domains.push({ host: "", is_primary: false });
}

function removeDomain(index: number) {
  if (formData.value.domains.length === 1) return;
  const removedPrimary = formData.value.domains[index]?.is_primary;
  formData.value.domains.splice(index, 1);
  if (removedPrimary && formData.value.domains[0]) {
    setPrimaryDomain(0);
  }
}

function setPrimaryDomain(index: number) {
  formData.value.domains.forEach((item, itemIndex) => {
    item.is_primary = itemIndex === index;
  });
}

function payloadFromForm(): SiteCreateForm {
  const { id: _id, ...payload } = formData.value;
  return {
    ...payload,
    code: payload.code.trim().toLowerCase(),
    name: payload.name.trim(),
    domains: payload.domains.map((item: SiteDomain) => ({
      host: item.host.trim(),
      is_primary: item.is_primary,
    })),
  };
}

async function submitForm() {
  if (!(await formRef.value?.validate().catch(() => false))) return;
  const payload = payloadFromForm();
  const domainError = validateSiteDomains(payload.domains);
  if (domainError) {
    activeTab.value = "domains";
    ElMessage.warning(domainError);
    return;
  }
  submitLoading.value = true;
  try {
    if (dialogMode.value === "create") {
      await SiteAPI.createSite(payload);
      await refreshCreate();
    } else if (formData.value.id !== undefined) {
      await SiteAPI.updateSite(formData.value.id, payload);
      await refreshUpdate();
    }
    closeDialog();
  } finally {
    submitLoading.value = false;
  }
}

async function deleteRow(id: number) {
  try {
    await confirmDelete();
    await SiteAPI.deleteSites([id]);
    tableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 取消删除或接口错误由统一处理器处理。
  }
}

async function handleBatchDelete() {
  if (selectedIds.value.length === 0) return;
  try {
    await confirmBatchDelete(selectedIds.value.length);
    batchDeleting.value = true;
    await SiteAPI.deleteSites(selectedIds.value);
    tableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 取消删除或接口错误由统一处理器处理。
  } finally {
    batchDeleting.value = false;
  }
}

async function toggleStatus(row: SiteTable) {
  const status = row.status === 0 ? 1 : 0;
  try {
    await confirmToggleStatus(status);
    await SiteAPI.updateSite(row.id!, { status });
    await refreshData();
  } catch {
    // 取消操作或接口错误由统一处理器处理。
  }
}

async function handleSearch(params: SiteSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({
    name: params.name || undefined,
    code: params.code || undefined,
    status: params.status ?? undefined,
  });
  getData();
}

function handleReset() {
  searchForm.value = {};
  void resetSearchParams();
}
</script>

<style scoped>
.domain-row {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
}

.domain-host {
  flex: 1;
  margin-bottom: 0;
}
</style>
