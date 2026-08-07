# Frontend Loading Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the deployed static-resource path and materially reduce development cold start, production entry dependencies, and configuration bootstrap latency without changing business or permission behavior.

**Architecture:** Keep the application entry limited to framework primitives, move optional capabilities behind dynamic imports, derive Vite Element Plus style warmup entries from checked-in source usage, and fetch independent configuration layers concurrently before merging them in the existing priority order. Nginx will temporarily force HTTP/1.1 and apply separate immutable-asset and no-cache-HTML policies.

**Tech Stack:** Vue 3, TypeScript, Vite 7, Vitest, Pinia, Element Plus, Nginx.

---

## File map

- Create `frontend/web/build/elementPlusStyleIncludes.ts`: pure component-name conversion plus source-tree style dependency collection.
- Modify `frontend/web/vite.config.ts`: consume the focused Element Plus style collector and stop prebuilding optional page-only packages.
- Modify `frontend/web/src/plugins/index.ts`: remove optional plugin side effects from startup.
- Modify `frontend/web/src/plugins/iconify.ts`: make offline Iconify collection loading idempotent and dynamic.
- Modify `frontend/web/src/components/base/fa-svg-icon/index.vue`: request offline collections only when an Iconify component is actually rendered.
- Modify `frontend/web/src/App.vue`: load the AI assistant asynchronously only when its existing guard is true.
- Modify `frontend/web/src/directives/business/highlight.ts`: dynamically import Highlight.js on first directive use.
- Modify `frontend/web/src/store/modules/config.store.ts`: request system, site, and optional tenant layers concurrently, then merge deterministically.
- Modify `docker/nginx/nginx.conf`: temporarily disable HTTP/2, serve precompressed files, cache hashed assets, and revalidate HTML.
- Extend `frontend/web/src/__tests__/vite-cache-config.spec.ts`: focused dependency warmup regression.
- Create `frontend/web/src/__tests__/startup-dependencies.spec.ts`: startup source-boundary and async component regression.
- Extend `frontend/web/src/__tests__/site-public-config.spec.ts`: concurrent-request and merge/fallback regression.
- Create `frontend/web/src/__tests__/nginx-web-config.spec.ts`: Nginx reliability/cache policy regression.

### Task 1: Focus Vite dependency warmup on checked-in usage

**Files:**
- Create: `frontend/web/build/elementPlusStyleIncludes.ts`
- Modify: `frontend/web/vite.config.ts:24-66,237-289`
- Test: `frontend/web/src/__tests__/vite-cache-config.spec.ts`

- [x] **Step 1: Write the failing focused-warmup tests**

Add imports and assertions that describe the desired pure API:

```ts
import {
  collectElementPlusComponentNames,
  createElementPlusStyleIncludes,
} from "../../build/elementPlusStyleIncludes";

it("只为源码中实际使用的 Element Plus 组件生成样式预构建入口", () => {
  const names = collectElementPlusComponentNames(`
    <ElButton/><el-input/><ElDatePicker/><ElButton />
  `);
  expect(names).toEqual(["button", "date-picker", "input"]);
  expect(createElementPlusStyleIncludes(names, new Set(["button", "input"]))).toEqual([
    "element-plus/es/components/button/style/index",
    "element-plus/es/components/input/style/index",
  ]);
});

it("开发配置不再预构建页面级重型依赖", () => {
  const development = createViteConfig({ mode: "development" });
  const include = development.optimizeDeps?.include ?? [];
  expect(include).not.toContain("exceljs");
  expect(include).not.toContain("@wangeditor-next/editor");
  expect(include).not.toContain("xgplayer");
  expect(
    include.filter((item) => item.includes("element-plus/es/components/")).length
  ).toBeGreaterThan(0);
  expect(include.length).toBeLessThan(180);
});
```

- [x] **Step 2: Run RED**

Run:

```bash
cd frontend/web
pnpm vitest run src/__tests__/vite-cache-config.spec.ts
```

Expected: FAIL because `build/elementPlusStyleIncludes.ts` does not exist and the current configuration still includes optional heavy packages.

- [x] **Step 3: Implement the collector and focused Vite configuration**

Create a build helper with these exported contracts:

