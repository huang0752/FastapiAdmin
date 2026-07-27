import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestMock } = vi.hoisted(() => ({
  requestMock: vi.fn((config: unknown) => config),
}));

vi.mock("@utils", () => ({
  request: requestMock,
}));

import TicketAPI, { createTicketComment, getTicketComments } from "@/api/module_system/ticket";

describe("ticket api contracts", () => {
  beforeEach(() => {
    requestMock.mockClear();
  });

  it("exports tickets with the existing GET blob contract", () => {
    TicketAPI.exportTicket({ page_no: 1, page_size: 10, title: "隔离" });

    expect(requestMock).toHaveBeenCalledWith({
      url: "/system/ticket/export",
      method: "get",
      params: { page_no: 1, page_size: 10, title: "隔离" },
      responseType: "blob",
    });
  });

  it("lists and creates comments under the parent ticket", () => {
    getTicketComments(42, { page_no: 1, page_size: 50 });
    createTicketComment(42, { content: "评论内容" });

    expect(requestMock).toHaveBeenNthCalledWith(1, {
      url: "/system/ticket/42/comments",
      method: "get",
      params: { page_no: 1, page_size: 50 },
    });
    expect(requestMock).toHaveBeenNthCalledWith(2, {
      url: "/system/ticket/42/comments",
      method: "post",
      data: { content: "评论内容" },
    });
  });
});
