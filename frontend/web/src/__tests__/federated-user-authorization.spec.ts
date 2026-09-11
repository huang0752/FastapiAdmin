import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  authorizationLabel,
  buildUserReplaceParams,
  editActionLabel,
  isFederatedIdentityFieldReadonly,
  isPendingFederatedUser,
  shouldShowResetPassword,
  sourceLabel,
} from "@/views/module_system/user/user-authorization";

const source = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

describe("federated user authorization", () => {
  it("forwards source and authorization filters", () => {
    expect(
      buildUserReplaceParams({
        auth_source: "federated",
        authorization_status: "pending",
      })
    ).toMatchObject({ auth_source: "federated", authorization_status: "pending" });
  });

  it("normalizes authorization status searches to federated users", () => {
    expect(
      buildUserReplaceParams({
        auth_source: "local",
        authorization_status: "pending",
      })
    ).toMatchObject({ auth_source: "federated", authorization_status: "pending" });
  });

  it("shows grant action only for pending federated users", () => {
    expect(
      isPendingFederatedUser({
        id: 9,
        auth_source: "federated",
        authorization_status: "pending",
      })
    ).toBe(true);
    expect(
      isPendingFederatedUser({
        id: 10,
        auth_source: "local",
        authorization_status: null,
      })
    ).toBe(false);
  });

  it("renames the existing edit action instead of adding a second grant action", () => {
    expect(editActionLabel({ auth_source: "federated", authorization_status: "pending" })).toBe(
      "去授权"
    );
    expect(editActionLabel({ auth_source: "federated", authorization_status: "authorized" })).toBe(
      "编辑"
    );
    expect(editActionLabel({ auth_source: "local", authorization_status: null })).toBe("编辑");
  });

  it("keeps central fields readonly only for federated users", () => {
    for (const field of ["username", "name", "mobile", "email", "avatar", "status"]) {
      expect(isFederatedIdentityFieldReadonly(field, "federated")).toBe(true);
    }
    for (const field of ["dept_id", "role_ids", "position_ids", "description"]) {
      expect(isFederatedIdentityFieldReadonly(field, "federated")).toBe(false);
    }
    expect(isFederatedIdentityFieldReadonly("name", "local")).toBe(false);
  });

  it("hides password reset only for federated users", () => {
    expect(shouldShowResetPassword({ auth_source: "federated" })).toBe(false);
    expect(shouldShowResetPassword({ auth_source: "local" })).toBe(true);
    expect(shouldShowResetPassword({})).toBe(true);
  });

  it("formats source and authorization labels", () => {
    expect(sourceLabel("federated")).toBe("统一登录");
    expect(sourceLabel("local")).toBe("本地账号");
    expect(authorizationLabel("pending")).toBe("仅基础访问");
    expect(authorizationLabel("authorized")).toBe("已授权");
    expect(authorizationLabel(null)).toBe("—");
  });

  it("explains local authorization and performs an in-app recheck", () => {
    const waitingSource = source("src/views/module_system/auth/control-waiting/index.vue");

    expect(waitingSource).toContain("个人工作区");
    expect(waitingSource).not.toContain("等待管理员授权");
    expect(waitingSource).toContain("基础访问已开通");
    expect(waitingSource).toContain("displayName");
    expect(waitingSource).toContain("account");
    expect(waitingSource).toContain("getUserInfo");
    expect(waitingSource).toContain("createAuthorizationReloader");
    expect(waitingSource).not.toContain("window.location");
    expect(waitingSource).not.toContain("setInterval");
    expect(waitingSource).not.toMatch(/accessToken|refreshToken|launchCode/);
  });
});
