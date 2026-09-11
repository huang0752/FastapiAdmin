export function resolveSourceReturnUrl(referrer: string, currentOrigin: string): string | null {
  if (!referrer.trim()) return null;
  try {
    const url = new URL(referrer);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.origin === currentOrigin) return null;
    return `${url.origin}/`;
  } catch {
    return null;
  }
}
