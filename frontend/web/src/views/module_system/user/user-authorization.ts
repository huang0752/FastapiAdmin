import type { UserAuthSource, UserAuthorizationStatus, UserInfo } from "@/api/module_system/user";

export interface UserAuthorizationSearch {
  username?: string;
  name?: string;
  status?: number;
  auth_source?: UserAuthSource;
  authorization_status?: UserAuthorizationStatus;
  created_id?: number;
  created_time?: string[];
}

export function buildUserReplaceParams(state: UserAuthorizationSearch): Record<string, unknown> {
  return {
    username: state.username,
    name: state.name,
    status: state.status,
    auth_source: state.auth_source,
    authorization_status: state.authorization_status,
    created_id: state.created_id,
    created_time:
      Array.isArray(state.created_time) && state.created_time.length === 2
        ? state.created_time
        : undefined,
  };
}

export const FEDERATED_READONLY_FIELDS = new Set([
  "username",
  "name",
  "mobile",
  "email",
  "avatar",
  "status",
]);

export function isFederatedIdentityFieldReadonly(field: string, source?: UserAuthSource): boolean {
  return source === "federated" && FEDERATED_READONLY_FIELDS.has(field);
}

export function isPendingFederatedUser(user: UserInfo): boolean {
  return user.auth_source === "federated" && user.authorization_status === "pending";
}

export function shouldShowResetPassword(user: Pick<UserInfo, "auth_source">): boolean {
  return user.auth_source !== "federated";
}

export function sourceLabel(source?: UserAuthSource): string {
  return source === "federated" ? "统一登录" : "本地账号";
}

export function authorizationLabel(status?: UserAuthorizationStatus | null): string {
  if (status === "pending") return "待授权";
  if (status === "authorized") return "已授权";
  return "—";
}
