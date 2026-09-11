import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, nextTick } from "vue";
import {
  createAuthorizationReloader,
  reloadAuthorization,
} from "@/views/module_system/auth/control-waiting/retry";
import ControlCallback from "@/views/module_system/auth/control-callback/index.vue";
import { resolveSourceReturnUrl } from "@/views/module_system/auth/control-callback/control-return";

const mocks = vi.hoisted(() => ({
  establishSession: vi.fn(),
  exchange: vi.fn(),
  consumeExchange: vi.fn(),
  persist: vi.fn(),
  resume: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { code: "launch-code" } }),
  useRouter: () => ({ replace: mocks.replace, back: mocks.back }),
}));

vi.mock("@stores", () => ({
  useUserStore: () => ({
    establishSession: mocks.establishSession,
    routeList: [{ route_name: "TraceLedger" }],
    prems: [],
  }),
}));

vi.mock("@/views/module_system/auth/control-callback/control-exchange", () => ({
  exchangeControlCodeOnce: mocks.exchange,
  consumeControlCodeExchange: mocks.consumeExchange,
}));

vi.mock("@/views/module_system/auth/control-callback/control-session", () => ({
  persistControlExchange: mocks.persist,
  resumeControlExchange: mocks.resume,
}));

const ElCardStub = defineComponent({ template: "<section><slot /></section>" });
const ElButtonStub = defineComponent({
  emits: ["click"],
  template: "<button @click=\"$emit('click')\"><slot /></button>",
});

function createRetryDependencies(menus: unknown[]) {
  return {
    userStore: {
      getUserInfo: vi.fn().mockImplementation(async function (this: { routeList: unknown[] }) {
        this.routeList = menus;
      }),
      routeList: [] as unknown[],
      prems: [] as string[],
      clearAuthorizationSnapshot: vi.fn().mockImplementation(function (this: {
        routeList: unknown[];
        prems: string[];
      }) {
        this.routeList = [];
        this.prems = [];
      }),
    },
    routerUtils: {
      resetDynamicRoutesSync: vi.fn(),
      rebuildDynamicRoutesFromCurrentUser: vi.fn().mockResolvedValue(undefined),
    },
    router: { replace: vi.fn().mockResolvedValue(undefined) },
  };
}

