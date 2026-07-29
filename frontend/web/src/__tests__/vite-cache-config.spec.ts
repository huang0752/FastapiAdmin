// @vitest-environment node

import path from "node:path";
import { describe, expect, it } from "vitest";
import createViteConfig from "../../vite.config";

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
});
