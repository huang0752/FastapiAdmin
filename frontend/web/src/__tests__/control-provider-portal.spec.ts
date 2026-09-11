import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { flushPromises, mount, shallowMount } from "@vue/test-utils";
import { nextTick } from "vue";
import { beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";

vi.hoisted(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi
      .fn()
      .mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  });
});
vi.mock("@/views/module_control/portal/PlatformApplicationCenter.vue", () => ({
  default: { name: "PlatformApplicationCenter", template: "<div>全部应用</div>" },
}));
vi.mock("@/mock/upgrade/changeLog", () => ({ upgradeLogList: { value: [] } }));

const requestMock = vi.hoisted(() =>
  vi.fn(async (config) => ({ data: { code: 200, data: config } }))
);
const portalUserStore = vi.hoisted(() => ({
  info: {
    is_superuser: false,
    session_tenant_id: 2,
  } as { is_superuser?: boolean; session_tenant_id?: number },
  currentTenant: { id: 2, name: "测试租户", code: "test" } as {
    id: number;
    name: string;
    code: string;
  } | null,
}));

vi.mock("@utils", () => ({ request: requestMock }));
vi.mock("@/store/modules/user.store", () => ({ useUserStore: () => portalUserStore }));
vi.mock("vue-router", () => ({ useRoute: () => ({ name: "ControlTenantApplication" }) }));
vi.mock("@/hooks/core/useAuth", () => ({ useAuth: () => ({ hasAuth: () => true }) }));
vi.mock("@/hooks/core/useCrudDialog", () => ({
  useCrudDialog: () => ({
    dialogVisible: { title: "", visible: false, type: "create" },
    openDialog: vi.fn(),
    closeDialog: vi.fn(),
  }),
}));
vi.mock("@/hooks/core/useTable", () => ({
  useTable: () => ({
    columns: [],
    columnChecks: [],
    data: [],
    loading: false,
    pagination: { current: 1, size: 10, total: 0 },
    getData: vi.fn(),
    handleSizeChange: vi.fn(),
    handleCurrentChange: vi.fn(),
    refreshData: vi.fn(),
  }),
}));

import ControlAPI, {
  type ApplicationListItem,
  type ClientSecretResult,
} from "@/api/module_control";
import ClientSecretDialog from "@/views/module_control/application/ClientSecretDialog.vue";
import PortalView from "@/views/module_control/portal/index.vue";
import TenantApplicationView from "@/views/module_control/tenant-application/index.vue";
import TenantAPI from "@/api/module_platform/tenant";

const source = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

