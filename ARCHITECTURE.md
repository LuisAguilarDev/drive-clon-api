# Arquitectura — Drive Clon (POC)

> Documento de la **arquitectura objetivo**. El código actual en `drive-clon-fast-api`
> (auth con Firebase) y `drive-clon-ui` está **deprecado** y se migrará a lo descrito aquí.
> Esto es una Prueba de Concepto (POC) para experimentación, no producción.

## 1. Objetivo

Un clon de Google Drive multi-tenant donde:

- El usuario se loguea (Google federado a través de Keycloak).
- Cada usuario pertenece a una **organización** (tenant). Los usuarios **no pueden ver
  archivos de otras organizaciones** — aislamiento total entre tenants.
- Los archivos se guardan en **MinIO** (almacenamiento de objetos S3-compatible).
- Un archivo puede ser **público** (compartible por URL, accesible sin login) o
  **privado** (solo el dueño / personas con acceso explícito dentro de la organización).

## 2. Componentes

| Componente   | Tecnología                         | Puerto | Rol                                                        |
|--------------|------------------------------------|--------|------------------------------------------------------------|
| Frontend     | React + Vite + Tailwind            | 5173   | SPA. Login vía Keycloak, sube/lista/comparte archivos.     |
| Backend      | FastAPI (Python)                   | 8000   | API REST. Valida JWT, autoriza, orquesta MinIO + Postgres. |
| Identidad    | Keycloak                           | 8080   | IdP OIDC. Federación Google, organizaciones, roles.        |
| Objetos      | MinIO                              | 9000   | Almacena los bytes de los archivos. Console en 9001.       |
| Base de datos| PostgreSQL                         | 5432   | Metadatos: archivos, visibilidad, shares, organizaciones.  |

**Principio clave:** MinIO y Postgres **nunca** se exponen al navegador. El frontend solo
habla con el backend (FastAPI) y con Keycloak. El backend es el único que toca el storage.

## 3. Diagrama

```
                         ┌──────────────────────┐
                         │   Keycloak (8080)    │
                         │  - Realm: driveclon  │
                         │  - Google IdP broker │
                         │  - Organizations     │
                         │  - Roles             │
                         └─────────▲────────────┘
                  login (OIDC)     │  emite JWT (con org + roles)
                         │         │
                  ┌──────┴─────────┴───────┐
                  │   Frontend (Vite/5173) │
                  │   keycloak-js          │
                  └──────────┬─────────────┘
                             │  Bearer JWT
                             ▼
                  ┌────────────────────────┐        ┌──────────────────────┐
                  │   Backend FastAPI      │ ─────► │  PostgreSQL (5432)   │
                  │   - valida JWT (JWKS)  │ meta   │  files, shares, orgs │
                  │   - autoriza por org   │        └──────────────────────┘
                  │   - presigned URLs     │
                  └──────────┬─────────────┘        ┌──────────────────────┐
                             └─────────────────────►│   MinIO (9000)       │
                                  bytes / presign   │  bucket: driveclon   │
                                                    └──────────────────────┘
```

## 4. Autenticación y multi-tenancy

### 4.1 Login (Google a través de Keycloak)

Keycloak actúa como **Identity Provider**. Google se configura como **Identity Provider
brokering** dentro del realm `driveclon`, así el usuario hace "Login with Google" pero el
token final lo emite Keycloak (no Google directo). Ventaja: un solo formato de token,
control central de roles y organizaciones.

```
Usuario → "Login with Google" → Keycloak → Google OAuth → Keycloak emite JWT
```

### 4.2 Organizaciones (aislamiento de tenants)

Se usa la feature **Organizations** de Keycloak (GA desde Keycloak 26). El
frontend es un SPA Keycloak (keycloak-js, Bearer token) y el backend es un
**resource server** (valida el token vía JWKS). Una **organización por usuario**.
Flujo:

1. El usuario se loguea (Google federado en Keycloak). El primer token **no** trae
   organización todavía.