describe("control entitlement recheck", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    mocks.resume.mockReturnValue(null);
    mocks.exchange.mockResolvedValue({
      data: {
        data: {
          access_token: "access",
          refresh_token: "refresh",
          token_type: "bearer",
          expires_in: 1800,
        },
      },
    });
    mocks.establishSession.mockResolvedValue(undefined);
    mocks.replace.mockResolvedValue(undefined);
    Object.defineProperty(document, "referrer", { configurable: true, value: "" });
  });

  it("rechecks current user and rebuilds routes without top-level reload", async () => {
    const deps = createRetryDependencies([{ name: "TraceLedger", path: "/trace/ledger" }]);

    const result = await reloadAuthorization(deps);

    expect(deps.userStore.getUserInfo).toHaveBeenCalledTimes(1);
    expect(deps.routerUtils.resetDynamicRoutesSync).toHaveBeenCalledTimes(1);
    expect(deps.routerUtils.rebuildDynamicRoutesFromCurrentUser).toHaveBeenCalledTimes(1);
    expect(deps.router.replace).toHaveBeenCalledWith("/");
    expect(result).toEqual({ status: "authorized" });
  });

  it("keeps waiting when the fresh response still has no effective menu", async () => {
    const deps = createRetryDependencies([]);

    const result = await reloadAuthorization(deps);

    expect(result).toEqual({ status: "pending" });
    expect(deps.routerUtils.rebuildDynamicRoutesFromCurrentUser).not.toHaveBeenCalled();
    expect(deps.router.replace).not.toHaveBeenCalledWith("/");
  });

  it("keeps waiting when only permissions remain without an effective route", async () => {
    const deps = createRetryDependencies([]);
    deps.userStore.prems = ["trace:ledger:list"];

    const result = await reloadAuthorization(deps);

    expect(result).toEqual({ status: "pending" });
    expect(deps.routerUtils.rebuildDynamicRoutesFromCurrentUser).not.toHaveBeenCalled();
  });

  it("clears an old authorization snapshot before a failed fresh request", async () => {
    const deps = createRetryDependencies([]);
    deps.userStore.routeList = [{ name: "StaleRoute" }];
    deps.userStore.prems = ["stale:permission"];
    deps.userStore.getUserInfo.mockRejectedValue(new Error("network failure"));

    await expect(reloadAuthorization(deps)).rejects.toThrow("network failure");

    expect(deps.userStore.clearAuthorizationSnapshot).toHaveBeenCalledTimes(1);
    expect(deps.userStore.routeList).toEqual([]);
    expect(deps.userStore.prems).toEqual([]);
    expect(deps.routerUtils.rebuildDynamicRoutesFromCurrentUser).not.toHaveBeenCalled();
  });

  it("coalesces repeated authorization clicks into one request", async () => {
    let resolveUserInfo!: () => void;
    const deps = createRetryDependencies([{ name: "TraceLedger" }]);
    deps.userStore.getUserInfo.mockImplementation(
      () => new Promise<void>((resolve) => (resolveUserInfo = resolve))
    );
    const retry = createAuthorizationReloader(deps);

    const first = retry();
    const second = retry();

    expect(deps.userStore.getUserInfo).toHaveBeenCalledTimes(1);
    resolveUserInfo();
    await Promise.all([first, second]);
  });

  it("shows retry and return controls after the callback times out", async () => {
    vi.useFakeTimers();
    mocks.exchange.mockReturnValue(new Promise(() => {}));
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: {
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElIcon: true,
          Loading: true,
        },
      },
    });

    await vi.advanceTimersByTimeAsync(15_000);
    await flushPromises();

    expect(wrapper.text()).toContain("统一登录失败");
    expect(wrapper.text()).toContain("重新尝试");
    expect(wrapper.text()).toContain("返回登录");
    wrapper.unmount();
  });

  it("runs only one callback request for repeated retry clicks", async () => {
    mocks.exchange.mockRejectedValueOnce(new Error("first failure"));
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: {
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElIcon: true,
          Loading: true,
        },
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("重新尝试");

    mocks.exchange.mockReturnValue(new Promise(() => {}));
    const retryButton = wrapper.findAll("button").find((button) => button.text() === "重新尝试");
    await Promise.all([retryButton!.trigger("click"), retryButton!.trigger("click")]);
    await nextTick();

    expect(mocks.exchange).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it("continues one slow exchange after timeout and lets only the current retry establish session", async () => {
    vi.useFakeTimers();
    let resolveExchange!: (value: unknown) => void;
    const exchangeResponse = new Promise((resolve) => (resolveExchange = resolve));
    const networkExchange = vi.fn(() => exchangeResponse);
    const inFlightExchange = networkExchange();
    mocks.exchange.mockImplementation(() => inFlightExchange);
    const tokens = {
      access_token: "slow-access",
      refresh_token: "slow-refresh",
      token_type: "bearer",
      expires_in: 1800,
    };
    const sideEffects: string[] = [];
    mocks.establishSession.mockImplementation(
      async (_tokens, _remember, _tenants, options: { isCurrent: () => boolean }) => {
        if (options.isCurrent()) sideEffects.push("current");
      }
    );
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: {
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElIcon: true,
          Loading: true,
        },
      },
    });

    await vi.advanceTimersByTimeAsync(15_000);
    await flushPromises();
    const retryButton = wrapper.findAll("button").find((button) => button.text() === "重新尝试");
    await retryButton!.trigger("click");
    resolveExchange({ data: { data: tokens } });
    await flushPromises();

    expect(networkExchange).toHaveBeenCalledTimes(1);
    expect(mocks.establishSession).toHaveBeenCalledTimes(1);
    expect(sideEffects).toEqual(["current"]);
    expect(mocks.replace).toHaveBeenCalledWith("/");
    wrapper.unmount();
  });

  it("reuses a successful exchange that settles after timeout but before retry is clicked", async () => {
    vi.useFakeTimers();
    let resolveExchange!: (value: unknown) => void;
    const exchangeResponse = new Promise((resolve) => (resolveExchange = resolve));
    const networkExchange = vi.fn(() => exchangeResponse);
    const cachedExchange = networkExchange();
    mocks.exchange.mockImplementation(() => cachedExchange);
    const tokens = {
      access_token: "settled-access",
      refresh_token: "settled-refresh",
      token_type: "bearer",
      expires_in: 1800,
    };
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: {
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElIcon: true,
          Loading: true,
        },
      },
    });

    await vi.advanceTimersByTimeAsync(15_000);
    await flushPromises();
    resolveExchange({ data: { data: tokens } });
    await flushPromises();
    expect(mocks.establishSession).not.toHaveBeenCalled();
    const retryButton = wrapper.findAll("button").find((button) => button.text() === "重新尝试");
    await retryButton!.trigger("click");
    await flushPromises();

    expect(networkExchange).toHaveBeenCalledTimes(1);
    expect(mocks.establishSession).toHaveBeenCalledTimes(1);
    expect(mocks.consumeExchange).toHaveBeenCalledWith("launch-code");
    wrapper.unmount();
  });

  it("reuses persisted exchange tokens when session establishment fails before retry", async () => {
    const tokens = {
      access_token: "persisted-access",
      refresh_token: "persisted-refresh",
      token_type: "bearer",
      expires_in: 1800,
    };
    mocks.exchange.mockResolvedValue({ data: { data: tokens } });
    mocks.persist.mockImplementation(() => mocks.resume.mockReturnValue(tokens));
    mocks.establishSession
      .mockRejectedValueOnce(new Error("config failed"))
      .mockResolvedValueOnce(undefined);
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: {
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElIcon: true,
          Loading: true,
        },
      },
    });
    await flushPromises();
    const retryButton = wrapper.findAll("button").find((button) => button.text() === "重新尝试");
    await retryButton!.trigger("click");
    await flushPromises();

    expect(mocks.exchange).toHaveBeenCalledTimes(1);
    expect(mocks.establishSession).toHaveBeenCalledTimes(2);
    expect(mocks.consumeExchange).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("fences a late old session after timeout so it cannot overwrite the successful retry", async () => {
    vi.useFakeTimers();
    const tokens = {
      access_token: "access",
      refresh_token: "refresh",
      token_type: "bearer",
      expires_in: 1800,
    };
    mocks.exchange.mockResolvedValue({ data: { data: tokens } });
    mocks.persist.mockImplementation(() => mocks.resume.mockReturnValue(tokens));
    const writes: string[] = [];
    let finishOld!: () => void;
    mocks.establishSession
      .mockImplementationOnce(
        async (_tokens, _remember, _tenants, options: { isCurrent: () => boolean }) =>
          new Promise<void>((resolve) => {
            finishOld = () => {
              if (options.isCurrent()) writes.push("old");
              resolve();
            };
          })
      )
      .mockImplementationOnce(
        async (_tokens, _remember, _tenants, options: { isCurrent: () => boolean }) => {
          if (options.isCurrent()) writes.push("new");
        }
      );
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: {
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElIcon: true,
          Loading: true,
        },
      },
    });

    await flushPromises();
    await vi.advanceTimersByTimeAsync(15_000);
    await flushPromises();
    const retryButton = wrapper.findAll("button").find((button) => button.text() === "重新尝试");
    await retryButton!.trigger("click");
    await flushPromises();
    finishOld();
    await flushPromises();

    expect(mocks.establishSession).toHaveBeenCalledTimes(2);
    expect(writes).toEqual(["new"]);
    expect(mocks.replace).toHaveBeenCalledTimes(1);
    expect(mocks.replace).toHaveBeenCalledWith("/");
    wrapper.unmount();
  });

  it("does not establish a late session after leaving the callback", async () => {
    let resolveExchange!: (value: unknown) => void;
    mocks.exchange.mockReturnValue(
      new Promise((resolve) => {
        resolveExchange = resolve;
      })
    );
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: { ElCard: ElCardStub, ElButton: ElButtonStub, ElIcon: true, Loading: true },
      },
    });
    wrapper.unmount();
    resolveExchange({
      data: {
        data: {
          access_token: "late",
          refresh_token: "late",
          token_type: "bearer",
          expires_in: 1800,
        },
      },
    });
    await flushPromises();
    expect(mocks.establishSession).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("resolves only a safe cross-origin http(s) referrer as a generic source return URL", () => {
    expect(
      resolveSourceReturnUrl(
        "https://control.example.com/apps?tenant=1",
        "https://trace.example.com"
      )
    ).toBe("https://control.example.com/");
    expect(
      resolveSourceReturnUrl("https://trace.example.com/control", "https://trace.example.com")
    ).toBeNull();
    expect(
      resolveSourceReturnUrl("https://evil.example/phishing", "https://trace.example.com")
    ).toBe("https://evil.example/");
    expect(resolveSourceReturnUrl("javascript:alert(1)", "https://trace.example.com")).toBeNull();
    expect(resolveSourceReturnUrl("", "https://trace.example.com")).toBeNull();
  });

  it("labels any safe cross-origin referrer as a source page without claiming it is Control", async () => {
    Object.defineProperty(document, "referrer", {
      configurable: true,
      value: "https://evil.example/application",
    });
    mocks.exchange.mockRejectedValue(new Error("failed"));
    const wrapper = mount(ControlCallback, {
      global: {
        stubs: {
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElIcon: true,
          Loading: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("返回来源页");
    expect(wrapper.text()).not.toContain("返回中控");
    expect(wrapper.text()).not.toContain("返回登录");
    wrapper.unmount();
  });
});
