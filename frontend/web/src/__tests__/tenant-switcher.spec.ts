import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import FaTenantSwitcher from "@/components/layouts/fa-header-bar/widgets/FaTenantSwitcher.vue";

const mocks = vi.hoisted(() => ({
  sequence: [] as string[],
  tenantList: {
    __v_isRef: true,
    value: [
      { id: 1, name: "租户一" },
      { id: 2, name: "租户二" },
    ],
  },
  currentTenant: {
    __v_isRef: true,
    value: { id: 1, name: "租户一" } as { id: number; name: string } | null,
  },
  userStore: {
    info: { id: 7, tenant_id: 1 },
    routeList: [{ path: "/tenant-one" }],
    prems: ["tenant-one:read"],
    hasGetRoute: true,
    searchHistory: [{ path: "/tenant-one" }],
    clearUserInfo: vi.fn(),
    selectTenant: vi.fn(),
  },
  resetDynamicRoutesSync: vi.fn(),
  clearAllWorktabs: vi.fn(),
  clearDictData: vi.fn(),
  clearNoticeData: vi.fn(),
  clearTableRequestCaches: vi.fn(),
  routerGo: vi.fn(),
  messageInfo: vi.fn(),
}));

vi.mock("pinia", () => ({
  storeToRefs: () => ({
    tenantList: mocks.tenantList,
    currentTenant: mocks.currentTenant,
  }),
}));

vi.mock("@stores", () => ({
  useUserStore: () => mocks.userStore,
  useWorktabStore: () => ({ clearAll: mocks.clearAllWorktabs }),
  useDictStore: () => ({ clearDictData: mocks.clearDictData }),
  useNoticeStore: () => ({ clearUserInfo: mocks.clearNoticeData }),
}));

vi.mock("@/router/beforeEach", () => ({
  resetDynamicRoutesSync: mocks.resetDynamicRoutesSync,
}));

vi.mock("@/router", () => ({
  router: { go: mocks.routerGo },
}));

vi.mock("@/hooks/core/useTable", () => ({
  clearTableRequestCaches: mocks.clearTableRequestCaches,
}));

vi.mock("element-plus", () => ({
  ElMessage: { info: mocks.messageInfo },
}));

vi.mock("@element-plus/icons-vue", () => ({
  Loading: { name: "Loading", template: "<span />" },
}));

type Deferred = {
  promise: Promise<void>;
  resolve: () => void;
  reject: (reason?: unknown) => void;
};