```ts
import fs from "node:fs";
import path from "node:path";

export function toKebabCase(name: string): string {
  return name
    .replace(/([a-z\d])([A-Z])/g, "$1-$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1-$2")
    .toLowerCase();
}

export function collectElementPlusComponentNames(source: string): string[] {
  const names = new Set<string>();
  for (const match of source.matchAll(/<(?:El|el-)([A-Za-z][A-Za-z0-9-]*)\b/g)) {
    names.add(toKebabCase(match[1]));
  }
  return [...names].sort();
}

export function createElementPlusStyleIncludes(
  used: Iterable<string>,
  available: ReadonlySet<string>
): string[] {
  return [...new Set(used)]
    .filter((name) => available.has(name))
    .sort()
    .map((name) => `element-plus/es/components/${name}/style/index`);
}

export function scanElementPlusStyleIncludes(sourceRoot: string, componentsRoot: string): string[] {
  const sources: string[] = [];
  const walk = (directory: string) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(target);
      else if (/\.(vue|ts|tsx)$/.test(entry.name)) sources.push(fs.readFileSync(target, "utf8"));
    }
  };
  walk(sourceRoot);
  const available = new Set(
    fs
      .readdirSync(componentsRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
  );
  return createElementPlusStyleIncludes(
    sources.flatMap(collectElementPlusComponentNames),
    available
  );
}
```

Import `scanElementPlusStyleIncludes` in `vite.config.ts`, replace the installation-directory full scan, and remove page-only packages from `optimizeDeps.include`. Keep Vue, Router, Pinia, Axios, Element Plus core, current locale modules, and the focused style list.

- [x] **Step 4: Run GREEN**

Run the focused Vitest command again. Expected: all tests in `vite-cache-config.spec.ts` pass and the generated include list stays below the asserted upper bound.

### Task 2: Remove optional capabilities from startup

**Files:**
- Modify: `frontend/web/src/plugins/index.ts`
- Modify: `frontend/web/src/plugins/iconify.ts`
- Modify: `frontend/web/src/components/base/fa-svg-icon/index.vue`
- Modify: `frontend/web/src/App.vue`
- Modify: `frontend/web/src/directives/business/highlight.ts`
- Create: `frontend/web/src/__tests__/startup-dependencies.spec.ts`

- [x] **Step 1: Write source-boundary tests**

Use `readFileSync` to assert the stable architectural boundary:

```ts
// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const source = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

describe("application startup dependency boundary", () => {
  it("does not install page-only plugins during application startup", () => {
    const plugins = source("src/plugins/index.ts");
    expect(plugins).not.toContain('export * from "./echarts"');
    expect(plugins).not.toContain("initCodeMirror");
    expect(plugins).not.toContain("initTerminal");
    expect(plugins).not.toContain("initIconify()");
  });

  it("loads the AI assistant asynchronously behind its feature guard", () => {
    const app = source("src/App.vue");
    expect(app).toContain("defineAsyncComponent");
    expect(app).toContain('import("./components/others/fa-ai-assistant/index.vue")');
  });

  it("loads highlight.js only when the directive is used", () => {
    const highlight = source("src/directives/business/highlight.ts");
    expect(highlight).not.toContain('import hljs from "highlight.js"');
    expect(highlight).toContain('import("highlight.js")');
  });
});
```

- [x] **Step 2: Run RED**

Run:

```bash
cd frontend/web
pnpm vitest run src/__tests__/startup-dependencies.spec.ts
```

Expected: FAIL on all three current eager-loading assertions.

- [x] **Step 3: Implement minimal lazy boundaries**

In `plugins/index.ts`, remove the ECharts re-export and CodeMirror, Terminal, Iconify startup installation. Retain Store, Router, directives, error handling, i18n, the existing Element Plus compatibility installation, and Element Plus icon compatibility until the build confirms per-page icon imports cover the routes.

In `iconify.ts`, expose one idempotent dynamic loader:

```ts
let collectionPromise: Promise<void> | null = null;

export function ensureIconifyCollections(): Promise<void> {
  if (!collectionPromise) {
    collectionPromise = Promise.all([
      import("@iconify-json/ri/icons.json"),
      import("@iconify-json/svg-spinners/icons.json"),
      import("@iconify-json/line-md/icons.json"),
    ]).then(([ri, spinners, lineMd]) => {
      addCollection(ri.default);
      addCollection(spinners.default);
      addCollection(lineMd.default);
    });
  }
  return collectionPromise;
}
```

In `FaSvgIcon`, call `void ensureIconifyCollections()` from `onMounted` only when `props.icon` is non-empty, and watch later icon changes with the same idempotent loader.

In `App.vue`, replace the eager assistant import with:

