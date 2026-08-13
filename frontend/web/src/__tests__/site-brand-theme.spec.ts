import { describe, expect, it } from "vitest";
import { resolveSiteBrandTheme } from "@/config/brand/siteBrandTheme";

const matrix = [
  ["food-traceability", "trace", "trace-scan"],
  ["agricultural-delivery", "agri", "agri-route"],
  ["cold-chain-vehicle", "logistic", "logistic-cold"],
] as const;

describe("food logistics Site brand themes", () => {
  it("returns six unique product and Site brand contracts", () => {
    const brands = matrix.flatMap(([assembly, system, motion]) =>
      (["data360", "znceedi"] as const).map((site) => {
        const brand = resolveSiteBrandTheme(assembly, `${system}.${site}.org.cn`, "/web/");
        expect(brand).toMatchObject({
          site,
          system,
          preset: site === "data360" ? "ocean" : "energy",
          logo: `/web/brand/logos/${site}-${system}.png`,
          favicon: `/web/brand/favicons/${site}-${system}.png`,
          motion,
        });
        return brand!;
      })
    );

    expect(new Set(brands.map((brand) => brand.logo)).size).toBe(6);
    expect(new Set(brands.map((brand) => brand.favicon)).size).toBe(6);
  });

  it("defaults unknown and local hosts to data360", () => {
    expect(resolveSiteBrandTheme("food-traceability", "localhost", "/web")?.site).toBe(
      "data360"
    );
  });

  it("does not brand the default framework assembly", () => {
    expect(resolveSiteBrandTheme("default", "trace.data360.org.cn", "/web")).toBeNull();
  });
});
