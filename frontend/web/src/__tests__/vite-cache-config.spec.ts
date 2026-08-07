// @vitest-environment node

import path from "node:path";
import { describe, expect, it } from "vitest";
import createViteConfig from "../../vite.config";
import {
  collectElementPlusComponentNames,
  createElementPlusStyleIncludes,
} from "../../build/elementPlusStyleIncludes";

describe("Vite dependency cache", () => {
  it("不同 mode 使用独立的依赖缓存目录", () => {
    const development = createViteConfig({ mode: "development" });
    const sitePreview = createViteConfig({ mode: "site-preview" });

    expect(development.cacheDir).toBe(
      path.resolve(process.cwd(), "node_modules/.vite", "development")
    );
    expect(sitePreview.cacheDir).toBe(
      path.resolve(process.cwd(), "node_modules/.vite", "site-preview")
    );
    expect(development.cacheDir).not.toBe(sitePreview.cacheDir);
  });

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
});
