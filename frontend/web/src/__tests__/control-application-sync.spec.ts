import { flushPromises, shallowMount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import ControlAPI, { type ApplicationForm, type ApplicationListItem } from "@/api/module_control";
import ApplicationView from "@/views/module_control/application/index.vue";
import { validateEntitlementSyncSettings } from "@/views/module_control/application/access-sync";

const flags = vi.hoisted(() => ({ controlUserEntitlements: true }));
vi.mock("@utils", () => ({ request: vi.fn(), renderTableOperationCell: vi.fn() }));
vi.mock("@/hooks/core/useAuth", () => ({ useAuth: () => ({ hasAuth: () => true }) }));
vi.mock("@/store/modules/assembly.store", () => ({
  useAssemblyStore: () => ({
    isFeatureEnabled: (name: string) => flags[name as keyof typeof flags] === true,
  }),
}));
vi.mock("@/hooks/core/useCrudDialog", () => ({
  useCrudDialog: () => ({
    dialogVisible: { visible: false, type: "create", title: "" },
    openDialog: vi.fn(),
    closeDialog: vi.fn(),
  }),
}));
vi.mock("@/hooks/core/useConfirm", () => ({ confirmAction: vi.fn(), confirmDelete: vi.fn() }));
vi.mock("@/hooks/core/useTable", () => ({
  useTable: () => ({
    columns: [],
    columnChecks: [],
    data: [],
    loading: false,
    pagination: {},
    getData: vi.fn(),
    refreshData: vi.fn(),
  }),
}));

type EditorVm = {
  openUpdate: (row: ApplicationListItem) => void;
  openCreate: () => void;
  submitEditor: () => void;
  formData: ApplicationForm;
  formItems: { key: string }[];
};
function mountView() {
  const wrapper = shallowMount(ApplicationView, {
    global: {
      directives: { auth: () => undefined },
      stubs: {
        FaDialog: { template: "<div><slot /></div>" },
        FaForm: defineComponent({
          template: "<div />",
          setup(_, { expose }) {
            expose({
              validate: (callback: (valid: boolean) => void) => callback(true),
              clearValidate: vi.fn(),
            });
          },
        }),
      },
    },
  });
  return Object.assign(wrapper, { editor: wrapper.vm as unknown as EditorVm });
}

const sync = {
  entitlement_sync_enabled: true,
  entitlement_sync_url: "https://app.example.test/custom/access/sync",
  entitlement_sync_timeout_seconds: 12,
};

describe("application entitlement synchronization settings", () => {
  beforeEach(() => {
    flags.controlUserEntitlements = true;
    vi.restoreAllMocks();
  });

  it("requires a safe URL and bounded timeout only for enabled synchronization", () => {
    expect(validateEntitlementSyncSettings(sync)).toBeNull();
    expect(validateEntitlementSyncSettings({ ...sync, entitlement_sync_url: "" })).toContain(
      "接口地址"
    );
    for (const url of [
      "javascript:alert(1)",
      "/relative",
      "https://u:p@app.example.test/sync",
      "https://app.example.test/sync?secret=x",
      "https://app.example.test/sync#token",
    ]) {
      expect(
        validateEntitlementSyncSettings({ ...sync, entitlement_sync_url: url })
      ).not.toBeNull();
    }
    for (const timeout of [0, 61, 1.5, NaN])
      expect(
        validateEntitlementSyncSettings({ ...sync, entitlement_sync_timeout_seconds: timeout })
      ).toContain("1–60");
    expect(
      validateEntitlementSyncSettings({
        ...sync,
        entitlement_sync_enabled: false,
        entitlement_sync_url: null,
      })
    ).toBeNull();
  });

  it("loads saved synchronization fields and submits them in the update payload", async () => {
    const update = vi.spyOn(ControlAPI, "updateApplication").mockResolvedValue({} as never);
    const wrapper = mountView();
    wrapper.editor.openUpdate({
      id: 11,
      name: "Product",
      code: "product",
      base_url: "https://app.example.test",
      callback_url: "https://app.example.test/callback",
      ...sync,
    } as ApplicationListItem);
    expect(wrapper.editor.formData).toMatchObject(sync);
    expect(wrapper.editor.formItems.map((item) => item.key)).toEqual(
      expect.arrayContaining(Object.keys(sync))
    );
    wrapper.editor.submitEditor();
    await flushPromises();
    expect(update).toHaveBeenCalledWith(11, expect.objectContaining(sync));
  });

  it("defaults new apps to disabled synchronization and hides controls without the capability", () => {
    const wrapper = mountView();
    wrapper.editor.openCreate();
    expect(wrapper.editor.formData).toMatchObject({
      entitlement_sync_enabled: false,
      entitlement_sync_url: null,
      entitlement_sync_timeout_seconds: 10,
    });
    flags.controlUserEntitlements = false;
    const standalone = mountView();
    expect(
      standalone.editor.formItems.some((item) => item.key.startsWith("entitlement_sync"))
    ).toBe(false);
  });
});