function deferred(): Deferred {
  let resolve!: () => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const ElDropdownStub = defineComponent({
  name: "ElDropdown",
  props: { disabled: Boolean },
  emits: ["command"],
  setup(props, { emit, slots }) {
    return () =>
      h(
        "button",
        {
          "data-test": "tenant-dropdown",
          disabled: props.disabled,
          onClick: () => emit("command", 2),
        },
        slots.default?.()
      );
  },
});

beforeEach(() => {
  mocks.sequence.splice(0);
  mocks.currentTenant.value = { id: 1, name: "租户一" };
  Object.assign(mocks.userStore, {
    info: { id: 7, tenant_id: 1 },
    routeList: [{ path: "/tenant-one" }],
    prems: ["tenant-one:read"],
    hasGetRoute: true,
    searchHistory: [{ path: "/tenant-one" }],
  });
  sessionStorage.setItem("iframeRoutes", "tenant-one-routes");

  mocks.resetDynamicRoutesSync.mockReset().mockImplementation(() => {
    mocks.sequence.push("routes");
  });
  mocks.userStore.clearUserInfo.mockReset().mockImplementation(() => {
    mocks.sequence.push("permissions");
    mocks.userStore.info = {} as { id: number; tenant_id: number };
    mocks.userStore.routeList = [];
    mocks.userStore.hasGetRoute = false;
  });
  mocks.clearAllWorktabs.mockReset().mockImplementation(() => {
    mocks.sequence.push("worktabs");
  });
  mocks.clearDictData.mockReset().mockImplementation(() => {
    mocks.sequence.push("dict");
  });
  mocks.clearNoticeData.mockReset().mockImplementation(() => {
    mocks.sequence.push("notice");
  });
  mocks.clearTableRequestCaches.mockReset().mockImplementation(() => {
    mocks.sequence.push("requests");
  });
  mocks.routerGo.mockReset().mockImplementation(() => {
    mocks.sequence.push("reload");
  });
  mocks.messageInfo.mockReset();
});

describe("FaTenantSwitcher tenant/session isolation", () => {
  it("clears old tenant state before activating the new session and reloads immediately", async () => {
    const activation = deferred();
    mocks.userStore.selectTenant.mockReset().mockImplementation(() => {
      mocks.sequence.push("activate");
      return activation.promise;
    });
    const timeoutSpy = vi
      .spyOn(window, "setTimeout")
      .mockImplementation((() => 0) as unknown as typeof window.setTimeout);
    const wrapper = mount(FaTenantSwitcher, {
      global: {
        stubs: {
          ElDropdown: ElDropdownStub,
          ElDropdownMenu: true,
          ElDropdownItem: true,
          ElTag: true,
          ElIcon: true,
          FaSvgIcon: true,
        },
      },
    });

    await wrapper.get('[data-test="tenant-dropdown"]').trigger("click");
    await flushPromises();

    const stateBeforeActivation = {
      sequence: [...mocks.sequence],
      routeList: [...mocks.userStore.routeList],
      prems: [...mocks.userStore.prems],
      hasGetRoute: mocks.userStore.hasGetRoute,
      searchHistory: [...mocks.userStore.searchHistory],
      iframeRoutes: sessionStorage.getItem("iframeRoutes"),
      disabled: wrapper.get('[data-test="tenant-dropdown"]').attributes("disabled"),
    };

    activation.resolve();
    await flushPromises();
    await nextTick();

    const hasLegacyReloadDelay = timeoutSpy.mock.calls.some(([, delay]) => delay === 200);

    timeoutSpy.mockRestore();
    wrapper.unmount();

    expect(stateBeforeActivation).toEqual({
      sequence: ["routes", "permissions", "worktabs", "dict", "notice", "requests", "activate"],
      routeList: [],
      prems: [],
      hasGetRoute: false,
      searchHistory: [],
      iframeRoutes: null,
      disabled: "",
    });
    expect(mocks.routerGo).toHaveBeenCalledWith(0);
    expect(mocks.sequence.at(-1)).toBe("reload");
    expect(hasLegacyReloadDelay).toBe(false);
  });

  it("reloads the existing session when tenant activation fails after state cleanup", async () => {
    mocks.userStore.selectTenant.mockReset().mockImplementation(async () => {
      mocks.sequence.push("activate");
      throw new Error("tenant switch failed");
    });
    const timeoutSpy = vi
      .spyOn(window, "setTimeout")
      .mockImplementation((() => 0) as unknown as typeof window.setTimeout);
    const wrapper = mount(FaTenantSwitcher, {
      global: {
        stubs: {
          ElDropdown: ElDropdownStub,
          ElDropdownMenu: true,
          ElDropdownItem: true,
          ElTag: true,
          ElIcon: true,
          FaSvgIcon: true,
        },
      },
    });

    await wrapper.get('[data-test="tenant-dropdown"]').trigger("click");
    await flushPromises();
    await nextTick();

    const hasLegacyReloadDelay = timeoutSpy.mock.calls.some(([, delay]) => delay === 200);
    const disabled = wrapper.get('[data-test="tenant-dropdown"]').attributes("disabled");
    timeoutSpy.mockRestore();
    wrapper.unmount();

    expect(mocks.sequence).toEqual([
      "routes",
      "permissions",
      "worktabs",
      "dict",
      "notice",
      "requests",
      "activate",
      "reload",
    ]);
    expect(mocks.routerGo).toHaveBeenCalledWith(0);
    expect(disabled).toBe("");
    expect(hasLegacyReloadDelay).toBe(false);
  });
});
