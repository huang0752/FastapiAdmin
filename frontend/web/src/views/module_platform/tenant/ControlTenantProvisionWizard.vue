<template>
  <FaDialog
    :model-value="modelValue"
    title="创建租户并开通产品"
    width="980px"
    :confirm-loading="submitting"
    :show-footer="false"
    @update:model-value="updateVisible"
    @cancel="close"
  >
    <ElSteps :active="step" finish-status="success" align-center>
      <ElStep title="租户资料" />
      <ElStep title="产品与套餐" />
      <ElStep title="确认创建" />
    </ElSteps>

    <section v-if="step === 0" class="wizard-panel">
      <h3>基础资料</h3>
      <ElForm label-width="140px">
        <ElRow :gutter="20">
          <ElCol :span="12">
            <ElFormItem label="租户名称" required>
              <ElInput v-model="tenant.name" maxlength="100" />
            </ElFormItem>
          </ElCol>
          <ElCol :span="12">
            <ElFormItem label="租户编码" required>
              <ElInput v-model="tenant.code" maxlength="100" placeholder="仅字母和数字" />
            </ElFormItem>
          </ElCol>
          <ElCol :span="12">
            <ElFormItem label="所属站点" required>
              <ElSelect
                :model-value="tenant.site_id || undefined"
                placeholder="请选择所属站点"
                @update:model-value="tenant.site_id = Number($event) || 0"
                filterable
                class="field"
                @change="loadCentralPackages"
              >
                <ElOption
                  v-for="site in sites"
                  :key="site.id"
                  :label="site.name"
                  :value="site.id"
                />
              </ElSelect>
            </ElFormItem>
          </ElCol>
          <ElCol :span="12">
            <ElFormItem label="中控套餐" required>
              <ElSelect v-model="tenant.package_id" clearable class="field">
                <ElOption
                  v-for="item in centralPackages"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                />
              </ElSelect>
            </ElFormItem>
          </ElCol>
        </ElRow>
        <ElDivider content-position="left">企业信息</ElDivider>
        <ElRow :gutter="20">
          <ElCol :span="12">
            <ElFormItem label="统一社会信用代码">
              <ElInput v-model="tenant.unified_social_credit_code" maxlength="18" />
            </ElFormItem>
          </ElCol>
          <ElCol :span="12"
            ><ElFormItem label="联系人"><ElInput v-model="tenant.contact_name" /></ElFormItem
          ></ElCol>
          <ElCol :span="12"
            ><ElFormItem label="联系电话"><ElInput v-model="tenant.contact_phone" /></ElFormItem
          ></ElCol>
          <ElCol :span="12"
            ><ElFormItem label="联系邮箱"><ElInput v-model="tenant.contact_email" /></ElFormItem
          ></ElCol>
          <ElCol :span="24"
            ><ElFormItem label="企业地址"><ElInput v-model="tenant.address" /></ElFormItem
          ></ElCol>
        </ElRow>
      </ElForm>
    </section>

    <section v-else-if="step === 1" class="wizard-panel">
      <ElAlert
        title="请选择需要开通的产品及其目标套餐。目标租户编码默认与中控租户编码一致。"
        type="info"
        :closable="false"
      />
      <ElTable v-loading="loadingApplications" :data="applications" border>
        <ElTableColumn label="选择" width="76">
          <template #default="{ row }">
            <ElCheckbox
              :model-value="selectedApplicationIds.includes(row.id)"
              @change="toggleApplication(row, $event)"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn prop="name" label="产品" min-width="150" />
        <ElTableColumn label="目标套餐" min-width="180">
          <template #default="{ row }">
            <ElSelect
              :model-value="drafts[row.id]?.application_package_id"
              :disabled="!selectedApplicationIds.includes(row.id)"
              placeholder="请选择套餐"
              class="field"
              @update:model-value="setDraftPackage(row.id, $event)"
            >
              <ElOption
                v-for="item in packageOptions[row.id] || []"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
            </ElSelect>
          </template>
        </ElTableColumn>
        <ElTableColumn label="目标租户编码" min-width="190">
          <template #default="{ row }">
            <ElInput
              :model-value="drafts[row.id]?.desired_target_tenant_code"
              :disabled="!selectedApplicationIds.includes(row.id)"
              maxlength="100"
              @update:model-value="setDraftTargetCode(row.id, $event)"
            />
          </template>
        </ElTableColumn>
      </ElTable>
    </section>

    <section v-else class="wizard-panel confirmation">
      <ElDescriptions :column="2" border title="租户资料">
        <ElDescriptionsItem label="租户名称">{{ tenant.name }}</ElDescriptionsItem>
        <ElDescriptionsItem label="租户编码">{{ tenant.code }}</ElDescriptionsItem>
        <ElDescriptionsItem label="管理员账号">{{ tenant.code }}_admin</ElDescriptionsItem>
        <ElDescriptionsItem label="统一社会信用代码">
          {{ tenant.unified_social_credit_code || "-" }}
        </ElDescriptionsItem>
      </ElDescriptions>
      <ElTable :data="selectedSummary" border>
        <ElTableColumn prop="application_name" label="产品" />
        <ElTableColumn prop="package_name" label="套餐" />
        <ElTableColumn prop="target_tenant_code" label="目标租户编码" />
      </ElTable>
    </section>

    <div class="wizard-actions">
      <ElButton v-if="step > 0" :disabled="submitting" @click="step -= 1">上一步</ElButton>
      <ElButton v-if="step < 2" type="primary" @click="next">下一步</ElButton>
      <ElButton v-else type="primary" :loading="submitting" @click="submit">确认创建</ElButton>
    </div>
  </FaDialog>
