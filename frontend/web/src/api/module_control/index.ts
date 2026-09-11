import { request } from "@utils";
import type { TenantCreateForm, TenantCreateResult } from "@/api/module_platform/tenant";
import type { UserInfo } from "@/api/module_system/user";

const APPLICATION_PATH = "/control/applications";
const APPLICATION_PACKAGE_PATH = "/control/application-packages";
const TENANT_APPLICATION_PATH = "/control/tenant-applications";
const TENANT_PROVISION_CREATE_PATH = "/control/tenants/provision";
const TENANT_PROVISION_PATH = "/control/tenant-provisions";
const PORTAL_PATH = "/control/portal";

export interface ApplicationListItem extends BaseType {
  site_id: number;
  code: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  base_url: string;
  callback_url: string;
  provisioning_url?: string | null;
  provisioning_enabled: boolean;
  provisioning_timeout_seconds: number;
  entitlement_sync_url?: string | null;
  entitlement_sync_enabled: boolean;
  entitlement_sync_timeout_seconds: number;
  client_id: string;
  status: number;
  sort: number;
}

export interface ClientSecretResult {
  client_id: string;
  client_secret: string;
}

export interface ApplicationPageQuery extends PageQuery {
  code?: string;
  name?: string;
  status?: number;
}

export interface ApplicationForm {
  code: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  base_url: string;
  callback_url: string;
  provisioning_url?: string | null;
  provisioning_enabled: boolean;
  provisioning_timeout_seconds: number;
  entitlement_sync_url?: string | null;
  entitlement_sync_enabled: boolean;
  entitlement_sync_timeout_seconds: number;
  status: number;
  sort: number;
}

export type ApplicationUpdateForm = Partial<ApplicationForm>;

export interface ApplicationPackageListItem extends BaseType {
  site_id: number;
  application_id: number;
  code: string;
  name: string;
  description?: string | null;
  target_package_code: string;
  is_default: boolean;
  status: number;
  sort: number;
}

export interface ApplicationPackagePageQuery extends PageQuery {
  application_id?: number;
  code?: string;
  name?: string;
  status?: number;
}

export interface ApplicationPackageForm {
  application_id: number;
  code: string;
  name: string;
  description?: string | null;
  target_package_code: string;
  is_default: boolean;
  status: number;
  sort: number;
}

export type ApplicationPackageUpdateForm = Partial<Omit<ApplicationPackageForm, "application_id">>;

export type TenantProvisionStatus = "pending" | "processing" | "succeeded" | "failed";

export interface TenantProvisionSelection {
  application_id: number;
  application_package_id: number;
  desired_target_tenant_code?: string;
}

export interface CreateTenantWithProvisions {
  tenant: TenantCreateForm;
  applications: TenantProvisionSelection[];
}

