import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestMock } = vi.hoisted(() => ({
  requestMock: vi.fn((config: unknown) => config),
}));

vi.mock("@utils", () => ({
  request: requestMock,
}));

import AiChatAPI from "@/api/module_ai/chat";

describe("AI feature binding API contracts", () => {
  beforeEach(() => {
    requestMock.mockClear();
  });

  it("lists feature bindings", () => {
    AiChatAPI.getFeatureBindings();

    expect(requestMock).toHaveBeenCalledWith({
      url: "/ai/chat/feature",
      method: "get",
    });
  });

  it("updates one registered feature without sending provider secrets", () => {
    AiChatAPI.updateFeatureBinding("demo_data.blueprint", {
      model_config_id: "primary-id",
      fallback_config_id: "fallback-id",
      prompt_version: "v2",
      timeout_seconds: 45,
      allow_business_data: true,
      enabled: true,
    });

    expect(requestMock).toHaveBeenCalledWith({
      url: "/ai/chat/feature/demo_data.blueprint",
      method: "put",
      data: {
        model_config_id: "primary-id",
        fallback_config_id: "fallback-id",
        prompt_version: "v2",
        timeout_seconds: 45,
        allow_business_data: true,
        enabled: true,
      },
    });
  });
});
