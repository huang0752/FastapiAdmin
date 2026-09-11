<template>
  <FaDrawer v-model="visible" title="新增用户并授权" size="560px" append-to-body>
    <ElSteps :active="step" finish-status="success" align-center class="mb-6">
      <ElStep title="用户信息" />
      <ElStep title="产品授权" />
    </ElSteps>
    <ElAlert
      v-if="formOptionsError"
      class="mb-4"
      type="error"
      :title="formOptionsError"
      :closable="false"
      show-icon
    >
      <template #default
        ><ElButton link type="primary" @click="loadUserOptions">重试</ElButton></template
      >
    </ElAlert>

    <ElForm
      v-if="step === 0"
      ref="formRef"
      :model="user"
      :rules="rules"
      label-width="88px"
      status-icon
    >
      <ElFormItem label="账号" prop="username">
        <ElInput v-model="user.username" placeholder="字母开头，2-32 位" />
      </ElFormItem>
      <ElFormItem label="姓名" prop="name">
        <ElInput v-model="user.name" placeholder="请输入姓名" />
      </ElFormItem>
      <ElFormItem label="初始密码" prop="password">
        <ElInput v-model="user.password" type="password" show-password placeholder="至少 6 位" />
      </ElFormItem>
      <ElFormItem label="手机号" prop="mobile">
        <ElInput v-model="user.mobile" maxlength="11" placeholder="选填" />
      </ElFormItem>
      <ElFormItem label="邮箱" prop="email">
        <ElInput v-model="user.email" placeholder="选填" />
      </ElFormItem>
      <ElFormItem label="性别">
        <ElRadioGroup v-model="user.gender">
          <ElRadio :value="0">男</ElRadio>
          <ElRadio :value="1">女</ElRadio>
          <ElRadio :value="2">未知</ElRadio>
        </ElRadioGroup>
      </ElFormItem>
      <ElFormItem label="部门">
        <ElTreeSelect
          v-model="user.dept_id"
          :data="deptOptions"
          :props="{ children: 'children', label: 'label', disabled: 'disabled' }"
          placeholder="请选择部门"
          filterable
          check-strictly
        />
      </ElFormItem>
      <ElFormItem label="岗位">
        <ElSelect v-model="user.position_ids" multiple placeholder="请选择岗位">
          <ElOption
            v-for="position in positionOptions"
            :key="position.value"
            :label="position.label"
            :value="position.value"
          />
        </ElSelect>
      </ElFormItem>
      <ElFormItem label="备注">
        <ElInput v-model="user.description" type="textarea" :rows="3" maxlength="100" />
      </ElFormItem>
    </ElForm>

    <section v-else class="product-step">
      <ElAlert
        v-if="openingsError || createError || pollError"
        class="mb-4"
        type="error"
        :title="openingsError || createError || pollError"
        :closable="false"
        show-icon
      >
        <template #default>
          <ElButton v-if="openingsError" link type="primary" @click="loadOpenings"
            >重新加载</ElButton
          >
          <ElButton v-else-if="pollError" link type="primary" @click="startPolling">
            重试刷新
          </ElButton>
        </template>
      </ElAlert>
      <ElAlert
        v-if="!createdUserId"
        title="请至少选择一个产品"
        description="用户创建后同步产品访问资格；产品管理员还需在产品的用户管理中分配本地角色（含菜单权限和数据范围），员工才能使用业务功能。"
        type="info"
        :closable="false"
        show-icon
      />
      <div v-if="!createdUserId" v-loading="openingsLoading" class="product-list">
        <ElCheckboxGroup v-model="selectedOpeningIds">
          <ElCheckbox
            v-for="opening in openings"
            :key="opening.tenant_application_id"
            :value="opening.tenant_application_id"
            border
          >
            {{ opening.application_name }}
          </ElCheckbox>
        </ElCheckboxGroup>
        <ElEmpty
          v-if="!openingsLoading && !openingsError && openings.length === 0"
          description="当前租户暂无已开通产品"
        />
      </div>

      <div v-else class="result-list">
        <ElAlert
          title="用户已创建，访问资格同步状态会自动刷新"
          type="success"
          :closable="false"
          show-icon
        />
        <div v-for="item in entitlementRows" :key="item.tenant_application_id" class="result-row">
          <div>
            <strong>{{ productName(item.tenant_application_id) }}</strong>
            <p v-if="item.error">{{ item.error }}</p>
          </div>
          <div class="result-row__action">
            <ElTag :type="statusType(item)">{{ statusLabel(item) }}</ElTag>
            <ElButton
              v-if="item.sync_status === 'failed'"
              v-auth="'module_control:user_grant:retry'"
              link
              type="primary"
              :loading="retryingIds.has(item.tenant_application_id)"
              @click="retry(item)"
            >
              重试
            </ElButton>
          </div>
        </div>
      </div>
    </section>

    <template #footer>
      <ElButton @click="visible = false">{{ createdUserId ? "完成" : "取消" }}</ElButton>
      <ElButton v-if="step === 1 && !createdUserId" @click="step = 0">上一步</ElButton>
      <ElButton v-if="step === 0" type="primary" @click="nextStep">下一步</ElButton>
      <ElButton
        v-else-if="!createdUserId"
        type="primary"
        :loading="submitting"
        :disabled="openingsLoading"
        @click="submit"
      >
        创建并授权
      </ElButton>
    </template>
  </FaDrawer>
