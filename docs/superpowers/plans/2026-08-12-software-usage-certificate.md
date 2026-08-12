# Software Usage Certificate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a product-neutral, one-certificate-per-tenant software usage certificate with tenant-admin preview/PDF download, platform read-only lookup, QR-backed public verification, and live tenant/system data.

**Architecture:** Store only immutable certificate identity (`usage_certificate_no`, `usage_certificate_token`, `usage_certificate_created_at`) on `platform_tenant`; derive all visible business fields and current validity at request time. Centralize certificate context, HTML rendering, QR creation, PDF generation, authorization, and filename safety in a focused `module_platform.usage_certificate` package, then expose tenant, platform, and public read-only routers and three corresponding frontend surfaces.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, Jinja2, WeasyPrint, `qrcode[pil]`, Vue 3, TypeScript, Element Plus, Vitest, pytest.

---

## File map

- `backend/app/api/v1/module_platform/usage_certificate/identity.py`: generate fixed certificate numbers and high-entropy tokens.
- `backend/app/api/v1/module_platform/usage_certificate/schema.py`: tenant preview, platform page, and public verification contracts.
- `backend/app/api/v1/module_platform/usage_certificate/service.py`: live field resolution, validity derivation, tenant/platform isolation, HTML/QR/PDF generation.
- `backend/app/api/v1/module_platform/usage_certificate/controller.py`: tenant-admin, platform-admin, and public read-only HTTP routes.
- `backend/templates/includes/software_usage_certificate.html`: product-neutral A4 HTML/PDF template.
- `backend/app/api/v1/module_platform/tenant/model.py`: three immutable certificate identity columns.
- `backend/app/api/v1/module_platform/tenant/service.py`: assign certificate identity in every tenant creation path.
- `backend/app/alembic/versions/20260812_01_add_tenant_usage_certificate.py`: schema migration and deterministic safe backfill.
- `backend/app/api/v1/module_platform/__init__.py`: mount certificate routers.
- `backend/app/config/setting.py` and env examples: trusted public verification origin.
- `backend/app/scripts/data/platform_menu.json`: platform certificate menu and tenant-owner permission child.
- `backend/app/scripts/data/platform_package_menu.json`: make certificate access available to package-backed tenant owners.
- `backend/app/api/v1/module_platform/package/service.py`: preserve tenant-owner certificate permission.
- `frontend/web/src/api/module_platform/usage_certificate.ts`: typed certificate API client.
- `frontend/web/src/views/module_platform/self_service/index.vue`: tenant-admin certificate tab.
- `frontend/web/src/views/module_platform/usage_certificate/index.vue`: platform certificate registry.
- `frontend/web/src/views/public/usage_certificate/index.vue`: public token verification page.
- `frontend/web/src/router/staticRoutes.ts`: public verification route.
- `backend/tests/test_usage_certificate_identity.py`: identity and validity unit tests.
- `backend/tests/test_usage_certificate_routes.py`: auth, isolation, public minimization, HTML, PDF, and route-mount tests.
- `backend/tests/test_product_migrations.py`: migration structure/backfill contract.
- `backend/tests/test_tenant_governance_closure.py`: owner minimum permission coverage.
- `frontend/web/src/__tests__/usage-certificate.spec.ts`: API, permission rendering, and public page tests.

### Task 1: Persist fixed certificate identity

**Files:**
- Create: `backend/tests/test_usage_certificate_identity.py`
- Create: `backend/app/api/v1/module_platform/usage_certificate/__init__.py`
- Create: `backend/app/api/v1/module_platform/usage_certificate/identity.py`
- Modify: `backend/app/api/v1/module_platform/tenant/model.py`
- Modify: `backend/app/api/v1/module_platform/tenant/service.py`

- [ ] **Step 1: Write failing identity tests**

Add tests proving that the generated number is product-neutral, contains the creation-time tenant code plus a six-character suffix, the token is URL-safe and high entropy, and two calls do not collide:

