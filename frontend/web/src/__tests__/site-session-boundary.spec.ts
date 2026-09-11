import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("site session boundary recovery", () => {
  it("clears a stale cross-site token and redirects to login", () => {
    const source = fs.readFileSync(path.resolve(__dirname, "../utils/http/index.ts"), "utf8");
    expect(source).toContain('data?.msg?.includes("站点上下文不匹配")');
    expect(source).toContain('await redirectToLogin("站点已切换，请重新登录")');
  });

  it("redirects without sending an empty refresh token", () => {
    const source = fs.readFileSync(path.resolve(__dirname, "../utils/http/index.ts"), "utf8");
    const missingTokenGuard = source.indexOf("if (!currentRefreshToken)");
    const refreshRequest = source.indexOf("AuthAPI.refreshToken({");

    expect(missingTokenGuard).toBeGreaterThan(-1);
    expect(missingTokenGuard).toBeLessThan(refreshRequest);
    expect(source).toContain('await redirectToLogin("登录已失效，请重新登录")');
  });
});
