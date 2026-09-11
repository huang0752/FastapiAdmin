<template>
  <div class="fa-full-height">
    <ElCard class="opening-summary" shadow="never">
      <div>
        <h2>{{ isProvisionLedger ? "产品开通账本" : "租户应用开通" }}</h2>
        <p v-if="isProvisionLedger">查看真实开通状态、安全错误摘要，并处理失败记录。</p>
        <p v-else>高级兼容入口：维护既有租户与目标系统租户编码映射。</p>
      </div>
      <ElButton
        v-if="!isProvisionLedger"
        v-auth="'module_control:tenant_application:create'"
        type="primary"
        icon="Plus"
        @click="openLegacyCreate"
      >
        开通应用
      </ElButton>
    </ElCard>

    <ElCard class="fa-table-card" shadow="hover">
      <FaTableHeader v-model:columns="columnChecks" :loading="loading" @refresh="refreshData" />
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
      width="640px"
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
        :label-width="120"
        label-suffix=":"
      />
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import ControlAPI, {
  resolveProvisionActions,
  type ApplicationListItem,
  type ApplicationPackageListItem,
  type TenantApplicationForm,
  type TenantApplicationListItem,
  type TenantApplicationPageQuery,
  type TenantProvisionListItem,
  type TenantProvisionPageQuery,
} from "@/api/module_control";
import TenantAPI, { type TenantTable } from "@/api/module_platform/tenant";
import { useAuth } from "@/hooks/core/useAuth";
import { useCrudDialog } from "@/hooks/core/useCrudDialog";
import { confirmAction, confirmDelete } from "@/hooks/core/useConfirm";
import { useTable } from "@/hooks/core/useTable";
import { renderTableOperationCell, type TableOperationAction } from "@utils";
import { ElMessage } from "element-plus";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import type { ColumnOption } from "@/types/component";
import type { AxiosResponse } from "axios";
import { loadAllPages } from "@utils/http/pagination";

defineOptions({ name: "ControlTenantApplication", inheritAttrs: false });

type TableRow = TenantApplicationListItem | TenantProvisionListItem;
type EditorForm = {
  id?: number;
  tenant_id?: number;
  application_id?: number;
  application_package_id?: number;
  target_tenant_code: string;
  status: number;
};
type FormExpose = {
  validate: (callback: (valid: boolean) => void) => void;
  clearValidate: () => void;
};

const props = defineProps<{ ledger?: boolean }>();
const route = useRoute();
const isProvisionLedger = computed(
  () => props.ledger === true || String(route.name) === "ControlTenantProvision"
);
const { hasAuth } = useAuth();
const { dialogVisible, openDialog, closeDialog } = useCrudDialog();
const tenants = ref<TenantTable[]>([]);
const applications = ref<ApplicationListItem[]>([]);
const applicationPackages = ref<ApplicationPackageListItem[]>([]);
const submitLoading = ref(false);
const formRef = ref<FormExpose | null>(null);

const tenantName = (id: number) =>
  tenants.value.find((item) => item.id === id)?.name ?? `租户 #${id}`;
const applicationName = (id: number) =>
  applications.value.find((item) => item.id === id)?.name ?? `应用 #${id}`;
const packageName = (id: number) =>
  applicationPackages.value.find((item) => item.id === id)?.name ?? `套餐 #${id}`;

function initialForm(): EditorForm {
  return {
    tenant_id: undefined,
    application_id: undefined,
    application_package_id: undefined,
    target_tenant_code: "",
    status: 0,
  };
}
const formData = ref<EditorForm>(initialForm());

const formItems = computed<FormItem[]>(() => {
  if (isProvisionLedger.value) {
    return [
      {
        key: "application_package_id",
        label: "目标套餐",
        type: "select",
        props: {
          options: applicationPackages.value
            .filter((item) => item.application_id === formData.value.application_id)
            .map((item) => ({ label: item.name, value: item.id })),
        },
      },
      {
        key: "target_tenant_code",
        label: "目标租户编码",
        type: "input",
        props: { maxlength: 100 },
      },
    ];
  }
  return [
    {
      key: "tenant_id",
      label: "租户",
      type: "select",
      props: {
        disabled: dialogVisible.type === "update",
        options: tenants.value.map((item) => ({
          label: `${item.name}（${item.code}）`,
          value: item.id,
        })),
      },
    },
    {
      key: "application_id",
      label: "应用",
      type: "select",
      props: {
        disabled: dialogVisible.type === "update",
        options: applications.value.map((item) => ({
          label: `${item.name}（${item.code}）`,
          value: item.id,
        })),
      },
    },
    { key: "target_tenant_code", label: "目标租户编码", type: "input" },
    {
      key: "status",
      label: "状态",
      type: "radiogroup",
      props: {
        options: [
          { label: "启用", value: 0 },
          { label: "停用", value: 1 },
        ],
      },
    },
  ];
});

