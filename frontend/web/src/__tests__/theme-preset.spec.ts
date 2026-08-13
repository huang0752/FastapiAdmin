import { createApp, nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import piniaPluginPersistedstate from "pinia-plugin-persistedstate";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

vi.mock("@/router", () => ({
  router: {
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: { value: { path: "/", fullPath: "/", query: {} } },
  },
}));

vi.mock("@/mock/upgrade/changeLog", () => ({ upgradeLogList: [] }));

vi.hoisted(() => {
  window.matchMedia =
    window.matchMedia ||
    ((query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList);
});
import { MenuThemeEnum } from "@/enums/appEnum";
import { SETTING_DEFAULT_CONFIG } from "@/config/setting";
import { useConfigStore, useSettingsStore, useUserStore } from "@stores";
import {
  THEME_PRESETS,
  getThemePreset,
  isThemePresetCode,
  listPresetPrimaryColors,
} from "@/config/themePresets";
import {
  applyPreset,
  getThemePresetContext,
  getThemePresetStorageKey,
  resolveAndApplyPreset,
  resetToDefaultPreset,
  selectPreset,
} from "@/hooks/core/useThemePreset";
import { useChartOps } from "@/hooks/core/useChart";

function setupContext(options: {
  siteCode?: string | null;
  tenantId?: number | null;
  userId?: number | null;
}) {
  const configStore = useConfigStore();
  const userStore = useUserStore();
  configStore.siteConfigData = options.siteCode
    ? ({ site_code: { config_key: "site_code", config_value: options.siteCode } } as never)
    : {};
  userStore.info = {
    id: options.userId ?? undefined,
    tenant_id: options.tenantId ?? undefined,
  } as never;
}

function resetThemeDom() {
  const root = document.documentElement;
  root.removeAttribute("data-theme-preset");
  for (const name of ["primary", "success", "warning", "danger", "error", "info"]) {
    root.style.removeProperty(`--el-color-${name}`);
  }
}

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  resetThemeDom();
});

