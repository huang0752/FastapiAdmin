<!-- 登录页：顶栏固定；仅插画列与表单区随布局切换 -->
<template>
  <div class="login-page-root flex h-screen w-full flex-col overflow-hidden">
    <FaLoginCenterBackdrop v-if="panelAlign === 'center'" viewport-fixed />
    <FaAuthTopBar v-model:panel-align="panelAlign" />

    <div
      class="login-auth-split relative z-1 flex min-h-0 flex-1 overflow-hidden"
      :class="`login-auth-split--${panelAlign}`"
    >
      <div
        v-if="panelAlign !== 'center'"
        class="login-auth-split__col login-auth-split__col--illustration"
      >
        <FaLoginLeftView hide-top-branding />
      </div>

      <div
        class="login-auth-split__col login-auth-split__col--form login-page-panel relative flex min-h-0 min-w-0 flex-col"
        :class="panelAlign === 'center' ? 'bg-transparent' : 'bg-(--el-bg-color-page)'"
      >
        <div
          class="login-page-panel__main relative z-1 flex min-h-0 flex-1 flex-col overflow-hidden px-5 pb-2 pt-14 md:px-10 md:pt-18"
        >
          <ElScrollbar>
            <div
              class="login-page-panel__scroll pb-6"
              :class="panelAlign === 'center' && 'login-page-panel__scroll--centered'"
            >
              <div
                class="login-panel-align-row flex w-full items-center justify-center max-sm:min-h-0"
                :class="
                  panelAlign === 'center'
                    ? 'min-h-0 flex-1 py-4'
                    : 'min-h-[min(720px,calc(100vh-13rem))]'
                "
              >
                <div class="auth-right-wrap">
                  <div class="form">
                    <div class="form-intro">
                      <h3 class="title">{{ panelTitle }}</h3>
                      <p class="sub-title">{{ panelSubTitle }}</p>
                    </div>

                    <template v-if="authPanel === 'login'">
                      <template v-if="loginFlowMode === 'account'">
                        <FaLoginAccountForm
                          ref="accountFormRef"
                          v-model:is-passing="isPassing"
                          v-model:is-click-pass="isClickPass"
                          v-model:login-form="loginForm"
                          :rules="rules"
                          :captcha-state="captchaState"
                          :code-loading="codeLoading"
                          :demo-account-key="demoAccountKey"
                          :accounts="accounts"
                          :form-key="formKey"
                          :is-dark="isDark"
                          :drag-verify-text-color="dragVerifyTextColor"
                          :loading="loading"
                          :show-remember="showRememberMe"
                          :show-forget="showForgotPassword"
                          :show-mobile-login="showMobileLogin"
                          :show-qr-login="showQrLogin"
                          :show-register="showRegister"
                          :show-demo-accounts="showDemoAccounts"
                          :oauth-enabled="oauthEnabled"
                          :oauth-providers="oauthProviders"
                          @submit="handleSubmit"
                          @setup-account="setupAccount"
                          @get-captcha="getCaptcha"
                          @open-mobile="openMobileLogin"
                          @open-qr="openQrLogin"
                          @forget="setAuthPanel('forget')"
                          @register="setAuthPanel('register')"
                          @oauth="startOAuthLogin"
                        />
                      </template>

                      <FaLoginMobilePanel
                        v-else-if="loginFlowMode === 'mobile'"
                        :show-register="showRegister"
                        @back="backToAccountLogin"
                        @register="setAuthPanel('register')"
                      />

                      <FaLoginQrPanel
                        v-else-if="loginFlowMode === 'qr'"
                        :show-register="showRegister"
                        @back="backToAccountLogin"
                        @register="setAuthPanel('register')"
                      />
                    </template>

                    <FaLoginRegisterPanel
                      v-else-if="authPanel === 'register' && showRegister"
                      ref="registerPanelRef"
                      v-model:register-agreement-read="registerAgreementRead"
                      v-model:register-form="registerForm"
                      :register-rules="registerRules"
                      :form-key="formKey"
                      :register-loading="registerLoading"
                      :show-email="true"
                      :user-agreement-href="userAgreementHref"
                      @submit="submitRegister"
                      @to-login="setAuthPanel('login')"
                    />

                    <FaLoginForgetPanel
                      v-else-if="authPanel === 'forget' && showForgotPassword"
                      ref="forgetPanelRef"
                      v-model:forget-form="forgetForm"
                      :forget-rules="forgetRules"
                      :form-key="formKey"
                      :forget-loading="forgetLoading"
                      :code-sending="forgetCodeSending"
                      :code-countdown="forgetCodeCountdown"
                      @submit="submitForget"
                      @send-code="sendForgetEmailCode"
                      @to-login="setAuthPanel('login')"
                    />
                  </div>
                </div>
              </div>
            </div>
          </ElScrollbar>
        </div>

        <footer
          class="login-page-footer login-page-footer--pinned shrink-0 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3"
          :class="panelAlign === 'center' && 'login-page-footer--floating-layout'"
        >
          <div class="login-footer-text text-sm">
            <div class="login-footer-row">
              <a
                :href="configStore.configData?.git_code?.config_value || '#'"
                target="_blank"
                rel="noopener noreferrer"
                class="login-page-footer__link"
              >
                {{ configStore.configData?.copyright?.config_value || "" }}
              </a>
            </div>
            <span class="login-page-footer__sep login-footer-sep-center">|</span>
            <div class="login-footer-row">
              <a
                :href="configStore.configData?.help_doc?.config_value || '#'"
                target="_blank"
                rel="noopener noreferrer"
                class="login-page-footer__link"
              >
                帮助
              </a>
              <span class="login-page-footer__sep">|</span>
              <a
                :href="configStore.configData?.privacy?.config_value || '#'"
                target="_blank"
                rel="noopener noreferrer"
                class="login-page-footer__link"
              >
                隐私
              </a>
              <span class="login-page-footer__sep">|</span>
              <a
                :href="configStore.configData?.clause?.config_value || '#'"
                target="_blank"
                rel="noopener noreferrer"
                class="login-page-footer__link"
              >
                条款
              </a>
              <span
                v-if="configStore.configData?.keep_record?.config_value"
                class="login-page-footer__sep"
                >|</span
              >
              <span
                v-if="configStore.configData?.keep_record?.config_value"
                class="login-page-footer__record"
              >
                {{ configStore.configData.keep_record.config_value }}
              </span>
            </div>
          </div>
        </footer>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { LocationQuery, RouteLocationRaw } from "vue-router";