describe("Control Provider 前端契约", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    requestMock.mockClear();
    portalUserStore.info = { is_superuser: false, session_tenant_id: 2 };
    portalUserStore.currentTenant = { id: 2, name: "测试租户", code: "test" };
  });

  it("为管理、授权、应用中心暴露准确的请求路径和方法", async () => {
    await ControlAPI.listApplications({ page_no: 1, page_size: 10, name: "仓储" });
    await ControlAPI.getApplication(8);
    await ControlAPI.createApplication({
      code: "wms",
      name: "仓储系统",
      base_url: "https://wms.example.com",
      callback_url: "https://wms.example.com/#/sso/callback",
      provisioning_url: "https://wms.example.com/api/v1/system/auth/control/tenant/provision",
      provisioning_enabled: true,
      provisioning_timeout_seconds: 10,
      entitlement_sync_url: null,
      entitlement_sync_enabled: false,
      entitlement_sync_timeout_seconds: 10,
      status: 0,
      sort: 10,
    });
    await ControlAPI.updateApplication(8, { name: "仓储中心" });
    await ControlAPI.deleteApplication(8);
    await ControlAPI.resetApplicationSecret(8);
    await ControlAPI.listTenantApplications({ page_no: 1, page_size: 100 });
    await ControlAPI.listAvailableTenantApplications();
    await ControlAPI.createTenantApplication({
      tenant_id: 12,
      application_id: 8,
      target_tenant_code: "tenant12",
      status: 0,
    });
    await ControlAPI.updateTenantApplication(21, { status: 1 });
    await ControlAPI.deleteTenantApplication(21);
    await ControlAPI.listGrantMembers(21);
    await ControlAPI.grantUser(21, 31);
    await ControlAPI.revokeUser(21, 31);
    await ControlAPI.listMyApplications();
    await ControlAPI.launchApplication("wms");

    expect(requestMock.mock.calls.map(([config]) => [config.method, config.url])).toEqual([
      ["get", "/control/applications"],
      ["get", "/control/applications/8"],
      ["post", "/control/applications"],
      ["put", "/control/applications/8"],
      ["delete", "/control/applications/8"],
      ["post", "/control/applications/8/reset-secret"],
      ["get", "/control/tenant-applications"],
      ["get", "/control/tenant-applications/available"],
      ["post", "/control/tenant-applications"],
      ["put", "/control/tenant-applications/21"],
      ["delete", "/control/tenant-applications/21"],
      ["get", "/control/tenant-applications/21/grants"],
      ["put", "/control/tenant-applications/21/grants/31"],
      ["delete", "/control/tenant-applications/21/grants/31"],
      ["get", "/control/portal/my-applications"],
      ["post", "/control/portal/applications/wms/launch"],
    ]);
  });

  it("列表类型不暴露密钥，创建和重置仅使用一次性密钥结果", () => {
    expectTypeOf<ApplicationListItem>().not.toHaveProperty("client_secret");
    expectTypeOf<ApplicationListItem>().not.toHaveProperty("client_secret_hash");
    expectTypeOf<ClientSecretResult>().toHaveProperty("client_secret").toBeString();
    expectTypeOf(ControlAPI.createApplication).returns.resolves.toMatchTypeOf<{
      data: ApiResponse<ClientSecretResult>;
    }>();
    expectTypeOf(ControlAPI.resetApplicationSecret).returns.resolves.toMatchTypeOf<{
      data: ApiResponse<ClientSecretResult>;
    }>();
  });

  it("一次性密钥弹窗关闭后清空内存中的明文", async () => {
    const wrapper = mount(ClientSecretDialog, {
      global: {
        stubs: {
          FaDialog: {
            props: ["modelValue"],
            template: '<section v-if="modelValue"><slot /></section>',
          },
          ElAlert: { template: "<div><slot /></div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElInput: { props: ["modelValue"], template: "<span>{{ modelValue }}</span>" },
        },
      },
    });

    wrapper.vm.show({ client_id: "client-wms", client_secret: "plain-secret-once" });
    await nextTick();
    expect(wrapper.text()).toContain("plain-secret-once");

    wrapper.vm.close();
    await nextTick();
    expect(wrapper.text()).not.toContain("plain-secret-once");

    wrapper.vm.show({ client_id: "client-wms", client_secret: "second-secret" });
    await nextTick();
    wrapper.unmount();
    expect(wrapper.vm.secretSnapshot()).toEqual({ client_id: "", client_secret: "" });
  });

  it("管理操作使用 Seed 中的 canonical 权限码", () => {
    const application = source("src/views/module_control/application/index.vue");
    const opening = source("src/views/module_control/tenant-application/index.vue");
    const grant = source("src/views/module_control/user-grant/index.vue");

    expect(application).toContain("v-auth=\"'module_control:application:create'\"");
    expect(application).toContain("module_control:application:reset_secret");
    expect(application).toContain("module_control:application_package:query");
    expect(opening).toContain("v-auth=\"'module_control:tenant_application:create'\"");
    expect(grant).toContain("module_control:user_grant:update");
    expect(grant).toContain("module_control:user_grant:delete");
    expect(grant).not.toContain("module_control:user_application_grant");
  });

  it("租户开通选项请求遵守后端每页最多 100 条的分页契约", async () => {
    const listTenants = vi.spyOn(TenantAPI, "listTenant").mockResolvedValue({
      data: {
        code: 200,
        data: { page_no: 1, page_size: 100, total: 0, has_next: false, items: [] },
      },
    } as never);
    const listApplications = vi.spyOn(ControlAPI, "listApplications").mockResolvedValue({
      data: {
        code: 200,
        data: { page_no: 1, page_size: 100, total: 0, has_next: false, items: [] },
      },
    } as never);

    shallowMount(TenantApplicationView, {
      global: {
        directives: { auth: () => undefined },
        stubs: {
          ElButton: true,
          ElCard: true,
          FaTableHeader: true,
          FaTable: true,
          FaDialog: true,
          FaForm: true,
        },
      },
    });
    await flushPromises();

    expect(listTenants).toHaveBeenCalledWith({ page_no: 1, page_size: 100 });
    expect(listApplications).toHaveBeenCalledWith({ page_no: 1, page_size: 100 });
  });

  it("租户开通创建态不使用伪 ID，并在请求前校验正整数 ID", () => {
    const opening = source("src/views/module_control/tenant-application/index.vue");
    expect(opening).not.toMatch(/tenant_id:\s*0/);
    expect(opening).not.toMatch(/application_id:\s*0/);
    expect(opening).toContain("isPositiveId(payload.tenant_id)");
    expect(opening).toContain("isPositiveId(payload.application_id)");
  });

  it("应用中心只采用后端签发的 redirect_url 跳转", () => {
    const portal = source("src/views/module_control/portal/index.vue");
    const launchFlow = portal.slice(
      portal.indexOf("async function launch"),
      portal.indexOf("onMounted")
    );
    expect(portal).toContain("await ControlAPI.launchApplication(app.code)");
    expect(portal).toContain("window.location.assign(url)");
    expect(portal).toContain("navigate(data.redirect_url)");
    expect(launchFlow).not.toMatch(/URLSearchParams|access_token|tenant_id/);
  });

  it("应用中心不显示装饰性大写英文和内部应用编码", () => {
    const portal = source("src/views/module_control/portal/index.vue");
    expect(portal).not.toContain("APPLICATION DIRECTORY");
    expect(portal).not.toContain("eyebrow");
    expect(portal).not.toContain("{{ app.code }}");
  });

  it("平台管理员切换到业务租户后明确显示代管模式", async () => {
    portalUserStore.info = { is_superuser: true, session_tenant_id: 2 };
    vi.spyOn(ControlAPI, "listMyApplications").mockResolvedValue({
      data: { code: 200, data: [] },
    } as never);

    const wrapper = shallowMount(PortalView, {
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          ElButton: true,
          ElAlert: {
            props: ["title"],
            template: '<div data-test="managed-alert">{{ title }}</div>',
          },
          ElAvatar: true,
          ElTag: true,
          ElEmpty: { props: ["description"], template: "<div>{{ description }}</div>" },
          FaSvgIcon: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.get('[data-test="managed-alert"]').text()).toContain("平台代管模式");
    expect(wrapper.text()).toContain("测试租户");
    expect(wrapper.text()).toContain("该租户「测试租户」尚未开通可用应用");
    expect(wrapper.text()).not.toContain("请联系租户管理员");
  });

  it("租户用户保留个人授权语义且不显示代管标识", async () => {
    vi.spyOn(ControlAPI, "listMyApplications").mockResolvedValue({
      data: { code: 200, data: [] },
    } as never);

    const wrapper = shallowMount(PortalView, {
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          ElButton: true,
          ElAlert: { props: ["title"], template: "<div>{{ title }}</div>" },
          ElAvatar: true,
          ElTag: true,
          ElEmpty: { props: ["description"], template: "<div>{{ description }}</div>" },
          FaSvgIcon: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("已开通且已向你授权");
    expect(wrapper.findComponent({ name: "PlatformApplicationCenter" }).exists()).toBe(false);
    expect(wrapper.text()).toContain("请联系租户管理员开通并授权");
    expect(wrapper.text()).not.toContain("平台代管模式");
  });

  it("平台超管默认展示全局应用管理而非个人授权列表", async () => {
    portalUserStore.info = { is_superuser: true, session_tenant_id: 1 };
    portalUserStore.currentTenant = { id: 1, name: "平台租户", code: "platform" };
    vi.spyOn(ControlAPI, "listMyApplications").mockResolvedValue({
      data: { code: 200, data: [] },
    } as never);

    const wrapper = shallowMount(PortalView, {
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          ElButton: true,
          ElAlert: { props: ["title"], template: "<div>{{ title }}</div>" },
          ElAvatar: true,
          ElTag: true,
          ElEmpty: { props: ["description"], template: "<div>{{ description }}</div>" },
          FaSvgIcon: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("平台管理视图");
    expect(wrapper.findComponent({ name: "PlatformApplicationCenter" }).exists()).toBe(true);
    expect(ControlAPI.listMyApplications).not.toHaveBeenCalled();
    expect(wrapper.text()).not.toContain("平台代管模式");
  });

  it("中控业务页不使用装饰性大写英文或内部编码", () => {
    const opening = source("src/views/module_control/tenant-application/index.vue");
    const grant = source("src/views/module_control/user-grant/index.vue");
    for (const text of [
      "PROVISION LEDGER",
      "TENANT ACCESS",
      "EXPLICIT ACCESS",
      "SYSTEM ONLINE",
      ">NEW<",
    ])
      expect(`${opening}${grant}`).not.toContain(text);
    expect(grant).not.toContain("opening.application_code");
  });

  it("启动应用时把服务端 redirect_url 原样交给浏览器导航", async () => {
    vi.spyOn(ControlAPI, "listMyApplications").mockResolvedValue({
      data: { code: 200, data: [] },
    } as never);
    vi.spyOn(ControlAPI, "launchApplication").mockResolvedValue({
      data: {
        code: 200,
        data: {
          redirect_url: "https://wms.example.com/#/sso/callback?code=once",
          expires_at: "2026-08-10T12:00:00Z",
        },
      },
    } as never);
    const navigate = vi.fn();
    const wrapper = shallowMount(PortalView, {
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          ElButton: true,
          ElAlert: true,
          ElAvatar: true,
          ElTag: true,
          ElEmpty: true,
          FaIcon: true,
        },
      },
    });

    await wrapper.vm.launch(
      {
        code: "wms",
        name: "仓储系统",
        base_url: "https://wms.example.com",
        status: 0,
        sort: 0,
        grant_id: 9,
        desired_state: "active",
        sync_status: "succeeded",
        sync_version: 1,
        launchable: true,
      },
      navigate
    );

    expect(ControlAPI.launchApplication).toHaveBeenCalledWith("wms");
    expect(navigate).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith("https://wms.example.com/#/sso/callback?code=once");
  });

  it("同步中和失败应用显示状态且不允许启动", async () => {
    vi.spyOn(ControlAPI, "listMyApplications").mockResolvedValue({
      data: {
        code: 200,
        data: [
          {
            code: "wms",
            name: "仓储系统",
            base_url: "https://wms.example.com",
            status: 0,
            sort: 0,
            grant_id: 9,
            desired_state: "active",
            sync_status: "processing",
            sync_version: 2,
            launchable: false,
          },
        ],
      },
    } as never);
    const launch = vi.spyOn(ControlAPI, "launchApplication");
    const wrapper = shallowMount(PortalView, {
      global: {
        directives: { auth: () => undefined, loading: () => undefined },
        stubs: {
          ElButton: true,
          ElAlert: true,
          ElAvatar: true,
          ElTag: { props: ["type"], template: "<span><slot /></span>" },
          ElEmpty: true,
          FaSvgIcon: true,
        },
      },
    });
    await flushPromises();

    const card = wrapper.find("button.application-card");
    expect(card.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("同步中");
    await card.trigger("click");
    expect(launch).not.toHaveBeenCalled();
  });
});
