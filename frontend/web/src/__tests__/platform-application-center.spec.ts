import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
vi.mock("@/views/module_control/application/index.vue", () => ({
  default: { name: "ApplicationManagement", template: "<div>应用目录</div>" },
}));
vi.mock("@/views/module_control/tenant-application/index.vue", () => ({
  default: { name: "TenantApplications", props: ["ledger"], template: "<div>开通记录</div>" },
}));
import Center from "@/views/module_control/portal/PlatformApplicationCenter.vue";

describe("平台应用管理", () => {
  it("默认展示目录，切换开通账本时使用账本模式而非手动开通模式", async () => {
    const wrapper = mount(Center, {
      global: {
        stubs: {
          ElTabs: {
            name: "ElTabs",
            props: ["modelValue"],
            emits: ["update:modelValue"],
            template: "<div><slot /></div>",
          },
          ElTabPane: { props: ["label"], template: "<span>{{ label }}</span>" },
        },
      },
    });
    expect(wrapper.text()).toContain("全部应用");
    expect(wrapper.findComponent({ name: "ApplicationManagement" }).exists()).toBe(true);
    const tabs = wrapper.findComponent({ name: "ElTabs" });
    tabs.vm.$emit("update:modelValue", "provisions");
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent({ name: "TenantApplications" }).props("ledger")).toBe(true);
    expect(wrapper.findComponent({ name: "ApplicationManagement" }).exists()).toBe(false);
    tabs.vm.$emit("update:modelValue", "openings");
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent({ name: "TenantApplications" }).props("ledger")).toBe(false);
  });
});
