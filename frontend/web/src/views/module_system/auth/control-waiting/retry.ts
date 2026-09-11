export interface AuthorizationRetryDependencies {
  userStore: {
    getUserInfo: () => Promise<unknown>;
    routeList: unknown[];
    clearAuthorizationSnapshot: () => void;
  };
  routerUtils: {
    resetDynamicRoutesSync: () => void;
    rebuildDynamicRoutesFromCurrentUser: () => Promise<void>;
  };
  router: {
    replace: (path: string) => Promise<unknown>;
  };
}

export type AuthorizationRetryResult = { status: "authorized" | "pending" };

/** 丢弃持久化的旧权限快照，重新请求当前用户，再按最新菜单重建动态路由。 */
export async function reloadAuthorization(
  deps: AuthorizationRetryDependencies
): Promise<AuthorizationRetryResult> {
  deps.routerUtils.resetDynamicRoutesSync();
  deps.userStore.clearAuthorizationSnapshot();
  await deps.userStore.getUserInfo();

  if (deps.userStore.routeList.length === 0) {
    return { status: "pending" };
  }

  await deps.routerUtils.rebuildDynamicRoutesFromCurrentUser();
  await deps.router.replace("/");
  return { status: "authorized" };
}

/** 同一等待页上的重复点击共享一次在途请求，完成后才允许再次检查。 */
export function createAuthorizationReloader(deps: AuthorizationRetryDependencies) {
  let inFlight: Promise<AuthorizationRetryResult> | null = null;

  return () => {
    if (inFlight) return inFlight;
    inFlight = reloadAuthorization(deps).finally(() => {
      inFlight = null;
    });
    return inFlight;
  };
}
