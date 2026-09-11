<template>
  <FaDialog
    :model-value="modelValue"
    :title="`${application?.name || ''} · 套餐管理`"
    width="860px"
    :show-footer="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="package-toolbar">
      <ElButton
        v-auth="'module_control:application_package:create'"
        type="primary"
        icon="Plus"
        @click="openCreate"
      >
        新增套餐
      </ElButton>
    </div>
    <ElTable v-loading="loading" :data="packages" border>
      <ElTableColumn prop="name" label="套餐名称" min-width="130" />
      <ElTableColumn prop="code" label="中控编码" min-width="110" />
      <ElTableColumn prop="target_package_code" label="目标套餐编码" min-width="130" />
      <ElTableColumn prop="is_default" label="默认" width="76">
        <template #default="{ row }"
          ><ElTag v-if="row.is_default" type="success">默认</ElTag><span v-else>-</span></template
        >
      </ElTableColumn>
      <ElTableColumn prop="status" label="状态" width="80">
        <template #default="{ row }">{{ row.status === 0 ? "启用" : "停用" }}</template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <ElButton
            v-auth="'module_control:application_package:update'"
            link
            type="primary"
            @click="openUpdate(row)"
            >编辑</ElButton
          >
          <ElButton
            v-auth="'module_control:application_package:delete'"
            link
            type="danger"
            @click="remove(row)"
            >删除</ElButton
          >
        </template>
      </ElTableColumn>
    </ElTable>

    <FaDialog
      v-model="editorVisible"
      :title="form.id ? '编辑应用套餐' : '新增应用套餐'"
      width="620px"
      :confirm-loading="saving"
      @confirm="save"
      @cancel="editorVisible = false"
    >
      <ElForm label-width="120px">
        <ElFormItem label="套餐名称" required
          ><ElInput v-model="form.name" maxlength="100"
        /></ElFormItem>
        <ElFormItem label="中控编码" required
          ><ElInput v-model="form.code" maxlength="64"
        /></ElFormItem>
        <ElFormItem label="目标套餐编码" required
          ><ElInput v-model="form.target_package_code" maxlength="100"
        /></ElFormItem>
        <ElFormItem label="默认套餐"><ElSwitch v-model="form.is_default" /></ElFormItem>
        <ElFormItem label="状态"
          ><ElRadioGroup v-model="form.status"
            ><ElRadio :value="0">启用</ElRadio><ElRadio :value="1">停用</ElRadio></ElRadioGroup
          ></ElFormItem
        >
        <ElFormItem label="排序"><ElInputNumber v-model="form.sort" :min="0" /></ElFormItem>
        <ElFormItem label="说明"
          ><ElInput v-model="form.description" type="textarea" :rows="3"
        /></ElFormItem>
      </ElForm>
    </FaDialog>
  </FaDialog>
</template>

<script setup lang="ts">
import ControlAPI, {
  type ApplicationListItem,
  type ApplicationPackageForm,
  type ApplicationPackageListItem,
} from "@/api/module_control";
import { confirmDelete } from "@/hooks/core/useConfirm";
import { ElMessage } from "element-plus";
import { reactive, ref, watch } from "vue";
import { loadAllPages } from "@utils/http/pagination";

defineOptions({ name: "ApplicationPackageDialog" });

const props = defineProps<{
  modelValue: boolean;
  application: ApplicationListItem | null;
}>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

type EditorForm = ApplicationPackageForm & { id?: number };

const packages = ref<ApplicationPackageListItem[]>([]);
const loading = ref(false);
const saving = ref(false);
const editorVisible = ref(false);

function initialForm(): EditorForm {
  return {
    application_id: props.application?.id ?? 0,
    code: "",
    name: "",
    description: "",
    target_package_code: "",
    is_default: false,
    status: 0,
    sort: 0,
  };
}

const form = reactive<EditorForm>(initialForm());

watch(
  () => [props.modelValue, props.application?.id] as const,
  ([visible, applicationId]) => {
    if (visible && applicationId) void load();
    if (!visible) {
      packages.value = [];
      editorVisible.value = false;
      Object.assign(form, initialForm());
    }
  }
);

async function load() {
  if (!props.application?.id) return;
  loading.value = true;
  try {
    packages.value = await loadAllPages(ControlAPI.listApplicationPackages, {
      application_id: props.application.id,
    });
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  Object.assign(form, initialForm());
  editorVisible.value = true;
}

function openUpdate(row: ApplicationPackageListItem) {
  Object.assign(form, {
    id: row.id,
    application_id: row.application_id,
    code: row.code,
    name: row.name,
    description: row.description ?? "",
    target_package_code: row.target_package_code,
    is_default: row.is_default,
    status: row.status,
    sort: row.sort,
  });
  editorVisible.value = true;
}

async function save() {
  if (!form.name.trim() || !form.code.trim() || !form.target_package_code.trim()) {
    ElMessage.warning("请完整填写套餐名称、编码和目标套餐编码");
    return;
  }
  saving.value = true;
  try {
    const { id, application_id, ...editable } = form;
    if (id) await ControlAPI.updateApplicationPackage(id, editable);
    else await ControlAPI.createApplicationPackage({ application_id, ...editable });
    editorVisible.value = false;
    await load();
  } finally {
    saving.value = false;
  }
}

async function remove(row: ApplicationPackageListItem) {
  if (!row.id) return;
  await confirmDelete(`确定删除套餐「${row.name}」吗？`);
  await ControlAPI.deleteApplicationPackage(row.id);
  await load();
}
</script>

<style scoped lang="scss">
.package-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
</style>
