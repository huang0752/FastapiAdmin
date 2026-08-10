# Generic Theme Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generic named theme presets to FastapiAdmin while preserving the existing Light/Dark/System controls and isolating user choices by site, tenant, and user.

**Architecture:** Keep `systemThemeType/systemThemeMode/systemThemeColor` as the single runtime theme source. Add a registry-driven preset overlay that applies CSS variables and menu tokens, with a scoped storage key as its only persistence source. Track the last automatically applied primary/menu values so context replay can distinguish preset-owned values from user customization.

**Tech Stack:** Vue 3, TypeScript, Pinia 3, pinia-plugin-persistedstate, VueUse, Element Plus, SCSS, Vitest, pnpm.

---

## File map

- Create `frontend/web/src/config/themePresets.ts`: generic preset types, four preset definitions, validation and lookup helpers.
- Create `frontend/web/src/hooks/core/useThemePreset.ts`: context resolution, scoped persistence, preset application, default restoration and mode replay.
- Create `frontend/web/src/components/layouts/fa-settings-panel/widgets/FaThemePresetSettings.vue`: visible preset selector.
- Create `frontend/web/src/components/layouts/fa-settings-panel/widgets/FaThemePresetPreview.vue`: live HTML/CSS thumbnail.
- Create `frontend/web/src/styles/theme-presets/index.scss`: generic chart variables and narrowly scoped sidebar overrides.
- Create `frontend/web/src/__tests__/theme-preset.spec.ts`: registry, persistence, migration, contrast and CSS synchronization coverage.
- Modify `frontend/web/src/store/modules/setting.store.ts`: add runtime preset state, preset-aware menu tokens and remove the duplicate theme watcher.
- Modify `frontend/web/src/hooks/core/useTheme.ts`: keep the canonical theme state synchronized through one application path.
- Modify `frontend/web/src/App.vue`, `frontend/web/src/components/others/fa-watermark/index.vue`, and `frontend/web/src/views/module_generator/gencode/components/FaCreateTableDialog.vue`: consume canonical theme state.
- Modify `frontend/web/src/hooks/core/useAppBootstrap.ts` and `frontend/web/src/store/modules/user.store.ts`: replay the scoped preset after bootstrap, login, logout and tenant switches.
- Modify `frontend/web/src/components/layouts/fa-settings-panel/index.vue`, locale JSON files and `frontend/web/src/styles/index.scss`: expose and style the selector.
- Modify `frontend/web/src/hooks/core/useChart.ts`: consume `--fa-chart-1..8` with the current palette as fallback.
- Modify `frontend/web/src/components/layouts/fa-settings-panel/widgets/FaSettingActions.vue`: make reset clear the scoped preset and resolve the current default.

### Task 1: Lock the preset registry contract with failing tests

**Files:**

- Create: `frontend/web/src/__tests__/theme-preset.spec.ts`
- Create: `frontend/web/src/config/themePresets.ts`

- [x] **Step 1: Write failing registry tests**

Add tests importing `THEME_PRESETS`, `getThemePreset`, `isThemePresetCode`, and `listPresetPrimaryColors`. Assert codes equal `ocean`, `forest`, `violet`, `sunset`, are unique, each mode has eight chart colors, and `default`/unknown lookups return `null`.

- [x] **Step 2: Run the focused test and verify RED**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: FAIL because `@/config/themePresets` does not exist.

- [x] **Step 3: Implement the registry**

Define:

```ts
export type ThemePresetCode =
  | "default"
  | "ocean"
  | "forest"
  | "violet"
  | "sunset";

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
  code: Exclude<ThemePresetCode, "default">;
  nameKey: "ocean" | "forest" | "violet" | "sunset";
  primary: { light: string; dark: string };
  semantics: Record<
    "success" | "warning" | "danger" | "error" | "info",
    string
  >;
  recommendedMenuTheme: MenuThemeEnum;
  modes: { light: ThemePresetModeTokens; dark: ThemePresetModeTokens };
}
```

Use framework-neutral blue, green, purple and orange palettes. Export the four helpers exercised by the test.

- [x] **Step 4: Run the focused test and verify GREEN**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: registry tests PASS.

### Task 2: Add scoped persistence and correct state transitions

**Files:**

