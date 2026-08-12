/**
 * 系统配置状态管理模块
 *
 * 提供系统配置参数的状态管理
 *
 * ## 主要功能
 *
 * - 网站基础信息管理（标题、版本、描述）
 * - 网站图标配置（登录背景、favicon、Logo）
 * - 安全隐私配置（服务条款、版权、隐私政策）
 * - 接口安全配置（白名单、黑名单）
 * - 演示环境配置
 *
 * ## 使用场景
 *
 * - 系统初始化配置加载
 * - 登录页背景和Logo显示
 * - 底部版权信息展示
 * - 接口安全控制
 *
 * ## 持久化
 *
 * - 使用 localStorage 存储
 * - 自动缓存已加载的配置
 * - 支持强制刷新配置
 *
 * @module store/modules/config.store
 * @author FastapiAdmin Team
 */
import { store } from "@stores";
import ParamsAPI, { ConfigTable } from "@/api/module_system/params";
import SiteAPI, { type PublicSiteConfig } from "@/api/module_platform/site";
import TenantAPI from "@/api/module_platform/tenant";
import { defineStore } from "pinia";
import { ref } from "vue";
import { defaultAssemblySummary } from "@/config/assembly/default";
import {
  allowsFoodLogiTenantBrandField,
  resolveFoodLogiBrand,
} from "@/config/assembly/foodLogiBrand";

const SITE_CONFIG_ALIASES: Record<string, string> = {
  name: "tenant_name",
  logo_url: "tenant_logo",
};

const PUBLIC_SITE_BRAND_FIELDS = [
  "site_code",
  "name",
  "logo_url",
  "favicon",
  "login_bg",
  "copyright",
  "keep_record",
  "help_doc",
  "privacy",
  "clause",
] as const satisfies readonly (keyof PublicSiteConfig)[];