```python
from app.api.v1.module_platform.usage_certificate.identity import (
    build_usage_certificate_identity,
)


def test_build_usage_certificate_identity_is_product_neutral_and_unique() -> None:
    first = build_usage_certificate_identity("ACME")
    second = build_usage_certificate_identity("ACME")

    assert first.number.startswith("FA-SW-ACME-")
    assert len(first.number.rsplit("-", 1)[1]) == 6
    assert len(first.token) >= 43
    assert "=" not in first.token
    assert first != second
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `cd backend && uv run pytest tests/test_usage_certificate_identity.py -q`

Expected: FAIL with `ModuleNotFoundError` for `module_platform.usage_certificate`.

- [ ] **Step 3: Implement the focused identity generator**

Create a frozen `UsageCertificateIdentity` dataclass. Use `secrets.choice(string.ascii_uppercase + string.digits)` for the six-character suffix and `secrets.token_urlsafe(32)` for the token. Normalize the already-validated tenant code to uppercase only for the certificate number.

- [ ] **Step 4: Add immutable identity columns and creation defaults**

Add nullable-at-model-load but normally populated columns:

```python
usage_certificate_no: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
usage_certificate_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
usage_certificate_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

In `TenantService.create_tenant_record()`, build the identity before constructing or CRUD-creating the tenant, inject all three fields, and retry only certificate-number/token unique conflicts. Do not expose these fields through `TenantCreateSchema` or `TenantUpdateSchema`.

- [ ] **Step 5: Add creation-path tests**

Extend the test file with an async service test that creates a tenant through `create_tenant_record()`, asserts all three fields are populated, then changes `tenant.code` and asserts the stored number remains unchanged.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && uv run pytest tests/test_usage_certificate_identity.py tests/test_api_module_platform.py::TestTenant::test_tenant_create -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/module_platform/usage_certificate backend/app/api/v1/module_platform/tenant/model.py backend/app/api/v1/module_platform/tenant/service.py backend/tests/test_usage_certificate_identity.py
git commit -m "feat: 增加租户软件使用证明固定身份"
```

### Task 2: Add the migration and existing-tenant backfill

**Files:**
- Create: `backend/app/alembic/versions/20260812_01_add_tenant_usage_certificate.py`
- Modify: `backend/tests/test_product_migrations.py`

- [ ] **Step 1: Write the failing migration contract test**

Add assertions that revision `20260812_01` follows `20260811_01`, adds all three columns, creates unique constraints for number and token, backfills every row, and only then makes the columns non-null.

- [ ] **Step 2: Run the focused migration test**

Run: `cd backend && uv run pytest tests/test_product_migrations.py -q`

Expected: FAIL because the revision does not exist.

- [ ] **Step 3: Implement a PostgreSQL-safe backfill migration**

The migration must:

1. add nullable columns;
2. read existing `(id, code, created_time)` rows in stable `id` order;
3. generate `FA-SW-{CODE}-{SUFFIX}` and `token_urlsafe(32)` values in Python;
4. retry in-memory collisions before issuing updates;
5. use `created_time` as `usage_certificate_created_at`, falling back to the migration timestamp;
6. create unique constraints;
7. alter all three columns to non-null.

Downgrade drops constraints and columns only; it never changes unrelated tenant data.

- [ ] **Step 4: Run migration tests and a clean upgrade rehearsal**

Run: `cd backend && uv run pytest tests/test_product_migrations.py -q`

Expected: PASS.

Run against the project test database configured by the test harness: `cd backend && uv run alembic upgrade head`

Expected: exit 0 with head `20260812_01`. Do not run this command against an unidentified shared or production database.

- [ ] **Step 5: Commit**

```bash
git add backend/app/alembic/versions/20260812_01_add_tenant_usage_certificate.py backend/tests/test_product_migrations.py
git commit -m "feat: 迁移租户软件使用证明身份"
```

### Task 3: Build live certificate context, validity, QR, HTML, and PDF

**Files:**
- Create: `backend/app/api/v1/module_platform/usage_certificate/schema.py`
- Create: `backend/app/api/v1/module_platform/usage_certificate/service.py`
- Create: `backend/templates/includes/software_usage_certificate.html`
- Modify: `backend/app/config/setting.py`
- Modify: `backend/env/.env.dev.example`
- Modify: `backend/env/.env.prod.example`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.txt`
- Modify: `backend/tests/test_usage_certificate_identity.py`