- Modify: `frontend/web/src/store/modules/setting.store.ts`
- Create: `frontend/web/src/hooks/core/useThemePreset.ts`
- Test: `frontend/web/src/__tests__/theme-preset.spec.ts`

- [x] **Step 1: Write failing behavior tests**

Add tests for:

```ts
getThemePresetStorageKey({ siteCode: "main", tenantId: 3, userId: 15 });
// sys-v{version}-theme-preset-main-3-15
```

Cover incomplete contexts returning `null`, different tenants/users producing different keys, `themePreset` omitted from Pinia persistence, saved `default` restoring the factory primary/menu, automatic `sunset` dark-menu to `ocean` design-menu migration, and user-custom primary/menu preservation during non-interactive replay.

- [x] **Step 2: Run the focused test and verify RED**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: FAIL because preset state and `useThemePreset` exports do not exist.

- [x] **Step 3: Add runtime preset state**

In `setting.store.ts`, add:

```ts
const themePreset = ref<ThemePresetCode>("default");
const presetAppliedPrimary = ref<string | null>(null);
const presetAppliedMenuTheme = ref<MenuThemeEnum | null>(null);

const setThemePreset = (code: ThemePresetCode) => {
  themePreset.value = code;
};
```

Make `getMenuTheme` return preset menu tokens only while the current menu equals `presetAppliedMenuTheme`. Add all three fields to Pinia persistence `omit`.

- [x] **Step 4: Implement the preset hook**

Export `getThemePresetContext`, `getThemePresetStorageKey`, `applyPreset`, `selectPreset`, `resolveAndApplyPreset`, `restoreFactoryThemeAppearance`, and `resetToDefaultPreset`.

The transition rule is:

```ts
const primaryIsPresetOwned =
  currentPrimary.toLowerCase() === factoryPrimary.toLowerCase() ||
  currentPrimary.toLowerCase() ===
    settingStore.presetAppliedPrimary?.toLowerCase();
const menuIsPresetOwned =
  currentMenu === factoryMenu ||
  currentMenu === settingStore.presetAppliedMenuTheme;
```

Non-interactive replay replaces only preset-owned values. Interactive selection forces both values. Every `default` path calls `restoreFactoryThemeAppearance()` before returning.

- [x] **Step 5: Run the focused test and verify GREEN**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: persistence and transition tests PASS.

### Task 3: Converge the duplicate theme state

**Files:**

- Modify: `frontend/web/src/store/modules/setting.store.ts`
- Modify: `frontend/web/src/hooks/core/useTheme.ts`
- Modify: `frontend/web/src/App.vue`
- Modify: `frontend/web/src/components/others/fa-watermark/index.vue`
- Modify: `frontend/web/src/views/module_generator/gencode/components/FaCreateTableDialog.vue`
- Test: `frontend/web/src/__tests__/theme-preset.spec.ts`

- [x] **Step 1: Write failing canonical-state tests**

Assert `setElementTheme()` updates `systemThemeColor` and the runtime CSS primary, `setGlopTheme()` is the only Light/Dark/System state mutation path, and consumers derive dark mode from `isDark` rather than `theme`.

- [x] **Step 2: Run the focused test and verify RED**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: FAIL because the old `[theme, themeColor]` watcher still applies a second theme state.

- [x] **Step 3: Remove the duplicate application path**

Remove the `[theme, themeColor]` watcher and unused `applyTheme/generateThemeColors/toggleDarkMode` imports. Keep legacy `theme/themeColor` storage only as migration aliases; do not expose update methods as active theme APIs. Change App watermark, `fa-watermark`, and code editor theme consumers to `systemThemeColor` and `isDark`.

- [x] **Step 4: Synchronize canonical setters**

Ensure `setGlopTheme()` owns the HTML theme class through `useTheme`, and `setElementTheme()` owns `--el-color-primary`. Preset replay must call these existing paths instead of duplicating CSS generation.

- [x] **Step 5: Run the focused test and verify GREEN**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: canonical-state tests PASS.

### Task 4: Add the settings UI and generic CSS variables

**Files:**

