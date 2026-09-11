interface PermissionRecoveryDependencies {
  session: () => string;
  snapshot: () => string;
  refresh: () => Promise<unknown>;
  resetView: () => Promise<void>;
}

/** Only rebuild after a confirmed authorization change, never replay a denied write. */
export function createPermissionRecovery(deps: PermissionRecoveryDependencies) {
  let inFlight: Promise<boolean> | null = null;
  return () => {
    if (inFlight) return inFlight;
    const session = deps.session();
    const before = deps.snapshot();
    inFlight = (async () => {
      await deps.refresh();
      if (deps.session() !== session || deps.snapshot() === before) return false;
      await deps.resetView();
      return true;
    })().finally(() => {
      inFlight = null;
    });
    return inFlight;
  };
}
