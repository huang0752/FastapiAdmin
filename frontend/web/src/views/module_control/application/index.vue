<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="searchItems"
      :show-expand="false"
      @search="handleSearch"
      @reset="handleReset"
    />

    <ElCard class="fa-table-card" shadow="hover" :style="tableCardStyle">
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <ElButton
            v-auth="'module_control:application:create'"
            type="primary"
            icon="Plus"
            @click="openCreate"
          >
            注册应用
          </ElButton>
        </template>
      </FaTableHeader>
      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      :form-mode="dialogVisible.type"
      confirm-text="保存"
      width="760px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :confirm-loading="submitLoading"
      @cancel="closeEditor"
      @confirm="submitEditor"
    >
      <FaForm
        ref="formRef"
        v-model="formData"
        :items="formItems"
        :rules="formRules"
        :show-reset="false"
        :show-submit="false"
        :span="12"
        :label-width="116"
        label-suffix=":"
      />
    </FaDialog>

    <ClientSecretDialog ref="secretDialogRef" />
    <ApplicationPackageDialog
      v-if="autoProvisioningEnabled"
      v-model="packageDialogVisible"
      :application="packageApplication"
    />
  </div>
</template>

<script setup lang="ts">
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import ControlAPI, {
  type ApplicationForm,
  type ApplicationListItem,
  type ApplicationPageQuery,
  type ClientSecretResult,
} from "@/api/module_control";
import { useAuth } from "@/hooks/core/useAuth";
import { useAssemblyStore } from "@/store/modules/assembly.store";
import { useCrudDialog } from "@/hooks/core/useCrudDialog";
import { confirmAction, confirmDelete } from "@/hooks/core/useConfirm";
import { useTable } from "@/hooks/core/useTable";
import { renderTableOperationCell, type TableOperationAction } from "@utils";
import { computed, ref } from "vue";
import ClientSecretDialog from "./ClientSecretDialog.vue";
import ApplicationPackageDialog from "./ApplicationPackageDialog.vue";
import { validateEntitlementSyncSettings } from "./access-sync";
import type { FormRules } from "element-plus";

defineOptions({ name: "ControlApplication", inheritAttrs: false });

type EditorForm = ApplicationForm & { id?: number };
type FormExpose = {
  validate: (callback: (valid: boolean) => void) => void;
  clearValidate: () => void;
};
type SecretDialogExpose = {
  show: (result: ClientSecretResult) => void;
};

const { hasAuth } = useAuth();
const assemblyStore = useAssemblyStore();
const autoProvisioningEnabled = computed(() =>
  assemblyStore.isFeatureEnabled("tenantAutoProvisioning", false)
);
const entitlementSyncEnabled = computed(() =>
  assemblyStore.isFeatureEnabled("controlUserEntitlements", false)
);
const { dialogVisible, openDialog, closeDialog } = useCrudDialog();
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchForm = ref<Pick<ApplicationPageQuery, "code" | "name" | "status">>({});
const submitLoading = ref(false);
const formRef = ref<FormExpose | null>(null);
const secretDialogRef = ref<SecretDialogExpose | null>(null);
const packageDialogVisible = ref(false);
const packageApplication = ref<ApplicationListItem | null>(null);
const tableCardStyle = computed(() => ({ marginTop: showSearchBar.value ? "12px" : "0" }));

const statusOptions = [
  { label: "启用", value: 0 },
  { label: "停用", value: 1 },
];
const searchItems = computed<SearchFormItem[]>(() => [
  { label: "应用名称", key: "name", type: "input", placeholder: "请输入应用名称", span: 6 },
  { label: "应用编码", key: "code", type: "input", placeholder: "请输入应用编码", span: 6 },
  {
    label: "状态",
    key: "status",
    type: "select",
    span: 6,
    props: { options: statusOptions, clearable: true, placeholder: "请选择状态" },
  },
]);