```ts
import { computed, defineAsyncComponent, onBeforeMount, onMounted, onUnmounted } from "vue";

const AiAssistant = defineAsyncComponent(
  () => import("./components/others/fa-ai-assistant/index.vue")
);
```

In the highlight directive, replace the top-level import with a cached `loadHighlight()` promise. Make processing functions async and recheck `el._highlightActive` after awaiting the module so unmounted elements are never mutated.

- [x] **Step 4: Run GREEN and the existing smoke test**

Run:

```bash
cd frontend/web
pnpm vitest run src/__tests__/startup-dependencies.spec.ts src/__tests__/smoke.spec.ts
```

Expected: both test files pass.

### Task 3: Fetch independent configuration layers concurrently

**Files:**
- Modify: `frontend/web/src/store/modules/config.store.ts:135-203`
- Modify: `frontend/web/src/__tests__/site-public-config.spec.ts`

- [x] **Step 1: Write a failing concurrency regression**

Add explicit `SiteAPI` and `TenantAPI` mocks so their promises can be controlled independently. Assert all three functions have been called before resolving any of them:

```ts
it("starts system, site, and tenant configuration requests concurrently", async () => {
  let resolveSystem!: (value: any) => void;
  let resolveSite!: (value: any) => void;
  let resolveTenant!: (value: any) => void;
  getInitConfigMock.mockReturnValue(new Promise((resolve) => (resolveSystem = resolve)));
  getPublicConfigMock.mockReturnValue(new Promise((resolve) => (resolveSite = resolve)));
  getTenantConfigMock.mockReturnValue(new Promise((resolve) => (resolveTenant = resolve)));

  const pending = useConfigStore().getConfig(false, 9);
  await Promise.resolve();

  expect(getInitConfigMock).toHaveBeenCalledOnce();
  expect(getPublicConfigMock).toHaveBeenCalledOnce();
  expect(getTenantConfigMock).toHaveBeenCalledWith(9);

  resolveSystem({ data: { data: [] } });
  resolveSite({ data: { data: { site_code: "main", name: "站点" } } });
  resolveTenant({ data: { data: [] } });
  await pending;
});
```

- [x] **Step 2: Run RED**

Run:

```bash
cd frontend/web
pnpm vitest run src/__tests__/site-public-config.spec.ts
```

Expected: the site and tenant expectations fail because current code waits for the system request first.

- [x] **Step 3: Implement concurrent fetch with deterministic merge**

Start all applicable requests before awaiting:

```ts
const systemPromise = ParamsAPI.getInitConfig();
const sitePromise = SiteAPI.getPublicConfig().catch((error) => {
  console.warn("[configStore] 获取站点公开配置失败（非关键错误）", error);
  return null;
});
const tenantPromise =
  resolvedTenantId === null
    ? Promise.resolve(null)
    : TenantAPI.getTenantConfig(resolvedTenantId).catch((error) => {
        console.warn("[configStore] 获取认证租户配置失败（非关键错误）", error);
        return null;
      });

const [response, siteResp, tenantResp] = await Promise.all([
  systemPromise,
  sitePromise,
  tenantPromise,
]);
```

Keep the current validation of the system list, then clear and apply layers in this exact order: system, site, tenant. Clear stale site/tenant layers before applying optional responses, and preserve `currentTenantConfigId` behavior.

- [x] **Step 4: Run GREEN**

Run the full `site-public-config.spec.ts`; expected: concurrency plus all existing brand, tenant switch, refresh, and fallback tests pass.

### Task 4: Make Nginx static delivery reliable and cacheable

**Files:**
- Modify: `docker/nginx/nginx.conf:73-105`
- Create: `frontend/web/src/__tests__/nginx-web-config.spec.ts`

- [x] **Step 1: Write the failing Nginx policy test**

```ts
// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const nginx = readFileSync(resolve(process.cwd(), "../../docker/nginx/nginx.conf"), "utf8");

describe("web static delivery policy", () => {
  it("temporarily forces HTTP/1.1 for HTTPS", () => {
    expect(nginx).toContain("listen 443 ssl;");
    expect(nginx).not.toContain("listen 443 ssl http2;");
  });

  it("revalidates HTML and caches hashed assets immutably", () => {
    expect(nginx).toContain("location = /web/index.html");
    expect(nginx).toContain('add_header Cache-Control "no-cache" always;');
    expect(nginx).toContain('add_header Cache-Control "public, max-age=31536000, immutable" always;');
    expect(nginx).toContain("gzip_static on;");
  });
});
```

