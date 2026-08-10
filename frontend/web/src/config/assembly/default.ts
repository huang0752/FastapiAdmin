export interface AssemblySummary {
  name: string;
  title: string;
  enabledRouteGroups: string[];
  disabledRouteGroups: string[];
  featureFlags: Record<string, boolean>;
}

export type OAuthProvider = "wechat" | "qq" | "github" | "gitee";
export type PasswordResetMode = "email_code" | "legacy_mobile" | "disabled";

export interface AuthFeatures {
  register: boolean;
  forgotPassword: boolean;
  passwordResetMode: PasswordResetMode;
  oauth: boolean;
  oauthProviders: OAuthProvider[];
  mobileLogin: boolean;
  qrLogin: boolean;
  rememberMe: boolean;
  demoAccounts: boolean;
  controlSso: boolean;
}

export const defaultAssemblySummary: AssemblySummary = {
  name: "default",
  title: "默认完整装配",
  enabledRouteGroups: [],
  disabledRouteGroups: [],
  featureFlags: {
    aiAssistant: true,
    pluginMarket: true,
    tenantPackage: true,
    demoContent: true,
    fastEnter: true,
  },
};

export const defaultAuthFeatures: AuthFeatures = {
  register: false,
  forgotPassword: true,
  passwordResetMode: "email_code",
  oauth: false,
  oauthProviders: [],
  mobileLogin: false,
  qrLogin: false,
  rememberMe: true,
  demoAccounts: false,
  controlSso: false,
};