2. El frontend llama a `GET /auth/session` con el Bearer token.
3. El backend valida el token y, si el usuario no tiene organización, usa el
   **service account** (`driveclon-backend`, client_credentials) para llamar a la
   Admin API: crea la `Organization`, añade al usuario como miembro y la espeja en
   Postgres (`organizations` + `users.org_id`).
4. El backend responde con `X-Org-Provisioned: true`; el frontend fuerza un
   **refresh transparente** del token para que el siguiente refleje la membresía.
5. El backend resuelve `org_id` **desde su BD espejo** (por el `sub` del token), no
   del claim — así la primera petición funciona sin esperar el refresh — y
   **filtra TODA consulta por ese `org_id`**.

> El aislamiento entre organizaciones es responsabilidad del backend: cada query a
> Postgres y cada object key en MinIO va **scopeado por `org_id`**. Un JWT de la org A
> nunca puede resolver un recurso de la org B porque el `WHERE org_id = ...` no lo
> devuelve y la key del objeto (`{org_id}/...`) no coincide.

### 4.3 Roles

Roles de Keycloak (realm o client roles) viajan en el JWT:
`token["realm_access"]["roles"]`. Ejemplos POC: `org-admin` (gestiona miembros),
`member` (sube/comparte sus archivos). El backend tiene un guard `require_roles(...)`
en la capa de presentación.

### 4.4 Validación en el backend

- El backend valida la **firma del JWT** contra el **JWKS** de Keycloak
  (`/realms/driveclon/protocol/openid-connect/certs`), cacheado.
- Verifica `issuer` y `audience`.
- Extrae `sub` (user id), `org_id` y `roles`.
- Todo esto vive en una *dependency* de FastAPI (capa de presentación), no en la
  lógica de negocio.

## 5. Almacenamiento de archivos (MinIO)

- **Un bucket** para la POC: `driveclon`.
- **Object key** scopeada por organización: `{org_id}/{file_id}/{filename}`.
- MinIO se mantiene **privado** (sin bucket público). El acceso siempre pasa por el backend.

### Subida
1. Frontend pide al backend una **presigned PUT URL** (`POST /files` con metadatos).
2. Backend crea el registro en Postgres (`status = pending`), genera la presigned URL
   y la devuelve.
3. Frontend sube los bytes **directo a MinIO** con esa URL (no pasan por FastAPI).
4. Frontend confirma (`POST /files/{id}/complete`) → backend marca `status = ready`.

### Descarga
- **Privado:** backend valida JWT + autorización, genera una **presigned GET URL**
  temporal y redirige/responde con ella.
- **Público:** ver sección 6.

## 6. Público vs Privado y compartir por URL

| Estado   | Sin login                                  | Con login (misma org)                       |
|----------|--------------------------------------------|---------------------------------------------|
| `private`| ❌ 401/403 — no existe para él              | ✔ solo dueño o con share explícito          |
| `public` | ✔ accesible vía link de compartir          | ✔                                           |

### Link público

Cuando un archivo se marca `public`, el backend genera un **share slug** opaco y lo
guarda en `file_shares` (o un campo `public_slug` en `files`). El link es:

```
https://app/share/{slug}
```

La ruta `GET /share/{slug}` es **pública** (sin auth):
1. Busca el archivo por slug.
2. Si `visibility = public` → genera una presigned GET URL temporal de MinIO y
   redirige. Si no es público → 404 (no se filtra ni la existencia).

> Se usa un **slug aleatorio** (no el `file_id` secuencial) para que los links no sean
> adivinables/enumerables.

## 7. Modelo de datos (Postgres)

