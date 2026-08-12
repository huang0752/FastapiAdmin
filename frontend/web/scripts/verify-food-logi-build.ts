import path from "node:path";
import { findForbiddenBuildArtifacts } from "../build/foodLogiViewBoundary";

const [assembly, outputDirectory] = process.argv.slice(2);
if (!assembly || !outputDirectory) {
  throw new Error("用法: tsx scripts/verify-food-logi-build.ts <assembly> <output-directory>");
}

const resolvedOutput = path.resolve(process.cwd(), outputDirectory);
const forbidden = findForbiddenBuildArtifacts(resolvedOutput, assembly);
if (forbidden.length) {
  throw new Error(`产品构建中发现被禁止的视图:\n${forbidden.join("\n")}`);
}

console.log(`构建边界验证通过: ${assembly} -> ${resolvedOutput}`);
