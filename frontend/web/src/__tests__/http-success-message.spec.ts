import { describe, expect, it, vi, beforeEach } from "vitest";
const messages = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("element-plus", () => ({ ElMessage: messages }));
vi.mock("@/utils/auth", () => ({
  Auth: { getAccessToken: () => "", getTenantId: () => null },
  redirectToLogin: vi.fn(),
}));
vi.mock("@/locales", () => ({ $t: (key: string) => key }));
vi.mock("@/api/module_system/auth", () => ({ default: {} }));
import { request, type ExtendedRequestConfig } from "@/utils/http";
import { ResultEnum } from "@/enums/api/result.enum";
describe("HTTP success notification opt-out", () => {
  beforeEach(() => vi.clearAllMocks());
  it.each([false, undefined])(
    "honors showSuccessMessage=%s without changing response data",
    async (showSuccessMessage) => {
      const config: ExtendedRequestConfig = {
        url: "/notification-test",
        method: "post",

        showSuccessMessage,
        adapter: async (config) => ({
          config,
          status: 200,
          statusText: "OK",
          headers: {},
          data: { code: ResultEnum.SUCCESS, msg: "成功", data: { id: 1 } },
        }),
      };
      const result = await request(config);
      expect(result.data.data).toEqual({ id: 1 });
      expect(messages.success).toHaveBeenCalledTimes(showSuccessMessage === false ? 0 : 1);
    }
  );
});
