# Expanded Generic Theme Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add six visually distinct generic theme presets while preserving the existing theme application, persistence, customization, and product-isolation boundaries.

**Architecture:** Extend the existing declarative `THEME_PRESETS` registry so the settings UI and application hook consume the new presets without new branches. Add matching sidebar CSS variables and locale labels, then prove registry completeness, accessibility, styling, persistence, and runtime selection with the existing focused test suite and browser checks.

**Tech Stack:** Vue 3, TypeScript, Pinia, SCSS, Vue I18n, Vitest, Element Plus

---

## File map

- Modify `frontend/web/src/config/themePresets.ts`: register the six new preset codes and all light/dark theme tokens.
- Modify `frontend/web/src/styles/theme-presets/index.scss`: expose matching light/dark sidebar CSS variables.
- Modify `frontend/web/src/locales/langs/zh.json`: add Chinese preset labels.
- Modify `frontend/web/src/locales/langs/en.json`: add English preset labels.
- Modify `frontend/web/src/__tests__/theme-preset.spec.ts`: define the expected registry, locale, selector, contrast, and application behavior before production changes.
- Modify `frontend/web/src/hooks/core/useThemePreset.ts`: retry cold-start restoration once the isolated site/tenant/user context becomes available.
- Modify `frontend/web/src/components/layouts/fa-settings-panel/composables/useSettingsPanel.ts`: accept preset primary colors during settings-panel initialization.

### Task 1: Define the expected six-preset expansion

**Files:**

- Modify: `frontend/web/src/__tests__/theme-preset.spec.ts`

- [ ] **Step 1: Write the failing registry and locale test**

Update the registry expectation to require all ten active presets and extend the locale object checks:

```ts
const EXPECTED_PRESET_CODES = [
  "ocean",
  "forest",
  "violet",
  "sunset",
  "cherry",
  "amber",
  "porcelain",
  "midnight",
  "graphite",
  "neon",
] as const;

expect(THEME_PRESETS.map((preset) => preset.code)).toEqual(
  EXPECTED_PRESET_CODES,
);
expect(zh.setting.themePreset.presets).toMatchObject({
  cherry: "樱桃",
  amber: "琥珀",
  porcelain: "青瓷",
  midnight: "极夜",
  graphite: "石墨",
  neon: "荧光",
});
expect(en.setting.themePreset.presets).toMatchObject({
  cherry: "Cherry",
  amber: "Amber",
  porcelain: "Porcelain",
  midnight: "Midnight",
  graphite: "Graphite",
  neon: "Neon",
});
```

- [ ] **Step 2: Write the failing recognition and application test**

Add assertions that each new code is recognized and that one representative high-contrast preset applies its DOM attribute and primary color:

```ts
for (const code of [
  "cherry",
  "amber",
  "porcelain",
  "midnight",
  "graphite",
  "neon",
]) {
  expect(isThemePresetCode(code)).toBe(true);
}

expect(selectPreset("midnight")).toBe(true);
expect(document.documentElement.dataset.themePreset).toBe("midnight");
expect(useSettingsStore().systemThemeColor).toBe(
  getThemePreset("midnight")!.primary.light,
);
```

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```bash
cd frontend/web
pnpm test -- src/__tests__/theme-preset.spec.ts
```

Expected: FAIL because the new codes, locale labels, and SCSS selectors do not exist.

### Task 2: Add registry data, styles, and labels

**Files:**

- Modify: `frontend/web/src/config/themePresets.ts`
- Modify: `frontend/web/src/styles/theme-presets/index.scss`
- Modify: `frontend/web/src/locales/langs/zh.json`
- Modify: `frontend/web/src/locales/langs/en.json`

- [ ] **Step 1: Extend the preset code union**

Add the six codes to `ThemePresetCode` while retaining `default` and the existing codes:

```ts
export type ThemePresetCode =
  | "default"
  | "ocean"
  | "forest"
  | "violet"
  | "sunset"
  | "cherry"
  | "amber"
  | "porcelain"
  | "midnight"
  | "graphite"
  | "neon";
```

- [ ] **Step 2: Add six complete registry records**

Append six `ThemePreset` records. Each record must include legal six-digit primary and semantic colors, a `MenuThemeEnum` recommendation, complete light/dark sidebar tokens, and exactly eight chart colors. Use these identity directions:

