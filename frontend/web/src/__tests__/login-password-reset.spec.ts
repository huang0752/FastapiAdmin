import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick, reactive } from "vue";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import FaLoginAccountForm from "@/components/views/fa-login/forms/FaLoginAccountForm.vue";
import FaLoginForgetPanel from "@/components/views/fa-login/panels/FaLoginForgetPanel.vue";
import type { CaptchaInfo } from "@/api/module_system/auth";
import type { ForgetPasswordForm } from "@/api/module_system/user";

vi.mock("@/components/views/fa-login/widgets/FaLoginThirdPartySection.vue", () => ({
  default: {
    name: "FaLoginThirdPartySection",
    props: ["providers"],
    template:
      '<div data-test="oauth-section"><span v-for="provider in providers" :key="provider" :data-test="`oauth-${provider}`">{{ provider }}</span></div>',
  },
}));

const global = {
  directives: { ripple: {} },
  mocks: {
    $t: (key: string) => key,
  },
  stubs: {
    ElButton: { template: "<button><slot /></button>" },
    ElCheckbox: { template: "<label><slot /></label>" },
    ElDivider: { template: "<div><slot /></div>" },
    ElForm: {
      template: "<form><slot /></form>",
      methods: { validate: () => Promise.resolve(true), clearValidate: vi.fn() },
    },
    ElFormItem: { template: "<div><slot /></div>" },
    ElIcon: { template: "<i><slot /></i>" },
    ElImage: { template: "<img />" },
    ElInput: {
      template: '<label><input /><span>{{ placeholder }}</span><slot name="prefix" /></label>',
      props: ["modelValue", "placeholder"],
    },
    ElOption: { template: "<option><slot /></option>" },
    ElSelect: {
      template: "<label><span>{{ placeholder }}</span><select><slot /></select></label>",
      props: ["modelValue", "placeholder"],
    },
    ElTooltip: { template: "<div><slot /></div>" },
    FaLoginAuthLinkRow: {
      template: '<button data-test="auth-link" @click="$emit(\'link\')"><slot /></button>',
    },
    User: true,
    Lock: true,
    Message: true,
  },
};

describe("login password reset", () => {
  const captchaState: CaptchaInfo = { enable: false, key: "", img_base: "" };

  function mountAccountForm(overrides = {}) {
    return mount(FaLoginAccountForm, {
      global,
      props: {
        loginForm: { username: "", password: "", captcha: "", captcha_key: "", remember: true },
        "onUpdate:loginForm": vi.fn(),
        isPassing: false,
        "onUpdate:isPassing": vi.fn(),
        isClickPass: false,
        "onUpdate:isClickPass": vi.fn(),
        rules: {},
        captchaState,
        codeLoading: false,
        demoAccountKey: "super",
        accounts: [
          { key: "super", label: "super", username: "super", password: "123456", roles: [] },
        ],
        formKey: 1,
        isDark: false,
        dragVerifyTextColor: "#333",
        loading: false,
        showRemember: true,
        showForget: true,
        showMobileLogin: false,
        showQrLogin: false,
        showRegister: false,
        showDemoAccounts: false,
        oauthEnabled: false,
        oauthProviders: [],
        ...overrides,
      },
    });
  }

  it("hides optional login entries by default", () => {
    const wrapper = mountAccountForm();

    expect(wrapper.find('[data-test="oauth-section"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("login.mobileLogin");
    expect(wrapper.text()).not.toContain("login.qrLogin");
    expect(wrapper.find('[data-test="auth-link"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("login.quickSelectAccount");
  });

  it("renders configured OAuth providers only", () => {
    const wrapper = mountAccountForm({
      oauthEnabled: true,
      oauthProviders: ["github"],
    });

    expect(wrapper.find('[data-test="oauth-section"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="oauth-github"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="oauth-wechat"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="oauth-gitee"]').exists()).toBe(false);
  });

  it("renders enabled secondary login entries", () => {
    const wrapper = mountAccountForm({
      showMobileLogin: true,
      showQrLogin: true,
      showRegister: true,
      showDemoAccounts: true,
    });

    expect(wrapper.text()).toContain("login.mobileLogin");
    expect(wrapper.text()).toContain("login.qrLogin");
    expect(wrapper.find('[data-test="auth-link"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("login.quickSelectAccount");
  });

  it("does not render third-party login entry by default", () => {
    const wrapper = mountAccountForm();

    expect(wrapper.find('[data-test="oauth-section"]').exists()).toBe(false);
  });

  it("treats account form validation rejection as an expected invalid result", () => {
    const loginViewSource = readFileSync(
      resolve(process.cwd(), "src/views/module_system/auth/login/index.vue"),
      "utf8"
    );
    const handleSubmitSource = loginViewSource.slice(
      loginViewSource.indexOf("const handleSubmit = async () =>"),
      loginViewSource.indexOf("async function submitRegister()")
    );

    expect(handleSubmitSource).toContain(
      "const valid = await accountFormRef.value.validate?.().catch(() => false);"
    );
    expect(handleSubmitSource.indexOf("const valid =")).toBeLessThan(
      handleSubmitSource.indexOf("try {")
    );
  });

  it("renders email and code fields for reset password", async () => {
    const forgetForm = reactive<ForgetPasswordForm>({
      username: "",
      email: "",
      code: "",
      new_password: "",
      confirmPassword: "",
    });
    const wrapper = mount(FaLoginForgetPanel, {
      global,
      props: {
        forgetForm,
        "onUpdate:forgetForm": vi.fn(),
        forgetRules: {},
        formKey: 1,
        forgetLoading: false,
        codeSending: false,
        codeCountdown: 0,
      },
    });

    await nextTick();

    expect(wrapper.text()).toContain("forgetPassword.placeholder.email");
    expect(wrapper.text()).toContain("forgetPassword.placeholder.code");
    expect(wrapper.text()).toContain("forgetPassword.sendCode");
  });
});
