import { beforeEach, describe, expect, it, vi } from "vitest";

const requestMock = vi.hoisted(() => vi.fn(() => Promise.resolve({ data: {} })));

vi.mock("@utils", () => ({
  request: requestMock,
  NO_AUTH_FLAG: "NO_AUTH",
}));

import TenantAPI, {
  TENANT_MANUAL_STATUS_OPTIONS,
  TENANT_STATUS_META,
  TENANT_STATUS_OPTIONS,
  extractTenantInitialAdmin,
  resolveNextTenantManualStatus,
  type TenantBatchStatusForm,
} from "@/api/module_platform/tenant";

beforeEach(() => {
  requestMock.mockClear();
});

describe("tenant lifecycle status contract", () => {
  it("extracts the one-time initial administrator from the create response", () => {
    const credentials = { username: "demo_admin", password: "Temp#123456" };
    const response = {
      data: {
        code: 200,
        data: {
          id: 9,
          name: "演示租户",
          code: "demo",
          site_id: 1,
          initial_admin: credentials,
        },
        msg: "创建租户成功",
        status_code: 200,
        success: true,
      },
    };

    expect(extractTenantInitialAdmin(response)).toEqual(credentials);
  });

  it("describes every backend lifecycle status from active through archived", () => {
    expect(TENANT_STATUS_OPTIONS).toEqual([
      { label: "正常", value: 0 },
      { label: "宽限期", value: 1 },
      { label: "暂停", value: 2 },
      { label: "冻结", value: 3 },
      { label: "过期", value: 4 },
      { label: "归档", value: 5 },
    ]);
    expect(TENANT_STATUS_META[1].text).toBe("宽限期");
    expect(TENANT_STATUS_META[1].text).not.toContain("禁用");
  });

  it("allows manual status transitions only between active and suspended", () => {
    expect(TENANT_MANUAL_STATUS_OPTIONS).toEqual([
      { label: "正常", value: 0 },
      { label: "暂停", value: 2 },
    ]);
    expect(resolveNextTenantManualStatus(0)).toBe(2);
    expect(resolveNextTenantManualStatus(2)).toBe(0);
    expect([1, 3, 4, 5].map(resolveNextTenantManualStatus)).toEqual([null, null, null, null]);
  });

  it.each([0, 2] as const)(
    "sends an explicit active or suspended value for a row toggle: %s",
    async (status) => {
      await TenantAPI.toggleTenantStatus(9, status);

      expect(requestMock).toHaveBeenCalledWith({
        url: "/platform/tenant/status/batch",
        method: "patch",
        data: { ids: [9], status },
      });
    }
  );

  it.each([1, 3, 4, 5])("rejects lifecycle-only status %s from manual batch updates", (status) => {
    const invalidBody = { ids: [9], status } as unknown as TenantBatchStatusForm;
    expect(() => TenantAPI.batchTenantStatus(invalidBody)).toThrow(
      "租户手工状态仅支持正常(0)或暂停(2)"
    );
    expect(requestMock).not.toHaveBeenCalled();
  });
});
