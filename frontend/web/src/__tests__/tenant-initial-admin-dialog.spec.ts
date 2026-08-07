import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import TenantInitialAdminDialog from "@/views/module_platform/tenant/TenantInitialAdminDialog.vue";

const writeText = vi.fn(() => Promise.resolve());

Object.defineProperty(navigator, "clipboard", {
  configurable: true,
  value: { writeText },
});

const global = {
  stubs: {
    FaDialog: {
      name: "FaDialog",
      props: ["modelValue", "title", "closeOnClickModal", "closeOnPressEscape", "beforeClose"],
      template:
        '<section v-if="modelValue" data-test="dialog"><slot /><footer><slot name="footer" /></footer></section>',
    },
    ElAlert: { template: '<div data-test="alert"><slot />{{ title }}</div>', props: ["title"] },
    ElButton: {
      template: '<button :disabled="disabled"><slot /></button>',
      props: ["disabled", "type"],
    },
    ElCheckbox: {
      template:
        '<label><input data-test="acknowledge" type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" /><slot /></label>',
      props: ["modelValue"],
    },
  },
};

function mountDialog() {
  return mount(TenantInitialAdminDialog, {
    global,
    props: {
      modelValue: true,
      tenantName: "演示租户",
      credentials: { username: "demo_admin", password: "Temp#123456" },
    },
  });
}

beforeEach(() => {
  writeText.mockClear();
});

describe("tenant initial administrator dialog", () => {
  it("connects the create response to the one-time credentials dialog", () => {
    const tenantViewSource = readFileSync(
      resolve(process.cwd(), "src/views/module_platform/tenant/index.vue"),
      "utf8"
    );

    expect(tenantViewSource).toContain("<TenantInitialAdminDialog");
    expect(tenantViewSource).toContain(
      "const createResponse = await TenantAPI.createTenant(payload);"
    );
    expect(tenantViewSource).toContain(
      "const credentials = extractTenantInitialAdmin(createResponse);"
    );
    expect(tenantViewSource).toContain("initialAdminCredentials.value = credentials;");
    expect(tenantViewSource).toContain("initialAdminCredentials.value = null;");
  });

  it("shows the one-time credentials and prevents accidental dismissal", () => {
    const wrapper = mountDialog();
    const dialog = wrapper.findComponent({ name: "FaDialog" });

    expect(wrapper.text()).toContain("演示租户");
    expect(wrapper.text()).toContain("demo_admin");
    expect(wrapper.text()).toContain("Temp#123456");
    expect(wrapper.text()).toContain("仅显示一次");
    expect(dialog.props("closeOnClickModal")).toBe(false);
    expect(dialog.props("closeOnPressEscape")).toBe(false);
    expect(wrapper.get('[data-test="finish"]').attributes("disabled")).toBeDefined();
  });

  it("copies the account, password and complete handoff text", async () => {
    const wrapper = mountDialog();

    await wrapper.get('[data-test="copy-username"]').trigger("click");
    await wrapper.get('[data-test="copy-password"]').trigger("click");
    await wrapper.get('[data-test="copy-all"]').trigger("click");

    expect(writeText).toHaveBeenNthCalledWith(1, "demo_admin");
    expect(writeText).toHaveBeenNthCalledWith(2, "Temp#123456");
    expect(writeText).toHaveBeenNthCalledWith(
      3,
      "租户：演示租户\n登录账号：demo_admin\n临时密码：Temp#123456\n请首次登录后立即修改密码。"
    );
  });

  it("closes only after the operator confirms the credentials were saved", async () => {
    const wrapper = mountDialog();

    await wrapper.get('[data-test="finish"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();

    await wrapper.get('[data-test="acknowledge"]').setValue(true);
    await nextTick();
    await wrapper.get('[data-test="finish"]').trigger("click");

    expect(wrapper.emitted("update:modelValue")).toEqual([[false]]);
  });
});