```ts
cherry:    light primary #BE185D, dark primary #F472B6, burgundy sidebar
amber:     light primary #A16207, dark primary #FBBF24, warm ivory/brown sidebar
porcelain: light primary #0F766E, dark primary #2DD4BF, deep teal sidebar
midnight:  light primary #1D4ED8, dark primary #38BDF8, near-black blue sidebar
graphite:  light primary #475569, dark primary #94A3B8, monochrome sidebar
neon:      light primary #3F6212, dark primary #A3E635, dark olive sidebar
```

For every light/dark primary, choose `--el-color-primary-light-*` compatible button text through the existing contrast helper and keep sidebar text visibly separated from its background.

- [ ] **Step 3: Add matching SCSS chart selectors**

For every new code, add both selectors using the existing eight-color chart variable set. Sidebar colors continue to come from the registry through `getMenuTheme`:

```scss
:root[data-theme-preset="cherry"] {
  --fa-chart-1: #be185d;
  // ... --fa-chart-2 through --fa-chart-8
}

:root[data-theme-preset="cherry"].dark {
  --fa-chart-1: #f472b6;
  // ... --fa-chart-2 through --fa-chart-8
}
```

Repeat with the exact light/dark chart palettes from each registry record so chart consumers and preview data cannot diverge.

- [ ] **Step 4: Add locale labels**

Add the six Chinese and English labels under `setting.themePreset.presets` without changing existing labels.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run:

```bash
cd frontend/web
pnpm test -- src/__tests__/theme-preset.spec.ts
```

Expected: the focused theme preset suite passes with zero failures.

### Task 3: Validate compatibility and runtime behavior

**Files:**

- Verify only; fix only files listed in Task 2 if a theme-specific defect is found.

- [ ] **Step 1: Run type checking**

Run:

```bash
cd frontend/web
pnpm type-check
```

Expected: exit code 0 with no TypeScript errors.

- [ ] **Step 2: Run the complete frontend test suite**

Run:

```bash
cd frontend/web
pnpm test
```

Expected: all Vitest files and tests pass.

- [ ] **Step 3: Verify in the running browser**

Open the settings panel at `http://127.0.0.1:5180/web#/example/demo-center/demo`, confirm eleven visible choices, and select at least `樱桃`, `琥珀`, `极夜`, `石墨`, and `荧光`. For each choice verify:

```text
the active preview changes
the sidebar background and active item change
the application stays on the current route
there is no console error
```

Refresh once after selecting `极夜` and verify the same preset is restored for the current site, tenant, and user.

- [ ] **Step 4: Inspect the exact diff**

Run:

```bash
git diff --check
git status --short
git diff -- frontend/web/src/config/themePresets.ts frontend/web/src/styles/theme-presets/index.scss frontend/web/src/locales/langs/zh.json frontend/web/src/locales/langs/en.json frontend/web/src/__tests__/theme-preset.spec.ts
```

Expected: no whitespace errors, no unrelated tracked files, and `.understand-anything/` remains untouched.

- [ ] **Step 5: Commit the implementation**

Run:

```bash
git add frontend/web/src/config/themePresets.ts frontend/web/src/styles/theme-presets/index.scss frontend/web/src/locales/langs/zh.json frontend/web/src/locales/langs/en.json frontend/web/src/__tests__/theme-preset.spec.ts frontend/web/src/hooks/core/useThemePreset.ts frontend/web/src/components/layouts/fa-settings-panel/composables/useSettingsPanel.ts docs/superpowers/plans/2026-08-10-expanded-theme-presets.md
git commit -m "feat: 扩充通用主题预设"
```

Expected: one implementation commit containing only the plan, registry, styles, locales, and theme tests.

### Task 4: Close the cold-start context race found during browser verification

**Files:**

- Modify: `frontend/web/src/__tests__/theme-preset.spec.ts`
- Modify: `frontend/web/src/hooks/core/useThemePreset.ts`

- [ ] **Step 1: Add a failing delayed-context restoration test**

Save `midnight` under a known isolated key, call `resolveAndApplyPreset()` while context is incomplete, then populate the site, tenant, and user stores and assert that the preset is applied after `nextTick()`.

- [ ] **Step 2: Verify the test fails because restoration is never retried**

Run `pnpm exec vitest run src/__tests__/theme-preset.spec.ts --reporter=dot` and expect the new assertion to receive no `data-theme-preset` attribute.

- [ ] **Step 3: Add a single pending-context watcher**

When `resolveAndApplyPreset()` has no storage key, install one watcher for `getThemePresetStorageKey(getThemePresetContext())`. Stop and clear that watcher as soon as a complete key appears, then call `resolveAndApplyPreset()` again. Keep the immediate default fallback and all complete-context behavior unchanged.

