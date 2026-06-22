# Getting Started (new developers)

Goal: from a fresh machine to the app running at **http://localhost:5173** as
fast as possible. The **entire stack** (frontend, backend, database, storage,
auth) runs in Docker with a single command — you do **not** need Node, Python,
pnpm or Postgres installed locally.

> The only hard requirement is **Docker Desktop**. Everything else runs inside
> containers.

---

## 1. Install the prerequisites

| Tool | Windows | macOS | Why |
| --- | --- | --- | --- |
| **Docker Desktop** | [Download](https://www.docker.com/products/docker-desktop/) — install, reboot, launch it, wait for "Engine running" | [Download](https://www.docker.com/products/docker-desktop/) (pick **Apple Silicon** or **Intel** build), open the app | Runs the whole stack |
| **Git** | [git-scm.com](https://git-scm.com/download/win) | `xcode-select --install` or [git-scm.com](https://git-scm.com/download/mac) | Clone the repos |
| **DBeaver** _(optional)_ | [dbeaver.io](https://dbeaver.io/download/) Community Edition | same | Inspect the Postgres database with a GUI |

Verify Docker is ready (any terminal — PowerShell on Windows, Terminal on macOS):

```bash
docker --version
docker compose version
```

Both must print a version. If `docker` is "not recognized", Docker Desktop isn't
running or isn't on your PATH — open the Docker Desktop app first.

> **Windows note:** Docker Desktop needs the **WSL 2** backend (the installer
> sets this up and may ask you to install a kernel update + reboot). Just follow
> its prompts. No manual WSL config is required for this project.

---

## 2. Clone both repos as sibling folders

The compose file lives in the **backend** repo and mounts this UI repo from
`../drive-clon-ui`. They **must** sit next to each other in the same parent
folder, with these exact folder names:

```
<your-parent-folder>/
├── drive-clon-fast-api/   ← backend + docker-compose.yml (you run commands here)
└── drive-clon-ui/         ← this repo (frontend)
```

```bash
# pick any parent folder, then:
git clone <backend-repo-url> drive-clon-fast-api
git clone <frontend-repo-url> drive-clon-ui
```

> If you already cloned this UI repo, just make sure `drive-clon-fast-api/` is
> cloned **right next to it** (same parent), not inside it.

---

## 3. Launch everything

All `docker compose` commands run from the **backend** folder (that's where the
compose file is):

```bash
cd drive-clon-fast-api
docker compose up -d        # builds images + starts all services (first run: a few minutes)
```

First run downloads images and builds the frontend/backend, so it takes a while.
Subsequent runs start in seconds.

Watch it come up:

```bash
docker compose ps           # status of every service
docker compose logs -f      # follow logs (Ctrl+C just stops following, not the app)
```

### Apply database migrations / schema (one time after first `up`)

```bash
docker compose exec backend alembic upgrade head
```

> If migrations error out, the backend also materializes tables on startup; a
> clean reset is `docker compose down -v` (see Troubleshooting). Ask the team
> which is current if unsure.

That's it — open **http://localhost:5173**.

---

## 4. What's running & where

| Service | URL | Credentials |
| --- | --- | --- |
| **Frontend (this app)** | http://localhost:5173 | sign in via Google/Keycloak |
| **Backend API (Swagger)** | http://localhost:8000/docs | — |
| **Keycloak admin** | http://localhost:8080 | `admin` / `admin` |
| **MinIO console** (file storage) | http://localhost:9001 | `minioadmin` / `minioadmin` |
| MinIO S3 API | http://localhost:9000 | — |
| **PostgreSQL** | `localhost:5432` | `postgres` / `postgres`, db `driveclon` |

The Keycloak realm (`driveclon`), the UI client, and the MinIO `driveclon`
bucket are all created **automatically** on startup — no manual console setup
needed for the basics.

---

## 5. Connecting to the database with DBeaver (Windows)

Postgres runs in a container but its port is published to your host, so DBeaver
connects to it like a normal local database.

1. Open DBeaver → **Database** → **New Database Connection** → **PostgreSQL**.
2. Fill in:

   | Field | Value |
   | --- | --- |
   | Host | `localhost` |
   | Port | `5432` |
   | Database | `driveclon` |
   | Username | `postgres` |
   | Password | `postgres` |

3. Check **Save password**, click **Test Connection** (DBeaver will offer to
   download the Postgres driver the first time — accept), then **Finish**.

> The same settings work on macOS in DBeaver, or you can use the terminal:
> ```bash
> docker compose exec postgres psql -U postgres -d driveclon
> ```

---

## 6. Google sign-in (optional)

The app federates Google login through Keycloak. Basic local dev works without
it, but to enable the real Google button you need Google Cloud credentials set
in the **backend** repo's `.env` (this is configured on the backend side, not
here):

```env
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

In Google Cloud Console add this redirect URI:
`http://localhost:8080/realms/driveclon/broker/google/endpoint`

Then re-apply the Keycloak config:

```bash
docker compose up keycloak-init --no-deps
```

> Ask a teammate for shared dev credentials rather than creating your own.
> Never commit `.env` — it's gitignored.

---

## 7. Daily workflow

```bash
docker compose up -d         # start your day
docker compose logs -f       # watch logs
docker compose stop          # stop containers, keep data
docker compose down          # stop + remove containers, KEEP data (volumes)
docker compose down -v       # stop + DELETE all data (fresh Postgres + MinIO)
```

**Hot-reload is on.** This UI repo is mounted into the frontend container, so
saving a file in `src/` reloads the browser automatically — edit code on your
host with your normal editor, no rebuild needed. The backend reloads the same
way.

> **Why polling is on (Windows/Mac):** Docker Desktop runs containers in a Linux
> VM, and host filesystem events don't cross into it, so the watchers poll for
> changes instead. This is the default and works out of the box. On **native
> Linux** only, set `USE_POLLING=false` in the backend `.env` to save CPU.

---

## 8. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `docker: command not found` / "not recognized" | Docker Desktop isn't running. Open the app, wait for "Engine running". |
| Port already in use (5173/8000/8080/9000/5432) | Another app is using it. Stop it, or stop the conflicting container. Find it with `docker ps`. |
| Frontend won't load / blank page | Check `docker compose logs -f frontend`. Make sure both repos are sibling folders with the exact names. |
| Login / Keycloak errors | Keycloak uses in-memory storage and resets on recreate. Re-run `docker compose up keycloak-init --no-deps`. |
| Code changes don't reload | Confirm you edited inside `drive-clon-ui/src`. As a fallback restart: `docker compose restart frontend`. |
| Database is in a weird state | `docker compose down -v` wipes Postgres + MinIO for a clean slate, then `up -d` and re-run migrations. |
| "Cannot connect" in DBeaver | The Postgres container must be running (`docker compose ps`). Host is `localhost`, not the container name. |

Full backend reset:

```bash
docker compose down -v
docker compose up -d
docker compose exec backend alembic upgrade head
```

---

## Running the frontend outside Docker (optional)

You normally don't need this — the container already runs the dev server with
hot-reload. But if you want to run Vite directly on your host:

```bash
corepack enable          # activates the pinned pnpm version
pnpm install
cp .env.example .env     # set VITE_* endpoints (API + Keycloak URLs)
pnpm dev                 # http://localhost:5173
```

Requires Node.js 20+ (see `.nvmrc`) and pnpm 10. The backend stack still has to
be up in Docker for the app to function.

---

See [`README.md`](./README.md) for the tech stack and project scripts, and the
backend repo's `README.md` / `ARCHITECTURE.md` for the full architecture, auth
flows, and data model.
