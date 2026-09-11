import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";
import { shallowMount } from "@vue/test-utils";

const requestMock = vi.hoisted(() =>
  vi.fn(async (config) => ({ data: { code: 200, data: config } }))
);

vi.mock("@utils", () => ({ request: requestMock }));

import ControlAPI, {
  buildProvisionCreatePayload,
  resolveProvisionActions,
  resolveTenantCreateMode,
  type ApplicationPackageForm,
  type CreateTenantWithProvisions,
  type TenantProvisionListItem,
  type TenantProvisionSelection,
} from "@/api/module_control";
import ControlTenantProvisionWizard from "@/views/module_platform/tenant/ControlTenantProvisionWizard.vue";
import type { TenantCreateForm } from "@/api/module_platform/tenant";
import SiteAPI from "@/api/module_platform/site";
import PackageAPI from "@/api/module_platform/package";

const source = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

const tenant: TenantCreateForm = {
  name: "华东制造",
  code: "huadong01",
  site_id: 1,
  status: 0,
  unified_social_credit_code: "91350100M000100Y43",
};

describe("Control 租户自动开通前端契约", () => {
  beforeEach(() => requestMock.mockClear());

  it("只有 capability 和开户所需权限同时满足时进入中控向导", () => {
    const allProvisionPermissions = new Set([
      "module_platform:site:query",
      "module_package:package:query",
      "module_control:application:query",
      "module_control:application_package:query",
      "module_control:tenant_provision:create",
    ]);
    const hasAllProvisionPermissions = (permission: string) =>
      allProvisionPermissions.has(permission);

    expect(resolveTenantCreateMode({})).toBe("basic");
    expect(resolveTenantCreateMode({ tenantAutoProvisioning: false })).toBe("basic");
    expect(resolveTenantCreateMode({ tenantAutoProvisioning: true })).toBe("basic");
    expect(
      resolveTenantCreateMode(
        { tenantAutoProvisioning: true },
        (permission) =>
          permission !== "module_control:tenant_provision:create" &&
          hasAllProvisionPermissions(permission)
      )
    ).toBe("basic");
    expect(
      resolveTenantCreateMode({ tenantAutoProvisioning: true }, hasAllProvisionPermissions)
    ).toBe("control-wizard");

    const config = source("src/config/assembly/default.ts");
    const tenantView = source("src/views/module_platform/tenant/index.vue");
    expect(config).toContain("tenantAutoProvisioning: false");
    expect(tenantView).toContain('isFeatureEnabled("tenantAutoProvisioning", false)');
    expect(tenantView).toContain("resolveTenantCreateMode(");
    expect(tenantView).toContain("hasAuth");
    expect(tenantView).toContain("TenantAPI.createTenant(payload)");
    expect(requestMock).not.toHaveBeenCalled();
  });

  it("默认目标编码交给后端采用租户编码，仅显式覆盖时发送字段", () => {
    const selections: TenantProvisionSelection[] = [
      { application_id: 10, application_package_id: 101 },
    ];
    expect(buildProvisionCreatePayload(tenant, selections)).toEqual({
      tenant,
      applications: [{ application_id: 10, application_package_id: 101 }],
    });

    expect(
      buildProvisionCreatePayload(tenant, [
        {
          application_id: 10,
          application_package_id: 101,
          desired_target_tenant_code: "huadongwms",
        },
      ]).applications
    ).toEqual([
      {
        application_id: 10,
        application_package_id: 101,
        desired_target_tenant_code: "huadongwms",
      },
    ]);
  });

  it("application package 和 provision API 使用准确路径与方法", async () => {
    const packageForm: ApplicationPackageForm = {
      application_id: 10,
      code: "pro",
      name: "专业版",
      target_package_code: "pro",
      is_default: true,
      status: 0,
      sort: 10,
    };
    await ControlAPI.listApplicationPackages({ application_id: 10, page_no: 1, page_size: 100 });
    await ControlAPI.createApplicationPackage(packageForm);
    await ControlAPI.updateApplicationPackage(101, { name: "专业套餐" });
    await ControlAPI.deleteApplicationPackage(101);
    await ControlAPI.createTenantWithProvisions({
      tenant,
      applications: [{ application_id: 10, application_package_id: 101 }],
    });
    await ControlAPI.listTenantProvisions({ tenant_id: 20, page_no: 1, page_size: 10 });
    await ControlAPI.updateTenantProvision(201, {
      application_package_id: 102,
      desired_target_tenant_code: "huadongwms",
    });
    await ControlAPI.retryTenantProvision(201);
    await ControlAPI.reconcileTenantProvision(201);

    expect(requestMock.mock.calls.map(([config]) => [config.method, config.url])).toEqual([
      ["get", "/control/application-packages"],
      ["post", "/control/application-packages"],
      ["put", "/control/application-packages/101"],
      ["delete", "/control/application-packages/101"],
      ["post", "/control/tenants/provision"],
      ["get", "/control/tenant-provisions"],
      ["put", "/control/tenant-provisions/201"],
      ["post", "/control/tenant-provisions/201/retry"],
      ["post", "/control/tenant-provisions/201/reconcile"],
    ]);
  });

  it("typed API 公开创建结果和安全开通账本，不公开票据或目标响应", () => {
    expectTypeOf<CreateTenantWithProvisions>().toHaveProperty("tenant");
    expectTypeOf<CreateTenantWithProvisions>().toHaveProperty("applications");
    expectTypeOf<TenantProvisionListItem>().toHaveProperty("last_error_message");
    expectTypeOf<TenantProvisionListItem>().not.toHaveProperty("provision_code");
    expectTypeOf<TenantProvisionListItem>().not.toHaveProperty("target_response");
  });

  it("只有失败记录允许修改、重试和对账，成功记录只读", () => {
    expect(resolveProvisionActions("failed")).toEqual(["update", "retry", "reconcile"]);
    expect(resolveProvisionActions("pending")).toEqual([]);
    expect(resolveProvisionActions("processing")).toEqual([]);
    expect(resolveProvisionActions("succeeded")).toEqual([]);

    const operations = source("src/views/module_control/tenant-application/index.vue");
    expect(operations).toContain("ControlAPI.listTenantProvisions");
    expect(operations).toContain("last_error_message");
    expect(operations).toContain('provision.status === "failed"');
    expect(operations).toContain('provision.status === "succeeded"');
    expect(operations).toContain("loadAllPages");
    expect(operations).toContain("tenant_application_id");
    expect(operations).not.toContain("openingFor");
  });

  it("三步向导包含资料、产品套餐和确认，结果组件展示逐产品状态", () => {
    const wizard = source("src/views/module_platform/tenant/ControlTenantProvisionWizard.vue");
    const result = source("src/views/module_platform/tenant/TenantProvisionResult.vue");
    expect(wizard).toContain("租户资料");
    expect(wizard).toContain("产品与套餐");
    expect(wizard).toContain("确认创建");
    expect(wizard).toContain("desired_target_tenant_code");
    expect(wizard).toContain("buildProvisionCreatePayload");
    expect(result).toContain("attempt_count");
    expect(result).toContain("last_error_message");
  });

  it("三步向导逐步校验并以租户编码作为目标编码默认值", async () => {
    const wrapper = shallowMount(ControlTenantProvisionWizard, {
      props: { modelValue: false },
      global: {
        stubs: {
          FaDialog: true,
          ElStep: true,
          ElSteps: true,
          ElInput: true,
          ElFormItem: true,
          ElCol: true,
          ElOption: true,
          ElSelect: true,
          ElRow: true,
          ElDivider: true,
          ElForm: true,
          ElAlert: true,
          ElCheckbox: true,
          ElTableColumn: true,
          ElTable: true,
          ElDescriptionsItem: true,
          ElDescriptions: true,
          ElButton: true,
        },
        directives: { loading: () => undefined },
      },
    });

    expect(wrapper.vm.validateTenantStep()).toBe(false);
    Object.assign(wrapper.vm.tenant, tenant);
    expect(wrapper.vm.validateTenantStep()).toBe(false);
    wrapper.vm.tenant.package_id = 5;
    expect(wrapper.vm.validateTenantStep()).toBe(true);
    wrapper.vm.next();
    expect(wrapper.vm.step).toBe(1);

    wrapper.vm.selectedApplicationIds = [10];
    wrapper.vm.drafts[10] = {
      application_package_id: 101,
      desired_target_tenant_code: tenant.code,
    };
    expect(wrapper.vm.validateProductStep()).toBe(true);
    wrapper.vm.next();
    expect(wrapper.vm.step).toBe(2);

    await wrapper.vm.submit();
    const createRequest = requestMock.mock.calls.find(
      ([config]) => config.url === "/control/tenants/provision"
    )?.[0];
    expect(createRequest.data.tenant.code).toBe(tenant.code);
    expect(createRequest.data.applications).toEqual([
      { application_id: 10, application_package_id: 101 },
    ]);
    wrapper.unmount();
  });

  it("向导跨页加载全部站点、应用、平台套餐和应用套餐", async () => {
    const page = <T>(items: T[], pageNo: number, hasNext: boolean) => ({
      data: {
        code: 200,
        msg: "ok",
        status_code: 200,
        success: true,
        data: {
          items,
          total: hasNext ? items.length + 1 : items.length,
          page_no: pageNo,
          page_size: 1,
          has_next: hasNext,
        },
      },
    });
    vi.spyOn(SiteAPI, "listSites")
      .mockResolvedValueOnce(page([{ id: 1, name: "站点一" }], 1, true) as never)
      .mockResolvedValueOnce(page([{ id: 2, name: "站点二" }], 2, false) as never);
    vi.spyOn(ControlAPI, "listApplications")
      .mockResolvedValueOnce(
        page([{ id: 10, name: "WMS", status: 0, provisioning_enabled: true }], 1, true) as never
      )
      .mockResolvedValueOnce(
        page([{ id: 20, name: "MES", status: 0, provisioning_enabled: true }], 2, false) as never
      );
    vi.spyOn(ControlAPI, "listApplicationPackages").mockImplementation(
      async (query) =>
        page(
          [
            {
              id: Number(query?.application_id) * 100 + Number(query?.page_no),
              application_id: Number(query?.application_id),
              name: `套餐${query?.page_no}`,
              status: 0,
              is_default: query?.page_no === 1,
            },
          ],
          Number(query?.page_no),
          query?.page_no === 1
        ) as never
    );
    vi.spyOn(PackageAPI, "listPackage")
      .mockResolvedValueOnce(page([{ id: 101, name: "平台套餐一" }], 1, true) as never)
      .mockResolvedValueOnce(page([{ id: 102, name: "平台套餐二" }], 2, false) as never);

    const wrapper = shallowMount(ControlTenantProvisionWizard, {
      props: { modelValue: false },
      global: {
        stubs: {
          FaDialog: true,
          ElStep: true,
          ElSteps: true,
          ElInput: true,
          ElFormItem: true,
          ElCol: true,
          ElOption: true,
          ElSelect: true,
          ElRow: true,
          ElDivider: true,
          ElForm: true,
          ElAlert: true,
          ElCheckbox: true,
          ElTableColumn: true,
          ElTable: true,
          ElDescriptionsItem: true,
          ElDescriptions: true,
          ElButton: true,
        },
        directives: { loading: () => undefined },
      },
    });

    const vm = wrapper.vm as unknown as {
      loadInitialOptions: () => Promise<void>;
      loadCentralPackages: (siteId: number) => Promise<void>;
      sites: Array<{ id: number }>;
      applications: Array<{ id: number }>;
      packageOptions: Record<number, unknown[]>;
      centralPackages: Array<{ id: number }>;
    };
    await vm.loadInitialOptions();
    await vm.loadCentralPackages(1);

    expect(vm.sites.map((item) => item.id)).toEqual([1, 2]);
    expect(vm.applications.map((item) => item.id)).toEqual([10, 20]);
    expect(vm.packageOptions[10]).toHaveLength(2);
    expect(vm.packageOptions[20]).toHaveLength(2);
    expect(vm.centralPackages.map((item) => item.id)).toEqual([101, 102]);
    wrapper.unmount();
  });

  it.each([1, 2])("%i 个站点时只自动选择唯一合法站点", async (count) => {
    vi.spyOn(SiteAPI, "listSites").mockResolvedValue({
      data: {
        data: {
          items: Array.from({ length: count }, (_, i) => ({ id: i + 1, name: `站点${i + 1}` })),
          total: count,
        },
      },
    } as never);
    vi.spyOn(ControlAPI, "listApplications").mockResolvedValue({
      data: { data: { items: [], total: 0 } },
    } as never);
    const packages = vi
      .spyOn(PackageAPI, "listPackage")
      .mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never);
    packages.mockClear();
    const wrapper = shallowMount(ControlTenantProvisionWizard, {
      props: { modelValue: false },
      global: {
        stubs: { FaDialog: true, ElSteps: true, ElStep: true },
        directives: { loading: () => undefined },
      },
    });
    const vm = wrapper.vm as unknown as {
      loadInitialOptions: () => Promise<void>;
      tenant: { site_id: number };
    };
    await vm.loadInitialOptions();
    expect(vm.tenant.site_id).toBe(count === 1 ? 1 : 0);
    if (count === 1) expect(packages).toHaveBeenCalledWith(expect.objectContaining({ site_id: 1 }));
    else expect(packages).not.toHaveBeenCalled();
    wrapper.unmount();
    vi.restoreAllMocks();
  });

  it("应用管理包含开户配置并通过独立对话框维护套餐", () => {
    const application = source("src/views/module_control/application/index.vue");
    const packageDialog = source(
      "src/views/module_control/application/ApplicationPackageDialog.vue"
    );
    expect(application).toContain("provisioning_url");
    expect(application).toContain("provisioning_enabled");
    expect(application).toContain("provisioning_timeout_seconds");
    expect(application).toContain("ApplicationPackageDialog");
    expect(packageDialog).toContain("loadAllPages");
    expect(application).toContain('isFeatureEnabled("tenantAutoProvisioning", false)');
    expect(packageDialog).toContain("ControlAPI.listApplicationPackages");
    expect(packageDialog).toContain("ControlAPI.createApplicationPackage");
    expect(packageDialog).toContain("ControlAPI.updateApplicationPackage");
    expect(packageDialog).toContain("ControlAPI.deleteApplicationPackage");
  });
});