- [ ] **Step 1: Add failing live-context and validity tests**

Cover these exact cases:

- active tenant inside configured dates is valid;
- suspended, frozen, expired, archived, or soft-deleted tenant is invalid;
- missing start/end dates render as `-` and do not independently invalidate;
- title/version/name/USCC/date changes appear on the next render;
- request IP appears only when `include_request_ip=True`;
- missing public origin produces no QR data URL and renders “公开查验地址未配置”.

- [ ] **Step 2: Run tests to verify missing service failures**

Run: `cd backend && uv run pytest tests/test_usage_certificate_identity.py -q`

Expected: FAIL on missing context/service symbols.

- [ ] **Step 3: Add explicit certificate contracts**

Define `UsageCertificateView`, `UsageCertificatePreviewOut`, `UsageCertificatePublicOut`, `UsageCertificatePlatformItem`, and `UsageCertificatePlatformPage`. Public output must omit `request_ip`, internal tenant ID, token, contacts, package, and operator fields.

- [ ] **Step 4: Implement live context and validity derivation**

`UsageCertificateService.build_view()` must read the current `TenantModel`, `settings.TITLE`, `settings.VERSION`, current time, and optional request IP. Convert blank values to `-`. Treat only `TenantStatus.ACTIVE` and `TenantStatus.GRACE` as usable status candidates, then apply configured date bounds; soft-deleted records always produce `currently_valid=False`.

- [ ] **Step 5: Add trusted public-origin configuration**

Add `USAGE_CERTIFICATE_PUBLIC_ORIGIN: str = ""`. Normalize by trimming whitespace and a trailing slash. Never fall back to `Request.base_url`, `Host`, `localhost`, or `SITE_URL` when it is empty.

- [ ] **Step 6: Add QR generation without filesystem writes**

Add `qrcode[pil]==8.2` to both dependency manifests. Render QR PNG bytes into a base64 data URL only when the trusted public origin exists. The encoded URL is `{origin}/#/certificate/verify/{token}`.

- [ ] **Step 7: Implement the A4 product-neutral template**

Create the confirmed modern-document template with `@page { size: A4; margin: 0; }`, software name/version above the title, live fields, validity banner, identity creation time, optional authenticated request IP, QR or unconfigured message, and no issuer/operator/contact/package fields.

- [ ] **Step 8: Implement HTML and PDF rendering**

Use `render_template_file()` for HTML. Use `await run_in_threadpool(html_to_pdf, html)` for PDF. Validate `content.startswith(b"%PDF")`; raise `CustomException(msg="软件使用证明 PDF 生成失败，请稍后重试")` on rendering or validation failure. Return bytes only; never write a file.

- [ ] **Step 9: Run focused service tests**

Run: `cd backend && uv run pytest tests/test_usage_certificate_identity.py -q`

Expected: PASS, including an assertion that generated PDF bytes begin with `%PDF` when WeasyPrint runtime libraries are available.

- [ ] **Step 10: Commit**

```bash
git add backend/app/api/v1/module_platform/usage_certificate/schema.py backend/app/api/v1/module_platform/usage_certificate/service.py backend/templates/includes/software_usage_certificate.html backend/app/config/setting.py backend/env/.env.dev.example backend/env/.env.prod.example backend/pyproject.toml backend/requirements.txt backend/tests/test_usage_certificate_identity.py
git commit -m "feat: 增加软件使用证明实时渲染"
```

### Task 4: Expose tenant, platform, and public read-only APIs

**Files:**
- Create: `backend/app/api/v1/module_platform/usage_certificate/controller.py`
- Modify: `backend/app/api/v1/module_platform/__init__.py`
- Modify: `backend/app/config/setting.py`
- Create: `backend/tests/test_usage_certificate_routes.py`