import AuthAPI, {
  type CaptchaInfo,
  type LoginFormData,
  type OAuthProvider,
} from "@/api/module_system/auth";
import type { TenantRegisterForm } from "@/api/module_system/auth";
import UserAPI, { type ForgetPasswordForm, type RegisterForm } from "@/api/module_system/user";
import {
  useAssemblyStore,
  useConfigStore,
  useAppStore,
  useSettingsStore,
  useUserStore,
} from "@stores";
import { HttpError } from "@utils";
import { ElMessage, ElNotification, type FormRules } from "element-plus";
import type { Account, AccountKey } from "./types";
import FaLoginAccountForm from "@/components/views/fa-login/forms/FaLoginAccountForm.vue";
import FaLoginForgetPanel from "@/components/views/fa-login/panels/FaLoginForgetPanel.vue";
import FaLoginMobilePanel from "@/components/views/fa-login/panels/FaLoginMobilePanel.vue";
import FaLoginQrPanel from "@/components/views/fa-login/panels/FaLoginQrPanel.vue";
import FaLoginRegisterPanel from "@/components/views/fa-login/panels/FaLoginRegisterPanel.vue";
import FaAuthTopBar from "@/components/views/fa-login/widgets/FaAuthTopBar.vue";
import { useLoginPanelAlign } from "@/components/views/fa-login/composables/useLoginPanelAlign";
import { startOAuthLogin as startOAuthLoginRedirect } from "@/utils/oauth";

defineOptions({ name: "Login" });

type AuthPanel = "login" | "register" | "forget";

/** 登录区内：账号密码 ↔ 手机号 ↔ 扫码（扫码 / 手机号为演示交互） */
type LoginFlowMode = "account" | "mobile" | "qr";

