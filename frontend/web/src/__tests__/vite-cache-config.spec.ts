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
      "element-plus/es/components/button/style/css",
      "element-plus/es/components/input/style/index",
      "element-plus/es/components/input/style/css",
    ]);
  });

  it("识别函数调用和加载指令使用的样式", () => {
    expect(collectElementPlusComponentNames(`
      ElMessage.success("ok"); ElMessageBox.confirm("confirm");
      ElNotification({ title: "notice" }); ElLoading.service({});
      <div v-loading="busy" />
    `)).toEqual(["loading", "message", "message-box", "notification"]);
  });

  it("冷启动扫描懒加载页面依赖，并排除测试文件", () => {
    const config = createViteConfig({ mode: "development" });
    expect(config.optimizeDeps?.entries).toEqual([
      "index.html",
      "src/**/*.{vue,ts,tsx}",
      "!src/**/*.d.ts",
      "!src/**/__tests__/**",
      "!src/**/*.{test,spec}.{ts,tsx,js}",
    ]);
  });

  it("不强制枚举页面级重型依赖，样式入口按组件去重计数", () => {
    const development = createViteConfig({ mode: "development" });
    const include = development.optimizeDeps?.include ?? [];

    expect(include).not.toContain("exceljs");
    expect(include).not.toContain("@wangeditor-next/editor");
    expect(include).not.toContain("xgplayer");
    expect(
      include.filter((item) => item.includes("element-plus/es/components/")).length
    ).toBeGreaterThan(0);
    expect(new Set(include.map((item) => item.replace(/\/style\/css$/, "/style/index"))).size).toBeLessThan(180);
  });
});