- [x] **Step 2: Run RED**

Run:

```bash
cd frontend/web
pnpm vitest run src/__tests__/nginx-web-config.spec.ts
```

Expected: FAIL because HTTPS still advertises HTTP/2 and the cache locations do not exist.

- [x] **Step 3: Implement the Nginx policy**

Change the HTTPS listener to `listen 443 ssl;`, enable `gzip_static on;`, and place specific locations before the generic `/web` fallback:

```nginx
location = /web/index.html {
    alias /usr/share/nginx/html/web/dist/index.html;
    add_header Cache-Control "no-cache" always;
}

location ~* ^/web/((?:js|css|img|fonts)/.+\.[A-Za-z0-9_-]+\.(?:js|css|png|jpe?g|gif|svg|webp|woff2?|eot|ttf|otf))$ {
    alias /usr/share/nginx/html/web/dist/$1;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
}

location /web {
    alias /usr/share/nginx/html/web/dist;
    try_files $uri $uri/ /web/index.html;
    add_header Cache-Control "no-cache" always;
}
```

Before finalizing, validate the regular-expression alias syntax with `nginx -t` if a local Nginx binary or the repository container image is available. If not available, retain the Vitest static assertion and report the deployment-time `nginx -t` boundary explicitly.

- [x] **Step 4: Run GREEN**

Run the focused Nginx policy test; expected: pass.

### Task 5: Verify the whole optimization and commit exact paths

**Files:**
- All files listed above
- Include plan: `docs/superpowers/plans/2026-08-07-frontend-loading-performance.md`

- [x] **Step 1: Run focused regression tests**

```bash
cd frontend/web
pnpm vitest run \
  src/__tests__/vite-cache-config.spec.ts \
  src/__tests__/startup-dependencies.spec.ts \
  src/__tests__/site-public-config.spec.ts \
  src/__tests__/nginx-web-config.spec.ts \
  src/__tests__/smoke.spec.ts
```

Expected: all selected test files pass with zero failures.

- [x] **Step 2: Run static/type gates**

```bash
cd frontend/web
pnpm type-check
pnpm eslint \
  build/elementPlusStyleIncludes.ts \
  vite.config.ts \
  src/plugins/index.ts \
  src/plugins/iconify.ts \
  src/components/base/fa-svg-icon/index.vue \
  src/App.vue \
  src/directives/business/highlight.ts \
  src/store/modules/config.store.ts \
  src/__tests__/vite-cache-config.spec.ts \
  src/__tests__/startup-dependencies.spec.ts \
  src/__tests__/site-public-config.spec.ts \
  src/__tests__/nginx-web-config.spec.ts
```

Expected: exit code 0 for both commands.

- [x] **Step 3: Build production and inspect the entry**

```bash
cd frontend/web
pnpm build
```

Parse `dist/index.html` and record its modulepreload/style count and referenced raw size. Assert that `echarts`, `codemirror`, `vue-web-terminal`, `wangeditor`, and `xgplayer` no longer appear as unconditional entry references. If any remains, trace its static importer before committing.

- [x] **Step 4: Check scope and whitespace**

```bash
git diff --check
git status --short
git diff --stat
```

Confirm the existing tenant initial-admin files and `.understand-anything/` remain unstaged and unmodified by this task.

- [x] **Step 5: Commit only performance paths**

```bash
git add \
  docs/superpowers/plans/2026-08-07-frontend-loading-performance.md \
  frontend/web/build/elementPlusStyleIncludes.ts \
  frontend/web/vite.config.ts \
  frontend/web/src/plugins/index.ts \
  frontend/web/src/plugins/iconify.ts \
  frontend/web/src/components/base/fa-svg-icon/index.vue \
  frontend/web/src/App.vue \
  frontend/web/src/directives/business/highlight.ts \
  frontend/web/src/store/modules/config.store.ts \
  frontend/web/src/__tests__/vite-cache-config.spec.ts \
  frontend/web/src/__tests__/startup-dependencies.spec.ts \
  frontend/web/src/__tests__/site-public-config.spec.ts \
  frontend/web/src/__tests__/nginx-web-config.spec.ts \
  docker/nginx/nginx.conf
git diff --cached --check
git commit -m "fix: 优化前端加载与静态资源可靠性"
```

Expected: one implementation commit containing only the listed performance paths; no push.