const configStore = useConfigStore();
const settingStore = useSettingsStore();
const assemblyStore = useAssemblyStore();
const appStore = useAppStore();
const { isDark } = storeToRefs(settingStore);
const { t, locale } = useI18n();

const { panelAlign } = useLoginPanelAlign();

const authPanel = ref<AuthPanel>("login");
const loginFlowMode = ref<LoginFlowMode>("account");
const allowDemoContent = computed(() => assemblyStore.isFeatureEnabled("demoContent", true));
const authFeatures = computed(() => assemblyStore.authFeatures);
const showRegister = computed(() => authFeatures.value.register);
const showForgotPassword = computed(
  () => authFeatures.value.forgotPassword && authFeatures.value.passwordResetMode === "email_code"
);
const showMobileLogin = computed(() => authFeatures.value.mobileLogin);
const showQrLogin = computed(() => authFeatures.value.qrLogin);
const showRememberMe = computed(() => authFeatures.value.rememberMe);
const showDemoAccounts = computed(() => authFeatures.value.demoAccounts);
const oauthProviders = computed<OAuthProvider[]>(() => authFeatures.value.oauthProviders);
const oauthEnabled = computed(
  () => authFeatures.value.oauth && authFeatures.value.oauthProviders.length > 0
);

const panelTitle = computed(() => {
  if (authPanel.value === "register") return t("login.reg");
  if (authPanel.value === "forget") return t("login.resetPassword");
  if (
    authPanel.value === "login" &&
    (loginFlowMode.value === "mobile" || loginFlowMode.value === "qr")
  ) {
    return t("login.qrLoginTitle");
  }
  return t("login.title");
});

const panelSubTitle = computed(() => {
  if (authPanel.value === "register") return t("register.subTitle");
  if (authPanel.value === "forget") return t("forgetPassword.subTitle");
  if (authPanel.value === "login" && loginFlowMode.value === "mobile") {
    return t("login.mobileLoginSubTitle");
  }
  if (authPanel.value === "login" && loginFlowMode.value === "qr") {
    return t("login.qrLoginSubTitle");
  }
  return t("login.subTitle");
});

const userAgreementHref = computed(() => configStore.configData?.clause?.config_value || "#");

function setAuthPanel(panel: AuthPanel) {
  const nextPanel =
    (panel === "register" && !showRegister.value) ||
    (panel === "forget" && !showForgotPassword.value)
      ? "login"
      : panel;
  authPanel.value = nextPanel;
  if (nextPanel !== "login") {
    loginFlowMode.value = "account";
  }
  nextTick(() => {
    accountFormRef.value?.clearValidate?.();
    registerPanelRef.value?.clearValidate?.();
    forgetPanelRef.value?.clearValidate?.();
  });
}

function openMobileLogin() {
  if (!showMobileLogin.value) return;
  loginFlowMode.value = "mobile";
}

function openQrLogin() {
  if (!showQrLogin.value) return;
  loginFlowMode.value = "qr";
}

function backToAccountLogin() {
  loginFlowMode.value = "account";
  nextTick(() => {
    getCaptcha();
    loginForm.captcha = "";
    accountFormRef.value?.resetDragVerify?.();
    isPassing.value = false;
    isClickPass.value = false;
  });
}

const dragVerifyTextColor = computed(() =>
  isDark.value ? "rgba(255, 255, 255, 0.45)" : "var(--fa-gray-700)"
);
const formKey = ref(0);

watch(locale, () => {
  formKey.value++;
});

watch(authPanel, (panel) => {
  if (panel !== "login") return;
  if (loginFlowMode.value !== "account") return;
  getCaptcha();
  loginForm.captcha = "";
  accountFormRef.value?.resetDragVerify?.();
  isPassing.value = false;
  isClickPass.value = false;
});

const accounts = computed<Account[]>(() =>
  showDemoAccounts.value
    ? [
        {
          key: "super",
          label: t("login.roles.super"),
          username: "super",
          password: "123456",
          roles: ["R_SUPER"],
        },
        {
          key: "admin",
          label: t("login.roles.admin"),
          username: "admin",
          password: "123456",
          roles: ["R_ADMIN"],
        },
        {
          key: "user",
          label: t("login.roles.user"),
          username: "user",
          password: "123456",
          roles: ["R_USER"],
        },
      ]
    : []
);

