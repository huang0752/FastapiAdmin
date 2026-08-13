export const FOOD_LOGI_PRODUCT_BRANDS = {
  "food-traceability": {
    product: "trace",
    title: "食品安全质量追溯系统",
  },
  "agricultural-delivery": {
    product: "agri",
    title: "农产品配送管理系统",
  },
  "cold-chain-vehicle": {
    product: "logistic",
    title: "冷链物流配送车辆管理系统",
  },
} as const;

export type FoodLogiAssembly = keyof typeof FOOD_LOGI_PRODUCT_BRANDS;

export interface FoodLogiBrand {
  title: string;
  logo: string;
}

import { resolveSiteBrandTheme } from "../brand/siteBrandTheme";

const PRODUCT_AUTHORITATIVE_BRAND_FIELDS = new Set([
  "name",
  "tenant_name",
  "logo_url",
  "tenant_logo",
  "favicon",
]);

export function isFoodLogiProductAssembly(assembly: string): assembly is FoodLogiAssembly {
  return assembly in FOOD_LOGI_PRODUCT_BRANDS;
}

export function resolveFoodLogiBrand(
  assembly: string,
  siteOrHost: string,
  baseUrl: string = import.meta.env.BASE_URL
): FoodLogiBrand | null {
  const brand = resolveSiteBrandTheme(assembly, siteOrHost, baseUrl);
  return brand ? { title: brand.title, logo: brand.logo } : null;
}

export function allowsFoodLogiTenantBrandField(assembly: string, key: string): boolean {
  return !isFoodLogiProductAssembly(assembly) || !PRODUCT_AUTHORITATIVE_BRAND_FIELDS.has(key);
}
