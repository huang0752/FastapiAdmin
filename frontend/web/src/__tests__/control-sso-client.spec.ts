import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import AuthAPI, { type JWTOut } from "@/api/module_system/auth";
import UserAPI from "@/api/module_system/user";
import SystemConfigAPI from "@/api/module_system/config";
import { defaultAuthFeatures } from "@/config/assembly/default";
import { useAssemblyStore } from "@/store/modules/assembly.store";
import { useUserStore } from "@/store/modules/user.store";
import { useConfigStore } from "@/store/modules/config.store";
import { resolvePublicAuthCallbackAccess } from "@/router/beforeEach";
import { Auth } from "@utils";

vi.hoisted(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
});

vi.mock("@stores", async () => {
  const { createPinia } = await import("pinia");
  return {
    store: createPinia(),
    useDictStore: () => ({ clearDictData: vi.fn() }),
  };
});

vi.mock("@/mock/upgrade/changeLog", () => ({ upgradeLogList: { value: [] } }));
vi.mock("@/hooks/core/useThemePreset", () => ({
  applyPreset: vi.fn(),
  resolveAndApplyPreset: vi.fn(),
}));

const source = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

describe("control SSO client", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("is disabled by default and exposes the local exchange API", () => {
    expect(defaultAuthFeatures.controlSso).toBe(false);
    expect(AuthAPI.controlExchange).toBeDefined();
  });

  it("normalizes a legacy public-config payload fail closed", async () => {
    vi.spyOn(SystemConfigAPI, "getPublicConfigInfo").mockResolvedValue({
      data: {
        data: {
          assembly: null,
          authFeatures: {
            register: true,
          },
        },
      },
    } as never);

    const assemblyStore = useAssemblyStore();
    await assemblyStore.loadPublicConfig();

    expect(assemblyStore.authFeatures.controlSso).toBe(false);
  });

  it("blocks a legacy-config callback before exchange or navigation side effects", async () => {
    vi.spyOn(SystemConfigAPI, "getPublicConfigInfo").mockResolvedValue({
      data: {
        data: {
          assembly: null,
          authFeatures: {
            register: true,
          },
        },
      },
    } as never);
    const exchange = vi.spyOn(AuthAPI, "controlExchange");
    const replace = vi.fn();

    const assemblyStore = useAssemblyStore();
    await assemblyStore.loadPublicConfig();
    const decision = resolvePublicAuthCallbackAccess(
      true,
      assemblyStore.authFeatures.controlSso
    );
    if (!decision) {
      await AuthAPI.controlExchange("legacy-launch-code");
      await replace("/");
    }

    expect(decision).toEqual({ name: "404", replace: true });
    expect(exchange).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("explicitly allows an enabled callback before the unauthenticated fallback", () => {
    expect(resolvePublicAuthCallbackAccess(true, true)).toBe(true);
  });

  it("establishes password and control sessions through the same ordered workflow", async () => {
    const events: string[] = [];
    const tokens: JWTOut = {
      access_token: "access",
      refresh_token: "refresh",
      token_type: "bearer",
      expires_in: 1800,
    };
    vi.spyOn(Auth, "setTokens").mockImplementation(() => events.push("tokens"));
    vi.spyOn(UserAPI, "getCurrentUserInfo").mockImplementation(async () => {
      events.push("current-user");
      return {
        data: {
          data: { id: 7, tenant_id: 3, roles: [], menus: [] },
        },
      } as never;
    });
    vi.spyOn(AuthAPI, "getTenants").mockImplementation(async () => {
      events.push("tenants");
      return { data: { data: [{ id: 3, name: "租户", code: "tenant" }] } } as never;
    });
    vi.spyOn(useConfigStore(), "getConfig").mockResolvedValue(undefined);

    const userStore = useUserStore();
    await userStore.establishSession(tokens, true);

    expect(events).toEqual(["tokens", "current-user", "tenants"]);
    expect(userStore.isLogin).toBe(true);
    expect(userStore.accessToken).toBe("access");
  });

  it("keeps password login on its existing network sequence when tenants are already returned", async () => {
    const events: string[] = [];
    const tokens: JWTOut = {
      access_token: "password-access",
      refresh_token: "password-refresh",
      token_type: "bearer",
      expires_in: 1800,
    };
    const initialTenants = [{ id: 3, name: "租户", code: "tenant" }];
    vi.spyOn(Auth, "setTokens").mockImplementation(() => events.push("tokens"));
    vi.spyOn(UserAPI, "getCurrentUserInfo").mockImplementation(async () => {
      events.push("current-user");
      return {
        data: {
          data: { id: 7, tenant_id: 3, roles: [], menus: [] },
        },
      } as never;
    });
    const getTenants = vi.spyOn(AuthAPI, "getTenants");
    vi.spyOn(useConfigStore(), "getConfig").mockImplementation(async () => {
      events.push("config");
    });

    const userStore = useUserStore();
    await userStore.establishSession(tokens, true, initialTenants);

    expect(getTenants).not.toHaveBeenCalled();
    expect(events).toEqual(["tokens", "current-user", "config"]);
    expect(userStore.tenantList).toEqual(initialTenants);
  });

  it("registers guarded callback routes without adding a login-page redirect", () => {
    const userStoreSource = source("src/store/modules/user.store.ts");
    const guardSource = source("src/router/beforeEach.ts");
    const routesSource = source("src/router/staticRoutes.ts");
    const loginSource = source("src/views/module_system/auth/login/index.vue");

    expect(userStoreSource).toContain("establishSession");
    expect(guardSource).toContain("authFeatures.controlSso");
    expect(routesSource).toContain("ControlSsoCallback");
    expect(routesSource).toContain("ControlSsoWaiting");
    expect(loginSource).not.toContain("controlExchange");
    expect(loginSource).not.toContain("ControlSsoCallback");
    expect(loginSource).not.toContain("统一登录");
  });

  it("callback exchanges once and sends users without menus or permissions to waiting", () => {
    const callbackSource = source("src/views/module_system/auth/control-callback/index.vue");
    const exchangeHelperPath = resolve(
      process.cwd(),
      "src/views/module_system/auth/control-callback/control-exchange.ts"
    );

    expect(existsSync(exchangeHelperPath)).toBe(true);
    const exchangeHelperSource = readFileSync(exchangeHelperPath, "utf8");
    expect(exchangeHelperSource).toContain("controlExchangePromises");
    expect(callbackSource).toContain("exchangeControlCodeOnce");
    expect(exchangeHelperSource.match(/AuthAPI\.controlExchange\(/g)).toHaveLength(1);
    expect(callbackSource).not.toContain("AuthAPI.controlExchange(");
    expect(callbackSource).toContain("userStore.establishSession");
    expect(callbackSource).toContain("userStore.routeList.length === 0");
    expect(callbackSource).toContain("userStore.prems.length === 0");
    expect(callbackSource).toContain('name: "ControlSsoWaiting"');
  });
});