export const useConfigStore = defineStore(
  "configStore",
  () => {
    // 系统、Host 站点、登录租户与最终生效配置分层保存，避免来源混淆。
    const systemConfigData = ref<Record<string, ConfigTable>>({});
    const siteConfigData = ref<Record<string, ConfigTable>>({});
    const tenantConfigData = ref<Record<string, ConfigTable>>({});
    const effectiveConfigData = ref<Record<string, ConfigTable>>({});
    // 兼容历史调用方：configData 表示最终生效配置。
    const configData = effectiveConfigData;
    // 是否已加载配置
    const isConfigLoaded = ref(false);
    // 是否正在加载配置
    const configLoading = ref(false);
    // 当前认证租户覆盖层；null 表示登录前仅应用站点品牌。
    const currentTenantConfigId = ref<number | null>(null);
    // 最近一次 fetch 时间戳，用于 force=true 时防止短期重复请求
    let _lastFetchedAt = 0;
    const MIN_FETCH_INTERVAL_MS = 5000;

    function upsertConfigItem(target: Record<string, ConfigTable>, item: Partial<ConfigTable>) {
      if (item.config_value !== undefined && item.config_key) {
        target[item.config_key] = item as ConfigTable;
      }
    }

    function upsertSiteConfigItem(item: Partial<ConfigTable>) {
      upsertConfigItem(siteConfigData.value, item);
      const aliasKey = item.config_key ? SITE_CONFIG_ALIASES[item.config_key] : undefined;
      if (aliasKey) {
        upsertConfigItem(siteConfigData.value, {
          ...item,
          config_key: aliasKey,
        });
      }
    }

    function upsertTenantConfigItem(item: { config_key?: string; config_value?: string | null }) {
      const value = item.config_value;
      if (typeof value !== "string") return;
      if (
        item.config_key &&
        !allowsFoodLogiTenantBrandField(defaultAssemblySummary.name, item.config_key)
      ) {
        return;
      }
      const normalizedItem: Partial<ConfigTable> = {
        config_key: item.config_key,
        config_value: value,
      };
      upsertConfigItem(tenantConfigData.value, normalizedItem);
      const aliasKey = item.config_key ? SITE_CONFIG_ALIASES[item.config_key] : undefined;
      if (aliasKey) {
        upsertConfigItem(tenantConfigData.value, {
          ...normalizedItem,
          config_key: aliasKey,
        });
      }
    }

    function syncEffectiveConfig() {
      effectiveConfigData.value = {
        ...systemConfigData.value,
        ...siteConfigData.value,
        ...tenantConfigData.value,
      };
    }

    function replaceSiteConfig(site: PublicSiteConfig | null | undefined) {
      siteConfigData.value = {};
      if (site) {
        for (const field of PUBLIC_SITE_BRAND_FIELDS) {
          const value = site[field];
          if (typeof value !== "string") continue;
          upsertSiteConfigItem({ config_key: field, config_value: value });
        }
      }

      const productBrand = resolveFoodLogiBrand(
        defaultAssemblySummary.name,
        site?.site_code || window.location.hostname
      );
      if (productBrand) {
        upsertSiteConfigItem({ config_key: "name", config_value: productBrand.title });
        upsertSiteConfigItem({ config_key: "logo_url", config_value: productBrand.logo });
        upsertSiteConfigItem({ config_key: "favicon", config_value: productBrand.logo });
      }
    }

    /**
     * 获取系统配置 + 当前 Host 站点配置 + 可选的认证租户覆盖配置
     * @param force 是否强制刷新配置
     * @param tenantId 登录后的租户 ID；未传时不请求任何租户公开 ID 接口
     */
    async function getConfig(force = false, tenantId?: number | null) {
      const numericTenantId = Number(tenantId);
      const resolvedTenantId =
        tenantId === null
          ? null
          : Number.isInteger(numericTenantId) && numericTenantId > 0
            ? numericTenantId
            : currentTenantConfigId.value;
      if (configLoading.value) {
        return;
      }
      // force=true 时也需防短期内重复请求
      if (!force && isConfigLoaded.value && currentTenantConfigId.value === resolvedTenantId) {
        return;
      }
      if (
        force &&
        currentTenantConfigId.value === resolvedTenantId &&
        Date.now() - _lastFetchedAt < MIN_FETCH_INTERVAL_MS
      ) {
        return;
      }
      configLoading.value = true;
      try {
        // 三层配置互不依赖：并发请求，再按 system → site → tenant 的固定优先级合并。
        const systemPromise = ParamsAPI.getInitConfig();
        const sitePromise = SiteAPI.getPublicConfig().catch((error) => {
          console.warn("[configStore] 获取站点公开配置失败（非关键错误）", error);
          return null;
        });
        const tenantPromise =
          resolvedTenantId === null
            ? Promise.resolve(null)
            : TenantAPI.getTenantConfig(resolvedTenantId).catch((error) => {
                console.warn("[configStore] 获取认证租户配置失败（非关键错误）", error);
                return null;
              });

        const [response, siteResp, tenantResp] = await Promise.all([
          systemPromise,
          sitePromise,
          tenantPromise,
        ]);

        // 1. 系统级配置（演示模式、IP黑白名单等）
        const list = response?.data?.data;
        if (!Array.isArray(list)) {
          console.warn("[configStore] getInitConfig: 响应 data 非数组", response?.data);
          return;
        }
        systemConfigData.value = {};
        list.forEach((item: ConfigTable) => {
          upsertConfigItem(systemConfigData.value, item);
        });

        // 2. 当前 Host 由后端解析为 Site，前端不再传递可枚举的 tenant_id。
        replaceSiteConfig(null);
        if (siteResp) {
          replaceSiteConfig(siteResp?.data?.data);
        }

        // 3. 只有登录态明确传入 tenantId 时才加载认证租户覆盖层。
        tenantConfigData.value = {};
        if (resolvedTenantId !== null) {
          if (tenantResp) {
            const tenantList = tenantResp?.data?.data;
            if (Array.isArray(tenantList)) {
              tenantList.forEach((item) => upsertTenantConfigItem(item));
            }
            currentTenantConfigId.value = resolvedTenantId;
          }
        } else {
          currentTenantConfigId.value = null;
        }

        syncEffectiveConfig();
        isConfigLoaded.value = true;
        _lastFetchedAt = Date.now();
      } finally {
        configLoading.value = false;
      }
    }

    return {
      configData,
      systemConfigData,
      siteConfigData,
      tenantConfigData,
      effectiveConfigData,
      isConfigLoaded,
      configLoading,
      currentTenantConfigId,
      getConfig,
    };
  },
  {
    persist: true,
  }
);

export function useConfigStoreHook() {
  return useConfigStore(store);
}
