import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import AuthAPI, { type JWTOut } from "@/api/module_system/auth";
import UserAPI from "@/api/module_system/user";
import SystemConfigAPI from "@/api/module_system/config";
import ParamsAPI from "@/api/module_system/params";
import SiteAPI from "@/api/module_platform/site";
import SelfServiceAPI from "@/api/module_platform/self_service";
import { defaultAuthFeatures } from "@/config/assembly/default";
import { useAssemblyStore } from "@/store/modules/assembly.store";
import { useUserStore } from "@/store/modules/user.store";
import { useConfigStore } from "@/store/modules/config.store";
import { resolvePublicAuthCallbackAccess } from "@/router/beforeEach";
import { Auth } from "@utils";
import {
  consumeControlCodeExchange,
  exchangeControlCodeOnce,
} from "@/views/module_system/auth/control-callback/control-exchange";

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
    useMenuStore: () => ({
      removeAllDynamicRoutes: vi.fn(),
      setMenuList: vi.fn(),
    }),
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

  it("allows an explicit retry after an exchange request fails", async () => {
    const exchange = vi
      .spyOn(AuthAPI, "controlExchange")
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce({ data: { data: { access_token: "retried" } } } as never);

    await expect(exchangeControlCodeOnce("retryable-launch-code")).rejects.toThrow(
      "temporary failure"
    );
    await expect(exchangeControlCodeOnce("retryable-launch-code")).resolves.toMatchObject({
      data: { data: { access_token: "retried" } },
    });
    expect(exchange).toHaveBeenCalledTimes(2);
  });

  it("retains a settled successful exchange until the current session explicitly consumes it", async () => {
    let resolveExchange!: (value: unknown) => void;
    const pending = new Promise((resolve) => (resolveExchange = resolve));
    const exchange = vi
      .spyOn(AuthAPI, "controlExchange")
      .mockReturnValueOnce(pending as never)
      .mockResolvedValueOnce({ data: { data: { access_token: "second" } } } as never);

    const first = exchangeControlCodeOnce("settled-launch-code");
    const concurrent = exchangeControlCodeOnce("settled-launch-code");
    expect(exchange).toHaveBeenCalledTimes(1);
    resolveExchange({ data: { data: { access_token: "first" } } });
    await Promise.all([first, concurrent]);

    await exchangeControlCodeOnce("settled-launch-code");
    expect(exchange).toHaveBeenCalledTimes(1);
    consumeControlCodeExchange("settled-launch-code");
    await exchangeControlCodeOnce("settled-launch-code");
    expect(exchange).toHaveBeenCalledTimes(2);
  });

  it("does not apply late user, tenant, or config writes after the session fence closes", async () => {
    let current = true;
    let resolveUser!: (value: unknown) => void;
    const userResponse = new Promise((resolve) => (resolveUser = resolve));
    vi.spyOn(UserAPI, "getCurrentUserInfo").mockReturnValue(userResponse as never);
    const userStore = useUserStore();
    userStore.setRoute([{ id: 99, title: "old" }] as never);

    const userRequest = userStore.getUserInfo({ shouldApply: () => current });
    current = false;
    resolveUser({ data: { data: { id: 7, roles: [], menus: [{ id: 1, title: "late" }] } } });
    await userRequest;
    expect(userStore.routeList).toEqual([{ id: 99, title: "old" }]);

    current = true;
    let resolveTenants!: (value: unknown) => void;
    vi.spyOn(AuthAPI, "getTenants").mockReturnValue(
      new Promise((resolve) => (resolveTenants = resolve)) as never
    );
    const tenantRequest = userStore.fetchTenants({ shouldApply: () => current });
    current = false;
    resolveTenants({ data: { data: [{ id: 2, name: "late tenant", code: "late" }] } });
    await tenantRequest;
    expect(userStore.tenantList).toEqual([]);

    current = true;
    let resolveSelect!: (value: unknown) => void;
    vi.spyOn(AuthAPI, "selectTenant").mockReturnValue(
      new Promise((resolve) => (resolveSelect = resolve)) as never
    );
    const tokenWrite = vi.spyOn(Auth, "setTokens");
    const selectRequest = userStore.selectTenant(2, { shouldApply: () => current });
    current = false;
    resolveSelect({ data: { code: 200, data: { access_token: "late-token" } } });
    await selectRequest;
    expect(tokenWrite).not.toHaveBeenCalled();
    expect(userStore.currentTenant).toBeNull();
  });

  it("lets a current retry apply fresh config while an old guarded request is still fetching", async () => {
    let oldCurrent = true;
    let resolveOld!: (value: unknown) => void;
    let resolveFresh!: (value: unknown) => void;
    vi.spyOn(ParamsAPI, "getInitConfig")
      .mockReturnValueOnce(new Promise((resolve) => (resolveOld = resolve)) as never)
      .mockReturnValueOnce(new Promise((resolve) => (resolveFresh = resolve)) as never);
    vi.spyOn(SiteAPI, "getPublicConfig").mockResolvedValue({ data: { data: null } } as never);
    vi.spyOn(SelfServiceAPI, "getBrandConfig").mockResolvedValue({ data: { data: [] } } as never);
    const configStore = useConfigStore();

    const oldRequest = configStore.getConfig(true, 2, { shouldApply: () => oldCurrent });
    oldCurrent = false;
    const freshRequest = configStore.getConfig(true, 2, { shouldApply: () => true });
    resolveFresh({
      data: { data: [{ config_key: "site_name", config_value: "fresh config" }] },
    });
    await freshRequest;
    resolveOld({
      data: { data: [{ config_key: "site_name", config_value: "stale config" }] },
    });
    await oldRequest;

    expect(configStore.configData.site_name?.config_value).toBe("fresh config");
    expect(configStore.isConfigLoaded).toBe(true);
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
    const decision = resolvePublicAuthCallbackAccess(true, assemblyStore.authFeatures.controlSso);
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

  it("restores a saved tenant by switching the server session instead of only changing the label", async () => {
    localStorage.setItem("sys-last-tenant-id", "2");
    const tokens: JWTOut = {
      access_token: "platform-access",
      refresh_token: "refresh",
      token_type: "bearer",
      expires_in: 1800,
    };
    vi.spyOn(UserAPI, "getCurrentUserInfo")
      .mockResolvedValueOnce({
        data: { data: { id: 7, session_tenant_id: 1, roles: [], menus: [] } },
      } as never)
      .mockResolvedValueOnce({
        data: { data: { id: 7, session_tenant_id: 2, roles: [], menus: [] } },
      } as never);
    vi.spyOn(AuthAPI, "getTenants").mockResolvedValue({
      data: {
        data: [
          { id: 1, name: "平台租户", code: "system" },
          { id: 2, name: "测试租户", code: "test" },
        ],
      },
    } as never);
    const selectTenant = vi.spyOn(AuthAPI, "selectTenant").mockResolvedValue({
      data: { code: 200, data: { access_token: "tenant-access" } },
    } as never);
    vi.spyOn(useConfigStore(), "getConfig").mockResolvedValue(undefined);

    const userStore = useUserStore();
    await userStore.establishSession(tokens, true);

    expect(selectTenant).toHaveBeenCalledOnce();
    expect(selectTenant).toHaveBeenCalledWith(2);
    expect(userStore.currentTenant?.id).toBe(2);
    expect((userStore.info as any).session_tenant_id).toBe(2);
  });

  it("keeps the launch-code tenant instead of restoring a different saved tenant", async () => {
    localStorage.setItem("sys-last-tenant-id", "2");
    vi.spyOn(UserAPI, "getCurrentUserInfo").mockResolvedValue({
      data: { data: { id: 7, session_tenant_id: 1, roles: [], menus: [] } },
    } as never);
    vi.spyOn(AuthAPI, "getTenants").mockResolvedValue({
      data: {
        data: [
          { id: 1, name: "One", code: "one" },
          { id: 2, name: "Two", code: "two" },
        ],
      },
    } as never);
    const selectTenant = vi
      .spyOn(AuthAPI, "selectTenant")
      .mockRejectedValue(new Error("unexpected tenant switch"));
    vi.spyOn(useConfigStore(), "getConfig").mockResolvedValue(undefined);
    const userStore = useUserStore();
    await userStore.establishSession(
      { access_token: "access", refresh_token: "refresh", token_type: "bearer", expires_in: 1800 },
      false,
      [],
      { restoreSavedTenant: false }
    );
    expect(selectTenant).not.toHaveBeenCalled();
    expect(userStore.currentTenant?.id).toBe(1);
  });

  it("registers guarded callback routes without adding a login-page redirect", () => {
    const userStoreSource = source("src/store/modules/user.store.ts");
    const guardSource = source("src/router/beforeEach.ts");
    const routesSource = source("src/router/staticRoutes.ts");
    const loginSource = source("src/views/module_system/auth/login/index.vue");

    expect(userStoreSource).toContain("establishSession");
    expect(userStoreSource).toContain("routerUtils.resetDynamicRoutesSync()");
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
    const sessionHelperSource = source(
      "src/views/module_system/auth/control-callback/control-session.ts"
    );
    expect(exchangeHelperSource).toContain("controlExchangePromises");
    expect(callbackSource).toContain("exchangeControlCodeOnce");
    expect(exchangeHelperSource.match(/AuthAPI\.controlExchange\(/g)).toHaveLength(1);
    expect(callbackSource).not.toContain("AuthAPI.controlExchange(");
    expect(callbackSource).toContain("resumeControlExchange");
    expect(callbackSource).toContain("persistControlExchange");
    expect(sessionHelperSource).toContain("CONTROL_SSO_EXCHANGED_CODE_KEY");
    expect(sessionHelperSource).toContain("Auth.setTokens");
    expect(sessionHelperSource).toContain("Auth.getAccessToken()");
    expect(sessionHelperSource).toContain("sessionStorage.setItem");
    expect(callbackSource).toContain("userStore.establishSession");
    expect(callbackSource).toContain("userStore.routeList.length === 0");
    expect(callbackSource).not.toContain("userStore.prems.length === 0");
    expect(callbackSource).toContain('name: "ControlSsoWaiting"');
  });
});
