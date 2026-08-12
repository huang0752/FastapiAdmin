import fs from "node:fs";
import path from "node:path";
import type { PluginOption } from "vite";
import {
  FOOD_LOGI_PRODUCT_BRANDS,
  isFoodLogiProductAssembly,
} from "../src/config/assembly/foodLogiBrand";

const FOOD_PRODUCT_VIEWS = {
  "food-traceability": "module_trace",
  "agricultural-delivery": "module_agri",
  "cold-chain-vehicle": "module_logistic",
} as const;

type FoodLogiAssembly = keyof typeof FOOD_PRODUCT_VIEWS;

const ALWAYS_EXCLUDED_VIEW_DIRS = [
  "module_ai",
  "module_generator",
  "module_example",
  "fastlink/fachat",
  "fastlink/pricing",
  "fastlink/article",
  "fastlink/tutorial",
  "fastlink/changelog",
  "dashboard/workplace",
  "dashboard/analysis",
  "dashboard/screen",
] as const;

const ALWAYS_EXCLUDED_COMPONENT_DIRS = [
  "components/others/fa-ai-assistant",
  "components/layouts/fa-chat-window",
] as const;

const COMPONENT_LOADER_GLOB = 'import.meta.glob("../../views/**/*.vue")';

function isFoodLogiAssembly(assembly: string): assembly is FoodLogiAssembly {
  return assembly in FOOD_PRODUCT_VIEWS;
}

export function foodLogiViewGlobPatterns(assembly: string): string[] | null {
  if (!isFoodLogiAssembly(assembly)) return null;
  const currentProduct = FOOD_PRODUCT_VIEWS[assembly];
  const excluded = [
    ...ALWAYS_EXCLUDED_VIEW_DIRS,
    ...Object.values(FOOD_PRODUCT_VIEWS).filter((view) => view !== currentProduct),
  ];
  return ["../../views/**/*.vue", ...excluded.map((view) => `!../../views/${view}/**/*.vue`)];
}

export function isExcludedFoodLogiModule(id: string, assembly: string): boolean {
  if (!isFoodLogiAssembly(assembly)) return false;
  const normalized = id.replaceAll("\\", "/").split("?", 1)[0] ?? "";
  const currentProduct = FOOD_PRODUCT_VIEWS[assembly];
  const excludedViews = [
    ...ALWAYS_EXCLUDED_VIEW_DIRS,
    ...Object.values(FOOD_PRODUCT_VIEWS).filter((view) => view !== currentProduct),
  ];
  return (
    excludedViews.some((view) => normalized.includes(`/src/views/${view}/`)) ||
    ALWAYS_EXCLUDED_COMPONENT_DIRS.some((component) => normalized.includes(`/src/${component}/`))
  );
}

export function transformFoodLogiComponentLoader(
  code: string,
  id: string,
  assembly: string
): string {
  const patterns = foodLogiViewGlobPatterns(assembly);
  if (!patterns || !id.replaceAll("\\", "/").endsWith("/src/router/core/ComponentLoader.ts")) {
    return code;
  }
  if (!code.includes(COMPONENT_LOADER_GLOB)) {
    throw new Error("ComponentLoader import.meta.glob 契约已变更，无法安全应用产品构建边界");
  }
  return code.replace(COMPONENT_LOADER_GLOB, `import.meta.glob(${JSON.stringify(patterns)})`);
}

export function transformFoodLogiSource(code: string, id: string, assembly: string): string {
  if (!isFoodLogiAssembly(assembly)) return code;
  const normalized = id.replaceAll("\\", "/").split("?", 1)[0] ?? "";
  if (normalized.endsWith("/src/components/layouts/fa-header-bar/widgets/FaUserMenu.vue")) {
    return code.replace(/\s*<FaConfigInfoDrawer\b[^>]*\/>/, "");
  }
  return code;
}

export function foodLogiViewBoundaryPlugin(assembly: string): PluginOption {
  return {
    name: "food-logi:view-boundary",
    enforce: "pre",
    transform(code, id) {
      const sourceBounded = transformFoodLogiSource(code, id, assembly);
      const transformed = transformFoodLogiComponentLoader(sourceBounded, id, assembly);
      return transformed === code ? null : { code: transformed, map: null };
    },
    load(id) {
      if (!isExcludedFoodLogiModule(id, assembly)) return null;
      return '<template><span data-food-logi-excluded-view="true" /></template>';
    },
  };
}

function forbiddenMarkers(assembly: FoodLogiAssembly): string[] {
  const currentProduct = FOOD_PRODUCT_VIEWS[assembly];
  return [
    "FaChat",
    "FaAiModelConfigPanel",
    "FaGenCode",
    "FaGencode",
    "FaImportDbTable",
    "module_example:demo",
    ...Object.values(FOOD_PRODUCT_VIEWS)
      .filter((view) => view !== currentProduct)
      .map((view) => `/${view}/`),
  ];
}

function walkFiles(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? walkFiles(target) : [target];
  });
}

export function findForbiddenBuildArtifacts(outputDirectory: string, assembly: string): string[] {
  if (!isFoodLogiAssembly(assembly)) return [];
  if (!fs.existsSync(outputDirectory)) return [`构建产物目录不存在: ${outputDirectory}`];
  const markers = forbiddenMarkers(assembly);
  return walkFiles(outputDirectory).filter((file) => {
    const relative = path.relative(outputDirectory, file).replaceAll("\\", "/");
    if (markers.some((marker) => relative.includes(marker))) return true;
    if (!/\.(?:js|css|html|json|map|txt)$/i.test(file)) return false;
    const content = fs.readFileSync(file, "utf8");
    return markers.some((marker) => content.includes(marker));
  });
}

export function findFoodLogiBrandingViolations(
  outputDirectory: string,
  assembly: string
): string[] {
  if (!isFoodLogiProductAssembly(assembly)) return [];
  if (!fs.existsSync(outputDirectory)) return [`构建产物目录不存在: ${outputDirectory}`];

  const product = FOOD_LOGI_PRODUCT_BRANDS[assembly];
  const violations: string[] = [];
  const indexPath = path.join(outputDirectory, "index.html");
  const indexHtml = fs.existsSync(indexPath) ? fs.readFileSync(indexPath, "utf8") : "";
  if (!indexHtml.includes(`<title>${product.title}</title>`)) {
    violations.push(`index.html 未使用正式产品标题: ${product.title}`);
  }
  for (const site of ["data360", "znceedi"] as const) {
    const logo = path.join(outputDirectory, "brand", "logos", `${site}-${product.product}.png`);
    if (!fs.existsSync(logo))
      violations.push(`缺少产品 Logo: ${path.relative(outputDirectory, logo)}`);
  }
  return violations;
}
