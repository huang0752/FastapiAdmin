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
  tenantId: number | "platform" | null;
  userId: number | null;
}

export function getThemePresetContext(): ThemePresetContext {
  const configStore = useConfigStore();
  const userStore = useUserStore();
  const info = (userStore.info ?? {}) as Record<string, unknown>;
  const rawUserId = info.id ?? info.user_id;
  const rawTenantId = info.tenant_id ?? userStore.currentTenant?.id;
  const userId = rawUserId == null || rawUserId === "" ? null : Number(rawUserId);
  const numericTenantId = rawTenantId == null || rawTenantId === "" ? null : Number(rawTenantId);
  const tenantId =
    numericTenantId !== null && Number.isFinite(numericTenantId)
      ? numericTenantId
      : userId !== null
        ? "platform"
        : null;

  return {
    siteCode:
      configStore.siteConfigData?.site_code?.config_value ??
      configStore.configData?.site_code?.config_value ??
      (configStore.isConfigLoaded ? "origin" : null),
    tenantId,
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
let stopPendingContextWatcher: (() => void) | null = null;

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

/**
 * 冷启动时配置、用户与路由可能并发就绪。首次解析若缺少隔离上下文，等待完整 key
 * 出现后只重放一次，避免初始化早到导致已保存主题永久回落为 default。
 */
function ensurePendingContextWatcher(): void {
  if (stopPendingContextWatcher) return;
  stopPendingContextWatcher = watch(
    () => getThemePresetStorageKey(getThemePresetContext()),
    (key) => {
      if (!key) return;
      const stop = stopPendingContextWatcher;
      stopPendingContextWatcher = null;
      stop?.();
      resolveAndApplyPreset();
    },
    { flush: "post" }
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
  const targetPrimary = store.isDark ? preset.primary.dark : preset.primary.light;
  const primaryIsPresetOwned =
    force ||
    currentPrimary === factoryPrimary ||
    currentPrimary === appliedPrimary ||
    currentPrimary === targetPrimary.toLowerCase();
  const menuIsPresetOwned =
    force ||
    store.menuThemeType === SETTING_DEFAULT_CONFIG.menuThemeType ||
    store.menuThemeType === store.presetAppliedMenuTheme ||
    store.menuThemeType === preset.recommendedMenuTheme;

  store.setThemePreset(preset.code);
  document.documentElement.setAttribute("data-theme-preset", preset.code);
  if (primaryIsPresetOwned) {
    store.systemThemeColor = targetPrimary;
    setElementThemeColor(targetPrimary);
    store.presetAppliedPrimary = targetPrimary;
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
  const context = getThemePresetContext();
  const key = getThemePresetStorageKey(context);
  if (key) localStorage.setItem(key, normalized);
  return true;
}

export function resolveAndApplyPreset(): ThemePresetCode {
  const key = getThemePresetStorageKey(getThemePresetContext());
  if (!key) ensurePendingContextWatcher();
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
