import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import { useAssemblyStore, useMenuStore, useUserStore } from "@stores";
import { setupBeforeEachGuard } from "@/router/beforeEach";
import { staticRoutes } from "@/router/staticRoutes";

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

vi.mock("@/mock/upgrade/changeLog", () => ({ upgradeLogList: { value: [] } }));

describe("Control entitlement waiting route guard", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("mounts callback and waiting static auth routes without empty-menu dynamic initialization", async () => {
    const Waiting = defineComponent({ template: "<div>waiting</div>" });
    const callbackMeta = staticRoutes.find((route) => route.name === "ControlSsoCallback")?.meta;
    const waitingMeta = staticRoutes.find((route) => route.name === "ControlSsoWaiting")?.meta;
    expect(callbackMeta?.skipDynamicRouteInit).toBe(true);
    expect(waitingMeta?.skipDynamicRouteInit).toBe(true);
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/auth/control/callback",
          name: "ControlSsoCallback",
          component: Waiting,
          meta: callbackMeta,
        },
        {
          path: "/auth/control/waiting",
          name: "ControlSsoWaiting",
          component: Waiting,
          meta: waitingMeta,
        },
        { path: "/500", name: "500", component: Waiting },
        { path: "/404", name: "404", component: Waiting },
        { path: "/login", name: "Login", component: Waiting },
      ],
    });
    const assemblyStore = useAssemblyStore();
    assemblyStore.loaded = true;
    assemblyStore.summary.enabledRouteGroups = ["auth"];
    const userStore = useUserStore();
    userStore.setLoginStatus(true);
    userStore.setRoute([]);
    useMenuStore().setMenuList([]);
    const replace = vi.spyOn(router, "replace");
    setupBeforeEachGuard(router);

    await router.push({ name: "ControlSsoCallback", query: { code: "launch-code" } });
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("ControlSsoCallback");

    await router.push({ name: "ControlSsoWaiting" });
    expect(router.currentRoute.value.name).toBe("ControlSsoWaiting");
    expect(replace).not.toHaveBeenCalled();
  });
});
