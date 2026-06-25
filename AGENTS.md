# Repository Guidelines

## Project Structure & Module Organization

This repository is split into a FastAPI backend and a Vue 3 admin frontend.

- `backend/app/`: application code, grouped by API modules, plugins, config, core utilities, and scripts.
- `backend/tests/`: pytest API, security, plugin, and regression tests.
- `backend/app/scripts/data/`: seed data for menus, packages, tenants, plugins, and sample packs.
- `frontend/web/src/`: Vue 3 source code, including `api/`, `views/`, `components/`, `store/`, `router/`, `directives/`, and tests.
- `docs/`: framework and product documentation. Generated uploads and product drafts are ignored by default.

## Build, Test, and Development Commands

Backend commands run from `backend/`:

```bash
uv sync
uv run python main.py run --env=dev
uv run pytest tests -q
uv run ruff check app tests
```

Frontend commands run from `frontend/web/`:

```bash
pnpm install
pnpm dev
pnpm type-check
pnpm test
pnpm build
```

Use local PostgreSQL and Redis for non-Docker development. Confirm `.env` / settings before changing connection details.

## Coding Style & Naming Conventions

Python uses Ruff with 4-space indentation, double quotes, import sorting, and a 200-character line limit. Follow existing service/CRUD/controller/schema patterns.

Frontend code uses TypeScript, Vue SFCs, ESLint, Prettier, and Stylelint. Prefer existing `fa-*` components and Element Plus patterns before adding new primitives. Avoid importing heavy libraries in router, store, or app entry files.

Permission codes should follow module namespaces, for example `module_platform:tenant:update`.

## Testing Guidelines

Use pytest for backend tests and Vitest for frontend tests. Name backend tests as `test_*.py`; place frontend specs under `src/__tests__/` or near the changed feature.

For shared foundation changes, cover tenant isolation, permission denial, menu/route consistency, and API success paths. Run focused tests first, then broader checks.

## Commit & Pull Request Guidelines

History uses Conventional Commit prefixes such as `feat:`, `fix:`, and `merge:` with concise Chinese summaries. Commit by issue or domain group, not by unrelated file batches.

PRs should include a clear summary, affected modules, verification commands, screenshots for UI changes, and notes on migrations, seed data, permissions, or tenant behavior. Do not include generated uploads, local logs, or product draft files unless explicitly required.

## Security & Configuration Tips

Keep secrets out of the repo. Never return plaintext AI or SMTP keys in API responses. Public endpoints must be deliberate and must not leak tenant-private data.
