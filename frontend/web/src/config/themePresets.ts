import { MenuThemeEnum } from "@/enums/appEnum";

export type ThemePresetCode = "default" | "ocean" | "forest" | "violet" | "sunset";
export type ActiveThemePresetCode = Exclude<ThemePresetCode, "default">;

export interface ThemePresetModeTokens {
  sidebarBackground: string;
  sidebarText: string;
  sidebarIcon: string;
  sidebarTitle: string;
  sidebarActiveBackground: string;
  sidebarActiveText: string;
  chartPalette: readonly string[];
}

export interface ThemePreset {
  code: ActiveThemePresetCode;
  nameKey: ActiveThemePresetCode;
  primary: { light: string; dark: string };
  semantics: Record<"success" | "warning" | "danger" | "error" | "info", string>;
  recommendedMenuTheme: MenuThemeEnum;
  modes: { light: ThemePresetModeTokens; dark: ThemePresetModeTokens };
}

const COMMON_SEMANTICS = {
  success: "#16A34A",
  warning: "#D97706",
  danger: "#DC2626",
  error: "#DC2626",
  info: "#64748B",
} as const;

export const THEME_PRESETS: readonly ThemePreset[] = [
  {
    code: "ocean",
    nameKey: "ocean",
    primary: { light: "#2563EB", dark: "#60A5FA" },
    semantics: { ...COMMON_SEMANTICS },
    recommendedMenuTheme: MenuThemeEnum.DESIGN,
    modes: {
      light: {
        sidebarBackground: "#FFFFFF",
        sidebarText: "#29343D",
        sidebarIcon: "#64748B",
        sidebarTitle: "#1E293B",
        sidebarActiveBackground: "#E8F0FD",
        sidebarActiveText: "#1D4ED8",
        chartPalette: [
          "#2563EB",
          "#0891B2",
          "#7C3AED",
          "#059669",
          "#D97706",
          "#DB2777",
          "#65A30D",
          "#64748B",
        ],
      },
      dark: {
        sidebarBackground: "#0E1729",
        sidebarText: "#B7C6DA",
        sidebarIcon: "#93A8C2",
        sidebarTitle: "#E5EDF7",
        sidebarActiveBackground: "#1A2C4D",
        sidebarActiveText: "#93C5FD",
        chartPalette: [
          "#60A5FA",
          "#22D3EE",
          "#A78BFA",
          "#34D399",
          "#FBBF24",
          "#F472B6",
          "#A3E635",
          "#94A3B8",
        ],
      },
    },
  },
  {
    code: "forest",
    nameKey: "forest",
    primary: { light: "#15803D", dark: "#4ADE80" },
    semantics: { ...COMMON_SEMANTICS, success: "#15803D" },
    recommendedMenuTheme: MenuThemeEnum.DESIGN,
    modes: {
      light: {
        sidebarBackground: "#FFFFFF",
        sidebarText: "#29343D",
        sidebarIcon: "#64748B",
        sidebarTitle: "#1F3528",
        sidebarActiveBackground: "#E6F4EB",
        sidebarActiveText: "#166534",
        chartPalette: [
          "#15803D",
          "#65A30D",
          "#0E7490",
          "#D97706",
          "#059669",
          "#B45309",
          "#4D7C0F",
          "#78716C",
        ],
      },
      dark: {
        sidebarBackground: "#101A14",
        sidebarText: "#B5C8BC",
        sidebarIcon: "#93B09F",
        sidebarTitle: "#E3ECE6",
        sidebarActiveBackground: "#1C3A29",
        sidebarActiveText: "#86EFAC",
        chartPalette: [
          "#4ADE80",
          "#A3E635",
          "#22D3EE",
          "#FBBF24",
          "#34D399",
          "#FB923C",
          "#BEF264",
          "#A8A29E",
        ],
      },
    },
  },
  {
    code: "violet",
    nameKey: "violet",
    primary: { light: "#6D28D9", dark: "#A78BFA" },
    semantics: { ...COMMON_SEMANTICS },
    recommendedMenuTheme: MenuThemeEnum.DARK,
    modes: {
      light: {
        sidebarBackground: "#20133D",
        sidebarText: "#C9B9E8",
        sidebarIcon: "#B7A4DA",
        sidebarTitle: "#F1EAFF",
        sidebarActiveBackground: "#3C226F",
        sidebarActiveText: "#DDD6FE",
        chartPalette: [
          "#6D28D9",
          "#2563EB",
          "#DB2777",
          "#0891B2",
          "#059669",
          "#D97706",
          "#4F46E5",
          "#64748B",
        ],
      },
      dark: {
        sidebarBackground: "#150D29",
        sidebarText: "#C9B9E8",
        sidebarIcon: "#AD9ACF",
        sidebarTitle: "#F1EAFF",
        sidebarActiveBackground: "#35205D",
        sidebarActiveText: "#DDD6FE",
        chartPalette: [
          "#A78BFA",
          "#60A5FA",
          "#F472B6",
          "#22D3EE",
          "#34D399",
          "#FBBF24",
          "#818CF8",
          "#CBD5E1",
        ],
      },
    },
  },
  {
    code: "sunset",
    nameKey: "sunset",
    primary: { light: "#C2410C", dark: "#FB923C" },
    semantics: { ...COMMON_SEMANTICS, warning: "#C2410C" },
    recommendedMenuTheme: MenuThemeEnum.DARK,
    modes: {
      light: {
        sidebarBackground: "#32160C",
        sidebarText: "#E7C4B5",
        sidebarIcon: "#D7AD9B",
        sidebarTitle: "#FFF1EB",
        sidebarActiveBackground: "#63290F",
        sidebarActiveText: "#FED7AA",
        chartPalette: [
          "#C2410C",
          "#D97706",
          "#DB2777",
          "#2563EB",
          "#059669",
          "#7C3AED",
          "#0891B2",
          "#78716C",
        ],
      },
      dark: {
        sidebarBackground: "#211009",
        sidebarText: "#E7C4B5",
        sidebarIcon: "#D7AD9B",
        sidebarTitle: "#FFF1EB",
        sidebarActiveBackground: "#54240F",
        sidebarActiveText: "#FED7AA",
        chartPalette: [
          "#FB923C",
          "#FBBF24",
          "#F472B6",
          "#60A5FA",
          "#34D399",
          "#A78BFA",
          "#22D3EE",
          "#D6D3D1",
        ],
      },
    },
  },
] as const;

const PRESET_MAP = new Map<ActiveThemePresetCode, ThemePreset>(
  THEME_PRESETS.map((preset) => [preset.code, preset])
);

export function isThemePresetCode(value: unknown): value is ActiveThemePresetCode {
  return typeof value === "string" && PRESET_MAP.has(value as ActiveThemePresetCode);
}

export function getThemePreset(value: string | null | undefined): ThemePreset | null {
  if (!isThemePresetCode(value)) return null;
  return PRESET_MAP.get(value) ?? null;
}

export function listPresetPrimaryColors(): string[] {
  return THEME_PRESETS.flatMap((preset) => [
    preset.primary.light.toLowerCase(),
    preset.primary.dark.toLowerCase(),
  ]);
}