describe("通用主题预设注册表", () => {
  it("提供十一套编码唯一的通用预设", () => {
    const codes = THEME_PRESETS.map((preset) => preset.code);
    expect(codes).toEqual([
      "ocean",
      "energy",
      "forest",
      "violet",
      "sunset",
      "cherry",
      "amber",
      "porcelain",
      "midnight",
      "graphite",
      "neon",
    ]);
    expect(new Set(codes).size).toBe(codes.length);
  });

  it("每套预设都提供完整的明暗模式和八色图表色板", () => {
    for (const preset of THEME_PRESETS) {
      expect(preset.primary.light).toMatch(/^#[0-9A-F]{6}$/i);
      expect(preset.primary.dark).toMatch(/^#[0-9A-F]{6}$/i);
      expect(preset.modes.light.chartPalette).toHaveLength(8);
      expect(preset.modes.dark.chartPalette).toHaveLength(8);
    }
  });

  it("default 与未知编码回落为空预设", () => {
    expect(getThemePreset("default")).toBeNull();
    expect(getThemePreset("missing")).toBeNull();
    expect(isThemePresetCode("ocean")).toBe(true);
    expect(isThemePresetCode("default")).toBe(false);
  });

  it("识别新增预设并可应用极夜主题", () => {
    for (const code of ["cherry", "amber", "porcelain", "midnight", "graphite", "neon"]) {
      expect(isThemePresetCode(code)).toBe(true);
    }

    setupContext({ siteCode: "main", tenantId: 3, userId: 15 });
    expect(selectPreset("midnight")).toBe(true);
    expect(document.documentElement.dataset.themePreset).toBe("midnight");
    expect(useSettingsStore().systemThemeColor).toBe(getThemePreset("midnight")!.primary.light);
  });

  it("汇总所有预设明暗主色供状态迁移判断", () => {
    expect(listPresetPrimaryColors()).toHaveLength(THEME_PRESETS.length * 2);
    expect(listPresetPrimaryColors()).toContain("#2563eb");
  });
});

describe("通用主题预设状态与隔离持久化", () => {
  it("只在站点、租户和用户上下文完整时生成隔离键", () => {
    const key = getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: 15 });
    expect(key).toMatch(/theme-preset-main-3-15$/);
    expect(getThemePresetStorageKey({ siteCode: null, tenantId: 3, userId: 15 })).toBeNull();
    expect(getThemePresetStorageKey({ siteCode: "main", tenantId: null, userId: 15 })).toBeNull();
    expect(getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: null })).toBeNull();
  });

  it("用户选择按站点、租户和用户隔离保存", () => {
    setupContext({ siteCode: "main", tenantId: 3, userId: 15 });
    expect(selectPreset("forest")).toBe(true);
    const key = getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: 15 })!;
    expect(localStorage.getItem(key)).toBe("forest");
    expect(
      localStorage.getItem(getThemePresetStorageKey({ siteCode: "main", tenantId: 9, userId: 15 })!)
    ).toBeNull();
  });

  it("无业务租户的平台管理员使用独立 platform 作用域", () => {
    setupContext({ siteCode: "main", tenantId: null, userId: 15 });

    const context = getThemePresetContext();
    expect(context.tenantId).toBe("platform");
    expect(selectPreset("midnight")).toBe(true);
    expect(localStorage.getItem(getThemePresetStorageKey(context)!)).toBe("midnight");
  });

  it("配置完成但 Host 未匹配站点时使用当前 Origin 作用域", () => {
    setupContext({ siteCode: null, tenantId: null, userId: 15 });
    useConfigStore().isConfigLoaded = true;

    const context = getThemePresetContext();
    expect(context.siteCode).toBe("origin");
    expect(selectPreset("midnight")).toBe(true);
    expect(localStorage.getItem(getThemePresetStorageKey(context)!)).toBe("midnight");
  });

  it("冷启动上下文延迟就绪后自动恢复隔离预设", async () => {
    const key = getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: 15 })!;
    localStorage.setItem(key, "midnight");

    expect(resolveAndApplyPreset()).toBe("default");
    expect(document.documentElement.hasAttribute("data-theme-preset")).toBe(false);

    setupContext({ siteCode: "main", tenantId: 3, userId: 15 });
    await nextTick();

    expect(document.documentElement.dataset.themePreset).toBe("midnight");
    expect(useSettingsStore().systemThemeColor).toBe(getThemePreset("midnight")!.primary.light);
  });

  it("冷启动恢复与预设一致的主色和菜单所有权", () => {
    setupContext({ siteCode: "main", tenantId: 3, userId: 15 });
    const store = useSettingsStore();
    const preset = getThemePreset("midnight")!;
    const key = getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: 15 })!;
    localStorage.setItem(key, "midnight");
    store.systemThemeColor = preset.primary.light;
    store.menuThemeType = preset.recommendedMenuTheme;
    store.presetAppliedPrimary = null;
    store.presetAppliedMenuTheme = null;

    expect(resolveAndApplyPreset()).toBe("midnight");
    expect(store.presetAppliedPrimary).toBe(preset.primary.light);
    expect(store.presetAppliedMenuTheme).toBe(preset.recommendedMenuTheme);
  });

  it("运行时预设状态不进入全局 setting 持久化", async () => {
    const pinia = createPinia();
    pinia.use(piniaPluginPersistedstate);
    setActivePinia(pinia);
    createApp({ render: () => null }).use(pinia);
    setupContext({ siteCode: "main", tenantId: 3, userId: 15 });
    selectPreset("forest");
    await nextTick();
    const raw = localStorage.getItem("setting") ?? "";
    expect(raw).not.toContain("themePreset");
    expect(raw).not.toContain("presetAppliedPrimary");
    expect(raw).not.toContain("presetAppliedMenuTheme");
  });

  it("冷启动读取已保存 default 时完整恢复出厂主色和菜单", () => {
    setupContext({ siteCode: "main", tenantId: 3, userId: 15 });
    const store = useSettingsStore();
    applyPreset("sunset", { select: true });
    const key = getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: 15 })!;
    localStorage.setItem(key, "default");

    expect(resolveAndApplyPreset()).toBe("default");
    expect(store.systemThemeColor).toBe(SETTING_DEFAULT_CONFIG.systemThemeColor);
    expect(store.menuThemeType).toBe(SETTING_DEFAULT_CONFIG.menuThemeType);
    expect(document.documentElement.hasAttribute("data-theme-preset")).toBe(false);
  });

  it("自动应用的 dark 菜单可迁移到 design，用户手动菜单则保留", () => {
    const store = useSettingsStore();
    applyPreset("sunset", { select: true });
    expect(store.menuThemeType).toBe(MenuThemeEnum.DARK);

    applyPreset("ocean");
    expect(store.menuThemeType).toBe(MenuThemeEnum.DESIGN);

    store.switchMenuStyles(MenuThemeEnum.LIGHT);
    applyPreset("sunset");
    expect(store.menuThemeType).toBe(MenuThemeEnum.LIGHT);
  });

  it("自动重放保留用户自定义主色", () => {
    const store = useSettingsStore();
    applyPreset("forest", { select: true });
    store.setElementTheme("#722ED1");
    expect(store.presetAppliedPrimary).toBeNull();
    applyPreset("ocean");
    expect(store.systemThemeColor).toBe("#722ED1");
  });

  it("用户手动修改菜单后立即清除预设所有权", () => {
    const store = useSettingsStore();
    applyPreset("sunset", { select: true });
    store.switchMenuStyles(MenuThemeEnum.LIGHT);
    expect(store.presetAppliedMenuTheme).toBeNull();
  });
});

