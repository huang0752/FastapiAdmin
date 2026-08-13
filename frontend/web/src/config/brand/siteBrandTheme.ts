import {
  FOOD_LOGI_PRODUCT_BRANDS,
  isFoodLogiProductAssembly,
  type FoodLogiAssembly,
} from "../assembly/foodLogiBrand";

export type SiteCode = "data360" | "znceedi";
export type FoodSystem = "trace" | "agri" | "logistic";
export type SiteThemePreset = "ocean" | "energy";
export type FoodBrandMotion = "trace-scan" | "agri-route" | "logistic-cold";

export interface SiteBrandTokens {
  primary: string;
  highlight: string;
  paper: string;
  base: string;
}

export interface SiteBrandTheme {
  site: SiteCode;
  system: FoodSystem;
  title: string;
  preset: SiteThemePreset;
  logo: string;
  favicon: string;
  motion: FoodBrandMotion;
  tokens: SiteBrandTokens;
}

const SYSTEM_MOTION: Record<FoodSystem, FoodBrandMotion> = {
  trace: "trace-scan",
  agri: "agri-route",
  logistic: "logistic-cold",
};

export function normalizeBrandBaseUrl(baseUrl: string): string {
  const value = baseUrl.trim() || "/";
  const rooted = value.startsWith("/") ? value : `/${value}`;
  return rooted.endsWith("/") ? rooted : `${rooted}/`;
}

export function resolveFoodLogiSite(siteOrHost: string): SiteCode {
  const normalized = siteOrHost.trim().toLowerCase();
  return normalized === "znceedi" || normalized.includes(".znceedi.") ? "znceedi" : "data360";
}

export function resolveSiteBrandTheme(
  assembly: string,
  siteOrHost: string,
  baseUrl: string = import.meta.env.BASE_URL
): SiteBrandTheme | null {
  if (!isFoodLogiProductAssembly(assembly)) return null;

  const product = FOOD_LOGI_PRODUCT_BRANDS[assembly as FoodLogiAssembly];
  const site = resolveFoodLogiSite(siteOrHost);
  const root = normalizeBrandBaseUrl(baseUrl);

  return {
    site,
    system: product.product,
    title: product.title,
    preset: site === "znceedi" ? "energy" : "ocean",
    logo: `${root}brand/logos/${site}-${product.product}.png`,
    favicon: `${root}brand/favicons/${site}-${product.product}.png`,
    motion: SYSTEM_MOTION[product.product],
    tokens:
      site === "znceedi"
        ? { primary: "#078C72", highlight: "#2DD4BF", paper: "#E4F1EE", base: "#052E2B" }
        : { primary: "#2563EB", highlight: "#38BDF8", paper: "#EAF1FF", base: "#07172E" },
  };
}
