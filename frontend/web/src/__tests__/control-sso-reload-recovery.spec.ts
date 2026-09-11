import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JWTOut } from "@/api/module_system/auth";
import { Auth } from "@/utils/auth/token";
import {
  persistControlExchange,
  resumeControlExchange,
} from "@/views/module_system/auth/control-callback/control-session";

vi.hoisted(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
});

describe("control SSO reload recovery", () => {
  const tokens: JWTOut = {
    access_token: "control-access",
    refresh_token: "control-refresh",
    token_type: "bearer",
    expires_in: 1800,
  };

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("在继续请求用户信息前持久化兑换结果", () => {
    persistControlExchange("launch-code", tokens);

    expect(Auth.getAccessToken()).toBe("control-access");
    expect(Auth.getRefreshToken()).toBe("control-refresh");
    expect(resumeControlExchange("launch-code")).toEqual(tokens);
  });

  it("logout clears exchange recovery before another account logs in", () => {
    persistControlExchange("old-code", tokens);
    Auth.clearAuth();
    Auth.setTokens("other-access", "other-refresh", false);
    expect(resumeControlExchange("old-code")).toBeNull();
  });

  it("不会用其他启动码留下的会话恢复当前登录", () => {
    persistControlExchange("old-code", tokens);

    expect(resumeControlExchange("new-code")).toBeNull();
  });

  it("记住我标志与存储位置不一致时迁移完整令牌对", () => {
    localStorage.setItem("remember_me", "true");
    sessionStorage.setItem("access_token", "session-access");
    sessionStorage.setItem("refresh_token", "session-refresh");

    expect(Auth.getAccessToken()).toBe("session-access");
    expect(Auth.getRefreshToken()).toBe("session-refresh");
    expect(localStorage.getItem("access_token")).toBe("session-access");
    expect(localStorage.getItem("refresh_token")).toBe("session-refresh");
    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
  });

  it("拒绝用空值覆盖已有的完整令牌对", () => {
    Auth.setTokens("valid-access", "valid-refresh", true);

    expect(() => Auth.setTokens("", "", true)).toThrow("认证令牌不完整");
    expect(Auth.getAccessToken()).toBe("valid-access");
    expect(Auth.getRefreshToken()).toBe("valid-refresh");
  });
});