- [ ] **Step 1: Write failing route-mount and response-boundary tests**

Assert these routes exist:

```text
GET /platform/tenant/usage-certificate/preview
GET /platform/tenant/usage-certificate/download
GET /platform/usage-certificate/list
GET /platform/usage-certificate/{id}/preview
GET /platform/usage-certificate/{id}/download
GET /platform/public/usage-certificate/{token}
```

Also assert tenant endpoints require `module_platform:usage-certificate:tenant-query`, platform endpoints require `module_platform:usage-certificate:platform-query`, and the public endpoint has no auth dependency.

- [ ] **Step 2: Run route tests to verify 404/missing-router failures**

Run: `cd backend && uv run pytest tests/test_usage_certificate_routes.py -q`

Expected: FAIL because the routers are not mounted.

- [ ] **Step 3: Implement tenant-admin preview and download**

Resolve only `auth.tenant_id`; never accept a tenant ID parameter. Use `get_client_ip(request)` for authenticated preview/PDF. Return preview JSON and a direct `application/pdf` attachment named with a sanitized current enterprise name.

- [ ] **Step 4: Implement platform list, preview, and download**

Use `require_platform_admin` semantics plus `module_platform:usage-certificate:platform-query`. Support `page_no`, `page_size`, tenant name, tenant code, certificate number, and derived validity filtering. Include soft-deleted tenants in this specific registry query so their fixed certificates remain visible and invalid; do not weaken normal tenant queries.

- [ ] **Step 5: Implement minimal public verification**

Look up the exact token including soft-deleted tenants. Return only `UsageCertificatePublicOut`. Convert missing/malformed tokens to the same 404 payload/message: `无法核验此证明`. Add endpoint-local rate limiting using the project’s existing limiter mechanism; do not create Redis keys from raw token values.

- [ ] **Step 6: Whitelist only the exact public API prefix**

Add `/api/v1/platform/public/usage-certificate/` to `TENANT_WHITELIST_PATHS`. Do not whitelist the tenant or platform-admin certificate routes.

- [ ] **Step 7: Verify authorization, isolation, minimization, and PDF**

Tests must prove:

- a tenant admin can access its own proof;
- a member without the permission gets 403;
- tenant endpoints cannot select another tenant;
- platform admin can list/preview/download;
- public JSON lacks IP and private fields;
- invalid token and deleted tenant behavior match the spec;
- authenticated preview/PDF request IP changes between requests and is never persisted.

Run: `cd backend && uv run pytest tests/test_usage_certificate_routes.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/v1/module_platform/usage_certificate/controller.py backend/app/api/v1/module_platform/__init__.py backend/app/config/setting.py backend/tests/test_usage_certificate_routes.py
git commit -m "feat: 增加软件使用证明查询与公开核验接口"
```

### Task 5: Seed permissions and preserve tenant-owner access

**Files:**
- Modify: `backend/app/scripts/data/platform_menu.json`
- Modify: `backend/app/scripts/data/platform_package_menu.json`
- Modify: `backend/app/api/v1/module_platform/package/service.py`
- Modify: `backend/tests/test_tenant_governance_closure.py`
- Modify: `backend/tests/test_security_foundation.py`

- [ ] **Step 1: Write failing permission closure tests**

Assert that:

- platform menu contains a “软件使用证明” page with `module_platform:usage-certificate:platform-query` and platform scope;
- tenant workspace contains a button permission `module_platform:usage-certificate:tenant-query` with tenant scope;
- owner required permissions include tenant certificate access;
- package-menu seed packs include the tenant certificate permission;
- ordinary tenant member roles are not automatically granted it.

- [ ] **Step 2: Run the focused governance tests**

Run: `cd backend && uv run pytest tests/test_tenant_governance_closure.py tests/test_security_foundation.py -q`

Expected: FAIL on missing permission/menu entries.

- [ ] **Step 3: Add menu and package seed entries**

