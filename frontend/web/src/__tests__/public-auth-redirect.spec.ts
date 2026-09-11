import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({
  route: { meta: { anonymousPublic: false }, fullPath: "/business/orders" },
  reset: vi.fn(async () => {}),
  push: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("@/router", () => ({ router: { currentRoute: { value: mocks.route }, push: mocks.push } }));
vi.mock("@stores", () => ({ useUserStore: () => ({ resetAllState: mocks.reset }) }));
vi.mock("element-plus", () => ({ ElNotification: mocks.notify, ElMessage: { error: vi.fn() } }));
import { redirectToLogin } from "@/utils/auth";
beforeEach(() => {
  vi.clearAllMocks();
  mocks.route.meta.anonymousPublic = false;
  mocks.reset.mockResolvedValue();
});
it("clears an expired session without redirecting or notifying on a public page", async () => {
  mocks.route.meta.anonymousPublic = true;
  await redirectToLogin();
  expect(mocks.reset).toHaveBeenCalledOnce();
  expect(mocks.push).not.toHaveBeenCalled();
  expect(mocks.notify).not.toHaveBeenCalled();
});
it("still redirects authenticated backend routes on session failure", async () => {
  await redirectToLogin();
  expect(mocks.push).toHaveBeenCalledWith("/login?redirect=%2Fbusiness%2Forders");
});
it("does not redirect if navigation reaches a public page during session cleanup", async () => {
  mocks.reset.mockImplementationOnce(async () => {
    mocks.route.meta.anonymousPublic = true;
  });
  await redirectToLogin();
  expect(mocks.push).not.toHaveBeenCalled();
});
