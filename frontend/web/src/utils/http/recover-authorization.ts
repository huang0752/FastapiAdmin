import { Auth } from "@/utils/auth";
import { createPermissionRecovery } from "./permission-recovery";

let activeRecovery: Promise<boolean> | null = null;

/** Lazy imports keep router/store dependencies outside the HTTP entry path. */
export function recoverAuthorization() {
  if (activeRecovery) return activeRecovery;
  activeRecovery = (async () => {
    const [{ useUserStore }, { useWorktabStore }, routes, { router }] = await Promise.all([
      import("@/store/modules/user.store"),
      import("@/store/modules/worktab.store"),
      import("@/router/beforeEach"),
      import("@/router"),
    ]);
    const user = useUserStore();
    const token = Auth.getAccessToken();
    if (!token) return false;
    return createPermissionRecovery({
      session: () => Auth.getAccessToken(),
      snapshot: () => JSON.stringify([user.prems, user.routeList]),
      refresh: () => user.getUserInfo({ shouldApply: () => Auth.getAccessToken() === token }),
      resetView: async () => {
        const { clearTableRequestCaches } = await import("@/hooks/core/useTable");
        if (Auth.getAccessToken() !== token) return;
        clearTableRequestCaches();
        routes.resetDynamicRoutesSync();
        useWorktabStore().clearAll();
        // Unmount the old layout and its cached pages before registering new routes.
        await router.replace("/workspace/personal");
        if (Auth.getAccessToken() !== token) return;
        if (user.routeList.length > 0) {
          await routes.rebuildDynamicRoutesFromCurrentUser();
          await router.replace("/");
        }
      },
    })();
  })().finally(() => {
    activeRecovery = null;
  });
  return activeRecovery;
}
