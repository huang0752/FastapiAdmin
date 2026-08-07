import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const requestMock = vi.hoisted(() => vi.fn(() => Promise.resolve({ data: { data: {} } })));

vi.mock("@utils", () => ({
  request: requestMock,
  NO_AUTH_FLAG: "NO_AUTH",
}));

import UserAPI, {
  buildCurrentUserProfilePayload,
  type InfoFormState,
} from "@/api/module_system/user";

beforeEach(() => {
  requestMock.mockClear();
});

describe("current profile request contract", () => {
  it("uses the backend password-change route without sending confirmation", async () => {
    await UserAPI.changeCurrentUserPassword({
      old_password: "old-pass",
      new_password: "new-pass",
      confirm_password: "new-pass",
    });

    expect(requestMock).toHaveBeenCalledWith({
      url: "/system/user/password/change",
      method: "put",
      data: { old_password: "old-pass", new_password: "new-pass" },
    });
  });

  it("builds a minimal profile payload and normalizes numeric gender", () => {
    const form: InfoFormState = {
      id: 7,
      username: "demo",
      name: "演示用户",
      gender: 1,
      mobile: "13800000000",
      email: "demo@example.com",
      avatar: "https://example.com/avatar.png",
      description: "个人描述",
      roles: [{ id: 1, name: "管理员" }],
      status: 0,
    };

    expect(buildCurrentUserProfilePayload(form)).toEqual({
      name: "演示用户",
      gender: "1",
      mobile: "13800000000",
      email: "demo@example.com",
      avatar: "https://example.com/avatar.png",
      description: "个人描述",
    });
  });

  it("submits the minimal payload and reloads complete current-user state", () => {
    const profileSource = readFileSync(
      resolve(process.cwd(), "src/views/fastlink/current/profile.vue"),
      "utf8"
    );

    expect(profileSource).toContain(
      "const payload = buildCurrentUserProfilePayload(infoFormState);"
    );
    expect(profileSource).toContain("await UserAPI.updateCurrentUserInfo(payload);");
    expect(profileSource).toContain("await userStore.getUserInfo();");
    expect(profileSource).not.toContain("userStore.setUserInfo(response.data.data)");
  });
});
