import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { AssemblySummary } from "@/config/assembly/default";
import { filterRoutesByAssembly } from "@/router/filterByAssembly";
import { staticRoutes } from "@/router/staticRoutes";

const webRoot = path.resolve(__dirname, "../..");
const repositoryRoot = path.resolve(webRoot, "../..");
const assemblies = [
  "food-traceability",
  "agricultural-delivery",
  "cold-chain-vehicle",
] as const;

function source(relativePath: string): string {
  return fs.readFileSync(path.join(repositoryRoot, relativePath), "utf8");
}

function summary(name: string): AssemblySummary {
  return {
    name,
    title: name,
    enabledRouteGroups: [
      "auth",
      "home",
      "system",
      "platform",
      "user-profile",
      "workspace",
      "exception",
    ],
    disabledRouteGroups: [
      "dashboard",
      "ai-chat",
      "pricing",
      "article",
      "tutorial",
      "changelog",
    ],
    featureFlags: {},
  };
}

function allPaths(routes: Array<{ path: string; children?: unknown[] }>): string[] {
  return routes.flatMap((route) => [
    route.path,
    ...allPaths((route.children ?? []) as Array<{ path: string; children?: unknown[] }>),
  ]);
}

describe("food logi subtraction boundary", () => {
  it.each(assemblies)("%s keeps workspace and excludes generic showcase groups", (name) => {
    const assembly = source(`backend/app/assemblies/${name}.toml`);
    expect(assembly).toContain('"workspace"');
    expect(assembly).not.toMatch(/enabled_route_groups\s*=.*"dashboard"/);
    for (const group of ["ai-chat", "pricing", "article", "tutorial", "changelog"]) {
      expect(assembly).toMatch(new RegExp(`disabled_route_groups\\s*=.*"${group}"`));
    }
  });

  it.each(assemblies)("%s static routes omit generic dashboard and chat views", (name) => {
    const paths = allPaths(filterRoutesByAssembly(staticRoutes, summary(name)) as never[]);
    expect(paths).toContain("workspace");
    expect(paths).not.toContain("dashboard");
    expect(paths).not.toContain("workplace");
    expect(paths).not.toContain("analysis");
    expect(paths).not.toContain("screen");
    expect(paths).not.toContain("fachat");
  });

  it("workspace consumes only real tenant workspace and usage certificate APIs", () => {
    const workspace = source("frontend/web/src/views/module_platform/self_service/index.vue");
    const workspaceApi = source("frontend/web/src/api/module_platform/self_service.ts");
    const certificateApi = source("frontend/web/src/api/module_platform/usage_certificate.ts");
    expect(workspaceApi).toContain('const API_PATH = "/platform/tenant"');
    expect(workspaceApi).toContain('url: `${API_PATH}/workspace`');
    expect(certificateApi).toContain("/platform/tenant/usage-certificate/preview");
    for (const fakeDomain of ["追溯批次", "车辆数", "轨迹数", "温控数"]) {
      expect(workspace).not.toContain(fakeDomain);
    }
  });

  it("does not expose generic AI chat UI from product assemblies", () => {
    for (const name of assemblies) {
      const assembly = source(`backend/app/assemblies/${name}.toml`);
      expect(assembly).toContain('"module_ai"');
      expect(assembly).toContain('"ai-chat"');
      expect(assembly).toContain("demo_data_blueprint = true");
    }
  });
});
