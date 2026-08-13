import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = path.resolve(__dirname, "../..");

describe("Site brand motion", () => {
  it("supports each food product motion and accessibility pause rules", () => {
    const component = fs.readFileSync(
      path.join(webRoot, "src/components/brand/FaSiteBrandMotion.vue"),
      "utf8"
    );
    const style = fs.readFileSync(
      path.join(webRoot, "src/styles/brand/site-brand-motion.scss"),
      "utf8"
    );

    for (const motion of ["trace-scan", "agri-route", "logistic-cold"]) {
      expect(style).toContain(motion);
    }
    expect(component).toContain("visibilitychange");
    expect(component).toContain('aria-hidden="true"');
    expect(style).toMatch(/animation-duration:\s*(?:[6-9]|10)s/);
    expect(style).toContain("prefers-reduced-motion: reduce");
    expect(style).toContain("pointer-events: none");
    expect(style).toContain("width <= 768px");
  });

  it("replaces the generic product illustration with the Site brand component", () => {
    const left = fs.readFileSync(
      path.join(webRoot, "src/components/views/fa-login/backdrops/FaLoginLeftView.vue"),
      "utf8"
    );
    expect(left).toContain("FaSiteBrandMotion");
    expect(left).toContain("resolveSiteBrandTheme");
    expect(left).toContain(':brand="productBrand"');
  });
});