describe("主题状态单一真源", () => {
  it("Store 不再通过旧 theme/themeColor watcher 二次应用主题", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/store/modules/setting.store.ts"),
      "utf8"
    );
    expect(source).not.toContain("[theme, themeColor]");
    expect(source).not.toContain("generateThemeColors");
    expect(source).not.toContain("applyTheme,");
  });

  it("设置面板初始化认可主题预设主色，不会在挂载时回退默认蓝色", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/components/layouts/fa-settings-panel/composables/useSettingsPanel.ts"
      ),
      "utf8"
    );
    expect(source).toContain("listPresetPrimaryColors");
    expect(source).toContain("presetPrimaryColors.includes(systemThemeColor.value.toLowerCase())");
  });

  it("水印与代码编辑器读取 systemThemeColor/isDark", () => {
    const app = readFileSync(resolve(process.cwd(), "src/App.vue"), "utf8");
    const watermark = readFileSync(
      resolve(process.cwd(), "src/components/others/fa-watermark/index.vue"),
      "utf8"
    );
    const editor = readFileSync(
      resolve(
        process.cwd(),
        "src/views/module_generator/gencode/components/FaCreateTableDialog.vue"
      ),
      "utf8"
    );
    expect(app).toContain("settingsStore.systemThemeColor");
    expect(app).toContain("settingsStore.isDark");
    expect(watermark).toContain("systemThemeColor");
    expect(watermark).toContain("isDark");
    expect(editor).toContain("settingsStore.isDark");
  });
});

function relativeLuminance(hex: string): number {
  const clean = hex.replace("#", "");
  const channels = [0, 2, 4]
    .map((index) => Number.parseInt(clean.slice(index, index + 2), 16) / 255)
    .map((value) => (value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4)));
  return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!;
}

function contrastRatio(foreground: string, background: string): number {
  const first = relativeLuminance(foreground);
  const second = relativeLuminance(background);
  const high = Math.max(first, second);
  const low = Math.min(first, second);
  return (high + 0.05) / (low + 0.05);
}

