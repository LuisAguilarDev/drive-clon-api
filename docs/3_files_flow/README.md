# Flujo de ficheros

Documenta cómo viaja un fichero por el sistema. Las dos partes con más miga, y
las que más fácil se olvidan, son: la **subida con URL prefirmada** (el binario
no pasa por el backend) y la **descarga de una carpeta en ZIP** (asíncrona,
patrón petición / worker).

- Diagramas de flujo (fuente Graphviz): [`upload.gv`](./upload.gv) (subida) ·
  [`graph.gv`](./graph.gv) (descarga ZIP)
- Código: [`app/api/v1/files.py`](../../app/api/v1/files.py) ·
  [`app/services/files_service.py`](../../app/services/files_service.py) ·
  [`app/services/archive_service.py`](../../app/services/archive_service.py) ·
  [`app/jobs/archive_worker.py`](../../app/jobs/archive_worker.py) ·
  [`app/gateways/object_storage_gateway.py`](../../app/gateways/object_storage_gateway.py)

Para renderizar los diagramas necesitas [Graphviz](https://graphviz.org/download/):

```bash
# desde la raíz del repo (drive-clon-fast-api/)
dot -Tsvg docs/3_files_flow/upload.gv -o docs/3_files_flow/upload.svg
dot -Tsvg docs/3_files_flow/graph.gv  -o docs/3_files_flow/graph.svg
```

> Todos los endpoints van bajo el prefijo `/api/v1` (se omite aquí por brevedad)
> y exigen Bearer token. Toda operación se acota al `org_id` del llamante: un
> recurso de otro tenant se resuelve como **404** (no se filtra existencia).

---

## 1. Endpoints del grupo `files`

| Método | Ruta | Síncrono | Qué hace |
|--------|------|----------|----------|
| GET  | `/files/root` | sí | Carpeta raíz del usuario. |
| GET  | `/files?folder_id=<id>` | sí | Subcarpetas + ficheros (raíz si se omite). |
| POST | `/files/folders` | sí | Crea una carpeta. |
| **POST** | **`/files`** | **no (init)** | **Inicia subida prefirmada: metadatos → `{ file_id, upload_url }`. El binario lo sube el cliente DIRECTO al almacenamiento.** |
| **POST** | **`/files/{file_id}/confirm`** | **sí** | **Confirma la subida: verifica el objeto y activa el fichero (`pending → active`).** |
| GET  | `/files/{file_id}/download` | sí | Descarga el binario de un fichero. |
| **POST** | **`/files/folders/{folder_id}/archive`** | **no (encola)** | **Encola el ZIP de la carpeta + subárbol. Devuelve `202 { job_id }`.** |
| **GET** | **`/files/archives/{job_id}`** | **no (poll)** | **Estado del job; cuando está listo, trae `download_url` prefirmada.** |
| DELETE | `/files/{file_id}` | sí | A la papelera (soft delete). |
| DELETE | `/files/folders/{folder_id}` | sí | A la papelera el subárbol entero. |
| … | (papelera: `/trash`, `/restore`, `/permanent`) | sí | Ver `AGENTS.md` § Papelera. |

> ⚠️ **Cambio incompatible.** El antiguo `GET /files/folders/{folder_id}/download`
> (ZIP construido en memoria, síncrono) **ya no existe**: se sustituyó por los dos
> endpoints en negrita. El frontend debe migrarse (ver [§6](#6-pendiente-en-drive-clon-ui)).

---

## 2. Subida de fichero — URL prefirmada

El binario **no pasa por el backend**: éste sólo entrega una URL prefirmada y
verifica el resultado. Tres pasos (números = [`upload.gv`](./upload.gv)):

1. `POST /files` con **metadatos** (`filename`, `content_type`, `size_bytes`,
   `folder_id`). La API valida tamaño (`MAX_UPLOAD_SIZE_BYTES`) y tenant.
2. Inserta la fila en `files` con `status='pending'` (invisible en toda vista).
3. Firma una URL de **subida** (`presign_put`, endpoint público) y responde
   `201 { file_id, upload_url }`.
4. (respuesta al cliente)
5. El navegador hace **`PUT` del binario DIRECTO al almacenamiento** con esa URL.
   No se le adjunta el Bearer (rompería la firma SigV4).
6. `POST /files/{file_id}/confirm`.
7. La API hace `stat` del objeto: comprueba que llegó y su **tamaño real**.
8. Activa la fila (`pending → active`) con el tamaño real (revalida el máximo).
9. Responde el `FileResponse` ya activo.

**Por qué así:** memoria del backend plana (nunca ve los bytes), el ancho de
banda lo asume el almacenamiento, y hay límite de tamaño en el boundary. Las
subidas que se inician y nunca se confirman quedan `pending` y las limpia el job
`pending-uploads-cleanup` (`PENDING_UPLOAD_TIMEOUT_HOURS`) — borra fila + objeto
huérfano. Es el **único** sitio donde se borra físicamente una fila (una subida
abandonada no es contenido real ni dato de analítica).

> `presign_put` firma sólo método+clave+expiración (no el `Content-Type`), así
> que el navegador puede subir con cualquier tipo sin desajustar la firma.

---

## 3. Descarga de carpeta en ZIP — flujo asíncrono

El ZIP **no** se construye dentro de la petición HTTP. Se separa la **petición**
del **trabajo**: la petición encola un job y responde en milisegundos; un worker
aparte arma el ZIP y el navegador lo descarga **directo** del almacenamiento por
URL prefirmada. Los números coinciden con [`graph.gv`](./graph.gv).

**Petición (síncrono)**
1. `POST /files/folders/{id}/archive`. La API valida tenant + carpeta.
2. Inserta una fila en `download_jobs` (`status='queued'`) y emite
   `NOTIFY download_jobs_new`.
3. Responde `202 { job_id, status: "queued" }`. Fin de la petición.

**Worker (asíncrono)**
4. El worker despierta por el `NOTIFY` (o por sondeo) y reclama el siguiente job
   con `claim_next`: `UPDATE … WHERE id = (SELECT id … WHERE status='queued'
   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)` → lo pone `processing`.
5. Lee cada fichero origen **en streaming por trozos**
   (`open_object_stream`).
6. Arma el ZIP en un **fichero temporal en disco** (memoria acotada: nunca el
   archivo entero —ni un fichero entero— en RAM) y lo sube por **multipart**
   (`upload_file`) a `_archives/{org_id}/{job_id}.zip`.
7. Marca el job `ready` con `object_key` y `size_bytes`.

**Poll + descarga directa**
8. El navegador hace poll a `GET /files/archives/{job_id}`.
9. La API busca el job acotado a `org_id`.
10. Si está `ready` y no caducó, firma una URL de descarga de corta duración
    (`presign_get`, con el **endpoint público** del almacenamiento).
11. Responde `{ status: "ready", download_url, name, size_bytes }`.
12. El navegador descarga el ZIP **directo del almacenamiento** con esa URL: el
    ancho de banda no pasa por el backend.

---

## 4. Por qué este diseño (decisiones)

- **Petición / worker separados.** La petición no se bloquea armando el ZIP; el
  worker es un proceso aparte, **escalable por su cuenta**
  (`docker compose up --scale worker=N`).
- **Postgres como cola, sin broker.** `download_jobs` es a la vez cola y registro
  durable. `FOR UPDATE SKIP LOCKED` permite que N workers reclamen en paralelo
  sin pisarse. El **índice parcial** `ix_download_jobs_queued`
  (`WHERE status='queued'`) mantiene barata la reclamación aunque se acumulen
  filas terminadas. (No hace falta Redis/Celery a esta escala; sería sobreingeniería.)
- **Despertar al instante.** `LISTEN/NOTIFY` (canal `download_jobs_new`) despierta
  al worker al encolar; el sondeo (`ARCHIVE_POLL_INTERVAL_SECONDS`) es la red de
  seguridad (heartbeat).
- **Recuperación ante caídas (visibility timeout).** La reclamación commitea
  `processing` **antes** del trabajo pesado (no retiene el lock de fila mientras
  comprime). Un job atascado en `processing` más de
  `ARCHIVE_STALE_TIMEOUT_MINUTES` lo devuelve a la cola el *reaper* del worker.
- **Memoria acotada.** Streaming de lectura por trozos + ensamblado a disco +
  subida multipart: el uso de memoria es plano sea cual sea el tamaño del ZIP.
- **El ancho de banda lo asume el almacenamiento.** La descarga va por URL
  prefirmada directa, no a través del backend.
- **Caducidad nativa del almacenamiento.** Una **regla de ciclo de vida** del
  bucket sobre el prefijo `_archives/` (la pone `minio-init`, `--expire-days 1`)
  borra los ZIP temporales: no hay job de limpieza que mantener. El job también
  guarda `expires_at` y, si se consulta pasado ese punto, se marca `expired`.

---

## 5. Estados del job (`download_jobs.status`)

| Estado | Significado |
|--------|-------------|
| `queued` | En la cola, esperando worker. |
| `processing` | Un worker lo reclamó y está armando el ZIP. |
| `ready` | ZIP disponible; se sirve por URL prefirmada. |
| `failed` | El armado falló; el motivo queda en `error`. |
| `expired` | El ZIP temporal ya caducó (no hay descarga). |

Enum: [`app/models/download_job_status.py`](../../app/models/download_job_status.py).
Migración de la tabla: revisión `3e4f5a6b7c8d` en
[`app/migrations/versions/`](../../app/migrations/versions).

---

## 6. Configuración

Variables en [`app/core/config.py`](../../app/core/config.py) (todas con valor por
defecto; se sobreescriben por entorno):

| Variable | Por defecto | Para qué |
|----------|-------------|----------|
| `MINIO_ENDPOINT` | `localhost:9000` | Endpoint **interno** (backend/worker ↔ almacenamiento). |
| `MINIO_PUBLIC_ENDPOINT` | `""` | Endpoint **público** con el que se firman las URLs prefirmadas (subida y descarga) que abre el navegador. |
| `MAX_UPLOAD_SIZE_BYTES` | `5 GiB` | Tamaño máximo de subida (se valida al iniciar y se revalida con el tamaño real al confirmar). |
| `UPLOAD_URL_TTL_SECONDS` | `600` | Validez de la URL prefirmada de subida (PUT). |
| `PENDING_UPLOAD_TIMEOUT_HOURS` | `24` | Tras esto, una subida sin confirmar (`pending`) se limpia (fila + objeto). |
| `ARCHIVE_URL_TTL_SECONDS` | `300` | Validez de la URL prefirmada de descarga. |
| `ARCHIVE_RETENTION_HOURS` | `24` | Horas antes de considerar el ZIP `expired` (alinear con el ciclo de vida del bucket). |
| `ARCHIVE_POLL_INTERVAL_SECONDS` | `5` | Cada cuánto sondea el worker si no llega `NOTIFY`. |
| `ARCHIVE_STALE_TIMEOUT_MINUTES` | `15` | Tras esto, un job atascado en `processing` vuelve a la cola. |

### Endpoint público en local (ngrok)

Dentro de Docker el navegador **no resuelve** `minio:9000`, así que para firmar
URLs que el navegador pueda abrir se expone el puerto 9000 por un túnel:

```bash
ngrok http 9000 --host-header=preserve
# y en .env:
MINIO_PUBLIC_ENDPOINT=https://<tu-subdominio>.ngrok-free.app
```

> **`--host-header=preserve` no es opcional.** SigV4 firma la cabecera `Host`; si
> el túnel reescribe el Host hacia el upstream, **tanto la subida (PUT) como la
> descarga (GET)** fallan con `SignatureDoesNotMatch`. El bucket además necesita
> una regla **CORS** para el origen del SPA que permita `PUT` y `GET` (la subida
> prefirmada es un PUT cross-origin → preflight OPTIONS).

Con **S3 real** no hay desdoblamiento: `MINIO_PUBLIC_ENDPOINT` es la propia URL
del bucket y todo sigue funcionando sin tocar código (presign, multipart y ciclo
de vida son API estándar de S3). MinIO queda **solo para desarrollo local**.

---

## 7. Estado en `drive-clon-ui`

El frontend (repo hermano) **ya está migrado** a estos flujos, en
[`src/lib/api/files.ts`](../../../drive-clon-ui/src/lib/api/files.ts):

- `uploadFile()` → init (`POST /files`) → `PUT` directo al almacenamiento →
  `POST /files/{id}/confirm`. Lo usa la drop zone vía `useUploads`.
- `downloadFolder()` → `POST …/archive` → poll a `GET /files/archives/{job_id}`
  (intervalo 1.5 s, timeout 120 s) → al estar `ready`, abre `download_url`; si
  `failed`/`expired`, lanza error.

El antiguo `GET /files/folders/{id}/download` ya no se usa en ningún sitio.

Esquema de respuesta del poll de descarga:

```json
{
  "job_id": "uuid",
  "status": "queued | processing | ready | failed | expired",
  "name": "Fotos.zip",
  "size_bytes": 12345,
  "download_url": "https://…  (solo cuando status=ready)",
  "error": "…  (solo cuando status=failed)"
}
```
