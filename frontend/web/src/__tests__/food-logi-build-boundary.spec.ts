import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = path.resolve(__dirname, "../..");
const boundaryModule = path.join(webRoot, "build/foodLogiViewBoundary.ts");
const boundaryImport = "../../build/foodLogiViewBoundary";

describe("food logi build-time view boundary", () => {
  it("provides a Vite transform that narrows the ComponentLoader glob", async () => {
    expect(fs.existsSync(boundaryModule)).toBe(true);
    const boundary = await import(/* @vite-ignore */ boundaryImport);
    const source = 'this.modules = import.meta.glob("../../views/**/*.vue");';
    const transformed = boundary.transformFoodLogiComponentLoader(
      source,
      "/workspace/src/router/core/ComponentLoader.ts",
      "food-traceability"
    );

    expect(transformed).not.toContain('"!../../views/module_trace/**/*.vue"');
    expect(transformed).toContain('"!../../views/module_agri/**/*.vue"');
    expect(transformed).toContain('"!../../views/module_logistic/**/*.vue"');
    expect(transformed).toContain('"!../../views/module_ai/**/*.vue"');
    expect(transformed).toContain('"!../../views/dashboard/screen/**/*.vue"');
    expect(
      boundary.transformFoodLogiComponentLoader(
        source,
        "/workspace/src/router/core/ComponentLoader.ts",
        "default"
      )
    ).toBe(source);
  });

  it("reports forbidden components in a built product artifact", async () => {
    expect(fs.existsSync(boundaryModule)).toBe(true);
    const boundary = await import(/* @vite-ignore */ boundaryImport);
    const output = fs.mkdtempSync(path.join(os.tmpdir(), "food-logi-build-"));
    fs.mkdirSync(path.join(output, "js"));
    fs.writeFileSync(path.join(output, "js/FaChat.deadbeef.js"), "FaAiModelConfigPanel");

    expect(boundary.findForbiddenBuildArtifacts(output, "food-traceability")).toEqual([
      expect.stringContaining("FaChat.deadbeef.js"),
    ]);
  });

  it("classifies direct imports outside ComponentLoader as excluded", async () => {
    const boundary = await import(/* @vite-ignore */ boundaryImport);
    const root = "/workspace/src";
    expect(
      boundary.isExcludedFoodLogiModule(
        `${root}/views/dashboard/workplace/index.vue`,
        "food-traceability"
      )
    ).toBe(true);
    expect(
      boundary.isExcludedFoodLogiModule(
        `${root}/views/module_ai/chat/components/FaAiModelConfigPanel.vue`,
        "food-traceability"
      )
    ).toBe(true);
    expect(
      boundary.isExcludedFoodLogiModule(
        `${root}/components/others/fa-ai-assistant/index.vue`,
        "food-traceability"
      )
    ).toBe(true);
    expect(
      boundary.isExcludedFoodLogiModule(
        `${root}/views/module_trace/screen/index.vue`,
        "food-traceability"
      )
    ).toBe(false);
    expect(
      boundary.isExcludedFoodLogiModule(`${root}/views/module_ai/chat/index.vue`, "default")
    ).toBe(false);
  });

  it("removes the AI model drawer reference before Vue component auto-import", async () => {
    const boundary = await import(/* @vite-ignore */ boundaryImport);
    const source = '<FaConfigInfoDrawer v-model="paramDrawerVisible" />';
    expect(
      boundary.transformFoodLogiSource(
        source,
        "/workspace/src/components/layouts/fa-header-bar/widgets/FaUserMenu.vue",
        "food-traceability"
      )
    ).not.toContain("FaConfigInfoDrawer");
    expect(
      boundary.transformFoodLogiSource(
        source,
        "/workspace/src/components/layouts/fa-header-bar/widgets/FaUserMenu.vue",
        "default"
      )
    ).toBe(source);
  });

  it("registers the build boundary plugin in Vite", () => {
    const config = fs.readFileSync(path.join(webRoot, "vite.config.ts"), "utf8");
    expect(config).toContain('foodLogiViewBoundaryPlugin(env.VITE_APP_ASSEMBLY || "")');
  });
});
