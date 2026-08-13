import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { getLockedFoodLogiThemePreset, getThemePreset } from "@/config/themePresets";

const webRoot = path.resolve(__dirname, "../..");

describe("food logistics Site theme lock", () => {
  it("maps the two Sites to dedicated light and dark tokens", () => {
    expect(getLockedFoodLogiThemePreset("food-traceability", "trace.data360.org.cn")).toBe("ocean");
    expect(getLockedFoodLogiThemePreset("food-traceability", "trace.znceedi.org.cn")).toBe(
      "energy"
    );
    expect(getLockedFoodLogiThemePreset("default", "trace.znceedi.org.cn")).toBeNull();

    expect(getThemePreset("ocean")?.primary).toEqual({ light: "#2563EB", dark: "#60A5FA" });
    expect(getThemePreset("ocean")?.modes.dark.sidebarBackground).toBe("#07172E");
    expect(getThemePreset("energy")?.primary).toEqual({ light: "#078C72", dark: "#2DD4BF" });
    expect(getThemePreset("energy")?.modes.dark.sidebarBackground).toBe("#052E2B");
  });

  it("locks the store setter to the current Site preset for food assemblies", () => {
    const store = fs.readFileSync(path.join(webRoot, "src/store/modules/setting.store.ts"), "utf8");
    expect(store).toContain("getLockedFoodLogiThemePreset");
    expect(store).toContain("lockedSiteThemePreset");
    expect(store).toContain("preset === lockedSiteThemePreset.value");
  });

  it("hides palette and settings preset controls while retaining light and dark toggle", () => {
    const topBar = fs.readFileSync(
      path.join(webRoot, "src/components/views/fa-login/widgets/FaAuthTopBar.vue"),
      "utf8"
    );
    const settings = fs.readFileSync(
      path.join(webRoot, "src/components/layouts/fa-settings-panel/index.vue"),
      "utf8"
    );

    expect(topBar).toContain('v-if="!isFoodProduct"');
    expect(topBar).toContain('@click="themeAnimation"');
    expect(settings).toContain('v-if="!isFoodProduct"');
    expect(settings).toContain("<FaThemeSettings />");
  });
});
