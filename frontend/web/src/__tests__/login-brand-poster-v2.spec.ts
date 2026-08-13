import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = path.resolve(__dirname, "../..");
const read = (file: string) => fs.readFileSync(path.join(webRoot, file), "utf8");

describe("food product login brand poster v2", () => {
  it("defines six complete Site and product identities without fake metrics", async () => {
    const { LOGIN_BRAND_POSTERS } = await import("@/config/brand/loginBrandPoster");
    expect(Object.keys(LOGIN_BRAND_POSTERS)).toHaveLength(6);
    for (const poster of Object.values(LOGIN_BRAND_POSTERS)) {
      expect(poster.headline.length).toBeGreaterThan(6);
      expect(poster.description.length).toBeGreaterThan(12);
      expect(poster.keywords).toHaveLength(3);
      expect(poster.statuses.length).toBeGreaterThanOrEqual(2);
      expect(poster.statuses.length).toBeLessThanOrEqual(3);
      expect(
        JSON.stringify([poster.headline, poster.description, poster.keywords, poster.statuses])
      ).not.toMatch(/订单量|交易量|成交额|实时营收|\d{3,}/);
    }
  });

  it("dispatches trace, agri and logistic to independent business scenes", () => {
    const dispatcher = read("src/components/brand/LoginBrandScene.vue");
    for (const scene of ["TraceBrandScene", "AgriBrandScene", "LogisticBrandScene"]) {
      expect(dispatcher).toContain(scene);
    }
    expect(read("src/components/brand/scenes/TraceBrandScene.vue")).toContain("trace-batch-chain");
    expect(read("src/components/brand/scenes/AgriBrandScene.vue")).toContain("agri-field-route");
    expect(read("src/components/brand/scenes/LogisticBrandScene.vue")).toContain(
      "logistic-temperature-lane"
    );
  });

  it("replaces the central badge with all five poster layers", () => {
    const left = read("src/components/views/fa-login/backdrops/FaLoginLeftView.vue");
    for (const layer of [
      "brand-layer",
      "narrative-layer",
      "status-layer",
      "spatial-layer",
      "copy-layer",
    ]) {
      expect(left).toContain(layer);
    }
    expect(left).toContain("LoginBrandScene");
    expect(left).not.toContain("FaSiteBrandMotion");
    expect(left).not.toContain("兼具设计美学与高效开发");
  });

  it("provides motion safety and two materially different Site compositions", () => {
    const motion = read("src/components/brand/BrandMotionLayer.vue");
    const style = read("src/styles/brand/login-brand-poster.scss");
    expect(motion).toContain("visibilitychange");
    expect(style).toContain("prefers-reduced-motion: reduce");
    expect(style).toContain("brand-poster--data360");
    expect(style).toContain("brand-poster--znceedi");
    expect(style).toContain("--poster-cut");
    expect(style).toContain("--poster-path-duration");
  });
});