const formRules = computed(() => ({
  ...(isProvisionLedger.value
    ? { application_package_id: [{ required: true, message: "请选择目标套餐", trigger: "change" }] }
    : {
        tenant_id: [{ required: true, message: "请选择租户", trigger: "change" }],
        application_id: [{ required: true, message: "请选择应用", trigger: "change" }],
      }),
  target_tenant_code: [{ required: true, message: "请输入目标租户编码", trigger: "blur" }],
}));

function rowActions(row: TableRow): TableOperationAction[] {
  if (!isProvisionLedger.value) {
    const opening = row as TenantApplicationListItem;
    const legacyActions: TableOperationAction[] = [
      {
        key: "edit",
        label: "编辑",
        artType: "edit",
        perm: "module_control:tenant_application:update",
        run: () => openLegacyUpdate(opening),
      },
      {
        key: "delete",
        label: "撤销",
        artType: "delete",
        perm: "module_control:tenant_application:delete",
        run: () => void removeOpening(opening),
      },
    ];
    return legacyActions.filter((action) => action.perm && hasAuth(action.perm));
  }

  const provision = row as TenantProvisionListItem;
  const actions: TableOperationAction[] = [];
  if (provision.status === "failed") {
    for (const action of resolveProvisionActions(provision.status)) {
      actions.push({
        key: action,
        label: action === "update" ? "修改" : action === "retry" ? "重试" : "对账",
        artType: action === "update" ? "edit" : "more",
        perm: `module_control:tenant_provision:${action}`,
        run: () =>
          action === "update"
            ? openProvisionUpdate(provision)
            : void dispatchProvisionAction(provision, action),
      });
    }
  }
  if (provision.status === "succeeded") {
    const tenantApplicationId = provision.tenant_application_id;
    if (tenantApplicationId) {
      actions.push({
        key: "revoke-access",
        label: "撤销访问",
        artType: "delete",
        perm: "module_control:tenant_application:delete",
        run: () =>
          void removeOpening({
            id: tenantApplicationId,
            tenant_id: provision.tenant_id,
            application_id: provision.application_id,
          }),
      });
    }
  }
  return actions.filter((action) => action.perm && hasAuth(action.perm));
}

type TablePageQuery = TenantProvisionPageQuery & TenantApplicationPageQuery;
type TablePageResult = {
  items: TableRow[];
  total: number;
  page_no: number;
  page_size: number;
  has_next: boolean;
};

const listRows = async (
  query: TablePageQuery
): Promise<AxiosResponse<ApiResponse<TablePageResult>>> => {
  const response = isProvisionLedger.value
    ? await ControlAPI.listTenantProvisions(query)
    : await ControlAPI.listTenantApplications(query);
  return response as AxiosResponse<ApiResponse<TablePageResult>>;
};

const provisionColumns: ColumnOption<TableRow>[] = [
  {
    prop: "application_package_id",
    label: "套餐",
    minWidth: 120,
    formatter: (row: TableRow) =>
      packageName((row as TenantProvisionListItem).application_package_id),
  },
  { prop: "desired_target_tenant_code", label: "目标租户编码", minWidth: 150 },
  {
    prop: "status",
    label: "状态",
    width: 100,
    status: {
      pending: { type: "info", text: "等待开通" },
      processing: { type: "warning", text: "开通中" },
      succeeded: { type: "success", text: "已开通" },
      failed: { type: "danger", text: "开通失败" },
    },
  },
  { prop: "attempt_count", label: "尝试次数", width: 90 },
  {
    prop: "last_error_message",
    label: "安全错误摘要",
    minWidth: 220,
    showOverflowTooltip: true,
  },
];

const legacyColumns: ColumnOption<TableRow>[] = [
  { prop: "target_tenant_code", label: "目标租户编码", minWidth: 160 },
  {
    prop: "status",
    label: "状态",
    width: 84,
    status: { 0: { type: "success", text: "启用" }, 1: { type: "info", text: "停用" } },
  },
];

