import { describe, expect, it } from "vitest";
import zh from "@/locales/langs/zh.json";
import en from "@/locales/langs/en.json";

describe("unified password login identifiers", () => {
  it("labels the password field as username, mobile or email login", () => {
    expect(zh.login.placeholder.username).toBe("请输入用户名 / 手机号 / 邮箱");
    expect(zh.login.message.username.required).toBe("请输入用户名、手机号或邮箱");
    expect(en.login.placeholder.username).toBe("Username / mobile / email");
  });

  it("distinguishes SMS login from mobile password login", () => {
    expect(zh.login.mobileLogin).toBe("短信验证码登录");
    expect(en.login.mobileLogin).toBe("SMS code login");
  });
});
