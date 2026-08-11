import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { store } from "@stores";
import SystemConfigAPI from "@/api/module_system/config";
import {
  defaultAuthFeatures,
  defaultAssemblySummary,
  type AuthFeatures,
  type AssemblySummary,
} from "@/config/assembly/default";

function normalizeSummary(summary?: Partial<AssemblySummary> | null): AssemblySummary {
  return {
    ...defaultAssemblySummary,
    ...summary,
    enabledRouteGroups: summary?.enabledRouteGroups ?? defaultAssemblySummary.enabledRouteGroups,
    disabledRouteGroups: summary?.disabledRouteGroups ?? defaultAssemblySummary.disabledRouteGroups,
    featureFlags: {
      ...defaultAssemblySummary.featureFlags,
      ...(summary?.featureFlags ?? {}),
    },
  };
}

function normalizeAuthFeatures(features?: Partial<AuthFeatures> | null): AuthFeatures {
  return {
    ...defaultAuthFeatures,
    ...features,
    oauthProviders: features?.oauthProviders ?? defaultAuthFeatures.oauthProviders,
    controlSso: Boolean(features?.controlSso ?? false),
  };
}

export const useAssemblyStore = defineStore("assemblyStore", () => {
  const summary = ref<AssemblySummary>(defaultAssemblySummary);
  const authFeatures = ref<AuthFeatures>(defaultAuthFeatures);
  const loaded = ref(false);
  const loading = ref(false);
  let loadPromise: Promise<void> | null = null;

  const disabledRouteGroups = computed(() => new Set(summary.value.disabledRouteGroups));
  const enabledRouteGroups = computed(() => new Set(summary.value.enabledRouteGroups));

  function isRouteGroupEnabled(routeGroup?: string): boolean {
    if (!routeGroup) return true;
    if (disabledRouteGroups.value.has(routeGroup)) return false;
    return enabledRouteGroups.value.size === 0 || enabledRouteGroups.value.has(routeGroup);
  }

  function isFeatureEnabled(feature: string, fallback = true): boolean {
    const value = summary.value.featureFlags[feature];
    return typeof value === "boolean" ? value : fallback;
  }

  async function loadPublicConfig(force = false) {
    if (loading.value && loadPromise) return loadPromise;
    if (loaded.value && !force) return;
    loading.value = true;
    loadPromise = (async () => {
      try {
        const response = await SystemConfigAPI.getPublicConfigInfo();
        summary.value = normalizeSummary(response?.data?.data?.assembly);
        authFeatures.value = normalizeAuthFeatures(response?.data?.data?.authFeatures);
      } catch (error) {
        console.warn("[assemblyStore] 获取能力装配摘要失败，使用本地默认配置", error);
        summary.value = normalizeSummary(summary.value);
        authFeatures.value = normalizeAuthFeatures(authFeatures.value);
      } finally {
        loaded.value = true;
        loading.value = false;
        loadPromise = null;
      }
    })();
    return loadPromise;
  }

  return {
    summary,
    authFeatures,
    loaded,
    loading,
    disabledRouteGroups,
    enabledRouteGroups,
    isRouteGroupEnabled,
    isFeatureEnabled,
    loadPublicConfig,
  };
});

export function useAssemblyStoreHook() {
  return useAssemblyStore(store);
}