</template>

<script setup lang="ts">
import ControlAPI, {
  type AvailableTenantApplication,
  type ControlUserCreatePayload,
  type UserApplicationGrant,
} from "@/api/module_control";
import DeptAPI from "@/api/module_system/dept";
import PositionAPI from "@/api/module_system/position";
import { formatTree } from "@utils";
import { ElMessage, type FormInstance, type FormRules, type TagProps } from "element-plus";
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";

defineOptions({ name: "ControlUserCreateDrawer" });

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean]; created: [] }>();

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});
const formRef = ref<FormInstance>();
const step = ref(0);
const openings = ref<AvailableTenantApplication[]>([]);
const deptOptions = ref<OptionType[]>([]);
const positionOptions = ref<Array<{ value: number; label: string }>>([]);
const openingsLoading = ref(false);
const openingsError = ref("");
const formOptionsError = ref("");
const createError = ref("");
const pollError = ref("");
const selectedOpeningIds = ref<number[]>([]);
const entitlementRows = ref<UserApplicationGrant[]>([]);
const createdUserId = ref<number>();
const submitting = ref(false);
const retryingIds = reactive(new Set<number>());
let pollingTimer: ReturnType<typeof setInterval> | undefined;
let pollGeneration = 0;
let pollInFlight = false;
let consecutivePollFailures = 0;

const user = reactive<ControlUserCreatePayload["user"]>({
  username: "",
  name: "",
  password: "",
  gender: 2,
  dept_id: undefined,
  position_ids: [],
  mobile: "",
  email: "",
  status: 0,
  description: "",
});
const rules: FormRules = {
  username: [
    { required: true, message: "请输入账号", trigger: "blur" },
    { pattern: /^[A-Za-z][A-Za-z0-9_.-]{1,31}$/, message: "账号格式不正确", trigger: "blur" },
  ],
  name: [{ required: true, message: "请输入姓名", trigger: "blur" }],
  password: [
    { required: true, message: "请输入初始密码", trigger: "blur" },
    { min: 6, max: 128, message: "密码长度为 6-128 位", trigger: "blur" },
  ],
  mobile: [
    {
      pattern: /^$|^1[3-9]\d{9}$/,
      message: "请输入正确的 11 位手机号",
      trigger: "blur",
    },
  ],
  email: [
    {
      type: "email",
      message: "请输入正确的邮箱地址",
      trigger: "blur",
    },
  ],
};

function stopPolling() {
  pollGeneration += 1;
  if (pollingTimer) clearInterval(pollingTimer);
  pollingTimer = undefined;
}

function reset() {
  stopPolling();
  step.value = 0;
  selectedOpeningIds.value = [];
  entitlementRows.value = [];
  createdUserId.value = undefined;
  retryingIds.clear();
  openingsError.value = "";
  formOptionsError.value = "";
  createError.value = "";
  pollError.value = "";
  consecutivePollFailures = 0;
  Object.assign(user, {
    username: "",
    name: "",
    password: "",
    gender: 2,
    dept_id: undefined,
    position_ids: [],
    mobile: "",
    email: "",
    status: 0,
    description: "",
  });
}

async function loadUserOptions() {
  formOptionsError.value = "";
  try {
    const [deptResponse, positionResponse] = await Promise.all([
      DeptAPI.listDept({}),
      PositionAPI.listPosition(),
    ]);
    deptOptions.value = formatTree(deptResponse.data.data);
    positionOptions.value = (positionResponse.data.data.items ?? [])
      .filter((item) => item.id !== undefined && item.name && item.status !== 1)
      .map((item) => ({ value: item.id as number, label: item.name as string }));
  } catch {
    formOptionsError.value = "部门和岗位加载失败，请重试。";
  }
}

async function loadOpenings() {
  openingsLoading.value = true;
  openingsError.value = "";
  try {
    const response = await ControlAPI.listAvailableTenantApplications();
    openings.value = response.data.data ?? [];
  } catch {
    openingsError.value = "已开通产品加载失败，请重试。";
  } finally {
    openingsLoading.value = false;
  }
}

async function nextStep() {
  if (!(await formRef.value?.validate().catch(() => false))) return;
  step.value = 1;
  if (openings.value.length === 0) await loadOpenings();
}