- Create: `frontend/web/src/components/layouts/fa-settings-panel/widgets/FaThemePresetSettings.vue`
- Create: `frontend/web/src/components/layouts/fa-settings-panel/widgets/FaThemePresetPreview.vue`
- Create: `frontend/web/src/styles/theme-presets/index.scss`
- Modify: `frontend/web/src/components/layouts/fa-settings-panel/index.vue`
- Modify: `frontend/web/src/styles/index.scss`
- Modify: `frontend/web/src/locales/langs/zh.json`
- Modify: `frontend/web/src/locales/langs/en.json`
- Test: `frontend/web/src/__tests__/theme-preset.spec.ts`

- [x] **Step 1: Write failing UI/static-contract tests**

Assert the panel renders `FaThemePresetSettings`, locale keys exist for default/ocean/forest/violet/sunset/custom/hint, SCSS contains every `--fa-chart-1..8` value, and no file contains energy-carbon brand or package identifiers.

- [x] **Step 2: Run the focused test and verify RED**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: FAIL because the selector, locale keys and SCSS layer do not exist.

- [x] **Step 3: Implement the live preview and selector**

Render `default` plus all registry entries as existing `.setting-item` cards. Use a compact sidebar/button/three-bar chart preview. Clicking a preset calls `selectPreset(code)` and reloads the view only when accepted. Show “自定义/Custom” when current primary or menu no longer matches the preset-owned values.

- [x] **Step 4: Add the variable layer**

Set `data-theme-preset` selectors for `--fa-chart-1..8`. Add only narrowly scoped sidebar hover/active overrides needed for dark recommended menus. Import the layer from `styles/index.scss`.

- [x] **Step 5: Run the focused test and verify GREEN**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: UI/static-contract tests PASS.

### Task 5: Wire lifecycle replay, chart colors and reset

**Files:**

- Modify: `frontend/web/src/hooks/core/useAppBootstrap.ts`
- Modify: `frontend/web/src/store/modules/user.store.ts`
- Modify: `frontend/web/src/hooks/core/useChart.ts`
- Modify: `frontend/web/src/components/layouts/fa-settings-panel/widgets/FaSettingActions.vue`
- Test: `frontend/web/src/__tests__/theme-preset.spec.ts`

- [x] **Step 1: Write failing lifecycle tests**

Assert bootstrap replay runs after Site config, tenant selection replays after tenant config, logout removes the active overlay without writing another user's key, reset removes the current scoped key, and `useChartOps()` prefers contiguous `--fa-chart-*` variables before its existing fallback.

- [x] **Step 2: Run the focused test and verify RED**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: FAIL because lifecycle consumers do not call the preset hook.

- [x] **Step 3: Wire lifecycle calls**

Call `resolveAndApplyPreset()` after Site/config bootstrap and after authenticated tenant config refresh. On logout, call `applyPreset("default", { preserveFactoryControls: true })` so visual overrides are removed without persisting. Reset calls `resetToDefaultPreset()` after normal setting defaults.

- [x] **Step 4: Wire charts**

Read `--fa-chart-1` through `--fa-chart-8`, stop at the first missing value, and use the current color array when none are defined.

- [x] **Step 5: Run the focused test and verify GREEN**

Run: `cd frontend/web && pnpm vitest run src/__tests__/theme-preset.spec.ts`

Expected: lifecycle and chart tests PASS.

### Task 6: Full verification and one scoped feature commit

**Files:**

- Modify: `docs/superpowers/plans/2026-08-10-generic-theme-presets.md` checkboxes only as work completes.

- [x] **Step 1: Run formatting checks without broad rewrites**

Run Prettier check/fix only on changed files, then `git diff --check`.

Expected: no formatting or whitespace errors.

- [x] **Step 2: Run focused and complete verification**

Run:

```bash
cd frontend/web
pnpm vitest run src/__tests__/theme-preset.spec.ts
pnpm type-check
pnpm test
pnpm build
```

Expected: all commands exit 0.

- [x] **Step 3: Inspect the final scope**

Run `git status --short`, `git diff --stat`, `git diff --check`, and inspect every changed path. Confirm `.understand-anything/` is untracked and unstaged.

- [x] **Step 4: Commit exact paths**

Stage only the implementation plan, theme implementation, locale changes and tests. Commit:

```bash
git commit -m "feat: 增加通用主题预设"
```

- [x] **Step 5: Verify the commit**

Run `git show --stat --oneline HEAD` and `git status --short --branch`.

Expected: the feature commit contains only intended paths; `.understand-anything/` remains untracked.
