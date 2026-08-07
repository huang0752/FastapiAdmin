import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const requestMock = vi.hoisted(() => vi.fn());
const getInitConfigMock = vi.hoisted(() => vi.fn());

vi.mock("@utils", () => ({
  request: requestMock,
  NO_AUTH_FLAG: "NO_AUTH",
}));

vi.mock("@/api/module_system/params", () => ({
  default: {
    getInitConfig: getInitConfigMock,
  },
}));

vi.mock("@stores", async () => {
  const { createPinia } = await import("pinia");
  return { store: createPinia() };
});

vi.mock("@/utils/storage", () => ({
  StorageConfig: { LAST_TENANT_ID_KEY: "last-tenant-id" },
}));

import { useConfigStore } from "@/store/modules/config.store";
import SiteAPI from "@/api/module_platform/site";

describe("Host/Site public branding", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    requestMock.mockReset();
    getInitConfigMock.mockReset();
    getInitConfigMock.mockResolvedValue({ data: { data: [] } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the public site resolved from the current Host without a tenant id", async () => {
    requestMock.mockResolvedValue({
      data: {
        data: {
          site_code: "carbon",
          name: "能碳云",
          logo_url: "/carbon-logo.svg",
          favicon: "/carbon.ico",
          login_bg: "/carbon-bg.webp",
          copyright: "能碳云版权所有",
          keep_record: "ICP备案号",
          help_doc: "/help",
          privacy: "/privacy",
          clause: "/terms",
          status: 0,
        },
      },
    });

    const configStore = useConfigStore();
    await configStore.getConfig();

    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock).toHaveBeenCalledWith({
      url: "/platform/site/public/config",
      method: "get",
      headers: { Authorization: "NO_AUTH" },
    });
    expect(configStore.configData.tenant_name?.config_value).toBe("能碳云");
    expect(configStore.configData.tenant_logo?.config_value).toBe("/carbon-logo.svg");
    expect(configStore.configData.login_bg?.config_value).toBe("/carbon-bg.webp");
    expect(configStore.configData.favicon?.config_value).toBe("/carbon.ico");
  });

  it("starts system, site, and tenant configuration requests concurrently", async () => {
    let resolveSystem!: (value: any) => void;
    let resolveSite!: (value: any) => void;
    let resolveTenant!: (value: any) => void;
    getInitConfigMock.mockReturnValue(
      new Promise((resolve) => {
        resolveSystem = resolve;
      })
    );
    requestMock.mockImplementation(({ url }: { url: string }) => {
      if (url === "/platform/site/public/config") {
        return new Promise((resolve) => {
          resolveSite = resolve;
        });
      }
      if (url === "/platform/tenant/9/config") {
        return new Promise((resolve) => {
          resolveTenant = resolve;
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });

    const pending = useConfigStore().getConfig(false, 9);
    await Promise.resolve();

    expect(getInitConfigMock).toHaveBeenCalledOnce();
    expect(requestMock).toHaveBeenCalledTimes(2);

    resolveSystem({ data: { data: [] } });
    resolveSite({ data: { data: { site_code: "main", name: "站点" } } });
    resolveTenant({ data: { data: [] } });
    await pending;
  });

  it("clears stale public branding when the current Host has no configured site", async () => {
    vi.spyOn(Date, "now")
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(6_000)
      .mockReturnValue(6_000);
    requestMock
      .mockResolvedValueOnce({
        data: { data: { site_code: "carbon", name: "能碳云", logo_url: "/old.svg" } },
      })
      .mockRejectedValueOnce(new Error("site not found"));

    const configStore = useConfigStore();
    await configStore.getConfig();
    await configStore.getConfig(true);

    expect(configStore.configData.tenant_name).toBeUndefined();
    expect(configStore.configData.tenant_logo).toBeUndefined();
  });

  it("overlays authenticated tenant branding on the Host site baseline", async () => {
    requestMock
      .mockResolvedValueOnce({
        data: {
          data: {
            site_code: "carbon",
            name: "能碳云",
            logo_url: "/site-logo.svg",
            login_bg: "/site-bg.webp",
            status: 0,
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          data: [
            { config_key: "tenant_name", config_value: "甲方集团" },
            { config_key: "tenant_logo", config_value: "/tenant-logo.svg" },
          ],
        },
      });

    const configStore = useConfigStore();
    await configStore.getConfig(false, 9);

    expect(requestMock).toHaveBeenNthCalledWith(1, {
      url: "/platform/site/public/config",
      method: "get",
      headers: { Authorization: "NO_AUTH" },
    });
    expect(requestMock).toHaveBeenNthCalledWith(2, {
      url: "/platform/tenant/9/config",
      method: "get",
    });
    expect(configStore.configData.tenant_name?.config_value).toBe("甲方集团");
    expect(configStore.configData.tenant_logo?.config_value).toBe("/tenant-logo.svg");
    expect(configStore.configData.login_bg?.config_value).toBe("/site-bg.webp");
  });

  it("clears the previous tenant overlay when switching tenants", async () => {
    vi.spyOn(Date, "now")
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(6_000)
      .mockReturnValue(6_000);
    requestMock
      .mockResolvedValueOnce({
        data: { data: { site_code: "carbon", name: "能碳云", status: 0 } },
      })
      .mockResolvedValueOnce({
        data: {
          data: [
            { config_key: "tenant_name", config_value: "旧租户" },
            { config_key: "tenant_logo", config_value: "/old.svg" },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: { data: { site_code: "carbon", name: "能碳云", status: 0 } },
      })
      .mockResolvedValueOnce({
        data: { data: [{ config_key: "tenant_name", config_value: "新租户" }] },
      });

    const configStore = useConfigStore();
    await configStore.getConfig(false, 9);
    await configStore.getConfig(true, 10);

    expect(configStore.currentTenantConfigId).toBe(10);
    expect(configStore.configData.tenant_name?.config_value).toBe("新租户");
    expect(configStore.configData.tenant_logo).toBeUndefined();
  });

  it("keeps the authenticated tenant layer on an in-app refresh without a repeated id", async () => {
    vi.spyOn(Date, "now")
      .mockReturnValueOnce(0)
      .mockReturnValueOnce(6_000)
      .mockReturnValue(6_000);
    requestMock
      .mockResolvedValueOnce({
        data: { data: { site_code: "carbon", name: "能碳云", status: 0 } },
      })
      .mockResolvedValueOnce({
        data: { data: [{ config_key: "tenant_name", config_value: "甲方集团" }] },
      })
      .mockResolvedValueOnce({
        data: { data: { site_code: "carbon", name: "能碳云", status: 0 } },
      })
      .mockResolvedValueOnce({
        data: { data: [{ config_key: "tenant_name", config_value: "甲方集团新名称" }] },
      });

    const configStore = useConfigStore();
    await configStore.getConfig(false, 9);
    await configStore.getConfig(true);

    expect(requestMock).toHaveBeenNthCalledWith(4, {
      url: "/platform/tenant/9/config",
      method: "get",
    });
    expect(configStore.configData.tenant_name?.config_value).toBe("甲方集团新名称");
  });
});

describe("platform Site API", () => {
  beforeEach(() => {
    requestMock.mockReset().mockResolvedValue({ data: { data: {} } });
  });

  it("uses the platform Site CRUD routes", async () => {
    const body = {
      code: "carbon",
      name: "能碳云",
      domains: [{ host: "carbon.example.com", is_primary: true }],
      status: 0,
    };

    await SiteAPI.listSites({ page_no: 1, page_size: 10, name: "能碳" });
    await SiteAPI.detailSite(3);
    await SiteAPI.createSite(body);
    await SiteAPI.updateSite(3, { name: "能碳平台" });
    await SiteAPI.deleteSites([3, 4]);

    expect(requestMock.mock.calls).toEqual([
      [
        {
          url: "/platform/site/list",
          method: "get",
          params: { page_no: 1, page_size: 10, name: "能碳" },
        },
      ],
      [{ url: "/platform/site/detail/3", method: "get" }],
      [{ url: "/platform/site/create", method: "post", data: body }],
      [{ url: "/platform/site/update/3", method: "put", data: { name: "能碳平台" } }],
      [{ url: "/platform/site/delete", method: "delete", data: [3, 4] }],
    ]);
  });

  it("requires unique domains and exactly one primary domain before submit", async () => {
    const siteModule = await import("@/api/module_platform/site");
    const validate = (siteModule as any).validateSiteDomains;

    expect(validate).toBeTypeOf("function");
    expect(
      validate([
        { host: "Carbon.Example.com", is_primary: true },
        { host: "carbon.example.com", is_primary: false },
      ])
    ).toBe("域名不能重复");
    expect(validate([{ host: "a.example.com", is_primary: false }])).toBe(
      "必须且只能设置一个主域名"
    );
    expect(
      validate([
        { host: "a.example.com", is_primary: true },
        { host: "b.example.com", is_primary: false },
      ])
    ).toBeNull();
  });

  it("provides a Site management page with domain and brand editing", () => {
    const pagePath = resolve(process.cwd(), "src/views/module_platform/site/index.vue");
    expect(existsSync(pagePath)).toBe(true);
    if (!existsSync(pagePath)) return;

    const source = readFileSync(pagePath, "utf8");
    expect(source).toContain("SiteAPI.listSites");
    expect(source).toContain("validateSiteDomains");
    expect(source).toContain("addDomain");
    expect(source).toContain("setPrimaryDomain");
    expect(source).toContain("logo_url");
    expect(source).toContain("login_bg");
  });

  it("requires tenant and package editors to choose an owning Site", () => {
    const tenantPage = readFileSync(
      resolve(process.cwd(), "src/views/module_platform/tenant/index.vue"),
      "utf8"
    );
    const packagePage = readFileSync(
      resolve(process.cwd(), "src/views/module_platform/package/index.vue"),
      "utf8"
    );

    for (const source of [tenantPage, packagePage]) {
      expect(source).toContain('from "@/api/module_platform/site"');
      expect(source).toContain('key: "site_id"');
      expect(source).toContain("SiteAPI.listSites");
    }
  });
});
