# Base de datos

Documenta el **esquema actual** de Postgres y el **proceso de migraciones**
(Alembic) del backend.

- Diagrama ER (fuente Graphviz): [`schema.dot`](./schema.dot)
- Modelos ORM: [`app/models/`](../../app/models)
- Migraciones: [`app/migrations/`](../../app/migrations)

---

## 1. Diagrama entidad-relación

El diagrama vive como código en [`schema.dot`](./schema.dot) (notación DOT).
Para generarlo como imagen necesitas [Graphviz](https://graphviz.org/download/):

```bash
# desde la raíz del repo (drive-clon-fast-api/)
dot -Tpng docs/database/schema.dot -o docs/database/schema.png
dot -Tsvg docs/database/schema.dot -o docs/database/schema.svg
```

Vista lógica (resumen):

```
            ┌──────────────────┐
            │  organizations   │
            │  id (PK)         │
            └────────▲─────────┘
                     │ 1
          org_id     │
        ┌────────────┴────────────┐
        │ N                       │ N
┌───────┴────────┐       ┌────────┴───────┐
│     users      │       │     files      │
│  id (PK)       │       │  id (PK)       │
└────────────────┘       └────────────────┘
```

`organizations` es el centro del modelo multi-tenant: tanto `users` como `files`
apuntan a una organización vía `org_id`.

---

## 2. Tablas

> Convenciones: PK = primary key · FK = foreign key · U = unique · IX = index ·
> NN = not null · NULL = nullable.

### `organizations`

Espejo local de una organización de Keycloak (Keycloak es la fuente de verdad de
la membresía; esta tabla permite resolver el tenant sin depender del token).

| Campo             | Tipo    | Constraints  | Descripción                                   |
|-------------------|---------|--------------|-----------------------------------------------|
| `id`              | integer | PK, IX       | Identificador interno (autoincrement).        |
| `keycloak_org_id` | varchar | NN, U, IX    | Id de la organización en Keycloak.            |
| `name`            | varchar | NN           | Nombre visible (p. ej. `"Ana's Drive"`).      |

### `users`

Espejo local del usuario de Keycloak. La identidad y credenciales las gestiona
Keycloak; aquí sólo se guarda el `sub` y el tenant al que pertenece.

| Campo          | Tipo    | Constraints  | Descripción                                          |
|----------------|---------|--------------|------------------------------------------------------|
| `id`           | integer | PK, IX       | Identificador interno (autoincrement).               |
| `keycloak_sub` | varchar | NN, U, IX    | `sub` del token de Keycloak (id estable del usuario).|
| `email`        | varchar | NN, U        | Email del usuario.                                   |
| `name`         | varchar | NULL         | Nombre para mostrar.                                 |
| `picture`      | varchar | NULL         | URL del avatar.                                      |
| `org_id`       | integer | FK, NULL     | → `organizations.id`. Nulo hasta provisionar la org. |

### `files`

Metadatos de fichero. Cada fila va **scopeada por `org_id`** (aislamiento de
tenant: toda consulta filtra por la organización del usuario).

| Campo     | Tipo    | Constraints   | Descripción                                  |
|-----------|---------|---------------|----------------------------------------------|
| `id`      | integer | PK, IX        | Identificador interno (autoincrement).       |
| `address` | varchar | NULL, IX      | Referencia/ubicación del objeto.             |
| `org_id`  | integer | FK, NULL, IX  | → `organizations.id` (tenant propietario).   |

---

## 3. Relaciones

| Desde            | Hacia              | Cardinalidad | Regla                                   |
|------------------|--------------------|--------------|-----------------------------------------|
| `users.org_id`   | `organizations.id` | N : 1        | Muchos usuarios → una organización.     |
| `files.org_id`   | `organizations.id` | N : 1        | Muchos ficheros → una organización.     |

> A nivel de **esquema** la relación es N:1. A nivel de **aplicación** se aplica
> "una organización por usuario" (tenant personal): el backend crea la org en el
> primer login y vincula al usuario. Ver [`ARCHITECTURE.md`](../../ARCHITECTURE.md)
> §4.2.

**Notas de diseño**
- No hay FK física hacia Keycloak: el vínculo es por `keycloak_sub` /
  `keycloak_org_id`. Keycloak y Postgres se sincronizan en la capa de negocio
  (`EnsureOrganizationService`), no por integridad referencial entre sistemas.
- `org_id` es **nullable** a propósito: un usuario existe un instante antes de
  tener organización (durante el provisioning).

---

## 4. Proceso de migraciones (Alembic)

El esquema lo gestiona **Alembic** en modo **async**. La configuración relevante:

- [`alembic.ini`](../../alembic.ini) — config; la `sqlalchemy.url` real la inyecta
  `env.py` (no se usa la del `.ini`).
- [`app/migrations/env.py`](../../app/migrations/env.py) — runner async; importa
  `app.models` para que `--autogenerate` vea el `Base.metadata`, e inyecta
  `settings.async_database_url`.
- [`app/migrations/versions/`](../../app/migrations/versions) — migraciones
  versionadas (commitear cada archivo generado).

### 4.1 Aplicación automática al arrancar

El backend ejecuta `alembic upgrade head` en el `lifespan` de FastAPI
(`app/db/database.py` → `run_migrations()`), **antes** de servir peticiones. En
operación normal **no hay que ejecutar nada a mano**: al levantar el backend, la
BD queda al día.

### 4.2 Crear una migración nueva

Tras cambiar un modelo en `app/models/`:

```bash
# 1) Generar la migración por diff contra la BD (autogenerate)
docker compose run --rm backend alembic revision --autogenerate -m "describe el cambio"

# 2) REVISAR el archivo generado en app/migrations/versions/ (autogenerate no es
#    perfecto: renombrados, tipos server-side, datos, etc. requieren ajuste manual)

# 3) Aplicarla (el lifespan corre upgrade head al reiniciar)
docker compose restart backend
```

Fuera de Docker, los mismos comandos con `alembic ...` directo (requiere
`pip install -r requirements.txt` y el `.env` cargado).

### 4.3 Comandos útiles

```bash
docker compose run --rm backend alembic current     # revisión aplicada
docker compose run --rm backend alembic history     # historial de revisiones
docker compose run --rm backend alembic heads        # cabezas (detecta ramas)
docker compose run --rm backend alembic upgrade head # aplicar pendientes
docker compose run --rm backend alembic downgrade -1 # revertir la última
```

### 4.4 Resetear la BD de desarrollo

`down -v` borra el volumen de Postgres (datos + esquema). Al volver a levantar,
el backend aplica todas las migraciones desde cero:

```bash
docker compose down -v
docker compose up -d
```

### 4.5 Notas y gotchas

- **`alembic_version`**: tabla que Alembic mantiene en la BD con la revisión
  actual. No la edites a mano.
- **Autogenerate compara contra la BD conectada.** Si generas una migración con
  la BD ya al día, saldrá vacía. Para una migración inicial limpia, genera contra
  una BD vacía (`down -v` primero).
- **`compare_type=True`** está activo en `env.py`: detecta cambios de tipo de
  columna, no sólo añadir/quitar.
- **Una sola cabeza**: si `alembic heads` muestra más de una, hubo ramas en
  paralelo; resuélvelas con `alembic merge`.
