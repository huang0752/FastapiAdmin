const AUTH_KEYS = {
  ACCESS_TOKEN: "access_token",
  REFRESH_TOKEN: "refresh_token",
  REMEMBER_ME: "remember_me",
} as const;

export class Auth {
  private static readPair(storage: Storage): { accessToken: string; refreshToken: string } | null {
    const accessToken = storage.getItem(AUTH_KEYS.ACCESS_TOKEN) || "";
    const refreshToken = storage.getItem(AUTH_KEYS.REFRESH_TOKEN) || "";
    return accessToken && refreshToken ? { accessToken, refreshToken } : null;
  }

  private static resolvePair(): { accessToken: string; refreshToken: string } | null {
    const rememberMe = Auth.getRememberMe();
    const preferredStorage = rememberMe ? localStorage : sessionStorage;
    const fallbackStorage = rememberMe ? sessionStorage : localStorage;
    const preferredPair = Auth.readPair(preferredStorage);
    if (preferredPair) return preferredPair;

    const fallbackPair = Auth.readPair(fallbackStorage);
    if (!fallbackPair) return null;

    Auth.setTokens(fallbackPair.accessToken, fallbackPair.refreshToken, rememberMe);
    return fallbackPair;
  }

  static isLoggedIn(): boolean {
    return !!Auth.getAccessToken();
  }

  static getAccessToken(): string {
    return Auth.resolvePair()?.accessToken || "";
  }

  static getRefreshToken(): string {
    return Auth.resolvePair()?.refreshToken || "";
  }

  static setTokens(accessToken: string, refreshToken: string, rememberMe: boolean): void {
    if (!accessToken.trim() || !refreshToken.trim()) {
      throw new Error("认证令牌不完整");
    }

    localStorage.setItem(AUTH_KEYS.REMEMBER_ME, String(rememberMe));

    if (rememberMe) {
      localStorage.setItem(AUTH_KEYS.ACCESS_TOKEN, accessToken);
      localStorage.setItem(AUTH_KEYS.REFRESH_TOKEN, refreshToken);
      sessionStorage.removeItem(AUTH_KEYS.ACCESS_TOKEN);
      sessionStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
    } else {
      sessionStorage.setItem(AUTH_KEYS.ACCESS_TOKEN, accessToken);
      sessionStorage.setItem(AUTH_KEYS.REFRESH_TOKEN, refreshToken);
      localStorage.removeItem(AUTH_KEYS.ACCESS_TOKEN);
      localStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
    }
  }

  static clearAuth(): void {
    sessionStorage.removeItem("control_sso_exchanged_code");
    sessionStorage.removeItem("control_sso_exchange_meta");
    localStorage.removeItem(AUTH_KEYS.ACCESS_TOKEN);
    localStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
    sessionStorage.removeItem(AUTH_KEYS.ACCESS_TOKEN);
    sessionStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
  }

  static getRememberMe(): boolean {
    return localStorage.getItem(AUTH_KEYS.REMEMBER_ME) === "true";
  }
}

export { AUTH_KEYS };
