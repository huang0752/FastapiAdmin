# Multi-Tenant Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development and executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the confirmed tenant-isolation, tenant-owner initialization, package-menu, lifecycle, background-task, and frontend tenant-switching defects while preserving the framework's multi-tenant capability.

**Architecture:** Keep the existing tenant-context plus package-entitlement plus role-permission model, but make tenant ownership initialization a single idempotent backend workflow. Resolve package seed menus through stable permission/route identities rather than numeric primary keys, enforce tenant scope at service and authorization boundaries, and fail closed whenever tenant identity is absent or ambiguous. Frontend request state must be scoped by API, tenant, and session.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, PostgreSQL, Redis, pytest, Vue 3, TypeScript, Pinia, Vitest.

---

### Task 1: Tenant owner and package-menu lifecycle

**Files:**
- Modify: `backend/app/api/v1/module_platform/package/service.py`
- Modify: `backend/app/api/v1/module_platform/tenant/service.py`
- Modify: `backend/app/api/v1/module_system/auth/service.py`
- Modify: `backend/app/scripts/initialize.py`
- Modify: `backend/app/scripts/data/platform_package_menu.json`
- Test: `backend/tests/test_security_foundation.py`

- [x] Add failing tests proving both tenant-creation paths create and bind one owner role with usable organization-management and self-service permissions.
- [x] Add failing tests proving package seeds resolve stable menu identities, reject platform-scope menus, and include parent directories.
- [x] Implement one idempotent owner/bootstrap helper used by platform creation, self-service registration, and historical backfill.
- [x] Replace numeric menu seed assumptions with stable permission/route identities resolved to runtime IDs.
- [x] Synchronize owner additions on package expansion, remove invalid role permissions on contraction (including empty packages), and invalidate package-menu caches.
- [x] Correct tenant-expiry transitions so transitional states continue to be scanned and access policy matches the declared state.
- [x] Run focused tenant/package/security tests.

### Task 2: Backend isolation and execution boundaries

**Files:**
- Modify: `backend/app/core/base_crud.py`
- Modify: `backend/app/core/base_model.py`
- Modify: `backend/app/api/v1/module_system/auth/service.py`
- Modify: `backend/app/api/v1/module_system/user/service.py`
- Modify: `backend/app/plugin/module_task/workflow/nodes/controller.py`
- Modify: `backend/app/plugin/module_task/workflow/nodes/schema.py`
- Modify: `backend/app/core/ap_scheduler.py`
- Modify: `backend/app/core/middlewares.py`
- Test: `backend/tests/test_security_foundation.py`
- Test: `backend/tests/test_multitenant_hardening.py`

- [x] Add failing tests for duplicate cross-tenant login/password-reset ambiguity.
- [x] Add failing tests proving non-superusers cannot create, update, or batch-move rows through `tenant_id`.
- [x] Add failing tests proving tenant users cannot submit executable workflow Python and task logs retain tenant context.
- [x] Add failing tests for exact versus explicit-prefix whitelist behavior.
- [x] Implement fail-closed account lookup, immutable tenant ownership, and explicit tenant context without tenant-1 fallback.
- [x] Replace tenant-submitted workflow code execution with a server-controlled handler allowlist or make custom-code management platform-superadmin-only with no tenant execution path.
- [x] Namespace scheduler identities and persist tenant ID in jobs/logs.
- [x] Split exact and prefix whitelist matching.
- [x] Run focused security, auth, workflow, and scheduler tests.

### Task 3: Frontend tenant/session isolation

**Files:**
- Modify: `frontend/web/src/hooks/core/useTable.ts`
- Modify: `frontend/web/src/components/layouts/fa-header-bar/widgets/FaTenantSwitcher.vue`
- Test: `frontend/web/src/__tests__/useTable.spec.ts`
- Test: `frontend/web/src/__tests__/tenant-switcher.spec.ts`

- [x] Add failing tests proving request deduplication keys differ by API identity, tenant, and token/session.
- [x] Add failing tests proving tenant switching clears tenant-sensitive routes, tabs, permissions, and request cache before activating the new session.
- [x] Implement tenant/session-scoped dedupe keys.
- [x] Implement an atomic tenant-switch reset/reload sequence with controls disabled while switching.
- [x] Run focused Vitest tests, type-check, ESLint, and formatting checks.

### Task 4: Integration, data repair, and regression closure

**Files:**
- Create or modify: `backend/app/scripts/repair_tenant_permissions.py`
- Modify: `backend/tests/test_security_foundation.py`
- Modify: `docs/superpowers/plans/2026-07-27-multitenant-hardening.md`

- [x] Add a dry-run-first, idempotent repair command for existing package/menu and owner-role drift.
- [x] Run the repair command in dry-run mode against the configured development PostgreSQL database and inspect the proposed changes without exposing credentials.
- [x] Run focused backend and frontend suites, then full backend pytest, Ruff, frontend tests, type-check, and build.
- [x] Review the complete diff for unrelated files, secrets, generated artifacts, and migration/seed safety.
- [x] Mark plan items complete and create one focused Chinese Conventional Commit.

### Task 5: Tenant-scoped organization and superadmin impersonation

**Files:**
- Modify: `backend/app/core/base_schema.py`
- Modify: `backend/app/core/dependencies.py`
- Modify: `backend/app/core/base_crud.py`
- Modify: `backend/app/api/v1/module_system/user/service.py`
- Test: `backend/tests/test_tenant_context_scoping.py`

- [x] Add failing tests proving a superadmin in platform tenant 1 has global mode while the same user after selecting tenant 2 has tenant-scoped data/menu mode.
- [x] Add failing tests proving roles, positions, and department context from another tenant are removed after tenant switching.
- [x] Add an explicit `is_platform_global` authorization property derived from superadmin identity plus system-tenant context.
- [x] Apply tenant filtering to superadmin impersonation while retaining platform-global behavior in tenant 1.
- [x] Filter department and positions by the active tenant and fail closed when no tenant-specific organization assignment exists.
- [x] Run focused context-switching and security tests.

### Task 6: PostgreSQL defense-in-depth documentation

**Files:**
- Create: `backend/docs/postgresql-tenant-rls.sql`
- Modify: `backend/docs/README.md` if present, otherwise `docs/README.md`

- [x] Provide an opt-in PostgreSQL RLS policy template using a transaction-local tenant setting, without enabling it automatically for unsupported tables.
- [x] Document the required request/worker transaction hook, migration rollout order, bypass role, connection-pool reset behavior, and verification queries.
- [x] Verify the SQL parses in a rollback-only PostgreSQL transaction when a compatible local database is available; otherwise report the limitation explicitly.