const formItems = computed<FormItem[]>(() => [
  { key: "name", label: "应用名称", type: "input", props: { maxlength: 100 } },
  { key: "code", label: "应用编码", type: "input", props: { maxlength: 64 } },
  {
    key: "base_url",
    label: "应用地址",
    type: "input",
    span: 24,
    props: { placeholder: "https://app.example.com" },
  },
  {
    key: "callback_url",
    label: "回调地址",
    type: "input",
    span: 24,
    props: { placeholder: "https://app.example.com/#/auth/control/callback" },
  },
  ...(autoProvisioningEnabled.value
    ? [
        {
          key: "provisioning_url",
          label: "租户开户地址",
          type: "input" as const,
          span: 24,
          props: {
            placeholder: "https://app.example.com/api/v1/system/auth/control/tenant/provision",
          },
        },
        {
          key: "provisioning_enabled",
          label: "自动开户",
          type: "switch" as const,
          props: { activeText: "启用", inactiveText: "关闭" },
        },
        {
          key: "provisioning_timeout_seconds",
          label: "超时秒数",
          type: "number" as const,
          props: { min: 1, max: 60, precision: 0 },
        },
      ]
    : []),
  ...(entitlementSyncEnabled.value
    ? [
        {
          key: "entitlement_sync_url",
          label: "授权同步接口",
          type: "input" as const,
          span: 24,
          props: { placeholder: "https://app.example.com/api/v1/system/auth/control/access/sync" },
        },
        {
          key: "entitlement_sync_enabled",
          label: "授权同步",
          type: "switch" as const,
          props: { activeText: "启用", inactiveText: "关闭" },
        },
        {
          key: "entitlement_sync_timeout_seconds",
          label: "同步超时秒数",
          type: "number" as const,
          props: { min: 1, max: 60, precision: 0 },
        },
      ]
    : []),
  { key: "icon", label: "图标", type: "input", props: { placeholder: "图标名称或 URL" } },
  { key: "sort", label: "排序", type: "number", props: { min: 0, precision: 0 } },
  { key: "status", label: "状态", type: "radiogroup", props: { options: statusOptions } },
  {
    key: "description",
    label: "说明",
    type: "input",
    span: 24,
    props: { type: "textarea", rows: 3 },
  },
]);
const validateSync = (_rule: unknown, _value: unknown, callback: (error?: Error) => void) => {
  const error = entitlementSyncEnabled.value
    ? validateEntitlementSyncSettings(formData.value)
    : null;
  callback(error ? new Error(error) : undefined);
};
const formRules: FormRules = {
  entitlement_sync_url: [{ validator: validateSync, trigger: "blur" }],
  entitlement_sync_timeout_seconds: [{ validator: validateSync, trigger: "change" }],
  name: [{ required: true, message: "请输入应用名称", trigger: "blur" }],
  code: [{ required: true, message: "请输入应用编码", trigger: "blur" }],
  base_url: [{ required: true, message: "请输入应用地址", trigger: "blur" }],
  callback_url: [{ required: true, message: "请输入回调地址", trigger: "blur" }],
};

function initialForm(): EditorForm {
  return {
    name: "",
    code: "",
    description: "",
    icon: "",
    base_url: "",
    callback_url: "",
    provisioning_url: null,
    provisioning_enabled: false,
    provisioning_timeout_seconds: 10,
    entitlement_sync_url: null,
    entitlement_sync_enabled: false,
    entitlement_sync_timeout_seconds: 10,
    status: 0,
    sort: 0,
  };
}
const formData = ref<EditorForm>(initialForm());

