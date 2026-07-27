# FastapiAdmin Foundation Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development and executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven confirmed tenant, package, permission, lifecycle, clean-build, and container-runtime defects before product plugins are developed.

**Architecture:** Package changes are planned and applied through one tenant-domain service so platform edits and payment activation cannot diverge. Tenant membership governance and RBAC bindings are maintained atomically, while lifecycle transitions leave the generic update endpoint. Frontend type declarations are generated explicitly before type checking, and monitoring degrades safely in restricted containers.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, PostgreSQL, pytest, Vue 3, Vite, TypeScript, Vitest, psutil.

---

### Task 1: Unified package-change planning and application

**Files:**

- Modify: `backend/app/api/v1/module_platform/tenant/service.py`
- Modify: `backend/app/api/v1/module_system/role/service.py`
- Modify: `backend/app/api/v1/module_platform/order/service.py`
- Test: `backend/tests/test_foundation_closure.py`

- [x] Add failing tests proving package preview performs set-safe diffs and includes owner minimum menus.
- [x] Add failing tests proving platform update and paid new/upgrade/downgrade paths use the same package-change function.
- [x] Implement a pure package-change plan and one transactional apply function that sets `package_id`, synchronizes all tenant role menus, and invalidates authorization caches.
- [x] Route platform updates and payment activation through the shared function; retain downgrade quota checks before applying.
- [x] Run focused package, payment, and tenant security tests.

### Task 2: Workspace permissions and membership/RBAC consistency

**Files:**

- Modify: `backend/app/api/v1/module_platform/tenant/controller.py`
- Modify: `backend/app/api/v1/module_platform/self_service/controller.py`
- Modify: `backend/app/api/v1/module_platform/tenant/service.py`
- Modify: `backend/app/scripts/data/platform_menu.json`
- Modify: `backend/app/api/v1/module_platform/package/service.py`
- Test: `backend/tests/test_foundation_closure.py`

- [x] Add failing tests proving an authenticated user without workspace permissions cannot query workspace or update brand configuration.
- [x] Add failing tests proving removing and re-adding a member removes stale owner RBAC bindings and maps owner/admin/member to the intended tenant role.
- [x] Add explicit workspace update permission seed and include it in owner minimum permissions.
- [x] Replace login-only dependencies with query/update permission dependencies.
- [x] Implement one membership mutation helper that synchronizes `TenantUserModel` and `UserRolesModel` in the same transaction, protects the last owner, and clears stale tenant roles.
- [x] Protect owner/admin/member governance roles from generic role mutation and permission replacement.
- [x] Run focused permission and membership tests.

### Task 3: Explicit tenant lifecycle transitions

**Files:**

- Modify: `backend/app/api/v1/module_platform/tenant/schema.py`
- Modify: `backend/app/api/v1/module_platform/tenant/controller.py`
- Modify: `backend/app/api/v1/module_platform/tenant/service.py`
- Modify: `frontend/web/src/api/module_platform/tenant.ts`
- Modify: `frontend/web/src/views/module_platform/tenant/index.vue`
- Test: `backend/tests/test_foundation_closure.py`
- Test: `frontend/web/src/__tests__/tenant-status.spec.ts`

- [x] Add failing tests proving generic tenant update rejects lifecycle status and invalid manual transitions cannot bypass the state service.
- [x] Remove `status` from generic update input and keep manual operations limited to active/suspended.
- [x] Define frozen as access-blocked for the first product baseline and align comments, labels, and login behavior; do not claim unsupported read-only semantics.
- [x] Preserve scheduled active→grace→suspended→frozen→expired transitions and renewal recovery.
- [x] Run backend lifecycle and frontend status tests.

### Task 4: Reproducible frontend declaration generation

**Files:**

- Modify: `frontend/web/package.json`
- Create: `frontend/web/scripts/generate-types.ts`
- Test: clean `git archive` verification

- [x] Reproduce `pnpm type-check` failure from a clean archive without generated declarations.
- [x] Add a deterministic `typegen` script that loads the Vite plugin graph, generates both declaration files, and exits without starting a long-lived server.
- [x] Make `type-check`, `test`, and `build` safe in a clean checkout while avoiding generated-file commits.
- [x] Verify `pnpm install --frozen-lockfile`, `pnpm type-check`, `pnpm test`, and `pnpm build` from a clean archive.

### Task 5: Restricted-container monitoring fallback

**Files:**

- Modify: `backend/app/api/v1/module_monitor/server/service.py`
- Test: `backend/tests/test_foundation_closure.py`

- [x] Add failing tests for `psutil.AccessDenied`, `psutil.NoSuchProcess`, and unavailable executable paths.
- [x] Return a stable fallback for process name/home while preserving available CPU, time, and memory data.
- [x] Run focused monitor tests.

### Task 6: Integration and documentation

**Files:**

- Modify: `docs/superpowers/plans/2026-07-27-foundation-closure.md`
- Modify: framework documentation only if runtime contracts changed

- [x] Run full backend pytest and Ruff.
- [x] Run frontend Vitest, type-check, production build, ESLint, Prettier, and changed-file Stylelint.
- [x] Run a clean-archive frontend pipeline without relying on ignored files from the working copy.
- [x] Review the diff for secrets, unrelated files, stable seed identities, migration safety, and `.understand-anything/` exclusion.
- [x] Mark this plan complete and create one focused Chinese Conventional Commit.

### Deferred architecture decision: Host/Site dual-brand

This plan intentionally does not add `site_id`. After the seven defects pass all gates, decide whether both brands share one deployment and database. Only the shared model requires Host→Site resolution, site-scoped login/package/cache contracts, and replacement of public numeric tenant configuration lookup.