function statusLabel(item: UserApplicationGrant) {
  if (item.sync_status === "failed") return "同步失败";
  if (item.desired_state === "inactive" && item.sync_status !== "succeeded") return "撤权同步中";
  if (item.sync_status === "succeeded")
    return item.desired_state === "active" ? "访问资格已生效" : "未授权";
  return "同步中";
}

function statusType(item: UserApplicationGrant): TagProps["type"] {
  if (item.sync_status === "failed") return "danger";
  if (item.sync_status === "succeeded") return item.launchable ? "success" : "info";
  return "warning";
}

function productName(openingId: number) {
  return (
    openings.value.find((item) => item.tenant_application_id === openingId)?.application_name ??
    `产品 ${openingId}`
  );
}

async function refreshEntitlements(generation = pollGeneration) {
  if (!createdUserId.value || pollInFlight) return;
  pollInFlight = true;
  const currentUserId = createdUserId.value;
  try {
    const refreshed = await Promise.all(
      entitlementRows.value.map(async (item) => {
        const response = await ControlAPI.listGrantMembers(item.tenant_application_id);
        const member = (response.data.data ?? []).find((row) => row.user_id === currentUserId);
        return member?.grant_id
          ? ({ ...item, ...member, id: member.grant_id } as UserApplicationGrant)
          : item;
      })
    );
    if (generation !== pollGeneration) return;
    entitlementRows.value = refreshed;
    pollError.value = "";
    consecutivePollFailures = 0;
    if (refreshed.every((item) => ["succeeded", "failed"].includes(item.sync_status))) {
      stopPolling();
    }
  } catch {
    if (generation !== pollGeneration) return;
    consecutivePollFailures += 1;
    pollError.value = `授权状态刷新失败（${consecutivePollFailures}/3），请检查网络后重试。`;
    if (consecutivePollFailures >= 3) stopPolling();
  } finally {
    pollInFlight = false;
  }
}

function startPolling() {
  stopPolling();
  pollError.value = "";
  consecutivePollFailures = 0;
  const generation = pollGeneration;
  void refreshEntitlements(generation);
  pollingTimer = setInterval(() => void refreshEntitlements(generation), 2000);
}

async function submit() {
  if (submitting.value || createdUserId.value) return;
  if (selectedOpeningIds.value.length === 0) {
    ElMessage.warning("请至少选择一个产品");
    return;
  }
  submitting.value = true;
  createError.value = "";
  try {
    const email = user.email?.trim();
    const mobile = user.mobile?.trim();
    const response = await ControlAPI.createControlUser({
      user: {
        ...user,
        ...(email ? { email } : { email: undefined }),
        ...(mobile ? { mobile } : { mobile: undefined }),
      },
      tenant_application_ids: [...selectedOpeningIds.value],
    });
    const result = response.data.data;
    if (!result?.user.id) throw new Error("中控未返回新用户");
    createdUserId.value = result.user.id;
    entitlementRows.value = result.entitlements;
    emit("created");
    startPolling();
  } catch {
    createError.value = "用户或产品授权提交失败，请核对信息后重试。";
  } finally {
    submitting.value = false;
  }
}

async function retry(item: UserApplicationGrant) {
  if (!createdUserId.value || retryingIds.has(item.tenant_application_id)) return;
  retryingIds.add(item.tenant_application_id);
  try {
    const response = await ControlAPI.retryUserGrant(
      item.tenant_application_id,
      createdUserId.value
    );
    const updated = response.data.data;
    if (updated) {
      entitlementRows.value = entitlementRows.value.map((row) =>
        row.tenant_application_id === item.tenant_application_id ? updated : row
      );
    }
    startPolling();
  } catch {
    pollError.value = `「${productName(item.tenant_application_id)}」重试提交失败，请稍后重试。`;
  } finally {
    retryingIds.delete(item.tenant_application_id);
  }
}

watch(
  () => props.modelValue,
  (isOpen) => {
    if (isOpen) {
      reset();
      void loadUserOptions();
    } else stopPolling();
  }
);
onBeforeUnmount(stopPolling);

defineExpose({
  startPolling,
  refreshEntitlements,
  submit,
  retry,
  selectedOpeningIds,
  step,
  entitlementRows,
  createdUserId,
  submitting,
  pollError,
  loadOpenings,
  openingsError,
  user,
});
</script>

<style scoped lang="scss">
.product-list,
.result-list {
  margin-top: 18px;
}

.product-list :deep(.el-checkbox-group) {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.product-list :deep(.el-checkbox) {
  width: 100%;
  margin: 0;
}

.result-row {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 16px 2px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  p {
    margin: 5px 0 0;
    font-size: 12px;
    color: var(--el-color-danger);
  }
}

.result-row__action {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
