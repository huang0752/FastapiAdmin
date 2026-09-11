/** User store. */
import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { LanguageEnum } from "@/enums/appEnum";
import { router } from "@/router";
import { useSettingsStore } from "./setting.store";
import { useWorktabStore } from "./worktab.store";
import { useMenuStore } from "./menu.store";
import { useConfigStore } from "./config.store";
import { AppRouteRecord } from "@/types/router";
import { Auth, setPageTitle, StorageConfig } from "@utils";
import AuthAPI from "@/api/module_system/auth";
import UserAPI from "@/api/module_system/user";
import type { JWTOut, TenantOption } from "@/api/module_system/auth";
import type { MenuTable } from "@/api/module_platform/menu";
import { ResultEnum } from "@/enums/api/result.enum";
import { ElNotification } from "element-plus";
import { store, useDictStore } from "@stores";
import type { UserInfo } from "@/api/module_system/user";
import { applyPreset, resolveAndApplyPreset } from "@/hooks/core/useThemePreset";

/** 延迟加载 beforeEach 工具函数，避免 user.store 与 beforeEach 的循环依赖 */
let _routerUtilsPromise: Promise<typeof import("@/router/beforeEach")> | null = null;
const getRouterUtils = () => {
  if (!_routerUtilsPromise) _routerUtilsPromise = import("@/router/beforeEach");
  return _routerUtilsPromise;
};

/** {@link useUserStore} 的 `logout` 可选参数 */
export interface LogoutOptions {
  /**
   * 为 false 时仅清理状态，不跳转登录（由调用方自行 next/router，如锁屏页 replace）
   * @default true
   */
  navigate?: boolean;
}

export interface SessionMutationOptions {
  shouldApply?: () => boolean;
}

export interface EstablishSessionOptions {
  /** SSO launch codes already select the target tenant; do not replace it from local preferences. */
  restoreSavedTenant?: boolean;
  isCurrent?: () => boolean;
}

/**
 * 用户状态管理
 * 管理用户登录状态、个人信息、语言设置、搜索历史、锁屏状态等
 */
