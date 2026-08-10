// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  resolve(process.cwd(), "src/components/layouts/fa-menus/fa-sidebar-menu/index.vue"),
  "utf8"
);

describe("sidebar brand title layout", () => {
  it("clamps the expanded title to two vertically centered lines", () => {
    expect(source).toContain('class="sidebar-title"');
    expect(source).toContain("&.sidebar-title:not(.is-dual-menu-name)");
    expect(source).toContain("display: -webkit-box;");
    expect(source).toContain("-webkit-line-clamp: 2;");
    expect(source).toContain("-webkit-box-orient: vertical;");
    expect(source).toContain("transform: translateY(-50%);");
    expect(source).toContain("overflow-wrap: anywhere;");
  });
});