const demoAccountKey = ref<AccountKey>("super");
const userStore = useUserStore();
const router = useRouter();
const route = useRoute();
const isPassing = ref(false);
const isClickPass = ref(false);

const accountFormRef = ref<InstanceType<typeof FaLoginAccountForm> | null>(null);
const registerPanelRef = ref<InstanceType<typeof FaLoginRegisterPanel> | null>(null);
const forgetPanelRef = ref<InstanceType<typeof FaLoginForgetPanel> | null>(null);

const loading = ref(false);
const registerLoading = ref(false);
const forgetLoading = ref(false);
const forgetCodeSending = ref(false);
const forgetCodeCountdown = ref(0);
let forgetCodeTimer: ReturnType<typeof setInterval> | null = null;
const codeLoading = ref(false);

const registerAgreementRead = ref(false);

const registerForm = reactive<RegisterForm & { email: string }>({
  username: "",
  password: "",
  confirmPassword: "",
  email: "",
});

const forgetForm = reactive<ForgetPasswordForm>({
  username: "",
  email: "",
  code: "",
  new_password: "",
  confirmPassword: "",
});

const validateRegisterPassword = (_rule: unknown, value: string, callback: (e?: Error) => void) => {
  if (!value) {
    callback(new Error(t("login.message.password.required")));
    return;
  }
  if (registerForm.confirmPassword) {
    registerPanelRef.value?.validateField?.("confirmPassword");
  }
  callback();
};

const validateRegisterConfirm = (_rule: unknown, value: string, callback: (e?: Error) => void) => {
  if (!value) {
    callback(new Error(t("login.message.password.required")));
    return;
  }
  if (value !== registerForm.password) {
    callback(new Error(t("login.message.password.inconformity")));
    return;
  }
  callback();
};

const registerRules = computed<FormRules<RegisterForm & { email: string }>>(() => ({
  username: [{ required: true, message: t("login.message.username.required"), trigger: "blur" }],
  password: [
    { required: true, validator: validateRegisterPassword, trigger: "blur" },
    { min: 6, message: t("login.message.password.min"), trigger: "blur" },
  ],
  confirmPassword: [
    { required: true, message: t("login.message.password.required"), trigger: "blur" },
    { min: 6, message: t("login.message.password.min"), trigger: "blur" },
    { validator: validateRegisterConfirm, trigger: "blur" },
  ],
  email: [
    { required: true, message: t("login.email.required"), trigger: "blur" },
    {
      type: "email",
      message: t("login.email.invalid"),
      trigger: "blur",
    },
  ],
}));

const validateForgetConfirm = (_rule: unknown, value: string, callback: (e?: Error) => void) => {
  if (!value) {
    callback(new Error(t("login.message.password.required")));
    return;
  }
  if (value !== forgetForm.new_password) {
    callback(new Error(t("login.message.password.inconformity")));
    return;
  }
  callback();
};

const forgetRules = computed<FormRules<ForgetPasswordForm>>(() => ({
  username: [{ required: true, message: t("login.message.username.required"), trigger: "blur" }],
  email: [
    { required: true, message: t("login.email.required"), trigger: "blur" },
    { type: "email", message: t("login.email.invalid"), trigger: "blur" },
  ],
  code: [
    { required: true, message: t("forgetPassword.codeRequired"), trigger: "blur" },
    { min: 6, max: 6, message: t("forgetPassword.codeInvalid"), trigger: "blur" },
  ],
  new_password: [
    { required: true, message: t("login.message.password.required"), trigger: "blur" },
    { min: 6, message: t("login.message.password.min"), trigger: "blur" },
  ],
  confirmPassword: [
    { required: true, message: t("login.message.password.required"), trigger: "blur" },
    { min: 6, message: t("login.message.password.min"), trigger: "blur" },
    { validator: validateForgetConfirm, trigger: "blur" },
  ],
}));

const loginForm = reactive<LoginFormData>({
  username: "",
  password: "",
  captcha: "",
  captcha_key: "",
  remember: true,
  login_type: "PC端",
});

const captchaState = reactive<CaptchaInfo>({
  enable: false,
  key: "",
  img_base: "",
});

