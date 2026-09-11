import { describe, expect, it, vi } from "vitest";
import { createPermissionRecovery } from "@/utils/http/permission-recovery";

function setup() {
  let snapshot = "old";
  let session = "session-a";
  const deps = {
    session: () => session,
    snapshot: () => snapshot,
    refresh: vi.fn(async () => {
      snapshot = "new";
    }),
    resetView: vi.fn(async () => {}),
  };
  return {
    deps,
    setSession: (value: string) => {
      session = value;
    },
  };
}

describe("permission recovery", () => {
  it("refreshes changed permissions and clears stale view once for concurrent denials", async () => {
    const { deps } = setup();
    const recover = createPermissionRecovery(deps);
    expect(await Promise.all([recover(), recover()])).toEqual([true, true]);
    expect(deps.refresh).toHaveBeenCalledTimes(1);
    expect(deps.resetView).toHaveBeenCalledTimes(1);
  });
  it("does not navigate for a resource denial when permissions are unchanged", async () => {
    const { deps } = setup();
    deps.refresh.mockImplementation(async () => {});
    expect(await createPermissionRecovery(deps)()).toBe(false);
    expect(deps.resetView).not.toHaveBeenCalled();
  });
  it("does not reset a replacement session after an old request completes", async () => {
    const { deps, setSession } = setup();
    deps.refresh.mockImplementation(async () => {
      setSession("session-b");
    });
    expect(await createPermissionRecovery(deps)()).toBe(false);
    expect(deps.resetView).not.toHaveBeenCalled();
  });
  it("releases its lock after refresh fails", async () => {
    const { deps } = setup();
    deps.refresh.mockRejectedValueOnce(new Error("offline"));
    const recover = createPermissionRecovery(deps);
    await expect(recover()).rejects.toThrow("offline");
    expect(await recover()).toBe(true);
  });
});
