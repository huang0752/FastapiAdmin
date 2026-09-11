import { describe, expect, it } from "vitest";
import { filterControlMenus, resolveControlUserCreate } from "@/config/assembly/controlFeatures";
import type { MenuTable } from "@/api/module_platform/menu";

const menus = [
  { id: 1, component_path: "module_control/portal/index" },
  { id: 2, component_path: "module_control/user-grant/index" },
  { id: 3, component_path: "module_system/user/index" },
] as MenuTable[];

describe("optional control capabilities", () => {
  it("keeps standalone menus when control capability is absent or false", () => {
    expect(filterControlMenus(menus, {}).map((menu) => menu.id)).toEqual([3]);
    expect(filterControlMenus(menus, { appPortal: false }).map((menu) => menu.id)).toEqual([3]);
  });
  it("enables control and entitlement pages independently of assembly name", () => {
    expect(filterControlMenus(menus, { appPortal: true }).map((menu) => menu.id)).toEqual([1, 3]);
    expect(filterControlMenus(menus, { appPortal: true, controlUserEntitlements: true })).toEqual(
      menus
    );
  });
  it("requires the capability and both permissions for the employee provisioning drawer", () => {
    expect(resolveControlUserCreate({}, () => true)).toBe(false);
    expect(resolveControlUserCreate({ controlUserEntitlements: true }, () => false)).toBe(false);
    expect(
      resolveControlUserCreate(
        { controlUserEntitlements: true },
        (permission) => permission === "module_system:user:create"
      )
    ).toBe(false);
    expect(resolveControlUserCreate({ controlUserEntitlements: true }, () => true)).toBe(true);
  });
});