watch(
  authFeatures,
  () => {
    if (authPanel.value === "register" && !showRegister.value) {
      setAuthPanel("login");
    }
    if (authPanel.value === "forget" && !showForgotPassword.value) {
      setAuthPanel("login");
    }
    if (loginFlowMode.value === "mobile" && !showMobileLogin.value) {
      backToAccountLogin();
    }
    if (loginFlowMode.value === "qr" && !showQrLogin.value) {
      backToAccountLogin();
    }
    if (!showRememberMe.value) {
      loginForm.remember = false;
    }
  },
  { deep: true, immediate: true }
);

const rules = computed<FormRules>(() => {
  const base: FormRules = {
    username: [
      {
        required: true,
        trigger: "blur",
        message: t("login.message.username.required"),
      },
    ],
    password: [
      {
        required: true,
        trigger: "blur",
        message: t("login.message.password.required"),
      },
      {
        min: 6,
        message: t("login.message.password.min"),
        trigger: "blur",
      },
    ],
  };
  if (captchaState.enable) {
    base.captcha = [
      {
        required: true,
        trigger: "blur",
        message: t("login.message.captchaCode.required"),
      },
    ];
  }
  return base;
});

function setupAccount(key: AccountKey) {
  if (!showDemoAccounts.value) return;
  const selected = accounts.value.find((a: Account) => a.key === key);
  demoAccountKey.value = key;
  loginForm.username = selected?.username ?? "";
  loginForm.password = selected?.password ?? "";
}

function startOAuthLogin(provider: OAuthProvider) {
  if (!oauthEnabled.value || !oauthProviders.value.includes(provider)) return;
  startOAuthLoginRedirect(provider);
}

async function getCaptcha() {
  try {
    codeLoading.value = true;
    const response = await AuthAPI.getCaptcha();
    const data = response.data.data;
    loginForm.captcha_key = data.key;
    captchaState.img_base = data.img_base;
    captchaState.enable = data.enable;
  } catch {
    captchaState.enable = false;
    loginForm.captcha = "";
    loginForm.captcha_key = "";
  } finally {
    codeLoading.value = false;
  }
}

function resolveRedirectTarget(query: LocationQuery): RouteLocationRaw {
  const defaultPath = "/";
  const rawRedirect = (query.redirect as string) || defaultPath;
  try {
    const resolved = router.resolve(rawRedirect);
    return {
      path: resolved.path,
      query: resolved.query,
    };
  } catch {
    return { path: defaultPath };
  }
}

let notificationInstance: ReturnType<typeof ElNotification> | null = null;

const showVoteNotification = () => {
  if (!allowDemoContent.value) return;
  notificationInstance = ElNotification({
    title: "⭐ FastapiAdmin 完全开源 · 期待您的 Star 支持 🙏",
    message: `项目持续迭代中，若对您有所帮助，欢迎点亮 Star 支持！
    <br/><a href="https://github.com/fastapiadmin/FastapiAdmin" target="_blank" style="color: var(--el-color-primary); text-decoration: none; font-weight: 500;">Github仓库 →</a>
    <br/><a href="https://gitee.com/fastapiadmin/FastapiAdmin" target="_blank" style="color: var(--el-color-warning); text-decoration: none; font-weight: 500;">Gitee仓库 →</a>`,
    type: "success",
    position:
      panelAlign.value === "right" || panelAlign.value === "center"
        ? "bottom-left"
        : "bottom-right",
    duration: 0,
    dangerouslyUseHTMLString: true,
  });
};

let voteTimer: ReturnType<typeof setTimeout> | null = null;

onMounted(async () => {
  await assemblyStore.loadPublicConfig();
  if (showDemoAccounts.value) {
    setupAccount("super");
  }
  if (!showRememberMe.value) {
    loginForm.remember = false;
  }
  await configStore.getConfig(true);
  if (userStore.isLogin) {
    await router.replace(resolveRedirectTarget(route.query));
    return;
  }
  getCaptcha();
  if (allowDemoContent.value) {
    voteTimer = setTimeout(showVoteNotification, 500);
  }
});

onActivated(() => {
  if (authPanel.value !== "login" || loginFlowMode.value !== "account") return;
  getCaptcha();
  loginForm.captcha = "";
});