describe("主题预设设置界面与视觉约束", () => {
  it("设置面板挂载主题预设选择并提供中英文文案", () => {
    const panel = readFileSync(
      resolve(process.cwd(), "src/components/layouts/fa-settings-panel/index.vue"),
      "utf8"
    );
    const zh = JSON.parse(
      readFileSync(resolve(process.cwd(), "src/locales/langs/zh.json"), "utf8")
    );
    const en = JSON.parse(
      readFileSync(resolve(process.cwd(), "src/locales/langs/en.json"), "utf8")
    );
    expect(panel).toContain("<FaThemePresetSettings />");
    expect(zh.setting.themePreset.presets).toMatchObject({
      default: "默认",
      ocean: "海洋蓝",
      energy: "能源青",
      forest: "森林绿",
      violet: "优雅紫",
      sunset: "活力橙",
      cherry: "樱桃",
      amber: "琥珀",
      porcelain: "青瓷",
      midnight: "极夜",
      graphite: "石墨",
      neon: "荧光",
    });
    expect(en.setting.themePreset.presets).toMatchObject({
      default: "Default",
      ocean: "Ocean",
      energy: "Energy",
      forest: "Forest",
      violet: "Violet",
      sunset: "Sunset",
      cherry: "Cherry",
      amber: "Amber",
      porcelain: "Porcelain",
      midnight: "Midnight",
      graphite: "Graphite",
      neon: "Neon",
    });
  });

  it("SCSS 为每套预设提供八个通用图表变量", () => {
    const scss = readFileSync(
      resolve(process.cwd(), "src/styles/theme-presets/index.scss"),
      "utf8"
    );
    for (const preset of THEME_PRESETS) {
      expect(scss).toContain(`data-theme-preset="${preset.code}"`);
    }
    for (let index = 1; index <= 8; index++) {
      expect(scss).toContain(`--fa-chart-${index}`);
    }
  });

  it("关键按钮和侧栏文字组合达到 WCAG AA", () => {
    for (const preset of THEME_PRESETS) {
      for (const mode of ["light", "dark"] as const) {
        const tokens = preset.modes[mode];
        const buttonText = mode === "dark" ? "#0A0F1E" : "#FFFFFF";
        const minimumButtonContrast = preset.code === "energy" ? 3 : 4.5;
        expect(contrastRatio(buttonText, preset.primary[mode])).toBeGreaterThanOrEqual(
          minimumButtonContrast
        );
        expect(contrastRatio(tokens.sidebarText, tokens.sidebarBackground)).toBeGreaterThanOrEqual(
          4.5
        );
        expect(
          contrastRatio(tokens.sidebarActiveText, tokens.sidebarActiveBackground)
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});

describe("主题预设生命周期与图表消费", () => {
  it("重置删除当前上下文隔离键并恢复 default", () => {
    setupContext({ siteCode: "main", tenantId: 3, userId: 15 });
    selectPreset("violet");
    const key = getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: 15 })!;
    expect(localStorage.getItem(key)).toBe("violet");
    expect(resetToDefaultPreset()).toBe("default");
    expect(localStorage.getItem(key)).toBeNull();
    expect(useSettingsStore().themePreset).toBe("default");
  });

  it("图表优先读取连续的 --fa-chart-* 色板", () => {
    document.documentElement.style.setProperty("--fa-chart-1", "#111111");
    document.documentElement.style.setProperty("--fa-chart-2", "#222222");
    expect(useChartOps().colors).toEqual(["#111111", "#222222"]);
    document.documentElement.style.removeProperty("--fa-chart-1");
    document.documentElement.style.removeProperty("--fa-chart-2");
    expect(useChartOps().colors.length).toBeGreaterThan(2);
  });

  it("启动、租户切换、登出和设置重置均接入预设生命周期", () => {
    const bootstrap = readFileSync(
      resolve(process.cwd(), "src/hooks/core/useAppBootstrap.ts"),
      "utf8"
    );
    const userStore = readFileSync(
      resolve(process.cwd(), "src/store/modules/user.store.ts"),
      "utf8"
    );
    const actions = readFileSync(
      resolve(
        process.cwd(),
        "src/components/layouts/fa-settings-panel/widgets/FaSettingActions.vue"
      ),
      "utf8"
    );
    expect(bootstrap).toContain("resolveAndApplyPreset");
    expect(userStore).toContain("resolveAndApplyPreset");
    expect(userStore).toContain('applyPreset("default", { restoreFactory: false })');
    expect(actions).toContain("resetToDefaultPreset");
  });
});
