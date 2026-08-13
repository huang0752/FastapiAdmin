import { MenuThemeEnum } from "@/enums/appEnum";
import { isFoodLogiProductAssembly } from "@/config/assembly/foodLogiBrand";
import { resolveFoodLogiSite } from "@/config/brand/siteBrandTheme";

export type ThemePresetCode =
  | "default"
  | "ocean"
  | "energy"
  | "forest"
  | "violet"
  | "sunset"
  | "cherry"
  | "amber"
  | "porcelain"
  | "midnight"
  | "graphite"
  | "neon";
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
        sidebarBackground: "#07172E",
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
    code: "energy",
    nameKey: "energy",
    primary: { light: "#078C72", dark: "#2DD4BF" },
    semantics: { ...COMMON_SEMANTICS },
    recommendedMenuTheme: MenuThemeEnum.DESIGN,
    modes: {
      light: {
        sidebarBackground: "#F7FFFC",
        sidebarText: "#173B36",
        sidebarIcon: "#3D746B",
        sidebarTitle: "#083E35",
        sidebarActiveBackground: "#DDF7EF",
        sidebarActiveText: "#06735E",
        chartPalette: [
          "#078C72",
          "#0891B2",
          "#65A30D",
          "#2563EB",
          "#D97706",
          "#7C3AED",
          "#DB2777",
          "#64748B",
        ],
      },
      dark: {
        sidebarBackground: "#052E2B",
        sidebarText: "#B7D8D2",
        sidebarIcon: "#83B9AF",
        sidebarTitle: "#E1FAF4",
        sidebarActiveBackground: "#0B4A42",
        sidebarActiveText: "#5EEAD4",
        chartPalette: [
          "#2DD4BF",
          "#22D3EE",
          "#A3E635",
          "#60A5FA",
          "#FBBF24",
          "#A78BFA",
          "#F472B6",
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
  {
    code: "cherry",
    nameKey: "cherry",
    primary: { light: "#BE185D", dark: "#F472B6" },
    semantics: { ...COMMON_SEMANTICS, danger: "#BE123C", error: "#BE123C" },
    recommendedMenuTheme: MenuThemeEnum.DARK,
    modes: {
      light: {
        sidebarBackground: "#4A1028",
        sidebarText: "#F1C4D5",
        sidebarIcon: "#DBA9BC",
        sidebarTitle: "#FFF1F6",
        sidebarActiveBackground: "#7A1F45",
        sidebarActiveText: "#FBCFE8",
        chartPalette: [
          "#BE185D",
          "#E11D48",
          "#7C3AED",
          "#2563EB",
          "#0F766E",
          "#D97706",
          "#9333EA",
          "#64748B",
        ],
      },
      dark: {
        sidebarBackground: "#2B0B1A",
        sidebarText: "#E9B8CB",
        sidebarIcon: "#D99AB3",
        sidebarTitle: "#FFF1F6",
        sidebarActiveBackground: "#61183A",
        sidebarActiveText: "#F9A8D4",
        chartPalette: [
          "#F472B6",
          "#FB7185",
          "#C4B5FD",
          "#60A5FA",
          "#5EEAD4",
          "#FBBF24",
          "#D8B4FE",
          "#CBD5E1",
        ],
      },
    },
  },
  {
    code: "amber",
    nameKey: "amber",
    primary: { light: "#A16207", dark: "#FBBF24" },
    semantics: { ...COMMON_SEMANTICS, warning: "#A16207" },
    recommendedMenuTheme: MenuThemeEnum.LIGHT,
    modes: {
      light: {
        sidebarBackground: "#FFF8E7",
        sidebarText: "#5C3B0A",
        sidebarIcon: "#7C5A1D",
        sidebarTitle: "#3D2708",
        sidebarActiveBackground: "#FDE7B2",
        sidebarActiveText: "#713F12",
        chartPalette: [
          "#A16207",
          "#C2410C",
          "#CA8A04",
          "#854D0E",
          "#B45309",
          "#9A3412",
          "#4D7C0F",
          "#78716C",
        ],
      },
      dark: {
        sidebarBackground: "#2A1B08",
        sidebarText: "#F1D6A3",
        sidebarIcon: "#D8B876",
        sidebarTitle: "#FFF7E1",
        sidebarActiveBackground: "#553509",
        sidebarActiveText: "#FDE68A",
        chartPalette: [
          "#FBBF24",
          "#FB923C",
          "#FDE047",
          "#F59E0B",
          "#FDBA74",
          "#F87171",
          "#A3E635",
          "#D6D3D1",
        ],
      },
    },
  },
  {
    code: "porcelain",
    nameKey: "porcelain",
    primary: { light: "#0F766E", dark: "#2DD4BF" },
    semantics: { ...COMMON_SEMANTICS, success: "#0F766E", info: "#0E7490" },
    recommendedMenuTheme: MenuThemeEnum.DESIGN,
    modes: {
      light: {
        sidebarBackground: "#0E3B3A",
        sidebarText: "#C7E8E4",
        sidebarIcon: "#9CCDC7",
        sidebarTitle: "#ECFEFA",
        sidebarActiveBackground: "#145E59",
        sidebarActiveText: "#CCFBF1",
        chartPalette: [
          "#0F766E",
          "#0891B2",
          "#047857",
          "#0369A1",
          "#4D7C0F",
          "#A16207",
          "#7E22CE",
          "#64748B",
        ],
      },
      dark: {
        sidebarBackground: "#092927",
        sidebarText: "#B6DDD8",
        sidebarIcon: "#8FC1BA",
        sidebarTitle: "#ECFEFA",
        sidebarActiveBackground: "#104E4A",
        sidebarActiveText: "#99F6E4",
        chartPalette: [
          "#2DD4BF",
          "#22D3EE",
          "#34D399",
          "#38BDF8",
          "#A3E635",
          "#FBBF24",
          "#C084FC",
          "#CBD5E1",
        ],
      },
    },
  },
  {
    code: "midnight",
    nameKey: "midnight",
    primary: { light: "#1D4ED8", dark: "#38BDF8" },
    semantics: { ...COMMON_SEMANTICS, info: "#475569" },
    recommendedMenuTheme: MenuThemeEnum.DARK,
    modes: {
      light: {
        sidebarBackground: "#071426",
        sidebarText: "#C0D1E8",
        sidebarIcon: "#91A9C7",
        sidebarTitle: "#F0F7FF",
        sidebarActiveBackground: "#0D3266",
        sidebarActiveText: "#BFDBFE",
        chartPalette: [
          "#1D4ED8",
          "#0369A1",
          "#4338CA",
          "#0F766E",
          "#6D28D9",
          "#BE185D",
          "#A16207",
          "#475569",
        ],
      },
      dark: {
        sidebarBackground: "#030B16",
        sidebarText: "#B6CAE4",
        sidebarIcon: "#829DBE",
        sidebarTitle: "#F0F7FF",
        sidebarActiveBackground: "#0B2C59",
        sidebarActiveText: "#7DD3FC",
        chartPalette: [
          "#38BDF8",
          "#60A5FA",
          "#818CF8",
          "#2DD4BF",
          "#C084FC",
          "#F472B6",
          "#FBBF24",
          "#94A3B8",
        ],
      },
    },
  },
  {
    code: "graphite",
    nameKey: "graphite",
    primary: { light: "#475569", dark: "#94A3B8" },
    semantics: { ...COMMON_SEMANTICS, info: "#475569" },
    recommendedMenuTheme: MenuThemeEnum.LIGHT,
    modes: {
      light: {
        sidebarBackground: "#F8FAFC",
        sidebarText: "#334155",
        sidebarIcon: "#475569",
        sidebarTitle: "#0F172A",
        sidebarActiveBackground: "#E2E8F0",
        sidebarActiveText: "#1E293B",
        chartPalette: [
          "#475569",
          "#64748B",
          "#334155",
          "#0F766E",
          "#1D4ED8",
          "#6D28D9",
          "#A16207",
          "#78716C",
        ],
      },
      dark: {
        sidebarBackground: "#171A1F",
        sidebarText: "#CBD5E1",
        sidebarIcon: "#94A3B8",
        sidebarTitle: "#F8FAFC",
        sidebarActiveBackground: "#323842",
        sidebarActiveText: "#F8FAFC",
        chartPalette: [
          "#94A3B8",
          "#CBD5E1",
          "#64748B",
          "#5EEAD4",
          "#60A5FA",
          "#C4B5FD",
          "#FBBF24",
          "#A8A29E",
        ],
      },
    },
  },
  {
    code: "neon",
    nameKey: "neon",
    primary: { light: "#3F6212", dark: "#A3E635" },
    semantics: { ...COMMON_SEMANTICS, success: "#3F6212" },
    recommendedMenuTheme: MenuThemeEnum.DESIGN,
    modes: {
      light: {
        sidebarBackground: "#1A2609",
        sidebarText: "#D7E8B5",
        sidebarIcon: "#B6CF82",
        sidebarTitle: "#F4FFD8",
        sidebarActiveBackground: "#38500E",
        sidebarActiveText: "#D9F99D",
        chartPalette: [
          "#3F6212",
          "#0F766E",
          "#0369A1",
          "#6D28D9",
          "#A16207",
          "#BE185D",
          "#15803D",
          "#57534E",
        ],
      },
      dark: {
        sidebarBackground: "#101805",
        sidebarText: "#CCE1A4",
        sidebarIcon: "#A9C471",
        sidebarTitle: "#F4FFD8",
        sidebarActiveBackground: "#2E4509",
        sidebarActiveText: "#BEF264",
        chartPalette: [
          "#A3E635",
          "#2DD4BF",
          "#38BDF8",
          "#C084FC",
          "#FBBF24",
          "#F472B6",
          "#4ADE80",
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

export function getLockedFoodLogiThemePreset(
  assembly: string,
  siteOrHost: string
): ActiveThemePresetCode | null {
  if (!isFoodLogiProductAssembly(assembly)) return null;
  return resolveFoodLogiSite(siteOrHost) === "znceedi" ? "energy" : "ocean";
}