</template>

<script setup lang="ts">
import ControlAPI, {
  buildProvisionCreatePayload,
  type ApplicationListItem,
  type ApplicationPackageListItem,
  type TenantProvisionSelection,
  type TenantWithProvisionsResult,
} from "@/api/module_control";
import type { TenantCreateForm } from "@/api/module_platform/tenant";
import PackageAPI from "@/api/module_platform/package";
import SiteAPI from "@/api/module_platform/site";
import { normalizeCreditCode, validateCreditCode } from "./credit-code";
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { loadAllPages } from "@utils/http/pagination";

defineOptions({ name: "ControlTenantProvisionWizard" });

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  created: [result: TenantWithProvisionsResult, applicationNames: Record<number, string>];
}>();

type NamedOption = { id: number; name: string };
type ApplicationOption = ApplicationListItem & { id: number };
type ApplicationPackageOption = ApplicationPackageListItem & { id: number };
type Draft = { application_package_id?: number; desired_target_tenant_code: string };

const step = ref(0);
const submitting = ref(false);
const loadingApplications = ref(false);
const sites = ref<NamedOption[]>([]);
const centralPackages = ref<NamedOption[]>([]);
const applications = ref<ApplicationOption[]>([]);
const packageOptions = reactive<Record<number, ApplicationPackageOption[]>>({});
const drafts = reactive<Record<number, Draft>>({});
const selectedApplicationIds = ref<number[]>([]);

function initialTenant(): TenantCreateForm {
  return {
    name: "",
    code: "",
    site_id: 0,
    status: 0,
    contact_name: "",
    contact_phone: "",
    contact_email: "",
    unified_social_credit_code: undefined,
    address: "",
  };
}

const tenant = reactive<TenantCreateForm>(initialTenant());

const selections = computed<TenantProvisionSelection[]>(() =>
  selectedApplicationIds.value.flatMap((applicationId) => {
    const draft = drafts[applicationId];
    return draft
      ? [
          {
            application_id: applicationId,
            application_package_id: draft.application_package_id as number,
            desired_target_tenant_code: draft.desired_target_tenant_code,
          },
        ]
      : [];
  })
);

const selectedSummary = computed(() =>
  selections.value.map((selection) => ({
    application_name:
      applications.value.find((item) => item.id === selection.application_id)?.name ?? "-",
    package_name:
      packageOptions[selection.application_id]?.find(
        (item) => item.id === selection.application_package_id
      )?.name ?? "-",
    target_tenant_code: selection.desired_target_tenant_code || tenant.code,
  }))
);

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) void loadInitialOptions();
    else reset();
  }
);

watch(
  () => tenant.code,
  (code, previous) => {
    for (const draft of Object.values(drafts)) {
      if (!draft.desired_target_tenant_code || draft.desired_target_tenant_code === previous) {
        draft.desired_target_tenant_code = code;
      }
    }
  }
);

async function loadInitialOptions() {
  loadingApplications.value = true;
  try {
    const [siteItems, applicationItems] = await Promise.all([
      loadAllPages(SiteAPI.listSites, { status: 0 }),
      loadAllPages(ControlAPI.listApplications, { status: 0 }),
    ]);
    sites.value = siteItems as NamedOption[];
    if (!sites.value.some((site) => site.id === tenant.site_id)) {
      tenant.site_id = sites.value.length === 1 ? sites.value[0]!.id : 0;
      await loadCentralPackages(tenant.site_id);
    }
    applications.value = applicationItems.filter(
      (item): item is ApplicationOption =>
        typeof item.id === "number" && item.status === 0 && item.provisioning_enabled
    );
    for (const application of applications.value) {
      if (!application.id) continue;
      drafts[application.id] = {
        application_package_id: undefined,
        desired_target_tenant_code: tenant.code,
      };
    }
    await Promise.all(applications.value.map((item) => loadApplicationPackages(item)));
  } finally {
    loadingApplications.value = false;
  }
}

