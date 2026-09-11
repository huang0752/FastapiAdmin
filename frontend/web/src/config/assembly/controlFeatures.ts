import type { MenuTable } from "@/api/module_platform/menu";

type Features = Record<string, boolean | undefined>;

export function resolveControlUserCreate(
  features: Features,
  hasPermission: (permission: string) => boolean
): boolean {
  return (
    features.controlUserEntitlements === true &&
    hasPermission("module_system:user:create") &&
    hasPermission("module_control:user_grant:update")
  );
}

/** Stale menu snapshots must not expose modules disabled by the current assembly. */
export function filterControlMenus(menus: MenuTable[], features: Features): MenuTable[] {
  return menus.flatMap((menu) => {
    const component = (menu.component_path || "").replace(/^\/+/, "");
    const isControl =
      component.startsWith("module_control/") || menu.permission?.startsWith("module_control:");
    if (isControl && features.appPortal !== true) return [];
    if (
      component.startsWith("module_control/user-grant/") &&
      features.controlUserEntitlements !== true
    )
      return [];
    return [
      {
        ...menu,
        ...(menu.children ? { children: filterControlMenus(menu.children, features) } : {}),
      },
    ];
  });
}
