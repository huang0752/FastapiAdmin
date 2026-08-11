import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  normalizeCreditCode,
  validateCreditCode,
} from "@/views/module_platform/tenant/credit-code";

const VALID_CREDIT_CODE = "91350100M000100Y43";

function readSource(path: string) {
  return readFileSync(resolve(process.cwd(), path), "utf8");
}

describe("tenant enterprise information contract", () => {
  it("normalizes an optional enterprise credit code", () => {
    expect(normalizeCreditCode(" 91350100m000100y43 ")).toBe(VALID_CREDIT_CODE);
    expect(normalizeCreditCode(" ")).toBeUndefined();
    expect(normalizeCreditCode(undefined)).toBeUndefined();
  });

  it("validates the full credit-code character set and check digit", () => {
    expect(validateCreditCode(VALID_CREDIT_CODE)).toBe(true);
    expect(validateCreditCode(" ")).toBe(true);
    expect(validateCreditCode("91350100M000100Y44")).toBe(false);
    expect(validateCreditCode("91350100M000100I43")).toBe(false);
    expect(validateCreditCode("91350100M000100Y4")).toBe(false);
  });

  it("exposes the credit code in every tenant API contract", () => {
    const source = readSource("src/api/module_platform/tenant.ts");

    for (const contract of [
      "TenantPageQuery",
      "TenantTable",
      "TenantForm",
      "TenantCreateForm",
      "TenantUpdateForm",
    ]) {
      const block = source.match(new RegExp(`interface ${contract}[^}]+}`))?.[0];
      expect(block, `${contract} should exist`).toBeDefined();
      expect(block).toContain("unified_social_credit_code?: string;");
    }
  });

  it("supports exact search, list, detail, create and update without a product wizard", () => {
    const source = readSource("src/views/module_platform/tenant/index.vue");

    expect(source).toContain('label: "统一社会信用代码"');
    expect(source).toContain('key: "unified_social_credit_code"');
    expect(source).toContain('prop: "unified_social_credit_code"');
    expect(source).toMatch(
      /unified_social_credit_code:\s*normalizeCreditCode\(p\.unified_social_credit_code\)/
    );
    expect(
      source.match(
        /unified_social_credit_code:\s*normalizeCreditCode\(\s*formData\.value\.unified_social_credit_code\s*\)/g
      )
    ).toHaveLength(2);
    expect(source).not.toContain("产品与套餐");
    expect(source).not.toContain("provisioning");
  });
});