export const useUserStore = defineStore(
  "userStore",
  () => {
    // 语言设置
    const language = ref(LanguageEnum.ZH);
    // 登录状态
    const isLogin = ref(false);
    // 锁屏状态
    const isLock = ref(false);
    // 锁屏密码
    const lockPassword = ref("");
    // 用户信息
    const info = ref<Partial<UserInfo>>({});
    // 搜索历史记录
    const searchHistory = ref<AppRouteRecord[]>([]);
    // 访问令牌
    const accessToken = ref("");
    // 刷新令牌
    const refreshToken = ref("");
    // 路由列表
    const routeList = ref<MenuTable[]>([]);
    // 权限列表
    const prems = ref<string[]>([]);
    // 是否已获取路由
    const hasGetRoute = ref(false);
    // 记住我状态
    const rememberMe = ref(Auth.getRememberMe());
    // 租户列表
    const tenantList = ref<TenantOption[]>([]);
    // 当前选中租户
    const currentTenant = ref<TenantOption | null>(null);
    /** info 扩展类型：兼容 API 返回 `user_id`（非标准 UserInfo 字段） */
    type UserInfoLike = Partial<UserInfo> & Record<string, any>;

    // 计算属性：基础用户信息
    const basicInfo = computed(() => info.value as UserInfoLike);
    // 计算属性：获取设置状态
    const getSettingState = computed(() => useSettingsStore().$state);
    // 计算属性：获取工作台状态
    const getWorktabState = computed(() => useWorktabStore().$state);
    // 计算属性：获取基础信息
    const getBasicInfo = computed(() => info.value as UserInfoLike);
    // 计算属性：获取路由列表
    const getRouteList = computed(() => routeList.value);
    // 计算属性：获取权限列表
    const getPerms = computed(() => prems.value);
    // 计算属性：是否已获取路由
    const getHasGetRoute = computed(() => hasGetRoute.value);

    /**
     * 设置用户信息
     * @param newInfo 新的用户信息
     */
    const setUserInfo = (newInfo: UserInfo | UserInfo) => {
      info.value = newInfo;
      // 设置用户信息后自动更新权限
      setPermissions([]);
    };

    /**
     * 设置登录状态
     * @param status 登录状态
     */
    const setLoginStatus = (status: boolean) => {
      isLogin.value = status;
    };

    /**
     * 设置语言
     * @param lang 语言枚举值
     */
    const setLanguage = (lang: LanguageEnum) => {
      setPageTitle(router.currentRoute.value);
      language.value = lang;
    };

    /**
     * 设置搜索历史
     * @param list 搜索历史列表
     */
    const setSearchHistory = (list: AppRouteRecord[]) => {
      searchHistory.value = list;
    };

    /**
     * 设置锁屏状态
     * @param status 锁屏状态
     */
    const setLockStatus = (status: boolean) => {
      isLock.value = status;
    };

    /**
     * 设置锁屏密码
     * @param password 锁屏密码
     */
    const setLockPassword = (password: string) => {
      lockPassword.value = password;
    };

    /**
     * 设置令牌
     * @param newAccessToken 访问令牌
     * @param newRefreshToken 刷新令牌（可选）
     */
    const setToken = (newAccessToken: string, newRefreshToken?: string) => {
      accessToken.value = newAccessToken;
      if (newRefreshToken) {
        refreshToken.value = newRefreshToken;
      }
    };

    /**
     * 检查并清理工作台标签页
     * 如果不是同一用户登录，清空工作台标签页
     * 应在登录成功后调用
     */
    const checkAndClearWorktabs = () => {
      const lastUserId = localStorage.getItem(StorageConfig.LAST_USER_ID_KEY);
      const ui = info.value as UserInfoLike;
      const currentUserId = ui.id || ui.user_id;

      // 无法获取当前用户 ID，跳过检查
      if (!currentUserId) return;

      // 首次登录或缓存已清除，保留现有标签页
      if (!lastUserId) {
        return;
      }

      // 不同用户登录：清空工作台标签（含 current，避免与路由脱节；并与持久化一致）
      if (String(currentUserId) !== String(lastUserId)) {
        useWorktabStore().clearAll();
      }

      // 清除临时存储
      localStorage.removeItem(StorageConfig.LAST_USER_ID_KEY);
    };

    /**
     * 获取用户租户列表
     */
    async function fetchTenants(options?: SessionMutationOptions) {
      try {
        const response = await AuthAPI.getTenants();
        const data = response.data.data || [];
        if (options?.shouldApply?.() === false) return data;
        tenantList.value = data;
        return data;
      } catch (error) {
        console.error("获取租户列表失败:", error);
        return [];
      }
    }

    /**
     * 选择租户
     */
    async function selectTenant(tenantId: number, options?: SessionMutationOptions) {
      const response = await AuthAPI.selectTenant(tenantId);
      if (options?.shouldApply?.() === false) return;
      const data = response.data.data;
      if (response.data.code === ResultEnum.SUCCESS && data?.access_token) {
        const currentRefreshToken = Auth.getRefreshToken() || "";
        Auth.setTokens(data.access_token, currentRefreshToken, rememberMe.value);
        setToken(data.access_token);
        const found = tenantList.value.find((t) => String(t.id) === String(tenantId));
        if (found) {
          currentTenant.value = found;
          localStorage.setItem(StorageConfig.LAST_TENANT_ID_KEY, String(found.id));
        }
        await useConfigStore().getConfig(true, tenantId, options);
        if (options?.shouldApply?.() === false) return;
        resolveAndApplyPreset();
      }
    }

    /**
     * 设置当前租户
     */
    function setCurrentTenant(tenant: TenantOption | null) {
      currentTenant.value = tenant;
      if (tenant) {
        localStorage.setItem(StorageConfig.LAST_TENANT_ID_KEY, String(tenant.id));
      } else {
        localStorage.removeItem(StorageConfig.LAST_TENANT_ID_KEY);
      }
    }

    /**
     * 获取用户信息
     */
    async function getUserInfo(options?: SessionMutationOptions) {
      try {
        const response = await UserAPI.getCurrentUserInfo();
        if (options?.shouldApply?.() === false) return false;
        const data = response.data.data;
        const menus: MenuTable[] = data?.menus || [];
        delete data?.menus;
        info.value = { ...info.value, ...data } as Partial<UserInfo>;
        setRoute(menus);
        return true;
      } catch (error) {
        console.error("获取用户信息失败:", error);
        throw error;
      }
    }

    /**
     * 设置头像
     */
    function setAvatar(avatar: string) {
      info.value = { ...info.value, avatar };
    }

    /**
     * 设置路由
     */
    function setRoute(routers: MenuTable[]) {
      routeList.value = routers;
      hasGetRoute.value = true;
      setPermissions(routers);
    }

    function clearAuthorizationSnapshot() {
      routeList.value = [];
      prems.value = [];
      hasGetRoute.value = false;
    }

    /**
     * 设置权限
     */
    function setPermissions(menus: MenuTable[]) {
      prems.value = [];
      if (!info.value.roles) return;

      const roleMenus = info.value.roles
        .filter((role) => role.menus && role.menus.length > 0)
        .flatMap((role) => role.menus)
        .filter((menu): menu is MenuTable => menu !== undefined);

      const allMenus = [...menus, ...roleMenus];

      const permissionSet = new Set<string>();
      const collect = (items: MenuTable[]) => {
        items.forEach((item) => {
          if (item.permission) {
            permissionSet.add(item.permission);
          }
          if (item.children && item.children.length > 0) {
            collect(item.children.filter((child): child is MenuTable => child !== undefined));
          }
        });
      };

      collect(allMenus);
      prems.value = Array.from(permissionSet);
    }

    /**
     * 使用后端签发的令牌建立完整前端会话。
     * 密码登录与中控统一登录共用此流程，避免两类会话初始化顺序漂移。
     */
    async function establishSession(
      tokens: JWTOut,
      remember = Auth.getRememberMe(),
      initialTenants: TenantOption[] = [],
      options?: EstablishSessionOptions
    ) {
      const isCurrent = options?.isCurrent ?? (() => true);
      const mutationOptions: SessionMutationOptions = { shouldApply: isCurrent };
      if (!isCurrent()) return false;
      rememberMe.value = remember;
      const routerUtils = await getRouterUtils();
      if (!isCurrent()) return false;
      routerUtils.resetDynamicRoutesSync();
      if (!isCurrent()) return false;
      Auth.setTokens(tokens.access_token, tokens.refresh_token, remember);
      setToken(tokens.access_token, tokens.refresh_token);

      if (initialTenants.length > 0) {
        if (!isCurrent()) return false;
        tenantList.value = initialTenants;
      }

      if (!(await getUserInfo(mutationOptions)) || !isCurrent()) return false;
      const tenants =
        initialTenants.length > 0 ? initialTenants : await fetchTenants(mutationOptions);
      if (!isCurrent()) return false;
      const availableTenants = tenants;
      const ui = info.value as UserInfoLike;
      const sessionTenantId = ui.session_tenant_id;
      const savedTenantId = localStorage.getItem(StorageConfig.LAST_TENANT_ID_KEY);
      const savedTenant = savedTenantId
        ? availableTenants.find(
            (tenant: TenantOption) => String(tenant.id) === String(savedTenantId)
          )
        : undefined;

      if (
        options?.restoreSavedTenant !== false &&
        sessionTenantId &&
        savedTenant &&
        String(savedTenant.id) !== String(sessionTenantId)
      ) {
        await selectTenant(savedTenant.id, mutationOptions);
        if (!isCurrent()) return false;
        if (!(await getUserInfo(mutationOptions)) || !isCurrent()) return false;
      }

      const activeUi = info.value as UserInfoLike;
      const activeTenantId =
        activeUi.session_tenant_id ||
        sessionTenantId ||
        activeUi.tenant_id ||
        availableTenants[0]?.id;
      if (activeTenantId) {
        const found = availableTenants.find(
          (tenant: TenantOption) => String(tenant.id) === String(activeTenantId)
        );
        if (found) {
          if (!isCurrent()) return false;
          setCurrentTenant(found);
        }
      }
      await useConfigStore().getConfig(true, activeTenantId, mutationOptions);
      if (!isCurrent()) return false;
      resolveAndApplyPreset();
      if (!isCurrent()) return false;
      setLoginStatus(true);
      return true;
    }

    /**
     * 登录
     */
    async function login(loginForm: any) {
      const response = await AuthAPI.login(loginForm);
      const data = response.data.data;
      if (response.data.code === ResultEnum.SUCCESS) {
        ElNotification({
          title: "通知",
          message: response.data.msg,
          type: "success",
        });
      }
      rememberMe.value = loginForm.remember;

      const tokens: JWTOut = {
        access_token: data?.access_token || "",
        refresh_token: data?.refresh_token || "",
        token_type: data?.token_type || "bearer",
        expires_in: data?.expires_in || 0,
      };
      if (!tokens.access_token) {
        console.error("[Login Debug] ⚠️ 未获取到 access_token！字段名可能与后端不匹配");
      }

      const tenants = data?.tenants || [];
      await establishSession(tokens, rememberMe.value, tenants);
    }

    /**
     * 登出：有 token 时请求后端；统一清理状态；默认跳转登录页并带 redirect
     */
    async function logout(options?: LogoutOptions) {
      const shouldNavigate = options?.navigate !== false;

      const ui = info.value as UserInfoLike;
      const currentUserId = ui.id || ui.user_id;
      if (currentUserId) {
        localStorage.setItem(StorageConfig.LAST_USER_ID_KEY, String(currentUserId));
      }

      let apiError: unknown;
      const token = Auth.getAccessToken();
      if (token) {
        try {
          const response = await AuthAPI.logout({ token });
          if (response.data.code === ResultEnum.SUCCESS) {
            ElNotification({
              title: "通知",
              message: response.data.msg,
              type: "success",
            });
          }
        } catch (e) {
          apiError = e;
        }
      }

      applyPreset("default", { restoreFactory: false });
      resetAllState();
      sessionStorage.removeItem("iframeRoutes");
      useMenuStore().setHomePath("");
      (await getRouterUtils()).resetRouterState(500);

      if (shouldNavigate) {
        const currentRoute = router.currentRoute.value;
        const redirect = currentRoute.path !== "/login" ? currentRoute.fullPath : undefined;
        await router.push({
          name: "Login",
          query: redirect ? { redirect } : undefined,
        });
      }

      if (apiError) {
        throw apiError;
      }
    }

    /**
     * 重置所有状态
     */
    function resetAllState() {
      Auth.clearAuth();
      info.value = {};
      routeList.value = [];
      hasGetRoute.value = false;
      isLogin.value = false;
      isLock.value = false;
      lockPassword.value = "";
      accessToken.value = "";
      refreshToken.value = "";
      prems.value = [];
      tenantList.value = [];
      currentTenant.value = null;
      /** 登出 / 认证失效：会话结束，工作栏与 KeepAlive exclude 一并清空（pinia 持久化随之写入） */
      useWorktabStore().clearAll();
    }

    /**
     * 清空用户信息
     */
    function clearUserInfo() {
      info.value = {};
      routeList.value = [];
      hasGetRoute.value = false;
    }

    /**
     * 刷新token
     */
    async function refreshTokenFn() {
      const currentRefreshToken = Auth.getRefreshToken();

      if (!currentRefreshToken) {
        throw new Error("没有有效的刷新令牌");
      }

      const response = await AuthAPI.refreshToken({ refresh_token: currentRefreshToken });
      const data = response.data.data;
      // 更新令牌，保持当前记住我状态
      Auth.setTokens(data.access_token, data.refresh_token, Auth.getRememberMe());
      setToken(data.access_token, data.refresh_token);
    }

    /**
     * 完全重置所有状态（包括路由、标签页、字典等）
     */
    function fullResetAllState() {
      // 重置用户状态
      Auth.clearAuth();
      // 重置用户信息
      clearUserInfo();
      useWorktabStore().clearAll();
      // 重置字典
      useDictStore(store).clearDictData();

      return Promise.resolve();
    }

    return {
      language,
      isLogin,
      isLock,
      lockPassword,
      info,
      searchHistory,
      accessToken,
      routeList,
      prems,
      hasGetRoute,
      rememberMe,
      getUserInfo,
      getSettingState,
      getWorktabState,
      basicInfo,
      getBasicInfo,
      getRouteList,
      getPerms,
      getHasGetRoute,
      setUserInfo,
      setLoginStatus,
      setLanguage,
      setSearchHistory,
      setLockStatus,
      setLockPassword,
      setToken,
      setAvatar,
      setRoute,
      clearAuthorizationSnapshot,
      setPermissions,
      establishSession,
      login,
      logout,
      checkAndClearWorktabs,
      clearUserInfo,
      refreshTokenFn,
      resetAllState,
      fullResetAllState,
      tenantList,
      currentTenant,
      fetchTenants,
      selectTenant,
      setCurrentTenant,
    };
  },
  {
    persist: {
      key: "user",
      storage: localStorage,
    },
  }
);

export function useUserStoreHook() {
  return useUserStore(store);
}