- [ ] **Step 4: Re-run focused and full verification**

Run the focused theme test, type check, complete frontend suite, and browser refresh restoration check. All must pass before commit.

### Task 5: Persist themes for tenantless platform administrators

**Files:**

- Modify: `frontend/web/src/__tests__/theme-preset.spec.ts`
- Modify: `frontend/web/src/hooks/core/useThemePreset.ts`

- [ ] **Step 1: Add a failing platform-scope persistence test**

Create a context with a site and user but no business tenant. Require `getThemePresetContext()` to return the explicit `platform` tenant scope, then select `midnight` and assert that its isolated key stores the preset.

- [ ] **Step 2: Verify RED**

Run the focused test and expect the platform-scope assertion to receive `null` before implementation.

- [ ] **Step 3: Add the explicit platform scope**

Extend `ThemePresetContext.tenantId` to `number | "platform" | null`. In `getThemePresetContext()`, return `platform` only when a valid user exists and neither user info nor current tenant supplies a numeric tenant ID. Keep anonymous/incomplete contexts as `null`.

- [ ] **Step 4: Re-run all verification**

Run focused tests, type checking, the complete frontend suite, then select and refresh `midnight` in the platform-admin browser session. Require the `data-theme-preset` and sidebar style to survive.

### Task 6: Scope unmatched Hosts after configuration finishes

**Files:**

- Modify: `frontend/web/src/__tests__/theme-preset.spec.ts`
- Modify: `frontend/web/src/hooks/core/useThemePreset.ts`

- [ ] **Step 1: Add a failing unmatched-Host test**

Mark configuration as loaded with no `site_code`, provide a platform user, and require `getThemePresetContext()` to return `origin` as the site scope and persist `midnight` under that isolated key.

- [ ] **Step 2: Verify RED**

Run the focused test and expect `siteCode` to remain `null` before implementation.

- [ ] **Step 3: Add the post-load Origin fallback**

When `site_code` exists, keep using it. While `configStore.isConfigLoaded` is false, keep returning `null` so cold-start restoration waits. Once loading has completed without a matched Site, return `origin`; localStorage is already Origin-isolated, so this cannot cross browser Origins.

- [ ] **Step 4: Remove diagnostics and re-run all verification**

Delete the temporary context debug log. Run focused tests, type checking, the full suite, and the same browser select/reload check.

### Task 7: Restore preset ownership after cold start

**Files:**

- Modify: `frontend/web/src/__tests__/theme-preset.spec.ts`
- Modify: `frontend/web/src/hooks/core/useThemePreset.ts`

- [ ] **Step 1: Add a failing ownership restoration test**

Persist `midnight`, initialize the settings store with its exact light primary and recommended menu theme, clear the non-persisted ownership markers, resolve the preset, and require both ownership markers to be restored.

- [ ] **Step 2: Verify RED**

Run the focused test and expect both markers to remain `null` before implementation.

- [ ] **Step 3: Recognize exact preset-owned values**

In `applyPreset`, treat the current primary as preset-owned when it equals the target primary for the current mode, and treat the menu as preset-owned when it equals the preset's recommendation. Preserve the existing factory, previous-ownership, forced-selection, and arbitrary-custom-value branches.

- [ ] **Step 4: Re-run all verification**

Require focused tests, type checking, full Vitest, and browser refresh to restore both `data-theme-preset="midnight"` and the configured light-mode midnight sidebar background.

### Task 8: Keep preset primary colors during settings-panel initialization

**Files:**

- Modify: `frontend/web/src/__tests__/theme-preset.spec.ts`
- Modify: `frontend/web/src/components/layouts/fa-settings-panel/composables/useSettingsPanel.ts`

- [ ] **Step 1: Add a failing settings-initialization regression test**

Require the settings panel initializer to recognize the registry's light and dark preset primary colors instead of accepting only the legacy `AppConfig.systemMainColor` list.

- [ ] **Step 2: Verify RED**

Run the focused test and confirm the existing initializer still resets `midnight` to the first factory primary.

- [ ] **Step 3: Include preset primaries in the validation set**

Use `listPresetPrimaryColors()` and case-insensitive comparisons in `initSystemColor()`. Preserve the fallback for arbitrary invalid values.

- [ ] **Step 4: Verify the complete lifecycle**

Run focused tests, type checking, full Vitest, and the production build. In the browser, select `midnight`, refresh the current route, and require `data-theme-preset="midnight"` plus `--el-color-primary: #1D4ED8` to survive.
