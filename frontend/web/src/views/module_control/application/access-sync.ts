import type { ApplicationForm } from "@/api/module_control";

type SyncSettings = Pick<
  ApplicationForm,
  "entitlement_sync_enabled" | "entitlement_sync_url" | "entitlement_sync_timeout_seconds"
>;

export function validateEntitlementSyncSettings(settings: SyncSettings): string | null {
  const timeout = settings.entitlement_sync_timeout_seconds;
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 60)
    return "授权同步超时须为 1–60 秒的整数";
  const url = settings.entitlement_sync_url?.trim();
  if (!url) return settings.entitlement_sync_enabled ? "启用授权同步时请输入接口地址" : null;
  try {
    const parsed = new URL(url);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    ) {
      return "接口地址须为不含账号、密码、查询参数或片段的 HTTP(S) 绝对地址";
    }
  } catch {
    return "请输入有效的 HTTP(S) 授权同步接口地址";
  }
  return null;
}
