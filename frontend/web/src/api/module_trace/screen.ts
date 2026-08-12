import { request } from "@utils";

export const fetchTraceScreenOverview = () =>
  request({ url: "/food_traceability/screen/overview", method: "get" });
