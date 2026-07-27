import { request, NO_AUTH_FLAG } from "@utils";

const API_PATH = "/platform/tenant";

export const TENANT_STATUS = {
  ACTIVE: 0,
  GRACE: 1,
  SUSPENDED: 2,
  FROZEN: 3,
  EXPIRED: 4,
  ARCHIVED: 5,
} as const;

export type TenantStatus = (typeof TENANT_STATUS)[keyof typeof TENANT_STATUS];
export type TenantManualStatus = typeof TENANT_STATUS.ACTIVE | typeof TENANT_STATUS.SUSPENDED;

type TenantStatusMeta = {
  type: "primary" | "success" | "warning" | "danger" | "info";
  text: string;
};

export const TENANT_STATUS_META = {
  [TENANT_STATUS.ACTIVE]: { type: "success", text: "正常" },
  [TENANT_STATUS.GRACE]: { type: "warning", text: "宽限期" },
  [TENANT_STATUS.SUSPENDED]: { type: "danger", text: "暂停" },
  [TENANT_STATUS.FROZEN]: { type: "danger", text: "冻结" },
  [TENANT_STATUS.EXPIRED]: { type: "info", text: "过期" },
  [TENANT_STATUS.ARCHIVED]: { type: "info", text: "归档" },
} satisfies Record<TenantStatus, TenantStatusMeta>;

export const TENANT_STATUS_OPTIONS: Array<{ label: string; value: TenantStatus }> = [
  { label: "正常", value: TENANT_STATUS.ACTIVE },
  { label: "宽限期", value: TENANT_STATUS.GRACE },
  { label: "暂停", value: TENANT_STATUS.SUSPENDED },
  { label: "冻结", value: TENANT_STATUS.FROZEN },
  { label: "过期", value: TENANT_STATUS.EXPIRED },
  { label: "归档", value: TENANT_STATUS.ARCHIVED },
];

export const TENANT_MANUAL_STATUS_OPTIONS: Array<{
  label: string;
  value: TenantManualStatus;
}> = [
  { label: "正常", value: TENANT_STATUS.ACTIVE },
  { label: "暂停", value: TENANT_STATUS.SUSPENDED },
];

export interface TenantBatchStatusForm {
  ids: number[];
  status: TenantManualStatus;
}

export function resolveNextTenantManualStatus(status?: number): TenantManualStatus | null {
  if (status === TENANT_STATUS.ACTIVE) return TENANT_STATUS.SUSPENDED;
  if (status === TENANT_STATUS.SUSPENDED) return TENANT_STATUS.ACTIVE;
  return null;
}

function assertTenantManualStatus(status: number): asserts status is TenantManualStatus {
  if (status !== TENANT_STATUS.ACTIVE && status !== TENANT_STATUS.SUSPENDED) {
    throw new Error("租户手工状态仅支持正常(0)或暂停(2)");
  }
}

