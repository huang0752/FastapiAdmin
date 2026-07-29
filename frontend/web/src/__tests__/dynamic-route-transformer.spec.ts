import { describe, expect, it, vi } from "vitest";
import type { ComponentLoader } from "@/router/core/ComponentLoader";
import { RouteTransformer } from "@/router/core/RouteTransformer";
import type { AppRouteRecord } from "@/types/router";

vi.mock("@/router/staticRoutes", () => ({
  IframeRouteManager: {
    getInstance: () => ({ add: vi.fn() }),
  },
  ROUTE_COMPONENT_LAYOUT: "/index/index",
}));

function createLoaderStub(): ComponentLoader {
  const component = () => Promise.resolve({});
  return {
    load: () => component,
    loadIframe: () => component,
    loadLayout: () => component,
    loadNestedParent: () => component,
  } as unknown as ComponentLoader;
}

describe("RouteTransformer", () => {
  it("挂到根布局时保留多段一级业务路径", () => {
    const transformer = new RouteTransformer(createLoaderStub(), { shellChild: true });
    const route: AppRouteRecord = {
      path: "/business/data",
      name: "BusinessData",
      component: "/nested/router-view-parent",
      meta: { title: "业务数据" },
      children: [
        {
          path: "/business/data/records",
          name: "BusinessDataRecords",
          component: "/module_example/records/index",
          meta: { title: "数据记录" },
        },
      ],
    };

    const converted = transformer.transform(route);

    expect(converted.path).toBe("business/data");
    expect(converted.children?.[0]?.path).toBe("records");
  });
});
