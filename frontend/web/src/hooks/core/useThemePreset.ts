import { watch } from "vue";
import { SETTING_DEFAULT_CONFIG } from "@/config/setting";
import {
  getThemePreset,
  isThemePresetCode,
  type ThemePreset,
  type ThemePresetCode,
} from "@/config/themePresets";
import { useConfigStore, useSettingsStore, useUserStore } from "@stores";
import { StorageConfig, getDarkColor, getLightColor, setElementThemeColor } from "@utils";

export interface ThemePresetContext {
  siteCode: string | null;
  tenantId: number | null;
  userId: number | null;
}

export function getThemePresetContext(): ThemePresetContext {
  const configStore = useConfigStore();
  const userStore = useUserStore();
  const info = (userStore.info ?? {}) as Record<string, unknown>;
  const rawUserId = info.id ?? info.user_id;
  const rawTenantId = info.tenant_id ?? userStore.currentTenant?.id;
  const userId = rawUserId == null || rawUserId === "" ? null : Number(rawUserId);
  const tenantId = rawTenantId == null || rawTenantId === "" ? null : Number(rawTenantId);

  return {
    siteCode:
      configStore.siteConfigData?.site_code?.config_value ??
      configStore.configData?.site_code?.config_value ??
      null,
    tenantId: tenantId !== null && Number.isFinite(tenantId) ? tenantId : null,
    userId: userId !== null && Number.isFinite(userId) ? userId : null,
  };
}

export function getThemePresetStorageKey(ctx: ThemePresetContext): string | null {
  if (!ctx.siteCode || !ctx.tenantId || !ctx.userId) return null;
  return StorageConfig.generateStorageKey(
    `theme-preset-${ctx.siteCode}-${ctx.tenantId}-${ctx.userId}`
  );
}

const SEMANTIC_NAMES = ["success", "warning", "danger", "error", "info"] as const;

function applySemanticColors(preset: ThemePreset, isDark: boolean): void {
  const style = document.documentElement.style;
  for (const name of SEMANTIC_NAMES) {
    const base = preset.semantics[name];
    style.setProperty(`--el-color-${name}`, base);
    for (let i = 1; i <= 9; i++) {
      style.setProperty(
        `--el-color-${name}-light-${i}`,
        isDark ? getDarkColor(base, i / 10) : getLightColor(base, i / 10)
      );
    }
    style.setProperty(`--el-color-${name}-dark-2`, getDarkColor(base, 0.2));
  }
}

function removeSemanticColors(): void {
  const style = document.documentElement.style;
  for (const name of SEMANTIC_NAMES) {
    style.removeProperty(`--el-color-${name}`);
    for (let i = 1; i <= 9; i++) style.removeProperty(`--el-color-${name}-light-${i}`);
    style.removeProperty(`--el-color-${name}-dark-2`);
  }
}

export function restoreFactoryThemeAppearance(): void {
  const store = useSettingsStore();
  const color = SETTING_DEFAULT_CONFIG.systemThemeColor;
  store.systemThemeColor = color;
  setElementThemeColor(color);
  store.switchMenuStyles(SETTING_DEFAULT_CONFIG.menuThemeType);
  store.presetAppliedPrimary = null;
  store.presetAppliedMenuTheme = null;
}

let stopModeWatcher: (() => void) | null = null;

function ensureModeWatcher(): void {
  if (stopModeWatcher) return;
  const store = useSettingsStore();
  stopModeWatcher = watch(
    () => store.isDark,
    () => {
      const preset = getThemePreset(store.themePreset);
      if (preset) applyPreset(preset.code);
    }
  );
}

export interface ApplyThemePresetOptions {
  select?: boolean;
  restoreFactory?: boolean;
}

export function applyPreset(
  code: ThemePresetCode | string,
  options: ApplyThemePresetOptions = {}
): void {
  const store = useSettingsStore();
  const preset = getThemePreset(code);
  if (!preset) {
    store.setThemePreset("default");
    document.documentElement.removeAttribute("data-theme-preset");
    removeSemanticColors();
    if (options.restoreFactory !== false) restoreFactoryThemeAppearance();
    return;
  }

  const force = options.select === true;
  const currentPrimary = (store.systemThemeColor ?? "").toLowerCase();
  const factoryPrimary = SETTING_DEFAULT_CONFIG.systemThemeColor.toLowerCase();
  const appliedPrimary = store.presetAppliedPrimary?.toLowerCase() ?? null;
  const primaryIsPresetOwned =
    force || currentPrimary === factoryPrimary || currentPrimary === appliedPrimary;
  const menuIsPresetOwned =
    force ||
    store.menuThemeType === SETTING_DEFAULT_CONFIG.menuThemeType ||
    store.menuThemeType === store.presetAppliedMenuTheme;

  store.setThemePreset(preset.code);
  document.documentElement.setAttribute("data-theme-preset", preset.code);
  if (primaryIsPresetOwned) {
    const target = store.isDark ? preset.primary.dark : preset.primary.light;
    store.systemThemeColor = target;
    setElementThemeColor(target);
    store.presetAppliedPrimary = target;
  } else {
    setElementThemeColor(store.systemThemeColor);
    store.presetAppliedPrimary = null;
  }
  applySemanticColors(preset, store.isDark);
  if (menuIsPresetOwned) {
    store.switchMenuStyles(preset.recommendedMenuTheme);
    store.presetAppliedMenuTheme = preset.recommendedMenuTheme;
  } else {
    store.presetAppliedMenuTheme = null;
  }
  ensureModeWatcher();
}

export function selectPreset(code: ThemePresetCode | string): boolean {
  const normalized: ThemePresetCode = isThemePresetCode(code) ? code : "default";
  applyPreset(normalized, { select: true });
  const key = getThemePresetStorageKey(getThemePresetContext());
  if (key) localStorage.setItem(key, normalized);
  return true;
}

export function resolveAndApplyPreset(): ThemePresetCode {
  const key = getThemePresetStorageKey(getThemePresetContext());
  const saved = key ? localStorage.getItem(key) : null;
  const normalized: ThemePresetCode =
    saved === "default" || isThemePresetCode(saved) ? saved : "default";
  applyPreset(normalized);
  return normalized;
}

export function resetToDefaultPreset(): ThemePresetCode {
  const key = getThemePresetStorageKey(getThemePresetContext());
  if (key) localStorage.removeItem(key);
  applyPreset("default");
  return "default";
}

export function useThemePreset() {
  return {
    applyPreset,
    selectPreset,
    resolveAndApplyPreset,
    resetToDefaultPreset,
  };
}
