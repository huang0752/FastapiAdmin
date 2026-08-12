import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = path.resolve(__dirname, "../..");
const brandModule = path.join(webRoot, "src/config/assembly/foodLogiBrand.ts");
const brandImport = "../config/assembly/foodLogiBrand";

const products = [
  ["food-traceability", "trace", "食品安全质量追溯系统"],
  ["agricultural-delivery", "agri", "农产品配送管理系统"],
  ["cold-chain-vehicle", "logistic", "冷链物流配送车辆管理系统"],
] as const;

describe("food logi product branding", () => {
  it("declares the exact official product titles in each build mode", () => {
    for (const [, mode, title] of products) {
      const env = fs.readFileSync(path.join(webRoot, `.env.${mode}`), "utf8");
      expect(env).toContain(`VITE_APP_TITLE = ${title}`);
      expect(title).not.toMatch(/[华夏中能].*电投/);
    }

    const appConfig = fs.readFileSync(path.join(webRoot, "src/config/index.ts"), "utf8");
    expect(appConfig).toContain("defaultAssemblySummary.title");
    expect(appConfig).not.toContain('name: "FastapiAdmin"');
  });

  it("resolves the current product logo from Site and fails closed to its default", async () => {
    expect(fs.existsSync(brandModule)).toBe(true);
    if (!fs.existsSync(brandModule)) return;

    const brand = await import(/* @vite-ignore */ brandImport);
    for (const [assembly, mode, title] of products) {
      expect(brand.resolveFoodLogiBrand(assembly, "data360.org.cn")).toEqual({
        title,
        logo: `/brand/logos/data360-${mode}.png`,
      });
      expect(brand.resolveFoodLogiBrand(assembly, `x.znceedi.org.cn`)).toEqual({
        title,
        logo: `/brand/logos/znceedi-${mode}.png`,
      });
      expect(brand.resolveFoodLogiBrand(assembly, "unknown.example")).toEqual({
        title,
        logo: `/brand/logos/data360-${mode}.png`,
      });
    }
    expect(brand.resolveFoodLogiBrand("default", "trace.data360.org.cn")).toBeNull();
  });

  it("keeps product title and Host/Site logo authoritative over tenant brand fields", async () => {
    const brand = await import(/* @vite-ignore */ brandImport);
    for (const key of ["name", "tenant_name", "logo_url", "tenant_logo", "favicon"]) {
      expect(brand.allowsFoodLogiTenantBrandField("food-traceability", key)).toBe(false);
    }
    expect(brand.allowsFoodLogiTenantBrandField("food-traceability", "copyright")).toBe(true);
    expect(brand.allowsFoodLogiTenantBrandField("default", "tenant_logo")).toBe(true);
  });

  it("keeps the build verifier responsible for title and logo artifacts", () => {
    const verifier = fs.readFileSync(
      path.join(webRoot, "scripts/verify-food-logi-build.ts"),
      "utf8"
    );
    expect(verifier).toContain("findFoodLogiBrandingViolations");
  });
});