function rowActions(row: ApplicationListItem): TableOperationAction[] {
  const actions: TableOperationAction[] = [
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      perm: "module_control:application:update",
      run: () => openUpdate(row),
    },
    ...(autoProvisioningEnabled.value
      ? [
          {
            key: "packages",
            label: "套餐管理",
            artType: "more" as const,
            icon: "ri:price-tag-3-line",
            perm: "module_control:application_package:query",
            run: () => openPackages(row),
          },
        ]
      : []),
    {
      key: "reset-secret",
      label: "重置密钥",
      artType: "more",
      icon: "ri:key-2-line",
      perm: "module_control:application:reset_secret",
      run: () => void resetSecret(row),
    },
    {
      key: "delete",
      label: "删除",
      artType: "delete",
      perm: "module_control:application:delete",
      run: () => void removeApplication(row),
    },
  ];
  return actions.filter((action) => action.perm && hasAuth(action.perm));
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
} = useTable({
  core: {
    apiFn: ControlAPI.listApplications,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: () => [
      { type: "globalIndex", label: "序号", width: 64 },
      { prop: "name", label: "应用名称", minWidth: 150, showOverflowTooltip: true },
      { prop: "code", label: "应用编码", minWidth: 130, showOverflowTooltip: true },
      { prop: "client_id", label: "Client ID", minWidth: 220, showOverflowTooltip: true },
      { prop: "base_url", label: "应用地址", minWidth: 220, showOverflowTooltip: true },
      ...(autoProvisioningEnabled.value
        ? [
            {
              prop: "provisioning_enabled",
              label: "自动开户",
              width: 96,
              formatter: (row: ApplicationListItem) =>
                row.provisioning_enabled ? "已启用" : "已关闭",
            },
          ]
        : []),
      ...(entitlementSyncEnabled.value
        ? [
            {
              prop: "entitlement_sync_enabled",
              label: "授权同步",
              width: 96,
              formatter: (row: ApplicationListItem) =>
                row.entitlement_sync_enabled ? "已启用" : "已关闭",
            },
          ]
        : []),
      {
        prop: "status",
        label: "状态",
        width: 84,
        status: { 0: { type: "success", text: "启用" }, 1: { type: "info", text: "停用" } },
      },
      { prop: "sort", label: "排序", width: 72 },
      {
        prop: "operation",
        label: "操作",
        width: 330,
        fixed: "right",
        align: "right",
        formatter: (row: ApplicationListItem) => renderTableOperationCell(rowActions(row)),
      },
    ],
  },
});

async function handleSearch(params: typeof searchForm.value) {
  await searchBarRef.value?.validate();
  replaceSearchParams(params);
  await getData();
}

async function handleReset() {
  searchForm.value = {};
  await resetSearchParams();
}

function openCreate() {
  formData.value = initialForm();
  openDialog("create", "注册应用");
}

function openUpdate(row: ApplicationListItem) {
  formData.value = {
    id: row.id,
    name: row.name,
    code: row.code,
    description: row.description ?? "",
    icon: row.icon ?? "",
    base_url: row.base_url,
    callback_url: row.callback_url,
    provisioning_url: row.provisioning_url ?? null,
    provisioning_enabled: row.provisioning_enabled,
    provisioning_timeout_seconds: row.provisioning_timeout_seconds,
    entitlement_sync_url: row.entitlement_sync_url ?? null,
    entitlement_sync_enabled: row.entitlement_sync_enabled ?? false,
    entitlement_sync_timeout_seconds: row.entitlement_sync_timeout_seconds ?? 10,
    status: row.status,
    sort: row.sort,
  };
  openDialog("update", "编辑应用");
}

function openPackages(row: ApplicationListItem) {
  packageApplication.value = row;
  packageDialogVisible.value = true;
}

function closeEditor() {
  closeDialog();
  formRef.value?.clearValidate();
  formData.value = initialForm();
}

function submitEditor() {
  formRef.value?.validate(async (valid) => {
    if (!valid) return;
    submitLoading.value = true;
    try {
      const { id, ...payload } = formData.value;
      if (id) {
        await ControlAPI.updateApplication(id, payload);
      } else {
        const response = await ControlAPI.createApplication(payload);
        if (response.data.data) secretDialogRef.value?.show(response.data.data);
      }
      closeEditor();
      await getData();
    } finally {
      submitLoading.value = false;
    }
  });
}

async function resetSecret(row: ApplicationListItem) {
  if (!row.id) return;
  await confirmAction(`重置「${row.name}」的客户端密钥？旧密钥将立即失效。`, "重置密钥");
  const response = await ControlAPI.resetApplicationSecret(row.id);
  if (response.data.data) secretDialogRef.value?.show(response.data.data);
}

async function removeApplication(row: ApplicationListItem) {
  if (!row.id) return;
  await confirmDelete(`确定删除应用「${row.name}」吗？`);
  await ControlAPI.deleteApplication(row.id);
  await getData();
}
</script>
