import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({
  listAvailableTenantApplications: vi.fn(),
  listGrantMembers: vi.fn(),
}));
vi.mock("@/api/module_control", () => ({ default: api }));
vi.mock("@/hooks/core/useAuth", () => ({ useAuth: () => ({ hasAuth: () => true }) }));
vi.mock("@/hooks/core/useConfirm", () => ({ confirmAction: vi.fn() }));
vi.mock("@utils", () => ({ renderTableOperationCell: vi.fn() }));
import Page from "@/views/module_control/user-grant/index.vue";
afterEach(() => vi.useRealTimers());
it("同步中自动刷新，完成后停止轮询", async () => {
  vi.useFakeTimers();
  api.listAvailableTenantApplications.mockResolvedValue({
    data: { data: [{ tenant_application_id: 1 }] },
  });
  api.listGrantMembers
    .mockResolvedValueOnce({ data: { data: [{ user_id: 1, sync_status: "pending" }] } })
    .mockResolvedValue({ data: { data: [{ user_id: 1, sync_status: "succeeded" }] } });
  const wrapper = mount(Page, {
    global: {
      stubs: {
        ElCard: { template: "<div><slot /></div>" },
        FaTable: true,
        FaTableHeader: true,
        ElSelect: true,
        ElOption: true,
        ElAlert: true,
      },
    },
  });
  await flushPromises();
  expect(api.listGrantMembers).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(3000);
  await flushPromises();
  expect(api.listGrantMembers).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(6000);
  expect(api.listGrantMembers).toHaveBeenCalledTimes(2);
  wrapper.unmount();
});
