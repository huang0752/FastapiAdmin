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

const FOOD_LOGI_ASSEMBLIES = new Set([
  "food-traceability",
  "agricultural-delivery",
  "cold-chain-vehicle",
]);

export function isFoodLogiAssembly(name: string): boolean {
  return FOOD_LOGI_ASSEMBLIES.has(name);
}

function buildTimeAssemblySummary(): AssemblySummary | null {
  const name = String(import.meta.env.VITE_APP_ASSEMBLY || "").trim();
  if (!isFoodLogiAssembly(name)) return null;
  const productRouteGroup =
    name === "food-traceability"
      ? "food-traceability"
      : name === "agricultural-delivery"
        ? "agricultural-delivery"
        : "cold-chain-vehicle";
  return {
    name,
    title: String(import.meta.env.VITE_APP_TITLE || name),
    enabledRouteGroups: [
      "auth",
      "home",
      "system",
      "platform",
      "user-profile",
      "workspace",
      productRouteGroup,
      "exception",
    ],
    disabledRouteGroups: [
      "dashboard",
      "ai-chat",
      "generator",
      "module-generator",
      "pricing",
      "article",
      "tutorial",
      "changelog",
    ],
    featureFlags: {
      aiAssistant: false,
      pluginMarket: false,
      demoContent: false,
      tenantWorkspace: true,
      usageCertificate: true,
      aiModelFoundation: true,
      demoDataBlueprint: true,
    },
  };
}

export const defaultAssemblySummary: AssemblySummary = buildTimeAssemblySummary() ?? {
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