export interface TenantProvisionListItem extends BaseType {
  site_id: number;
  tenant_id: number;
  application_id: number;
  application_package_id: number;
  tenant_application_id?: number | null;
  owner_user_id: number;
  provision_request_uuid: string;
  desired_target_tenant_code: string;
  target_tenant_code?: string | null;
  target_tenant_uuid?: string | null;
  status: TenantProvisionStatus;
  attempt_count: number;
  max_attempts: number;
  last_error_code?: string | null;
  last_error_message?: string | null;
  next_retry_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface TenantWithProvisionsResult {
  tenant: TenantCreateResult;
  provisions: TenantProvisionListItem[];
}

export interface TenantProvisionPageQuery extends PageQuery {
  tenant_id?: number;
  application_id?: number;
  status?: TenantProvisionStatus;
}

export interface TenantProvisionUpdateForm {
  application_package_id?: number;
  desired_target_tenant_code?: string;
}

export type TenantProvisionAction = "update" | "retry" | "reconcile";

const TENANT_PROVISION_WIZARD_PERMISSIONS = [
  "module_platform:site:query",
  "module_package:package:query",
  "module_control:application:query",
  "module_control:application_package:query",
  "module_control:tenant_provision:create",
] as const;

export function resolveTenantCreateMode(
  featureFlags?: Record<string, boolean | undefined>,
  hasPermission?: (permission: string) => boolean
): "basic" | "control-wizard" {
  return featureFlags?.tenantAutoProvisioning === true &&
    hasPermission != null &&
    TENANT_PROVISION_WIZARD_PERMISSIONS.every(hasPermission)
    ? "control-wizard"
    : "basic";
}

export function resolveProvisionActions(status: TenantProvisionStatus): TenantProvisionAction[] {
  return status === "failed" ? ["update", "retry", "reconcile"] : [];
}

export function buildProvisionCreatePayload(
  tenant: TenantCreateForm,
  selections: TenantProvisionSelection[]
): CreateTenantWithProvisions {
  return {
    tenant,
    applications: selections.map((selection) => {
      const desiredCode = selection.desired_target_tenant_code?.trim();
      return {
        application_id: selection.application_id,
        application_package_id: selection.application_package_id,
        ...(desiredCode && desiredCode !== tenant.code
          ? { desired_target_tenant_code: desiredCode }
          : {}),
      };
    }),
  };
}

export interface TenantApplicationListItem extends BaseType {
  site_id: number;
  tenant_id: number;
  application_id: number;
  target_tenant_code: string;
  status: number;
}

export type TenantApplicationPageQuery = PageQuery;

export interface TenantApplicationForm {
  tenant_id: number;
  application_id: number;
  target_tenant_code: string;
  status: number;
}

export type TenantApplicationUpdateForm = Partial<
  Pick<TenantApplicationForm, "target_tenant_code" | "status">
>;

export interface AvailableTenantApplication {
  tenant_application_id: number;
  application_id: number;
  application_code: string;
  application_name: string;
  target_tenant_code: string;
  status: number;
}

export interface GrantMember {
  user_id: number;
  username: string;
  name: string;
  mobile?: string | null;
  email?: string | null;
  avatar?: string | null;
  granted: boolean;
  grant_id?: number | null;
  desired_state?: EntitlementDesiredState | null;
  sync_status?: EntitlementSyncStatus | null;
  sync_version: number;
  error?: string | null;
  launchable: boolean;
}

export type EntitlementDesiredState = "active" | "inactive";
export type EntitlementSyncStatus = "pending" | "processing" | "succeeded" | "failed";

export interface UserApplicationGrant extends BaseType {
  site_id: number;
  tenant_application_id: number;
  tenant_id: number;
  user_id: number;
  status: number;
  grant_id?: number | null;
  desired_state: EntitlementDesiredState;
  sync_status: EntitlementSyncStatus;
  sync_version: number;
  error?: string | null;
  launchable: boolean;
}

export interface ControlUserCreatePayload {
  user: {
    username?: string;
    name?: string;
    password?: string;
    gender?: number;
    dept_id?: number;
    position_ids?: number[];
    mobile?: string;
    email?: string;
    status: number;
    description?: string;
  };
  tenant_application_ids: number[];
}

export interface ControlUserCreateResult {
  user: UserInfo;
  entitlements: UserApplicationGrant[];
}

export interface PortalApplication {
  code: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  base_url: string;
  status: number;
  sort: number;
  grant_id: number;
  desired_state: EntitlementDesiredState;
  sync_status: EntitlementSyncStatus;
  sync_version: number;
  error?: string | null;
  launchable: boolean;
}

export interface LaunchResult {
  redirect_url: string;
  expires_at: string;
}

const ControlAPI = {
  listApplications(query?: ApplicationPageQuery) {
    return request<ApiResponse<PageResult<ApplicationListItem>>>({
      url: APPLICATION_PATH,
      method: "get",
      params: query,
    });
  },

  getApplication(id: number) {
    return request<ApiResponse<ApplicationListItem>>({
      url: `${APPLICATION_PATH}/${id}`,
      method: "get",
    });
  },

  createApplication(body: ApplicationForm) {
    return request<ApiResponse<ClientSecretResult>>({
      url: APPLICATION_PATH,
      method: "post",
      data: body,
    });
  },

  updateApplication(id: number, body: ApplicationUpdateForm) {
    return request<ApiResponse<ApplicationListItem>>({
      url: `${APPLICATION_PATH}/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteApplication(id: number) {
    return request<ApiResponse>({ url: `${APPLICATION_PATH}/${id}`, method: "delete" });
  },

  resetApplicationSecret(id: number) {
    return request<ApiResponse<ClientSecretResult>>({
      url: `${APPLICATION_PATH}/${id}/reset-secret`,
      method: "post",
    });
  },

  listApplicationPackages(query?: ApplicationPackagePageQuery) {
    return request<ApiResponse<PageResult<ApplicationPackageListItem>>>({
      url: APPLICATION_PACKAGE_PATH,
      method: "get",
      params: query,
    });
  },

  createApplicationPackage(body: ApplicationPackageForm) {
    return request<ApiResponse<ApplicationPackageListItem>>({
      url: APPLICATION_PACKAGE_PATH,
      method: "post",
      data: body,
    });
  },

  updateApplicationPackage(id: number, body: ApplicationPackageUpdateForm) {
    return request<ApiResponse<ApplicationPackageListItem>>({
      url: `${APPLICATION_PACKAGE_PATH}/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteApplicationPackage(id: number) {
    return request<ApiResponse>({
      url: `${APPLICATION_PACKAGE_PATH}/${id}`,
      method: "delete",
    });
  },

  createTenantWithProvisions(body: CreateTenantWithProvisions) {
    return request<ApiResponse<TenantWithProvisionsResult>>({
      url: TENANT_PROVISION_CREATE_PATH,
      method: "post",
      data: body,
    });
  },

  listTenantProvisions(query?: TenantProvisionPageQuery) {
    return request<ApiResponse<PageResult<TenantProvisionListItem>>>({
      url: TENANT_PROVISION_PATH,
      method: "get",
      params: query,
    });
  },

  updateTenantProvision(id: number, body: TenantProvisionUpdateForm) {
    return request<ApiResponse<TenantProvisionListItem>>({
      url: `${TENANT_PROVISION_PATH}/${id}`,
      method: "put",
      data: body,
    });
  },

  retryTenantProvision(id: number) {
    return request<ApiResponse<{ task_id: number }>>({
      url: `${TENANT_PROVISION_PATH}/${id}/retry`,
      method: "post",
    });
  },

  reconcileTenantProvision(id: number) {
    return request<ApiResponse<{ task_id: number }>>({
      url: `${TENANT_PROVISION_PATH}/${id}/reconcile`,
      method: "post",
    });
  },

  listTenantApplications(query?: TenantApplicationPageQuery) {
    return request<ApiResponse<PageResult<TenantApplicationListItem>>>({
      url: TENANT_APPLICATION_PATH,
      method: "get",
      params: query,
    });
  },

  listAvailableTenantApplications() {
    return request<ApiResponse<AvailableTenantApplication[]>>({
      url: `${TENANT_APPLICATION_PATH}/available`,
      method: "get",
    });
  },

  createTenantApplication(body: TenantApplicationForm) {
    return request<ApiResponse<TenantApplicationListItem>>({
      url: TENANT_APPLICATION_PATH,
      method: "post",
      data: body,
    });
  },

  updateTenantApplication(id: number, body: TenantApplicationUpdateForm) {
    return request<ApiResponse<TenantApplicationListItem>>({
      url: `${TENANT_APPLICATION_PATH}/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteTenantApplication(id: number) {
    return request<ApiResponse>({ url: `${TENANT_APPLICATION_PATH}/${id}`, method: "delete" });
  },

  listGrantMembers(tenantApplicationId: number) {
    return request<ApiResponse<GrantMember[]>>({
      url: `${TENANT_APPLICATION_PATH}/${tenantApplicationId}/grants`,
      method: "get",
    });
  },

  createControlUser(body: ControlUserCreatePayload) {
    return request<ApiResponse<ControlUserCreateResult>>({
      url: "/control/users",
      method: "post",
      data: body,
    });
  },

  grantUser(tenantApplicationId: number, userId: number) {
    return request<ApiResponse<UserApplicationGrant>>({
      url: `${TENANT_APPLICATION_PATH}/${tenantApplicationId}/grants/${userId}`,
      method: "put",
    });
  },

  revokeUser(tenantApplicationId: number, userId: number) {
    return request<ApiResponse>({
      url: `${TENANT_APPLICATION_PATH}/${tenantApplicationId}/grants/${userId}`,
      method: "delete",
    });
  },

  retryUserGrant(tenantApplicationId: number, userId: number) {
    return request<ApiResponse<UserApplicationGrant>>({
      url: `${TENANT_APPLICATION_PATH}/${tenantApplicationId}/grants/${userId}/retry`,
      method: "post",
    });
  },

  listMyApplications() {
    return request<ApiResponse<PortalApplication[]>>({
      url: `${PORTAL_PATH}/my-applications`,
      method: "get",
    });
  },

  launchApplication(code: string) {
    return request<ApiResponse<LaunchResult>>({
      url: `${PORTAL_PATH}/applications/${encodeURIComponent(code)}/launch`,
      method: "post",
    });
  },
};

export default ControlAPI;
