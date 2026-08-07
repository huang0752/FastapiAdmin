// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const nginx = readFileSync(resolve(process.cwd(), "../../docker/nginx/nginx.conf"), "utf8");

describe("web static delivery policy", () => {
  it("temporarily forces HTTP/1.1 for HTTPS", () => {
    expect(nginx).toContain("listen 443 ssl;");
    expect(nginx).not.toContain("listen 443 ssl http2;");
  });

  it("revalidates HTML and caches hashed assets immutably", () => {
    expect(nginx).toContain("location = /web/index.html");
    expect(nginx).toContain('add_header Cache-Control "no-cache" always;');
    expect(nginx).toContain(
      'add_header Cache-Control "public, max-age=31536000, immutable" always;'
    );
    expect(nginx).toContain("(?:js|css|img|webp|fonts|media)");
    expect(nginx).toContain("gzip_static on;");
  });
});