Add a platform-only registry menu pointing to `module_platform/usage_certificate/index`. Add the tenant certificate permission as a child of the existing tenant workspace/self-service menu, and include that permission in every package menu seed that grants the owner workspace.

- [ ] **Step 4: Preserve tenant-owner permission during package synchronization**

Add `module_platform:usage-certificate:tenant-query` to `OWNER_REQUIRED_MENU_PERMISSIONS`. Keep feature-neutral behavior: the certificate is a generic framework capability, not a product seed.

- [ ] **Step 5: Run governance tests**

Run: `cd backend && uv run pytest tests/test_tenant_governance_closure.py tests/test_security_foundation.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/scripts/data/platform_menu.json backend/app/scripts/data/platform_package_menu.json backend/app/api/v1/module_platform/package/service.py backend/tests/test_tenant_governance_closure.py backend/tests/test_security_foundation.py
git commit -m "feat: 配置软件使用证明菜单权限"
```

### Task 6: Add the typed frontend API and tenant-admin certificate tab

**Files:**
- Create: `frontend/web/src/api/module_platform/usage_certificate.ts`
- Modify: `frontend/web/src/views/module_platform/self_service/index.vue`
- Create: `frontend/web/src/__tests__/usage-certificate.spec.ts`

- [ ] **Step 1: Write failing frontend API and permission-rendering tests**

Mock the request utility and assert exact tenant preview/download URLs. Mount the self-service view with and without `module_platform:usage-certificate:tenant-query`; assert the tab exists only with permission, preview HTML is placed in the iframe `srcdoc`, and PDF download uses the response filename header or the safe fallback.

- [ ] **Step 2: Run the focused Vitest file**

Run: `cd frontend/web && pnpm test -- usage-certificate.spec.ts`

Expected: FAIL because the client and tab do not exist.

- [ ] **Step 3: Implement the typed API client**

Define shared interfaces matching backend snake_case responses. Use `responseType: "blob"` for PDF. Keep certificate calls in their own API module instead of expanding the already broad self-service API.

- [ ] **Step 4: Add the tenant-admin tab**

Use the existing `useAuth`/permission pattern. Add loading, empty, error, refresh, and download states. Display metadata above a sandboxed iframe; do not render backend HTML with `v-html` in the parent document.

- [ ] **Step 5: Run frontend tests and type-check**

Run: `cd frontend/web && pnpm test -- usage-certificate.spec.ts && pnpm type-check`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/web/src/api/module_platform/usage_certificate.ts frontend/web/src/views/module_platform/self_service/index.vue frontend/web/src/__tests__/usage-certificate.spec.ts
git commit -m "feat: 增加租户软件使用证明预览下载"
```

### Task 7: Add the platform registry page

**Files:**
- Create: `frontend/web/src/views/module_platform/usage_certificate/index.vue`
- Modify: `frontend/web/src/api/module_platform/usage_certificate.ts`
- Modify: `frontend/web/src/__tests__/usage-certificate.spec.ts`

- [ ] **Step 1: Add failing registry behavior tests**

Test filters for tenant name, tenant code, certificate number, and validity; pagination request mapping; status tags; preview dialog iframe; and direct PDF download. Assert there are no edit, issue, renew, replace, or revoke controls.

- [ ] **Step 2: Run the frontend test**

Run: `cd frontend/web && pnpm test -- usage-certificate.spec.ts`

Expected: FAIL because the registry page is missing.

- [ ] **Step 3: Implement the dense read-only registry**

Reuse `FaTable`, `FaTableHeader`, `FaDialog`, and Element Plus form controls. Show tenant, certificate number, software/version, authorization dates, current validity, and identity creation time. Keep IP out of the list. Preview HTML stays inside a sandboxed iframe.

- [ ] **Step 4: Run focused tests and type-check**

Run: `cd frontend/web && pnpm test -- usage-certificate.spec.ts && pnpm type-check`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/web/src/views/module_platform/usage_certificate/index.vue frontend/web/src/api/module_platform/usage_certificate.ts frontend/web/src/__tests__/usage-certificate.spec.ts
git commit -m "feat: 增加平台软件使用证明查询页"
```

