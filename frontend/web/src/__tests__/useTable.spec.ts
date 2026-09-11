import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import { mount, type VueWrapper } from "@vue/test-utils";

vi.stubGlobal("__APP_VERSION__", "test");
vi.stubGlobal("__APP_NAME__", "FastapiAdmin");

vi.mock("@utils", () => {
  class TableCache {
    clear() {}
    clearCurrentSearch() {
      return 0;
    }
    clearPagination() {
      return 0;
    }
    cleanupExpired() {
      return 0;
    }
    get() {
      return null;
    }
    getStats() {
      return { total: 0, size: "0KB", hitRate: "0 avg hits" };
    }
    set() {}
  }

  const CacheInvalidationStrategy = {
    CLEAR_ALL: "clear_all",
    CLEAR_CURRENT: "clear_current",
    CLEAR_PAGINATION: "clear_pagination",
    KEEP_ALL: "keep_all",
  } as const;

  return {
    TableCache,
    CacheInvalidationStrategy,
    tableConfig: { paginationKey: { current: "page_no", size: "page_size" } },
    defaultResponseAdapter: (value: unknown) => value,
    extractTableData: (value: TableResponse) => value.records,
    updatePaginationFromResponse: (
      pagination: { current: number; size: number; total: number },
      value: TableResponse
    ) => {
      pagination.current = value.current;
      pagination.size = value.size;
      pagination.total = value.total;
    },
    createSmartDebounce: <T extends (...args: any[]) => any>(fn: T) =>
      Object.assign(fn, { cancel: vi.fn() }),
    createErrorHandler: () => (error: unknown) => error,
    Auth: {
      getAccessToken: () =>
        localStorage.getItem("remember_me") === "true"
          ? localStorage.getItem("access_token") || ""
          : sessionStorage.getItem("access_token") || "",
    },
    StorageConfig: { LAST_TENANT_ID_KEY: "sys-last-tenant-id" },
  };
});

type TableResponse = {
  records: Array<{ id: number }>;
  total: number;
  current: number;
  size: number;
};

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const response: TableResponse = {
  records: [{ id: 1 }],
  total: 1,
  current: 1,
  size: 10,
};

let useTable: typeof import("../hooks/core/useTable").useTable;
let clearTableRequestCaches: typeof import("../hooks/core/useTable").clearTableRequestCaches;
const wrappers: VueWrapper[] = [];

beforeAll(async () => {
  ({ useTable, clearTableRequestCaches } = await import("../hooks/core/useTable"));
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem("remember_me", "true");
  localStorage.setItem("access_token", "session-a");
  localStorage.setItem("sys-last-tenant-id", "1");
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
});

function mountTable(apiFn: (params: Record<string, unknown>) => Promise<TableResponse>) {
  let table: any;
  const wrapper = mount(
    defineComponent({
      setup() {
        table = useTable({
          core: { apiFn, immediate: false },
          transform: { responseAdapter: (value) => value as never },
        });
        return () => null;
      },
    })
  );
  wrappers.push(wrapper);
  return table;
}

it("clears the previous total when a subsequent list request is denied", async () => {
  const api = vi.fn().mockResolvedValueOnce(response).mockRejectedValueOnce(new Error("forbidden"));
  const table = mountTable(api);
  await table.fetchData({ page_no: 1 });
  expect(table.pagination.total).toBe(1);
  await table.fetchData({ page_no: 2 });
  expect(table.data.value).toEqual([]);
  expect(table.pagination.total).toBe(0);
});

describe("useTable request deduplication scope", () => {
  it("does not merge identical params sent to different API functions", async () => {
    const first = deferred<TableResponse>();
    const second = deferred<TableResponse>();
    const apiA = vi.fn(() => first.promise);
    const apiB = vi.fn(() => second.promise);
    const tableA = mountTable(apiA);
    const tableB = mountTable(apiB);

    const requestA = tableA.fetchData({ page_no: 1, page_size: 10 });
    const requestB = tableB.fetchData({ page_size: 10, page_no: 1 });

    first.resolve(response);
    second.resolve(response);
    await Promise.all([requestA, requestB]);

    expect(apiA).toHaveBeenCalledTimes(1);
    expect(apiB).toHaveBeenCalledTimes(1);
  });

  it("does not merge identical API requests across tenants", async () => {
    const first = deferred<TableResponse>();
    const second = deferred<TableResponse>();
    const apiFn = vi
      .fn<(params: Record<string, unknown>) => Promise<TableResponse>>()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const tableA = mountTable(apiFn);
    const tableB = mountTable(apiFn);

    const requestA = tableA.fetchData({ page_no: 1, page_size: 10 });
    localStorage.setItem("sys-last-tenant-id", "2");
    const requestB = tableB.fetchData({ page_no: 1, page_size: 10 });

    first.resolve(response);
    second.resolve(response);
    await Promise.all([requestA, requestB]);

    expect(apiFn).toHaveBeenCalledTimes(2);
  });

  it("does not merge identical API requests across token sessions", async () => {
    const first = deferred<TableResponse>();
    const second = deferred<TableResponse>();
    const apiFn = vi
      .fn<(params: Record<string, unknown>) => Promise<TableResponse>>()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const tableA = mountTable(apiFn);
    const tableB = mountTable(apiFn);

    const requestA = tableA.fetchData({ page_no: 1, page_size: 10 });
    localStorage.setItem("access_token", "session-b");
    const requestB = tableB.fetchData({ page_no: 1, page_size: 10 });

    first.resolve(response);
    second.resolve(response);
    await Promise.all([requestA, requestB]);

    expect(apiFn).toHaveBeenCalledTimes(2);
  });

  it("keeps params in the key while normalizing object key order", async () => {
    const first = deferred<TableResponse>();
    const second = deferred<TableResponse>();
    const apiFn = vi
      .fn<(params: Record<string, unknown>) => Promise<TableResponse>>()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const tableA = mountTable(apiFn);
    const tableB = mountTable(apiFn);
    const tableC = mountTable(apiFn);

    const requestA = tableA.fetchData({ page_no: 1, page_size: 10, keyword: "same" });
    const requestB = tableB.fetchData({ keyword: "same", page_size: 10, page_no: 1 });
    const requestC = tableC.fetchData({ page_no: 1, page_size: 10, keyword: "different" });

    first.resolve(response);
    second.resolve(response);
    await Promise.all([requestA, requestB, requestC]);

    expect(apiFn).toHaveBeenCalledTimes(2);
  });

  it("keeps the replacement in-flight request registered when an invalidated request settles", async () => {
    const first = deferred<TableResponse>();
    const second = deferred<TableResponse>();
    const third = deferred<TableResponse>();
    const apiFn = vi
      .fn<(params: Record<string, unknown>) => Promise<TableResponse>>()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)
      .mockImplementationOnce(() => third.promise);
    const tableA = mountTable(apiFn);
    const tableB = mountTable(apiFn);
    const tableC = mountTable(apiFn);

    const requestA = tableA.fetchData({ page_no: 1, page_size: 10 });
    clearTableRequestCaches();
    const requestB = tableB.fetchData({ page_no: 1, page_size: 10 });

    first.resolve(response);
    await requestA;

    const requestC = tableC.fetchData({ page_no: 1, page_size: 10 });
    second.resolve(response);
    third.resolve(response);
    await Promise.all([requestB, requestC]);

    expect(apiFn).toHaveBeenCalledTimes(2);
  });
});