const TenantAPI = {
  listTenant(query?: TenantPageQuery) {
    return request<ApiResponse<PageResult<TenantTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailTenant(id: number) {
    return request<ApiResponse<TenantTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createTenant(body: TenantCreateForm) {
    return request<ApiResponse>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateTenant(id: number, body: TenantUpdateForm) {
    return request<ApiResponse>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteTenant(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  /** 批量修改租户状态 */
  batchTenantStatus(body: TenantBatchStatusForm) {
    assertTenantManualStatus(body.status);
    return request<ApiResponse>({
      url: `${API_PATH}/status/batch`,
      method: "patch",
      data: body,
    });
  },

  /** 显式设置单个租户的正常/暂停状态 */
  toggleTenantStatus(id: number, status: TenantManualStatus) {
    return this.batchTenantStatus({ ids: [id], status });
  },

  /** 租户续期 */
  renewTenant(id: number, body: { end_time: string }) {
    return request<ApiResponse<TenantTable>>({
      url: `${API_PATH}/renew/${id}`,
      method: "put",
      data: body,
    });
  },

  /** 套餐变更影响预览 */
  getPackageChangePreview(tenantId: number, newPackageId: number) {
    return request<ApiResponse<PackageChangePreview>>({
      url: `${API_PATH}/${tenantId}/package-change-preview`,
      method: "get",
      params: { new_package_id: newPackageId },
    });
  },

  getTenantUsers(tenantId: number) {
    return request<ApiResponse<TenantUser[]>>({
      url: `${API_PATH}/${tenantId}/users`,
      method: "get",
    });
  },

  addTenantUser(tenantId: number, body: TenantUserAddForm) {
    return request<ApiResponse>({
      url: `${API_PATH}/${tenantId}/users`,
      method: "post",
      data: body,
    });
  },

  removeTenantUser(tenantId: number, userId: number) {
    return request<ApiResponse>({
      url: `${API_PATH}/${tenantId}/users/${userId}`,
      method: "delete",
    });
  },

  /** 公开接口：无需登录即可获取租户配置（用于登录页等场景） */
  getTenantConfigInfo(tenantId: number) {
    return request<ApiResponse<TenantConfigItem[]>>({
      url: `${API_PATH}/${tenantId}/config/info`,
      method: "get",
      headers: {
        Authorization: NO_AUTH_FLAG,
      },
    });
  },

  /** 获取租户个性化配置 */
  getTenantConfig(tenantId: number) {
    return request<ApiResponse<TenantConfigItem[]>>({
      url: `${API_PATH}/${tenantId}/config`,
      method: "get",
    });
  },

  /** 批量更新租户个性化配置 */
  updateTenantConfig(tenantId: number, body: TenantConfigItem[]) {
    return request<ApiResponse<TenantConfigItem[]>>({
      url: `${API_PATH}/${tenantId}/config`,
      method: "put",
      data: body,
    });
  },
};

export default TenantAPI;

export interface TenantPageQuery extends PageQuery, UserByQueryParams, TenantByQueryParams {
  name?: string;
  code?: string;
  status?: TenantStatus;
}

export interface TenantTable extends BaseType {
  name: string;
  code: string;
  package_id?: number;
  start_time?: string;
  end_time?: string;
  contact_name?: string;
  contact_phone?: string;
  contact_email?: string;
  address?: string;
  domain?: string;
  logo_url?: string;
  sort?: number;
  version?: string;
  favicon?: string;
  login_bg?: string;
  copyright?: string;
  keep_record?: string;
  help_doc?: string;
  privacy?: string;
  clause?: string;
  git_code?: string;
  status?: TenantStatus;
  description?: string;
}

export interface TenantForm extends BaseFormType {
  name?: string;
  code?: string;
  package_id?: number;
  start_time?: string;
  end_time?: string;
  contact_name?: string;
  contact_phone?: string;
  contact_email?: string;
  address?: string;
  domain?: string;
  logo_url?: string;
  sort?: number;
  version?: string;
  favicon?: string;
  login_bg?: string;
  copyright?: string;
  keep_record?: string;
  help_doc?: string;
  privacy?: string;
  clause?: string;
  git_code?: string;
  status?: TenantStatus;
  description?: string;
}

export interface TenantCreateForm extends BaseFormType {
  name: string;
  code: string;
  package_id?: number;
  start_time?: string;
  end_time?: string;
  contact_name?: string;
  contact_phone?: string;
  contact_email?: string;
  address?: string;
  domain?: string;
  logo_url?: string;
  sort?: number;
  version?: string;
  favicon?: string;
  login_bg?: string;
  copyright?: string;
  keep_record?: string;
  help_doc?: string;
  privacy?: string;
  clause?: string;
  git_code?: string;
  status?: TenantStatus;
  description?: string;
}

export interface TenantUpdateForm extends BaseFormType {
  name?: string;
  code?: string;
  package_id?: number;
  start_time?: string;
  end_time?: string;
  contact_name?: string;
  contact_phone?: string;
  contact_email?: string;
  address?: string;
  domain?: string;
  logo_url?: string;
  sort?: number;
  version?: string;
  favicon?: string;
  login_bg?: string;
  copyright?: string;
  keep_record?: string;
  help_doc?: string;
  privacy?: string;
  clause?: string;
  git_code?: string;
  status?: TenantStatus;
  description?: string;
}

/** 套餐变更影响预览 */
export interface PackageChangePreview {
  new_package_id: number;
  new_package_name: string;
  affected_roles: Record<string, unknown>[];
  removed_menus: Record<string, unknown>[];
  added_menus: Record<string, unknown>[];
  quota_changes: Record<string, unknown>;
  total_affected_users: number;
}

export interface TenantUser {
  id: number;
  user_id: number;
  tenant_id: number;
  role: string;
  is_default: number;
  create_time?: string;
  username: string;
  name: string;
}

export interface TenantUserAddForm {
  user_id: number;
  role: string;
  is_default: number;
}

/** 租户配置项 */
export interface TenantConfigItem {
  key?: string;
  value?: string | null;
  config_key?: string;
  config_value?: string | null;
}