onBeforeUnmount(() => {
  if (voteTimer !== null) clearTimeout(voteTimer);
  if (forgetCodeTimer !== null) clearInterval(forgetCodeTimer);
  notificationInstance?.close();
  notificationInstance = null;
});

watch(
  () => route.fullPath,
  () => {
    if (authPanel.value !== "login" || loginFlowMode.value !== "account") return;
    getCaptcha();
    loginForm.captcha = "";
  }
);

const handleSubmit = async () => {
  if (!accountFormRef.value) return;

  const valid = await accountFormRef.value.validate?.().catch(() => false);
  if (!valid) return;

  try {
    if (!isPassing.value) {
      isClickPass.value = true;
      return;
    }

    loading.value = true;

    await userStore.login(loginForm);
    await router.replace(resolveRedirectTarget(route.query));

    if (settingStore.showGuide && allowDemoContent.value) {
      appStore.showGuide(true);
    }
  } catch (error) {
    await getCaptcha();
    if (!(error instanceof HttpError)) {
      console.error("[Login] Unexpected error:", error);
      ElNotification({
        title: "提示",
        message: error instanceof Error ? error.message : String(error),
        type: "error",
      });
    }
  } finally {
    loading.value = false;
    accountFormRef.value?.resetDragVerify?.();
  }
};

async function submitRegister() {
  if (!showRegister.value) {
    setAuthPanel("login");
    return;
  }
  if (!registerAgreementRead.value) {
    ElMessage.warning(t("login.message.agree.required"));
    return;
  }
  if (!registerPanelRef.value) return;
  try {
    await registerPanelRef.value.validate?.();
    registerLoading.value = true;
    // 租户自助注册（PRD §4.5）
    const regData: TenantRegisterForm = {
      username: registerForm.username,
      password: registerForm.password,
      email: registerForm.email || `${registerForm.username}@temp.com`,
    };
    await AuthAPI.tenantRegister(regData);
    loginForm.username = registerForm.username;
    loginForm.password = registerForm.password;
    registerForm.username = "";
    registerForm.password = "";
    registerForm.confirmPassword = "";
    registerForm.email = "";
    registerAgreementRead.value = false;
    setAuthPanel("login");
  } catch (error) {
    console.error("[Login] register:", error);
  } finally {
    registerLoading.value = false;
  }
}

async function submitForget() {
  if (!showForgotPassword.value) {
    setAuthPanel("login");
    return;
  }
  if (!forgetPanelRef.value) return;
  try {
    await forgetPanelRef.value.validate?.();
    forgetLoading.value = true;
    await UserAPI.resetForgetPasswordByEmail(forgetForm);
    loginForm.username = forgetForm.username;
    loginForm.password = "";
    forgetForm.username = "";
    forgetForm.email = "";
    forgetForm.code = "";
    forgetForm.new_password = "";
    forgetForm.confirmPassword = "";
    setAuthPanel("login");
  } catch (error) {
    console.error("[Login] forget password:", error);
  } finally {
    forgetLoading.value = false;
  }
}

async function sendForgetEmailCode() {
  if (!showForgotPassword.value) return;
  if (!forgetPanelRef.value) return;
  try {
    await forgetPanelRef.value.validateField?.(["username", "email"]);
    forgetCodeSending.value = true;
    await UserAPI.sendForgetPasswordEmailCode({
      username: forgetForm.username,
      email: forgetForm.email,
    });
    ElMessage.success(t("forgetPassword.codeSent"));
    forgetCodeCountdown.value = 60;
    if (forgetCodeTimer !== null) clearInterval(forgetCodeTimer);
    forgetCodeTimer = setInterval(() => {
      forgetCodeCountdown.value -= 1;
      if (forgetCodeCountdown.value <= 0 && forgetCodeTimer !== null) {
        clearInterval(forgetCodeTimer);
        forgetCodeTimer = null;
      }
    }, 1000);
  } catch (error) {
    console.error("[Login] send forget password email code:", error);
  } finally {
    forgetCodeSending.value = false;
  }
}
</script>

<style scoped lang="scss">
@use "../../../../components/views/fa-login/fa-login";
</style>
