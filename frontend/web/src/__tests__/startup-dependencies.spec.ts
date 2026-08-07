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