const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  getData,
  handleSizeChange,
  handleCurrentChange,
  refreshData,
} = useTable<typeof listRows>({
  core: {
    apiFn: listRows,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: (): ColumnOption<TableRow>[] => [
      { type: "globalIndex", label: "序号", width: 64 },
      {
        prop: "tenant_id",
        label: "租户",
        minWidth: 150,
        formatter: (row: TableRow) => tenantName(row.tenant_id),
      },
      {
        prop: "application_id",
        label: "应用",
        minWidth: 150,
        formatter: (row: TableRow) => applicationName(row.application_id),
      },
      ...(isProvisionLedger.value ? provisionColumns : legacyColumns),
      {
        prop: "operation",
        label: "操作",
        width: isProvisionLedger.value ? 220 : 160,
        fixed: "right",
        align: "right",
        formatter: (row: TableRow) => renderTableOperationCell(rowActions(row)),
      },
    ],
  },
});

async function loadOptions() {
  const [tenantItems, applicationItems] = await Promise.all([
    loadAllPages(TenantAPI.listTenant, {}),
    loadAllPages(ControlAPI.listApplications, {}),
  ]);
  tenants.value = tenantItems;
  applications.value = applicationItems;
  if (!isProvisionLedger.value) return;
  applicationPackages.value = await loadAllPages(ControlAPI.listApplicationPackages, {});
}

function openLegacyCreate() {
  formData.value = initialForm();
  openDialog("create", "开通租户应用");
}

function openLegacyUpdate(row: TenantApplicationListItem) {
  formData.value = {
    id: row.id,
    tenant_id: row.tenant_id,
    application_id: row.application_id,
    target_tenant_code: row.target_tenant_code,
    status: row.status,
  };
  openDialog("update", "编辑租户应用");
}

function openProvisionUpdate(row: TenantProvisionListItem) {
  formData.value = {
    id: row.id,
    application_id: row.application_id,
    application_package_id: row.application_package_id,
    target_tenant_code: row.desired_target_tenant_code,
    status: 0,
  };
  openDialog("update", "修改失败开通记录");
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
      const payload = formData.value;
      if (isProvisionLedger.value) {
        if (!payload.id || !payload.application_package_id) return;
        await ControlAPI.updateTenantProvision(payload.id, {
          application_package_id: payload.application_package_id,
          desired_target_tenant_code: payload.target_tenant_code,
        });
      } else if (payload.id) {
        await ControlAPI.updateTenantApplication(payload.id, {
          target_tenant_code: payload.target_tenant_code,
          status: payload.status,
        });
      } else {
        if (!isPositiveId(payload.tenant_id) || !isPositiveId(payload.application_id)) {
          ElMessage.warning("请选择有效的租户和应用");
          return;
        }
        const createPayload: TenantApplicationForm = {
          tenant_id: payload.tenant_id,
          application_id: payload.application_id,
          target_tenant_code: payload.target_tenant_code,
          status: payload.status,
        };
        await ControlAPI.createTenantApplication(createPayload);
      }
      closeEditor();
      await getData();
    } finally {
      submitLoading.value = false;
    }
  });
}

function isPositiveId(value: number | undefined): value is number {
  return Number.isInteger(value) && (value ?? 0) > 0;
}

async function dispatchProvisionAction(
  row: TenantProvisionListItem,
  action: "retry" | "reconcile"
) {
  if (!row.id) return;
  await confirmAction(
    action === "retry" ? "确认人工重试该产品开通吗？" : "确认与目标产品执行对账吗？",
    action === "retry" ? "人工重试" : "对账"
  );
  if (action === "retry") await ControlAPI.retryTenantProvision(row.id);
  else await ControlAPI.reconcileTenantProvision(row.id);
  await getData();
}

async function removeOpening(row: { id?: number; tenant_id: number; application_id: number }) {
  if (!row.id) return;
  await confirmDelete(
    `确认撤销「${tenantName(row.tenant_id)} / ${applicationName(row.application_id)}」的中控访问吗？目标产品租户不会被删除或暂停。`
  );
  await ControlAPI.deleteTenantApplication(row.id);
  await getData();
}

onMounted(() => void loadOptions());
</script>

<style scoped lang="scss">
.opening-summary {
  margin-bottom: 12px;

  :deep(.el-card__body) {
    display: flex;
    gap: 24px;
    align-items: center;
    justify-content: space-between;
  }

  h2 {
    margin: 4px 0 6px;
    font-size: 20px;
    color: var(--el-text-color-primary);
  }

  p {
    margin: 0;
    color: var(--el-text-color-secondary);
  }
}
</style>
