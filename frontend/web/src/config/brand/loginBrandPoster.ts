import type { FoodSystem, SiteCode } from "./siteBrandTheme";

export type ProductBrandScene = "trace" | "agri" | "logistic";

export interface LoginBrandPoster {
  identity: string;
  headline: string;
  description: string;
  keywords: readonly [string, string, string];
  statuses: readonly [string, string] | readonly [string, string, string];
  scene: ProductBrandScene;
  siteTone: SiteCode;
}

const SYSTEM_COPY: Record<
  FoodSystem,
  Pick<LoginBrandPoster, "headline" | "description" | "keywords" | "statuses" | "scene">
> = {
  trace: {
    headline: "让每一批食品，都拥有可验证的来路",
    description: "以批次为线索连接生产、检验、仓储与流通节点，形成清晰可信的质量追溯链。",
    keywords: ["批次追溯", "质量校验", "链路可信"],
    statuses: ["批次校验完成", "链路节点已连接", "质量档案已就绪"],
    scene: "trace",
  },
  agri: {
    headline: "从田间计划，到城市餐桌的有序抵达",
    description: "把基地、分拣、波次与配送线路组织为协同网络，让农产品流转更清晰、更稳定。",
    keywords: ["基地协同", "配送路线", "新鲜抵达"],
    statuses: ["采收计划已协同", "分拣波次进行中", "配送路线已优化"],
    scene: "agri",
  },
  logistic: {
    headline: "让温度与轨迹，在每一程持续可见",
    description: "以车辆、温区和运输节点构成冷链运行图谱，守护运输过程中的稳定与连续。",
    keywords: ["温区稳定", "轨迹同步", "车辆调度"],
    statuses: ["温区运行稳定", "车辆轨迹已同步", "冷链节点连接正常"],
    scene: "logistic",
  },
};

export const LOGIN_BRAND_POSTERS = Object.fromEntries(
  (["data360", "znceedi"] as const).flatMap((site) =>
    (["trace", "agri", "logistic"] as const).map((system) => [
      `${site}-${system}`,
      {
        identity: `${site}-${system}`,
        ...SYSTEM_COPY[system],
        siteTone: site,
      },
    ])
  )
) as Record<`${SiteCode}-${FoodSystem}`, LoginBrandPoster>;

export function resolveLoginBrandPoster(
  site: SiteCode,
  system: FoodSystem
): LoginBrandPoster | null {
  return LOGIN_BRAND_POSTERS[`${site}-${system}`] ?? null;
}