### Task 8: Add the public verification page and route

**Files:**
- Create: `frontend/web/src/views/public/usage_certificate/index.vue`
- Modify: `frontend/web/src/router/staticRoutes.ts`
- Modify: `frontend/web/src/api/module_platform/usage_certificate.ts`
- Modify: `frontend/web/src/__tests__/usage-certificate.spec.ts`

- [ ] **Step 1: Write failing public route and disclosure tests**

Assert `/certificate/verify/:token` is a static public route outside the authenticated layout, calls the exact public endpoint, renders valid/invalid status and only public fields, omits IP, and renders the same neutral “无法核验此证明” state for 404/error responses.

- [ ] **Step 2: Run the focused frontend test**

Run: `cd frontend/web && pnpm test -- usage-certificate.spec.ts`

Expected: FAIL because the public route/page does not exist.

- [ ] **Step 3: Implement the public page**

Use the A-style modern document language with responsive mobile behavior. Show no app sidebar, authenticated header, action buttons, or download link. Render a clear “当前有效” or “当前无效” banner and the minimum approved fields.

- [ ] **Step 4: Register the public static route**

Add a route with public/white-list metadata consistent with the existing login and error routes. Ensure guards do not redirect unauthenticated visitors to login.

- [ ] **Step 5: Run tests, type-check, and build**

Run: `cd frontend/web && pnpm test -- usage-certificate.spec.ts && pnpm type-check && pnpm build`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/web/src/views/public/usage_certificate/index.vue frontend/web/src/router/staticRoutes.ts frontend/web/src/api/module_platform/usage_certificate.ts frontend/web/src/__tests__/usage-certificate.spec.ts
git commit -m "feat: 增加软件使用证明公开查验页"
```

### Task 9: Run complete verification and close regression gaps

**Files:**
- Modify only files required by failures found in this task.

- [ ] **Step 1: Run backend focused suites**

Run:

```bash
cd backend
uv run pytest \
  tests/test_usage_certificate_identity.py \
  tests/test_usage_certificate_routes.py \
  tests/test_product_migrations.py \
  tests/test_tenant_governance_closure.py \
  tests/test_security_foundation.py \
  tests/test_api_module_platform.py -q
```

Expected: PASS.

- [ ] **Step 2: Run backend quality checks**

Run: `cd backend && uv run ruff check app tests`

Expected: PASS.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && uv run pytest tests -q`

Expected: PASS. If an unrelated pre-existing failure occurs, record exact test names and evidence instead of weakening certificate tests.

- [ ] **Step 4: Run frontend verification**

Run:

```bash
cd frontend/web
pnpm test
pnpm type-check
pnpm build
```

Expected: PASS.

- [ ] **Step 5: Perform an isolated browser acceptance pass**

Against an explicitly isolated local DB/Redis/runtime:

1. log in as a tenant owner and confirm preview plus PDF download;
2. confirm a tenant member lacks the entry and receives 403 by direct API call;
3. log in as platform admin and filter, preview, and download a tenant proof;
4. scan/open the QR URL without authentication;
5. update tenant name, USCC, version, and dates as platform admin, then confirm live proof changes while number/token stay fixed;
6. suspend the tenant and confirm public status becomes invalid;
7. verify authenticated previews show the request IP and public verification does not.

Capture screenshots only in ignored/local evidence paths; do not commit credentials, generated PDFs, or runtime artifacts.

- [ ] **Step 6: Inspect final Git scope**

Run: `git status --short --branch && git diff --check`

Expected: only certificate-related paths remain; `.superpowers/`, `.understand-anything/`, and unrelated existing plans remain untracked/untouched.

- [ ] **Step 7: Commit any final scoped fixes**

```bash
git add <exact certificate-related paths only>
git commit -m "test: 完善软件使用证明验收"
```

Skip this commit when no final fixes were required.
