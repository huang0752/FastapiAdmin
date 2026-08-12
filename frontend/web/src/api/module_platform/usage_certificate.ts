import { request } from "@utils";

export interface UsageCertificatePreview {
  certificate_no: string;
  filename: string;
  generated_at: string;
  html: string;
}

export interface UsageCertificateItem {
  tenant_id: number;
  tenant_code: string;
  certificate_no: string;
  enterprise_name: string;
  social_credit_code: string;
  system_name: string;
  system_version: string;
  start_time: string;
  end_time: string;
  currently_valid: boolean;
  status_label: string;
  certificate_created_at: string;
}

export type UsageCertificatePublic = Omit<
  UsageCertificateItem,
  "tenant_id" | "tenant_code" | "certificate_created_at"
>;

const UsageCertificateAPI = {
  tenantPreview() {
    return request<ApiResponse<UsageCertificatePreview>>({
      url: "/platform/tenant/usage-certificate/preview",
      method: "get",
    });
  },
  tenantDownload() {
    return request<Blob>({
      url: "/platform/tenant/usage-certificate/download",
      method: "get",
      responseType: "blob",
    });
  },
  platformList(params: Record<string, unknown>) {
    return request<ApiResponse<PageResult<UsageCertificateItem>>>({
      url: "/platform/usage-certificate/list",
      method: "get",
      params,
    });
  },
  platformPreview(tenantId: number) {
    return request<ApiResponse<UsageCertificatePreview>>({
      url: `/platform/usage-certificate/${tenantId}/preview`,
      method: "get",
    });
  },
  platformDownload(tenantId: number) {
    return request<Blob>({
      url: `/platform/usage-certificate/${tenantId}/download`,
      method: "get",
      responseType: "blob",
    });
  },
  publicVerify(token: string) {
    return request<ApiResponse<UsageCertificatePublic>>({
      url: `/platform/public/usage-certificate/${encodeURIComponent(token)}`,
      method: "get",
    });
  },
};

export function saveCertificateBlob(blob: Blob, filename: string) {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}

export default UsageCertificateAPI;
