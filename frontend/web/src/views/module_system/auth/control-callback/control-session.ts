import type { JWTOut } from "@/api/module_system/auth";
import { Auth } from "@/utils/auth/token";

const CONTROL_SSO_EXCHANGED_CODE_KEY = "control_sso_exchanged_code";
const CONTROL_SSO_EXCHANGE_META_KEY = "control_sso_exchange_meta";

export function persistControlExchange(code: string, tokens: JWTOut): void {
  Auth.setTokens(tokens.access_token, tokens.refresh_token, false);
  sessionStorage.setItem(
    CONTROL_SSO_EXCHANGE_META_KEY,
    JSON.stringify({ token_type: tokens.token_type, expires_in: tokens.expires_in })
  );
  sessionStorage.setItem(CONTROL_SSO_EXCHANGED_CODE_KEY, code);
}

export function resumeControlExchange(code: string): JWTOut | null {
  const accessToken = Auth.getAccessToken();
  if (!accessToken || sessionStorage.getItem(CONTROL_SSO_EXCHANGED_CODE_KEY) !== code) return null;

  try {
    const metadata = JSON.parse(sessionStorage.getItem(CONTROL_SSO_EXCHANGE_META_KEY) || "{}");
    if (typeof metadata.token_type !== "string" || typeof metadata.expires_in !== "number")
      return null;
    return {
      access_token: accessToken,
      refresh_token: Auth.getRefreshToken(),
      token_type: metadata.token_type,
      expires_in: metadata.expires_in,
    };
  } catch {
    return null;
  }
}
