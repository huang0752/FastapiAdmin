export function buildAuthorizationRetryUrl(currentUrl: string, rootHref: string): string {
  return new URL(rootHref, currentUrl).href;
}

export function reloadAuthorization(rootHref: string): void {
  window.location.replace(buildAuthorizationRetryUrl(window.location.href, rootHref));
}
