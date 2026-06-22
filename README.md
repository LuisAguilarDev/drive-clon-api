> 🚀 **New to the project?** Start with [`GETTING_STARTED.md`](./GETTING_STARTED.md)
> — it gets the whole stack running locally in Docker with a single command
> (Windows & macOS).

# Drive Clon (POC)

Clon de Google Drive multi-tenant. Login con Google federado vía **Keycloak**,
almacenamiento de archivos en **MinIO**, metadatos en **PostgreSQL**, backend en
**FastAPI** y frontend en **React + Vite**.

> ⚠️ POC para experimentación, **no producción**. El código actual está siendo migrado
> a la arquitectura objetivo descrita en [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Estructura

Este repo (**backend**) es además el **home de la orquestación**: contiene el
`docker-compose.yml` y los docs. El frontend vive en un repo **hermano** (`drive-clon-ui`),
que el compose monta vía `../drive-clon-ui`. Layout esperado en disco:

```
<carpeta-padre>/
├── drive-clon-fast-api/   ← ESTE repo: backend + docker-compose.yml + docs
│   ├── docker-compose.yml
│   ├── ARCHITECTURE.md
│   ├── README.md
│   ├── Dockerfile.dev
│   └── app/ ...
└── drive-clon-ui/         ← repo hermano: frontend React + Vite
    └── Dockerfile.dev
```

> ⚠️ El compose **asume que ambos repos están clonados como carpetas hermanas**.
> Clona los dos en el mismo directorio padre antes de `docker compose up`.
> Todos los comandos `docker compose` se ejecutan **desde esta carpeta**
> (`drive-clon-fast-api/`).

## Requisitos

- Docker + Docker Compose (única dependencia obligatoria — **todo** el desarrollo local
  corre en contenedores)
- _(Opcional)_ Node.js 20+ y Python 3.11+ solo si quieres correr backend/frontend fuera
  de Docker.

---

## 0. Arranque rápido — todo en Docker (recomendado)

Todo el stack (Postgres, MinIO, Keycloak, backend y frontend) se levanta con un solo
comando vía [`docker-compose.yml`](./docker-compose.yml). El backend y el frontend corren
con **hot-reload** (el código está montado como volumen):

```bash
docker compose up -d        # construye y levanta los 5 servicios
docker compose logs -f      # seguir logs de todo
docker compose ps           # estado de cada servicio
docker compose down         # parar (conserva los datos)
docker compose down -v      # parar y BORRAR datos (postgres + minio)
```

Tras `up`, disponibles en:

| Servicio        | URL                        |
| --------------- | -------------------------- |
| Frontend (Vite) | http://localhost:5173      |
| Backend (API)   | http://localhost:8000/docs |
| Keycloak admin  | http://localhost:8080      |
| MinIO consola   | http://localhost:9001      |
| MinIO API (S3)  | http://localhost:9000      |
| PostgreSQL      | `localhost:5432`           |

El bucket `driveclon` de MinIO se crea automáticamente (servicio `minio-init`). Solo queda
la **configuración inicial de Keycloak** (ver §1.3) y aplicar migraciones del backend:

```bash
docker compose exec backend alembic upgrade head
```

Conectarse a Postgres desde el host:

```bash
docker compose exec postgres psql -U postgres -d driveclon
```

> El resto del README (§1) documenta los `docker run` individuales por si prefieres
> levantar cada servicio por separado en lugar de Compose.

### Hot-reload (file watching)

El backend (`uvicorn --reload`) y el frontend (Vite) recargan al cambiar el código
montado por volumen. **En Docker Desktop (Windows/Mac)** los contenedores corren dentro de
una VM Linux y los eventos `inotify` del filesystem del host **no cruzan** esa frontera, así
que los watchers no se enteran de los cambios. La solución es **polling** (el watcher revisa
los archivos por intervalo en vez de esperar eventos).

Está controlado por la variable `USE_POLLING` (ver [`.env.example`](./.env.example)):

| Entorno                  | `USE_POLLING` | Motivo                                       |
| ------------------------ | ------------- | -------------------------------------------- |
| Windows (Docker Desktop) | `true` (def.) | inotify no cruza la VM → polling obligatorio |
| Mac (Docker Desktop)     | `true` (def.) | mismo caso que Windows                       |
| Linux nativo             | `false`       | inotify funciona; polling solo malgasta CPU  |

Por defecto está en `true` para que funcione out-of-the-box en Windows/Mac. Si desarrollas
en **Linux nativo**, crea un `.env` con `USE_POLLING=false` para evitar el costo de CPU.
La variable se mapea a `WATCHFILES_FORCE_POLLING` (backend) y `CHOKIDAR_USEPOLLING`
(frontend); el `vite.config.ts` solo activa polling si esa env está presente, así fuera de
Docker (Linux/CI) el watcher nativo se mantiene.

---

## 1. Infraestructura local — contenedores individuales (alternativa a Compose)

Si no usas Compose, puedes lanzar cada contenedor a mano. Los servicios comparten una red
Docker para verse entre sí; créala una vez:

```bash
docker network create driveclon
```

### 1.1 PostgreSQL (puerto 5432)

```bash
docker run -d --name driveclon-postgres --network driveclon \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=driveclon \
  -p 5432:5432 \
  -v driveclon_pgdata:/var/lib/postgresql/data \
  postgres:16
```

- Conexión: `postgresql://postgres:postgres@localhost:5432/driveclon`
- Inspeccionar: `psql -h localhost -U postgres -d driveclon`

### 1.2 MinIO (puerto 9000 API · 9001 consola)

```bash
docker run -d --name driveclon-minio --network driveclon \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  -p 9000:9000 -p 9001:9001 \
  -v driveclon_miniodata:/data \
  quay.io/minio/minio server /data --console-address ":9001"
```

- **API S3:** http://localhost:9000
- **Consola web:** http://localhost:9001 (usuario `minioadmin` / pass `minioadmin`)
- Tras arrancar, crea el bucket `driveclon` desde la consola web, o con el cliente `mc`:

```bash
docker run --rm --network driveclon \
  --entrypoint sh quay.io/minio/mc -c "\
  mc alias set local http://driveclon-minio:9000 minioadmin minioadmin && \
  mc mb --ignore-existing local/driveclon"
```

> El bucket se mantiene **privado**. El acceso a archivos siempre pasa por el backend
> mediante presigned URLs (ver `ARCHITECTURE.md` §5).

### 1.3 Keycloak (puerto 8080)

Versión mínima que pediste:

```bash
docker run quay.io/keycloak/keycloak start-dev
```

Versión completa recomendada para la POC (admin, organizaciones y puerto fijo):

```bash
docker run -d --name driveclon-keycloak --network driveclon \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -p 8080:8080 \
  quay.io/keycloak/keycloak:26.0 start-dev --features=organization
```

- **Consola admin:** http://localhost:8080 (usuario `admin` / pass `admin`)
- `start-dev` usa una base H2 en memoria — perfecto para POC, **no persiste** entre
  recreaciones del contenedor.
- `--features=organization` habilita la feature de **Organizations** (multi-tenancy).

#### Configuración inicial de Keycloak (automática)

El job **`keycloak-init`** del Compose (`keycloak/init-keycloak.sh`, mismo patrón
que `minio-init`) crea de forma **idempotente** el realm, el client y el IdP de
Google en cada arranque. No hay que tocar la consola admin para lo básico.

1. **Realm** `driveclon` y **client público** `driveclon-ui` (SPA, Authorization
   Code + PKCE, redirect `http://localhost:5173/*`): se crean solos.
2. **Client confidencial** `driveclon-backend` (service account) con los roles
   `manage-organizations`/`manage-users` de `realm-management`: lo usa el backend
   para crear organizaciones vía Admin API. El secreto se controla con
   `KC_BACKEND_CLIENT_SECRET` (default local: `driveclon-backend-secret`).
3. **Google como Identity Provider**: el job lo configura **solo si** defines en
   tu `.env` (no commiteado) las credenciales de Google Cloud:

   ```env
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ```

   Y en Google Cloud Console registra el redirect URI:
   `http://localhost:8080/realms/driveclon/broker/google/endpoint`

   Tras editar `.env`, re-aplica la config:

   ```bash
   docker compose up keycloak-init --no-deps
   ```

> El frontend salta directo a Google (`kc_idp_hint=google`), así que el usuario
> nunca ve la página de login de Keycloak.

#### Organización por usuario (automática, vía backend)

La feature **Organizations** requiere dos cosas, ambas ya automatizadas: el flag
global `--features=organization` (ver el `command` de Keycloak en el Compose) y
`organizationsEnabled=true` **en el realm** (lo activa `keycloak-init`; sin esto
la Admin API de organizaciones responde 404). La organización **no la crea
Keycloak al registrarse** — la crea el **backend** en la primera petición
autenticada:

1. El frontend (tras el login) llama a `GET /auth/session` con el Bearer token.
2. El backend valida el token (JWKS) y, si el usuario aún no tiene organización,
   usa el service account (`driveclon-backend`) para **crear su organización** y
   **añadirlo como miembro** vía Admin API, y la espeja en Postgres (una org por
   usuario, tenant personal).
3. El backend responde con la cabecera `X-Org-Provisioned: true`; el frontend
   fuerza un **refresh transparente** del token (`keycloak.updateToken`) para que
   el siguiente token refleje la membresía. El usuario no nota nada.

> El backend resuelve el `org_id` desde su BD espejo, así que la primera petición
> nunca falla aunque el token todavía no traiga el claim `organization`.

---

## 2. Backend (FastAPI) — fuera de Docker (opcional)

Con Compose el backend ya corre en su contenedor. Esta sección es solo si prefieres
ejecutarlo en tu host (desde esta carpeta):

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# variables de entorno (.env) — ver plantilla abajo
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Migraciones de base de datos (Alembic)

El esquema lo gestiona **Alembic** (async). El backend aplica las migraciones
pendientes **al arrancar** (`alembic upgrade head` en el `lifespan`), así que en
condiciones normales no hay que ejecutar nada a mano.

Cuando cambies un modelo (en `app/models/`), genera una nueva migración con
autogenerate (dentro del contenedor, que tiene alembic y la BD a mano):

```bash
docker compose run --rm backend alembic revision --autogenerate -m "describe el cambio"
docker compose restart backend     # el lifespan aplica la nueva migración
```

El archivo generado aparece en `app/migrations/versions/` (revísalo antes de
commitearlo). Otros comandos útiles:

```bash
docker compose run --rm backend alembic current      # revisión aplicada
docker compose run --rm backend alembic history       # historial
docker compose run --rm backend alembic downgrade -1  # revertir una migración
```

> Fuera de Docker, los mismos comandos funcionan con `alembic ...` directo (la
> URL la inyecta `env.py` desde `settings`, no desde el `.ini`).

Plantilla `.env` (objetivo). **Nota:** dentro de Compose los hosts son los nombres de
servicio (`postgres`, `minio`, `keycloak`); fuera de Docker son `localhost`:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/driveclon
ALLOWED_ORIGINS=http://localhost:5173

# Keycloak — validación de tokens (resource server)
KEYCLOAK_URL=http://localhost:8080          # URL interna (JWKS, token, admin API)
KEYCLOAK_PUBLIC_URL=http://localhost:8080   # URL pública (issuer del token); en
                                            # Docker el backend usa http://keycloak:8080
                                            # como KEYCLOAK_URL y localhost como pública
KEYCLOAK_REALM=driveclon
KEYCLOAK_CLIENT_ID=driveclon-ui

# Keycloak — Admin API (service account del backend)
KEYCLOAK_ADMIN_CLIENT_ID=driveclon-backend
KEYCLOAK_ADMIN_CLIENT_SECRET=driveclon-backend-secret

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=driveclon
MINIO_SECURE=false
```

> Ningún secreto se hardcodea: todo va por `.env` (no commiteado) o secret manager.

API docs (Swagger): http://localhost:8000/docs

## 3. Frontend (React + Vite) — fuera de Docker (opcional)

Con Compose el frontend ya corre en su contenedor. Para ejecutarlo en tu host:

```bash
cd ../drive-clon-ui     # repo hermano
corepack enable          # activa la versión de pnpm fijada en packageManager
pnpm install
cp .env.example .env     # ajusta las variables de Keycloak/API
pnpm dev                 # http://localhost:5173
```

---

## 4. Orden de arranque

**Con Compose (recomendado):** `docker compose up -d` lo arranca todo en el orden correcto
gracias a los `depends_on` y healthchecks. Después: configurar Keycloak (§1.3) y
`docker compose exec backend alembic upgrade head`.

**Contenedores individuales (alternativa):**

```
1. docker network create driveclon
2. PostgreSQL  → 5432
3. MinIO       → 9000 / 9001   (crear bucket driveclon)
4. Keycloak    → 8080          (crear realm, client, Google IdP, organizations)
5. Backend     → 8000          (alembic upgrade head + uvicorn)
6. Frontend    → 5173
```

## 5. Documentación

- **Arquitectura completa, flujos de auth, modelo de datos y endpoints:**
  [`ARCHITECTURE.md`](./ARCHITECTURE.md)

#launch app
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
#update dependencies
pip freeze > requirements.txt
#watch db locally
#psql -h localhost -U postgres -d DriveClon

#update db
alembic revision --autogenerate -m "initial migration"
alembic upgrade head