async function loadCentralPackages(siteId: number) {
  tenant.package_id = undefined;
  if (!siteId) {
    centralPackages.value = [];
    return;
  }
  centralPackages.value = (await loadAllPages(PackageAPI.listPackage, {
    site_id: siteId,
    status: 0,
  })) as NamedOption[];
}

async function loadApplicationPackages(application: ApplicationOption) {
  const items = await loadAllPages(ControlAPI.listApplicationPackages, {
    application_id: application.id,
    status: 0,
  });
  const activeItems = items.filter(
    (item): item is ApplicationPackageOption => typeof item.id === "number" && item.status === 0
  );
  packageOptions[application.id] = activeItems;
  drafts[application.id] = {
    application_package_id: activeItems.find((item) => item.is_default)?.id,
    desired_target_tenant_code: tenant.code,
  };
}

function toggleApplication(application: ApplicationOption, checked: unknown) {
  if (checked) {
    if (!selectedApplicationIds.value.includes(application.id)) {
      selectedApplicationIds.value.push(application.id);
    }
    return;
  }
  selectedApplicationIds.value = selectedApplicationIds.value.filter((id) => id !== application.id);
}

function setDraftPackage(applicationId: number, packageId: unknown) {
  const draft = drafts[applicationId];
  if (!draft) return;
  draft.application_package_id = typeof packageId === "number" ? packageId : undefined;
}

function setDraftTargetCode(applicationId: number, targetCode: string) {
  const draft = drafts[applicationId];
  if (!draft) return;
  draft.desired_target_tenant_code = targetCode;
}

function validateTenantStep() {
  tenant.name = tenant.name.trim();
  tenant.code = tenant.code.trim();
  tenant.unified_social_credit_code = normalizeCreditCode(tenant.unified_social_credit_code);
  if (!tenant.name || !tenant.code || !tenant.site_id) {
    ElMessage.warning("请完整填写租户名称、编码和所属站点");
    return false;
  }
  if (!tenant.package_id) {
    ElMessage.warning("请选择中控套餐；如无可选套餐，请先在套餐管理中配置");
    return false;
  }
  if (!/^[A-Za-z0-9]+$/.test(tenant.code)) {
    ElMessage.warning("租户编码仅允许字母和数字");
    return false;
  }
  if (!validateCreditCode(tenant.unified_social_credit_code)) {
    ElMessage.warning("请输入有效的统一社会信用代码");
    return false;
  }
  return true;
}

function validateProductStep() {
  if (!selectedApplicationIds.value.length) {
    ElMessage.warning("请至少选择一个产品");
    return false;
  }
  for (const selection of selections.value) {
    if (!selection.application_package_id) {
      ElMessage.warning("请为每个产品选择目标套餐");
      return false;
    }
    if (!/^[A-Za-z0-9]+$/.test(selection.desired_target_tenant_code || tenant.code)) {
      ElMessage.warning("目标租户编码仅允许字母和数字");
      return false;
    }
  }
  return true;
}

function next() {
  if (step.value === 0 && !validateTenantStep()) return;
  if (step.value === 1 && !validateProductStep()) return;
  step.value += 1;
}

async function submit() {
  if (!validateTenantStep() || !validateProductStep()) return;
  submitting.value = true;
  try {
    const response = await ControlAPI.createTenantWithProvisions(
      buildProvisionCreatePayload({ ...tenant }, selections.value)
    );
    const result = response.data.data;
    if (!result) return;
    emit(
      "created",
      result,
      Object.fromEntries(
        applications.value.flatMap((item) => (item.id ? [[item.id, item.name]] : []))
      )
    );
    emit("update:modelValue", false);
    reset();
  } finally {
    submitting.value = false;
  }
}

function updateVisible(value: boolean) {
  if (!value) close();
}

function close() {
  emit("update:modelValue", false);
  reset();
}

function reset() {
  step.value = 0;
  Object.assign(tenant, initialTenant());
  selectedApplicationIds.value = [];
  applications.value = [];
  sites.value = [];
  centralPackages.value = [];
  for (const key of Object.keys(packageOptions)) delete packageOptions[Number(key)];
  for (const key of Object.keys(drafts)) delete drafts[Number(key)];
}

onBeforeUnmount(reset);

defineExpose({
  tenant,
  step,
  drafts,
  selectedApplicationIds,
  validateTenantStep,
  validateProductStep,
  next,
  submit,
});
</script>

<style scoped lang="scss">
.wizard-panel {
  min-height: 380px;
  padding: 28px 4px 16px;

  h3 {
    margin: 0 0 18px;
    font-size: 16px;
  }
}

.confirmation {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.wizard-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.field {
  width: 100%;
}
</style>
