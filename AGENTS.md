# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Backend (FastAPI) for **Drive Clon**, a multi-tenant Google Drive clone POC. This repo is
also the **orchestration home**: it owns `docker-compose.yml` and the docs. The frontend
lives in a **sibling repo** `../drive-clon-ui` (React + Vite), which the compose file mounts
by relative path — both repos must be cloned as sibling folders under the same parent.

> POC for experimentation, **not production**. `ARCHITECTURE.md` describes the **target
> architecture**; some of it (shares, public links) is documented but not yet implemented.
> Implemented today: auth/session + organization provisioning, and **folders & files**
> (root folder per user, nested folders, drag & drop upload to MinIO) — see
> `docs/PRD/folders-and-files.md` and the `files` route group.

## Commands

All local development runs in Docker. Run `docker compose` from this repo's root.

```bash
docker compose up -d                          # build + start all 5 services (Postgres, MinIO, Keycloak, backend, frontend)
docker compose logs -f backend                # follow backend logs
docker compose down                           # stop (keeps data)
docker compose down -v                        # stop and DELETE volumes (postgres + minio)
docker compose exec backend alembic upgrade head   # apply migrations manually
docker compose exec postgres psql -U postgres -d driveclon   # psql shell
```

- Backend API + Swagger UI: http://localhost:8000/docs
- Keycloak admin: http://localhost:8080 · MinIO console: http://localhost:9001
- **First-run setup** (Keycloak realm/client config) is required — see `README.md` §1.3 and `docs/1_getting-started`.

There is **no test suite** and no linter configured in this repo yet.

### Migrations (Alembic)

Alembic is the **single source of truth** for the DB schema (models are not auto-created).
Migrations are applied **automatically on app startup** (`run_migrations()` in the FastAPI
lifespan) and can also be run manually with the `alembic upgrade head` command above.

```bash
docker compose exec backend alembic revision --autogenerate -m "describe change"
docker compose exec backend alembic upgrade head
```

`script_location` is `app/migrations`; `alembic.ini` lives at the repo root.

### Running outside Docker (optional)

Python 3.11. The `.venv` is git-ignored. `app/core/config.py` loads `.env` from the repo
root, so the backend can run either inside or outside the container. Run:
`uvicorn app.main:app --reload`.

## Architecture

This is a **resource server** — it never issues tokens. Keycloak is the IdP (Google
federated); the backend validates the Bearer JWT against Keycloak's JWKS and uses a
**service account** (`driveclon-backend`, client_credentials) to call Keycloak's Admin API.

### Layered structure (`app/`)

Strict one-directional layering — each layer depends only on the one below:

```
routes/        Presentation: FastAPI routers + Pydantic request/response DTOs. Only orchestrate services.
services/      Business logic. Depend on gateway interfaces + repositories, never on HTTP details.
gateways/      Abstractions (ABC) over external systems (Keycloak Admin API, MinIO object storage). Code depends on the interface.
repositories/  Data access over SQLAlchemy async sessions. One class per aggregate.
models/        SQLAlchemy ORM models (declarative Base from app/db/database.py).
core/          config.py (pydantic-settings) and security.py (JWT/JWKS validation).
db/            Async engine, session factory, and Alembic migration runner.
```

Dependencies are injected by constructor (e.g. `EnsureOrganizationService` takes its
repositories + gateway as args). Gateways expose an ABC (`KeycloakAdminGateway`) with an
HTTP implementation behind it — depend on the interface, not the concrete class.

### Multi-tenancy invariant (the core design rule)

**One organization per user** (personal tenant). Tenant isolation is the backend's
responsibility: every file query and every MinIO object key must be **scoped by `org_id`**.
A JWT from org A must never resolve a resource from org B (`WHERE org_id = ...` and object
keys prefixed `{org_id}/...`). When adding any data access, preserve this — filter by the
caller's `org_id`, resolved from the **DB mirror** (by token `sub`), not from the token claim.

### Auth / org provisioning flow

1. Frontend calls `GET /auth/session` with the Bearer token.
2. `get_current_user` (`core/security.py`) validates RS256 signature against cached JWKS,
   checks issuer, extracts `sub`/`email`/`name`/`picture`. (Audience is intentionally not
   verified — public SPA tokens carry `aud="account"`.)
3. `EnsureOrganizationService.ensure()` is idempotent: if the user already has an org it just
   syncs the profile; otherwise it creates the org in Keycloak via the Admin API, adds the
   user as member, and **mirrors** it into Postgres (`organizations` + `users.org_id`).
4. On first provisioning the response sets header `X-Org-Provisioned: true` (exposed via CORS
   in `main.py`); the frontend reacts by forcing a transparent token refresh so the next token
   carries the org membership.

### Folders & files (`files` route group)

A user's **root folder** (`parent_id = NULL`, name `"My Drive"`) is provisioned by
`EnsureOrganizationService.ensure()` alongside the org — idempotent, guaranteed unique by a
**partial unique index** on `folders(owner_id) WHERE parent_id IS NULL AND deleted_at IS NULL`.
The `files` router (`app/routes/files.py` → `FilesService`) exposes:

