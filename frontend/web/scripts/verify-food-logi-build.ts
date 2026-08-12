import path from "node:path";
import {
  findFoodLogiBrandingViolations,
  findForbiddenBuildArtifacts,
} from "../build/foodLogiViewBoundary";

const [assembly, outputDirectory] = process.argv.slice(2);
if (!assembly || !outputDirectory) {
  throw new Error("用法: tsx scripts/verify-food-logi-build.ts <assembly> <output-directory>");
}

const resolvedOutput = path.resolve(process.cwd(), outputDirectory);
const forbidden = findForbiddenBuildArtifacts(resolvedOutput, assembly);
const branding = findFoodLogiBrandingViolations(resolvedOutput, assembly);
if (forbidden.length || branding.length) {
  throw new Error(`产品构建验证失败:\n${[...forbidden, ...branding].join("\n")}`);
}

console.log(`构建边界验证通过: ${assembly} -> ${resolvedOutput}`);
