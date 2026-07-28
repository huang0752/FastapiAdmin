import type { RouteRecordRaw } from "vue-router";
import {
  defaultAssemblySummary,
  type AssemblySummary,
} from "@/config/assembly/default";

type AppRouteRecordRaw = RouteRecordRaw & { hidden?: boolean };

function routeGroupOf(route: RouteRecordRaw): string | undefined {
  return route.meta?.routeGroup as string | undefined;
}

export function isRouteGroupEnabled(
  routeGroup?: string,
  summary: AssemblySummary = defaultAssemblySummary
): boolean {
  if (!routeGroup) return true;
  if (summary.disabledRouteGroups.includes(routeGroup)) return false;
  return summary.enabledRouteGroups.length === 0 || summary.enabledRouteGroups.includes(routeGroup);
}

export function shouldIncludeShellRouteGroup(
  routeGroup: string,
  summary: AssemblySummary = defaultAssemblySummary
): boolean {
  return isRouteGroupEnabled(routeGroup, summary);
}

export function filterRoutesByAssembly<T extends RouteRecordRaw>(
  routes: T[],
  summary: AssemblySummary = defaultAssemblySummary
): T[] {
  return routes.reduce<T[]>((acc, route) => {
    if (!isRouteGroupEnabled(routeGroupOf(route), summary)) {
      return acc;
    }

    const next = { ...route } as T;
    if (route.children?.length) {
      const children = filterRoutesByAssembly(route.children, summary);
      next.children = children as T["children"];
      if (children.length === 0 && route.children.length > 0 && !route.component) {
        return acc;
      }
    }
    acc.push(next);
    return acc;
  }, []);
}

export function filterAppRoutesByAssembly(
  routes: AppRouteRecordRaw[],
  summary: AssemblySummary = defaultAssemblySummary
): AppRouteRecordRaw[] {
  return filterRoutesByAssembly(routes as RouteRecordRaw[], summary) as AppRouteRecordRaw[];
}
