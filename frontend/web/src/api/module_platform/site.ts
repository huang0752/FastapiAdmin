import { NO_AUTH_FLAG, request } from "@utils";

const API_PATH = "/platform/site";

export interface PublicSiteConfig {
  site_code: string;
  name: string;
  logo_url?: string | null;
  favicon?: string | null;
  login_bg?: string | null;
  copyright?: string | null;
  keep_record?: string | null;
  help_doc?: string | null;
  privacy?: string | null;
  clause?: string | null;
  status: number;
}

const SiteAPI = {
  /** 后端根据当前请求 Host 解析站点，不接收可枚举的租户标识。 */
  getPublicConfig() {
    return request<ApiResponse<PublicSiteConfig>>({
      url: `${API_PATH}/public/config`,
      method: "get",
      headers: {
        Authorization: NO_AUTH_FLAG,
      },
    });
  },

  listSites(query?: SitePageQuery) {
    return request<ApiResponse<PageResult<SiteTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailSite(id: number) {
    return request<ApiResponse<SiteTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createSite(body: SiteCreateForm) {
    return request<ApiResponse<SiteTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateSite(id: number, body: SiteUpdateForm) {
    return request<ApiResponse<SiteTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteSites(ids: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: ids,
    });
  },
};

export default SiteAPI;

export interface SiteDomain {
  host: string;
  is_primary: boolean;
}

function normalizeDomainHost(host: string): string {
  const value = host.trim();
  if (!value) return "";
  try {
    const parsed = new URL(value.includes("://") ? value : `http://${value}`);
    return parsed.hostname.replace(/\.$/, "").toLowerCase();
  } catch {
    return value.replace(/\.$/, "").toLowerCase();
  }
}

/** 与后端约束一致：域名唯一，且必须恰好有一个主域名。 */
export function validateSiteDomains(domains: SiteDomain[]): string | null {
  if (domains.length === 0) return "请至少配置一个域名";
  const hosts = domains.map((item) => normalizeDomainHost(item.host));
  if (hosts.some((host) => !host)) return "域名不能为空";
  if (new Set(hosts).size !== hosts.length) return "域名不能重复";
  if (domains.filter((item) => item.is_primary).length !== 1) {
    return "必须且只能设置一个主域名";
  }
  return null;
}

export interface SitePageQuery extends PageQuery {
  name?: string;
  code?: string;
  status?: number;
}

export interface SiteTable extends BaseType {
  site_code: string;
  name: string;
  domains: SiteDomain[];
  logo_url?: string | null;
  favicon?: string | null;
  login_bg?: string | null;
  copyright?: string | null;
  keep_record?: string | null;
  help_doc?: string | null;
  privacy?: string | null;
  clause?: string | null;
  status: number;
}

export interface SiteCreateForm {
  code: string;
  name: string;
  domains: SiteDomain[];
  logo_url?: string | null;
  favicon?: string | null;
  login_bg?: string | null;
  copyright?: string | null;
  keep_record?: string | null;
  help_doc?: string | null;
  privacy?: string | null;
  clause?: string | null;
  status?: number;
}

export type SiteUpdateForm = Partial<SiteCreateForm>;
