import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { flushPromises, shallowMount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import ControlAPI from "@/api/module_control";
import ControlUserCreateDrawer from "@/views/module_system/user/components/ControlUserCreateDrawer.vue";
import UserGrantView from "@/views/module_control/user-grant/index.vue";

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

vi.mock("@/hooks/core/useAuth", () => ({ useAuth: () => ({ hasAuth: () => true }) }));
vi.mock("@stores", async () => {
  const { createPinia } = await import("pinia");
  return { store: createPinia(), useDictStore: () => ({ clearDictData: vi.fn() }) };
});
vi.mock("@/mock/upgrade/changeLog", () => ({ upgradeLogList: { value: [] } }));
vi.mock("@/hooks/core/useConfirm", () => ({ confirmAction: vi.fn().mockResolvedValue(true) }));
vi.mock("@/api/module_system/dept", () => ({
  default: { listDept: vi.fn().mockResolvedValue({ data: { data: [] } }) },
}));
vi.mock("@/api/module_system/position", () => ({
  default: { listPosition: vi.fn().mockResolvedValue({ data: { data: { items: [] } } }) },
}));

function sourceOrEmpty(path: string) {
  try {
    return readFileSync(resolve(process.cwd(), path), "utf8");
  } catch {
    return "";
  }
}

describe("Control 建用户与授权状态界面", () => {
  it("新增用户是两步流程且至少选择一个产品", () => {
    const drawer = sourceOrEmpty(
      "src/views/module_system/user/components/ControlUserCreateDrawer.vue"
    );

    expect(drawer).toContain("ElSteps");
    expect(drawer).toContain("用户信息");
    expect(drawer).toContain("产品授权");
    expect(drawer).toContain("请至少选择一个产品");
    expect(drawer).toContain("createControlUser");
    expect(drawer).not.toContain("role_ids");
  });

  it("创建后保持抽屉并逐项呈现同步、生效、失败和重试", () => {
    const drawer = sourceOrEmpty(
      "src/views/module_system/user/components/ControlUserCreateDrawer.vue"
    );
    const api = sourceOrEmpty("src/api/module_control/index.ts");

    expect(drawer).toContain("同步中");
    expect(drawer).toContain("已生效");
    expect(drawer).toContain("同步失败");
    expect(drawer).toContain("重试");
    expect(drawer).toContain("startPolling");
    expect(api).toContain("createControlUser");
    expect(api).toContain("retryUserGrant");
  });

  it("授权页明确区分五态并防止处理中重复授权", () => {
    const grantView = sourceOrEmpty("src/views/module_control/user-grant/index.vue");
    for (const label of ["未授权", "同步中", "已生效", "同步失败", "撤权同步中"]) {
      expect(grantView).toContain(label);
    }
    expect(grantView).toContain("sync_status");
    expect(grantView).toContain("retryUserGrant");
    expect(grantView).toContain("processing");
  });

  it("授权动作双击只提交一次且失败后释放逐行锁", async () => {
    vi.spyOn(ControlAPI, "listAvailableTenantApplications").mockResolvedValue({
      data: {
        data: [
          {
            tenant_application_id: 11,
            application_id: 1,
            application_code: "wms",
            application_name: "仓储系统",
            target_tenant_code: "demo",
            status: 0,
          },
        ],
      },
    } as never);
    vi.spyOn(ControlAPI, "listGrantMembers").mockResolvedValue({ data: { data: [] } } as never);
    let rejectGrant!: (reason?: unknown) => void;
    const grantRequest = new Promise((_, reject) => {
      rejectGrant = reject;
    });
    const grant = vi.spyOn(ControlAPI, "grantUser").mockReturnValue(grantRequest as never);
    const wrapper = shallowMount(UserGrantView, {
      global: {
        stubs: {
          ElCard: { template: "<section><slot /></section>" },
          ElSelect: true,
          ElOption: true,
          ElAlert: { props: ["title"], template: "<div>{{ title }}<slot /></div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElTag: true,
          ElEmpty: true,
          FaTableHeader: true,
          FaTable: true,
        },
      },
    });
    await flushPromises();
    const member = {
      user_id: 88,
      username: "buyer",
      name: "采购员",
      granted: false,
      sync_version: 0,
      launchable: false,
    };

    const [grantAction] = wrapper.vm.memberActions(member);
    expect(grantAction).toBeDefined();
    grantAction?.run();
    const duplicate = wrapper.vm.grant(member);
    expect(grant).toHaveBeenCalledTimes(1);
    expect(wrapper.vm.busyUserIds.has(88)).toBe(true);
    rejectGrant(new Error("network"));
    await Promise.allSettled([duplicate]);
    await flushPromises();
    expect(wrapper.vm.busyUserIds.has(88)).toBe(false);
    expect(wrapper.text()).toContain("授权提交失败");

    let rejectRevoke!: (reason?: unknown) => void;
    const revokeRequest = new Promise((_, reject) => {
      rejectRevoke = reject;
    });
    const revoke = vi.spyOn(ControlAPI, "revokeUser").mockReturnValue(revokeRequest as never);
    const activeMember = {
      ...member,
      granted: true,
      desired_state: "active" as const,
      sync_status: "succeeded" as const,
      launchable: true,
    };
    wrapper.vm
      .memberActions(activeMember)
      .find((action) => action.key === "revoke")
      ?.run();
    const revokeDuplicate = wrapper.vm.revoke(activeMember);
    await flushPromises();
    expect(revoke).toHaveBeenCalledTimes(1);
    rejectRevoke(new Error("network"));
    await Promise.allSettled([revokeDuplicate]);
    await flushPromises();
    expect(wrapper.vm.actionError).toContain("撤权提交失败");

    let rejectRetry!: (reason?: unknown) => void;
    const retryRequest = new Promise((_, reject) => {
      rejectRetry = reject;
    });
    const retry = vi.spyOn(ControlAPI, "retryUserGrant").mockReturnValue(retryRequest as never);
    const failedMember = {
      ...member,
      granted: true,
      desired_state: "active" as const,
      sync_status: "failed" as const,
    };
    wrapper.vm
      .memberActions(failedMember)
      .find((action) => action.key === "retry")
      ?.run();
    const retryDuplicate = wrapper.vm.retry(failedMember);
    expect(retry).toHaveBeenCalledTimes(1);
    rejectRetry(new Error("network"));
    await Promise.allSettled([retryDuplicate]);
    await flushPromises();
    expect(wrapper.vm.actionError).toContain("重试提交失败");
    expect(wrapper.vm.busyUserIds.has(88)).toBe(false);
  });

  it("授权目录加载失败时显示可重试错误而不是空数据", async () => {
    vi.spyOn(ControlAPI, "listAvailableTenantApplications").mockRejectedValue(new Error("network"));
    const wrapper = shallowMount(UserGrantView, {
      global: {
        stubs: {
          ElCard: { template: "<section><slot /></section>" },
          ElSelect: true,
          ElOption: true,
          ElAlert: { props: ["title"], template: "<div>{{ title }}<slot /></div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElTag: true,
          ElEmpty: true,
          FaTableHeader: true,
          FaTable: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("已开通产品加载失败");
    expect(wrapper.text()).toContain("重新加载");
  });

  it("抽屉提交和轮询均防重入且轮询失败有可见错误", async () => {
    vi.useFakeTimers();
    vi.spyOn(ControlAPI, "listAvailableTenantApplications").mockResolvedValue({
      data: { data: [] },
    } as never);
    let resolveCreate!: (value: unknown) => void;
    const createRequest = new Promise((resolve) => {
      resolveCreate = resolve;
    });
    const create = vi
      .spyOn(ControlAPI, "createControlUser")
      .mockReturnValue(createRequest as never);
    let rejectPoll!: (reason?: unknown) => void;
    const pollRequest = new Promise((_, reject) => {
      rejectPoll = reject;
    });
    const poll = vi.spyOn(ControlAPI, "listGrantMembers").mockReturnValue(pollRequest as never);
    const wrapper = shallowMount(ControlUserCreateDrawer, {
      props: { modelValue: false },
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          FaDrawer: { template: "<div><slot /><slot name='footer' /></div>" },
          ElSteps: true,
          ElStep: true,
          ElForm: true,
          ElFormItem: true,
          ElInput: true,
          ElRadioGroup: true,
          ElRadio: true,
          ElTreeSelect: true,
          ElSelect: true,
          ElOption: true,
          ElAlert: { props: ["title"], template: "<div>{{ title }}<slot /></div>" },
          ElButton: true,
          ElCheckboxGroup: true,
          ElCheckbox: true,
          ElEmpty: true,
          ElTag: true,
        },
      },
    });
    await wrapper.setProps({ modelValue: true });
    poll.mockClear();
    wrapper.vm.step = 1;
    wrapper.vm.selectedOpeningIds = [11];

    const first = wrapper.vm.submit();
    const duplicate = wrapper.vm.submit();
    expect(create).toHaveBeenCalledTimes(1);
    resolveCreate({
      data: {
        data: {
          user: { id: 99 },
          entitlements: [
            {
              id: 5,
              grant_id: 5,
              tenant_application_id: 11,
              tenant_id: 1,
              site_id: 1,
              user_id: 99,
              status: 0,
              desired_state: "active",
              sync_status: "processing",
              sync_version: 1,
              launchable: false,
            },
          ],
        },
      },
    });
    await Promise.all([first, duplicate]);
    await vi.advanceTimersByTimeAsync(6000);
    expect(poll).toHaveBeenCalledTimes(1);
    rejectPoll(new Error("network"));
    await flushPromises();
    expect(wrapper.text()).toContain("授权状态刷新失败");
    await vi.advanceTimersByTimeAsync(4000);
    expect(wrapper.vm.pollError).toContain("3/3");
    const callsAfterStop = poll.mock.calls.length;
    await vi.advanceTimersByTimeAsync(6000);
    expect(poll).toHaveBeenCalledTimes(callsAfterStop);
    vi.useRealTimers();
  });

  it("抽屉产品加载失败时保留错误和重新加载入口", async () => {
    vi.spyOn(ControlAPI, "listAvailableTenantApplications").mockRejectedValue(new Error("network"));
    const wrapper = shallowMount(ControlUserCreateDrawer, {
      props: { modelValue: false },
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          FaDrawer: { template: "<div><slot /><slot name='footer' /></div>" },
          ElSteps: true,
          ElStep: true,
          ElForm: true,
          ElFormItem: true,
          ElInput: true,
          ElRadioGroup: true,
          ElRadio: true,
          ElTreeSelect: true,
          ElSelect: true,
          ElOption: true,
          ElAlert: { props: ["title"], template: "<div>{{ title }}<slot /></div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElCheckboxGroup: true,
          ElCheckbox: true,
          ElEmpty: true,
          ElTag: true,
        },
      },
    });
    await wrapper.setProps({ modelValue: true });
    wrapper.vm.step = 1;
    await wrapper.vm.loadOpenings();
    await flushPromises();

    expect(wrapper.vm.openingsError).toContain("已开通产品加载失败");
    expect(wrapper.text()).toContain("重新加载");
    expect(wrapper.text()).not.toContain("当前租户暂无已开通产品");
  });

  it("抽屉提交会去掉空联系方式", async () => {
    vi.spyOn(ControlAPI, "listAvailableTenantApplications").mockResolvedValue({
      data: { data: [] },
    } as never);
    const create = vi.spyOn(ControlAPI, "createControlUser").mockResolvedValue({
      data: { data: { user: { id: 99 }, entitlements: [] } },
    } as never);
    const wrapper = shallowMount(ControlUserCreateDrawer, {
      props: { modelValue: true },
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          FaDrawer: { template: "<div><slot /><slot name='footer' /></div>" },
          ElSteps: true,
          ElStep: true,
          ElForm: true,
          ElFormItem: true,
          ElInput: true,
          ElRadioGroup: true,
          ElRadio: true,
          ElTreeSelect: true,
          ElSelect: true,
          ElOption: true,
          ElAlert: true,
          ElButton: true,
          ElCheckboxGroup: true,
          ElCheckbox: true,
          ElEmpty: true,
          ElTag: true,
        },
      },
    });
    wrapper.vm.user.username = "real_payload";
    wrapper.vm.user.password = "Control123";
    wrapper.vm.user.name = "真实用户";
    wrapper.vm.user.email = "   ";
    wrapper.vm.user.mobile = "";
    wrapper.vm.selectedOpeningIds = [11];
    await wrapper.vm.submit();

    const payload = create.mock.calls[0]?.[0];
    expect(payload?.user.email).toBeUndefined();
    expect(payload?.user.mobile).toBeUndefined();
  });
});
