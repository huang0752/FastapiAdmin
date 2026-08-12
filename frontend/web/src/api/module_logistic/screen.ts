import { request } from "@utils";

export const fetchLogisticScreenOverview = () =>
  request({ url: "/cold_chain_vehicle/screen/overview", method: "get" });
