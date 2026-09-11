import { describe, expect, it, vi } from "vitest";
const request = vi.hoisted(() => vi.fn());
vi.mock("@utils", () => ({ request }));
import AiChatAPI, { TenantAiConfigAPI } from "@/api/module_ai/chat";

describe("AI configuration scope", () => {
  it("keeps personal and tenant reads on separate endpoints", () => {
    AiChatAPI.getModelConfig();
    expect(request).toHaveBeenLastCalledWith({ url: "/ai/chat/model", method: "get" });
    TenantAiConfigAPI.getModelConfig();
    expect(request).toHaveBeenLastCalledWith({ url: "/system/ai-config/model", method: "get" });
  });
  it("clears only the tenant default and tests saved models without resending keys", () => {
    TenantAiConfigAPI.activateModelConfig("");
    expect(request).toHaveBeenLastCalledWith({
      url: "/system/ai-config/model/__default__/activate",
      method: "post",
      showSuccessMessage: false,
    });
    TenantAiConfigAPI.probeModel("saved-model");
    expect(request).toHaveBeenLastCalledWith({
      url: "/system/ai-config/model/saved-model/probe",
      method: "post",
      showSuccessMessage: false,
    });
  });
});
