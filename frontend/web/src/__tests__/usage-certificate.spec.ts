import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const source = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

describe("software usage certificate", () => {
  it("registers tenant, platform, and public frontend surfaces", () => {
    expect(source("src/api/module_platform/usage_certificate.ts")).toContain(
      "/platform/tenant/usage-certificate/preview"
    );
    expect(source("src/views/module_platform/self_service/index.vue")).toContain(
      "module_platform:usage-certificate:tenant-query"
    );
    expect(source("src/views/module_platform/usage_certificate/index.vue")).toContain(
      "软件使用证明"
    );
    expect(source("src/views/public/usage_certificate/index.vue")).not.toContain("request_ip");
    expect(source("src/router/staticRoutes.ts")).toContain("/certificate/verify/:token");
    expect(source("src/router/beforeEach.ts")).toContain("anonymousPublic");
  });
});