- `GET /files/root` — caller's root folder.
- `GET /files?folder_id=<id>` — subfolders + files of a folder (root if omitted).
- `POST /files/folders` — create a folder (`{ name, parent_id }`).
- `POST /files` — multipart upload (`file`, `folder_id`) → stored in MinIO, row in Postgres.
- `GET /files/{file_id}/download` — stream a single file's bytes (Content-Disposition attachment).
- `GET /files/folders/{folder_id}/download` — stream a ZIP of all files in the folder and its subfolders (built in memory, paths preserved).
- `DELETE /files/{file_id}` — soft-delete a single file → moves it to the trash (204). The MinIO object is kept.
- `DELETE /files/folders/{folder_id}` — soft-delete a folder and its whole subtree (subfolders + files) recursively (204). The root folder cannot be deleted (400).

#### Lifecycle status (the deletion model)

Files and folders carry a single **`status`** column (Postgres native enum `resource_status`)
that is the **only discriminator queries filter on** — never a combination of timestamps:

- `active` → visible in My Drive; object exists in MinIO.
- `trashed` → in the trash, recoverable; object still in MinIO.
- `deleted` → permanently purged: the **MinIO object is removed but the DB row is kept** (for
  analytics — uploads/deletions per month, per user, etc.). Rows are **never** physically deleted.

Timestamps `trashed_at` and `deleted_at` are **metadata only** (when it entered the trash / when
it was purged); they are not used to decide visibility. Search is therefore trivial:
`WHERE status = 'active'` (drive), `WHERE status = 'trashed'` (trash). The auto-purge job is the
only place that reads a timestamp (`trashed_at < cutoff`).

#### Trash (papelera)

The trash is **per user** (scoped by `owner_id` + `org_id`) and lists only **top-level** trashed
items — a file whose folder is also trashed, or a subfolder of a trashed folder, is hidden
(it hangs from its parent and is restored/purged with it).

- `GET /files/trash` — caller's trash: top-level trashed folders + files (with `deleted_at`).
- `POST /files/{file_id}/restore` — revive a file. Returns `{ id, folder_id }`; if its folder was permanently deleted it is restored to the **root**.
- `POST /files/folders/{folder_id}/restore` — revive a folder and its whole trashed subtree. Returns `{ id, parent_id }`; restored to the root if its parent no longer exists.
- `DELETE /files/{file_id}/permanent` — purge a trashed file **for good** (DB row + MinIO object) (204).
- `DELETE /files/folders/{folder_id}/permanent` — purge a trashed folder and its subtree for good (204).
- `DELETE /files/trash` — empty the trash: purge everything for good (204).

`/files/trash` is declared **before** `/files/{file_id}` so "trash" is not parsed as an int id.
Auto-purge: a scheduled job (`app/jobs/trash_purge.py`, APScheduler in the lifespan) permanently
purges items trashed more than `TRASH_RETENTION_DAYS` days ago (default 30), every
`TRASH_PURGE_INTERVAL_HOURS` (default 24) across all orgs — removing the MinIO object and setting
`status='deleted'` (the row stays).

Object keys are `"{org_id}/{folder_id}/{uuid}-{name}"`. Every query filters `org_id` **and**
`status`; a folder/file from another tenant resolves as **404** (never leak existence).
`owner.is_me` is computed by comparing `owner_id` to the caller's DB user id.

### Key gotchas

- **Object storage (MinIO).** The `minio` SDK is **synchronous**; `MinioObjectStorageGateway`
  wraps `put_object` in `asyncio.to_thread`. The `driveclon` bucket is created by the
  `minio-init` job (docker-compose). Config: `MINIO_*` env vars (see `app/core/config.py`).
- **Internal vs public Keycloak URL.** Inside Docker the backend talks to `http://keycloak:8080`
  but token `iss` is the browser-facing `http://localhost:8080`. `KEYCLOAK_PUBLIC_URL` is
  validated as the issuer; `KEYCLOAK_URL` is used for server-to-server calls (JWKS, Admin API).
- **Lifecycle status (files & folders).** Use the `status` enum (`active`/`trashed`/`deleted`)
  as the single filter — never combine timestamps. Rows are **never** physically deleted; a
  permanent purge (`/permanent`, empty trash, or the auto-purge job) removes the MinIO object via
  `ObjectStorageGateway.remove_objects` and sets `status='deleted'`, keeping the row for analytics.
- **Soft delete (other models).** `organizations`/`users` still use `deleted_at` (NULL = alive);
  those queries filter `deleted_at IS NULL` (see `UserRepository.find_by_sub`).
- **Async everywhere.** SQLAlchemy uses the asyncpg driver; `Settings.async_database_url`
  rewrites a sync `postgresql://` URL to `postgresql+asyncpg://` automatically.
- **Migrations run in a thread.** `run_migrations()` offloads Alembic to a worker thread
  because Alembic's `env.py` calls `asyncio.run`, which can't nest in the active lifespan loop.
- **Hot-reload needs polling on Docker Desktop.** `WATCHFILES_FORCE_POLLING` (driven by
  `USE_POLLING`, default `true`) — inotify events don't cross the Docker Desktop VM on Win/Mac.

## Conventions

- All config comes from environment variables (`.env` is git-ignored; `.env.example` is the
  template). No secrets in code.
- Code comments and docstrings in this repo are written in **Spanish** — match the existing
  language when editing.
- Commit messages follow Conventional Commits.
