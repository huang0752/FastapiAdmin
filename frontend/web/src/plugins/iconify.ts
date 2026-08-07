/**
 * 本地注册 Iconify 图标集，实现离线显示（不再从 CDN 加载）
 */
import { addCollection } from "@iconify/vue";

let collectionPromise: Promise<void> | null = null;

/** 首个 Iconify 图标真正渲染时再加载离线图标集；并发调用共享同一个 Promise。 */
export function ensureIconifyCollections(): Promise<void> {
  if (!collectionPromise) {
    collectionPromise = Promise.all([
      import("@iconify-json/ri/icons.json"),
      import("@iconify-json/svg-spinners/icons.json"),
      import("@iconify-json/line-md/icons.json"),
    ]).then(([ri, svgSpinners, lineMd]) => {
      addCollection(ri.default);
      addCollection(svgSpinners.default);
      addCollection(lineMd.default);
    });
  }
  return collectionPromise;
}