```
organizations
  id            uuid  PK          -- espejo del id de la organización en Keycloak
  keycloak_org  text             -- id/alias en Keycloak
  name          text
  created_at    timestamptz

users
  id            uuid  PK          -- = sub del JWT de Keycloak
  org_id        uuid  FK organizations
  email         text
  name          text
  picture       text

files
  id            uuid  PK
  org_id        uuid  FK organizations   -- aislamiento de tenant
  owner_id      uuid  FK users
  filename      text
  content_type  text
  size_bytes    bigint
  object_key    text                     -- key en MinIO: {org_id}/{file_id}/{filename}
  visibility    text  CHECK (private|public)
  public_slug   text  UNIQUE NULL        -- solo si es público
  status        text  CHECK (pending|ready)
  created_at    timestamptz

file_shares                              -- compartir a usuarios concretos (sección 8)
  id            uuid  PK
  file_id       uuid  FK files
  shared_with   uuid  FK users           -- destinatario (misma org)
  permission    text  CHECK (view|edit)
  created_at    timestamptz
```

Toda query de archivos lleva `WHERE org_id = :jwt_org_id` salvo la ruta pública `/share/{slug}`.

## 8. ¿Qué tan difícil es compartir con otro usuario dentro de la app?

**Poco — es directo**, porque el modelo de organizaciones ya garantiza que ambos usuarios
están en el mismo tenant. Lo que hace falta:

1. **Tabla `file_shares`** (ya en el modelo): `file_id`, `shared_with`, `permission`.
2. **Listar destinatarios:** los miembros de la organización se obtienen de la
   Admin API de Keycloak (o de la tabla `users` sincronizada). El frontend muestra un
   selector "Compartir con…" con los miembros de la org.
3. **Endpoint** `POST /files/{id}/shares { user_id, permission }` — valida que el que
   comparte es el dueño y que el destinatario pertenece a la **misma org**.
4. **Autorización de lectura** pasa a ser:
   `dueño OR existe file_share para mi user_id OR visibility = public`.

Esfuerzo estimado para la POC: **1 tabla + 2 endpoints (crear/revocar share) + ajustar el
guard de autorización + un selector en el UI.** Es de las partes más sencillas del sistema
gracias a que Keycloak ya resuelve identidad y pertenencia a la organización.

**Lo que SÍ sería difícil** (fuera del scope POC): compartir **entre organizaciones
distintas**, porque rompe el invariante de aislamiento (`WHERE org_id`) y obligaría a un
modelo de permisos cross-tenant (invitaciones, ACLs que cruzan tenants). No recomendado
para la POC.

## 9. Endpoints principales (borrador)

| Método | Ruta                       | Auth        | Descripción                              |
|--------|----------------------------|-------------|------------------------------------------|
| POST   | `/orgs`                    | JWT         | Crea la organización del usuario.        |
| GET    | `/files`                   | JWT         | Lista archivos de mi organización.       |
| POST   | `/files`                   | JWT         | Crea metadato + devuelve presigned PUT.  |
| POST   | `/files/{id}/complete`     | JWT         | Marca el archivo como `ready`.           |
| GET    | `/files/{id}`              | JWT         | Presigned GET (privado, con auth).       |
| PATCH  | `/files/{id}/visibility`   | JWT (dueño) | Cambia private/public, genera slug.      |
| POST   | `/files/{id}/shares`       | JWT (dueño) | Comparte a un usuario de la org.         |
| DELETE | `/files/{id}/shares/{uid}` | JWT (dueño) | Revoca un share.                         |
| GET    | `/share/{slug}`            | **público** | Sirve un archivo público por link.       |

## 10. Decisiones y trade-offs (POC)

- **Presigned URLs** en vez de proxiar bytes por FastAPI → MinIO entrega/recibe directo,
  el backend no se vuelve cuello de botella.
- **Un bucket con keys por org** en vez de un bucket por org → más simple para POC;
  migrable a bucket-por-tenant si hace falta aislamiento físico.
- **Google federado vía Keycloak** (no Google directo en el front) → un solo tipo de token
  y roles/organizaciones centralizados.
- **Slug aleatorio** para links públicos → evita enumeración de archivos.
- **Sin CDN, sin antivirus-scan, sin versionado de archivos** → fuera del scope POC.

## 11. Cómo levantar el entorno

Ver [`README.md`](./README.md) para los comandos `docker run` de Keycloak, MinIO y
PostgreSQL, y la configuración de cada servicio.
