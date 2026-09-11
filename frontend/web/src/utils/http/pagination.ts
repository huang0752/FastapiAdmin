import type { AxiosResponse } from "axios";

type PaginationKeys = "page_no" | "page_size";

export async function loadAllPages<T, TQuery extends PageQuery>(
  fetchPage: (query?: TQuery) => Promise<AxiosResponse<ApiResponse<PageResult<T>>>>,
  query: Omit<TQuery, PaginationKeys>,
  pageSize = 100
): Promise<T[]> {
  const items: T[] = [];
  let pageNo = 1;

  while (true) {
    const response = await fetchPage({ ...query, page_no: pageNo, page_size: pageSize } as TQuery);
    const page = response.data.data;
    items.push(...page.items);
    if (!page.has_next) return items;
    pageNo += 1;
  }
}
