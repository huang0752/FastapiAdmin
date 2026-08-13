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
  const readSource = (relativePath: string) =>
    fs.readFileSync(path.join(webRoot, "src", relativePath), "utf8");

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
      expect(brand.resolveFoodLogiBrand(assembly, "data360.org.cn", "/web")).toEqual({
        title,
        logo: `/web/brand/logos/data360-${mode}.png`,
      });
      expect(brand.resolveFoodLogiBrand(assembly, `x.znceedi.org.cn`, "/web/")).toEqual({
        title,
        logo: `/web/brand/logos/znceedi-${mode}.png`,
      });
      expect(brand.resolveFoodLogiBrand(assembly, "unknown.example", "/web")).toEqual({
        title,
        logo: `/web/brand/logos/data360-${mode}.png`,
      });
    }
    expect(brand.resolveFoodLogiBrand("default", "trace.data360.org.cn")).toBeNull();
  });

  it("builds product logo URLs from Vite BASE_URL", () => {
    const source = fs.readFileSync(brandModule, "utf8");

    expect(source).toContain("import.meta.env.BASE_URL");
    expect(source).not.toContain("logo: `/brand/logos/");
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

  it("keeps captcha hidden until the API explicitly enables it", () => {
    const login = readSource("views/module_system/auth/login/index.vue");

    expect(login).toContain("const captchaState = reactive<CaptchaInfo>({\n  enable: false,");
    expect(login).toContain("captchaState.enable = data.enable === true;");
    expect(login).toContain('captchaState.img_base = "";');
    expect(login).toContain('loginForm.captcha_key = "";');
  });

  it("uses the build-time product brand in every real shell branding component", () => {
    const components = [
      "components/views/fa-login/backdrops/FaLoginLeftView.vue",
      "components/views/fa-login/widgets/FaAuthTopBar.vue",
      "components/layouts/fa-menus/fa-sidebar-menu/index.vue",
      "components/layouts/fa-header-bar/index.vue",
    ];

    for (const component of components) {
      const source = readSource(component);
      expect(source, component).toMatch(/resolve(?:FoodLogiBrand|SiteBrandTheme)/);
      expect(source, component).toContain("defaultAssemblySummary.name");
      expect(source, component).toContain("window.location.hostname");
      expect(source, component).toContain("productBrand.value?.logo");
      expect(source, component).toContain("productBrand.value?.title");
    }
  });

  it("uses the official product title for the login panel on first paint", () => {
    const login = readSource("views/module_system/auth/login/index.vue");
    const panelTitle = login.slice(
      login.indexOf("const panelTitle = computed(() =>"),
      login.indexOf("const panelSubTitle = computed(() =>")
    );

    expect(panelTitle).toContain("productBrand.value?.title");
    expect(panelTitle.indexOf("productBrand.value?.title")).toBeLessThan(
      panelTitle.indexOf('t("login.title")')
    );
  });
});
