import { request } from "@utils";

export const fetchAgriScreenOverview = () =>
  request({ url: "/agricultural_delivery/screen/overview", method: "get" });
