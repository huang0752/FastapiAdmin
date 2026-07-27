import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("demo form validation", () => {
  it("matches the backend name length constraint before submit", () => {
    const demoViewSource = readFileSync(
      resolve(process.cwd(), "src/views/module_example/demo/index.vue"),
      "utf8"
    );

    expect(demoViewSource).toContain(
      '{ min: 2, max: 50, message: "名称长度必须在2-50个字符之间", trigger: "blur" }'
    );
  });
});
