# Compact schema (LLM quick reference)

Terse, low-token view of the whole schema so a tool/LLM can grasp the full
picture without parsing the verbose HTML tables in [`schema.gv`](./schema.gv).
`schema.gv` is the source of truth for the rendered diagram; keep both in sync.

> `?` = nullable · `U` = unique · `PK`/`FK` as usual · relations are N:1.
> Every table has `deleted_at?` (soft delete; reads filter `deleted_at IS NULL`).
> `folders` and the extended `files` columns are **planned** (see
> [`../PRD/folders-and-files.md`](../PRD/folders-and-files.md)); not yet migrated.

```
organizations(id PK, keycloak_org_id U, name, deleted_at?)
users(id PK, keycloak_sub U, email U, name?, picture?, org_id? FK->organizations, deleted_at?)
folders(id PK, name, org_id FK->organizations, owner_id FK->users,
        parent_id? FK->folders, created_at, deleted_at?)    # parent_id NULL = root
files(id PK, name, object_key, content_type?, size_bytes?,
      folder_id FK->folders, org_id FK->organizations, owner_id FK->users,
      created_at, deleted_at?)

# constraints
folders: partial unique (owner_id) WHERE parent_id IS NULL AND deleted_at IS NULL  # one root per user

# relations (N:1)
users.org_id      -> organizations.id
folders.org_id    -> organizations.id
folders.owner_id  -> users.id
folders.parent_id -> folders.id           # self-reference; NULL = root folder
files.folder_id   -> folders.id
files.org_id      -> organizations.id
files.owner_id    -> users.id
```

## Why a separate file instead of "joining" .gv files

Graphviz/DOT has **no native `#include`** — you cannot import one `.gv` into
another. Ways people combine graphs, and why we don't here:

- **C preprocessor**: `cpp schema.gv.in | dot -Tpng` lets you `#include` partials,
  but adds a build step and a non-standard toolchain.
- **`gvpack`**: merges several *rendered* graphs into one image — for layout, not
  for a single readable source.
- **Subgraphs/clusters in one file**: the real "single source" approach — and
  that's exactly what `schema.gv` already is (one `digraph` with every table).

So the schema lives in **one** `schema.gv` (single source of truth, render it for
the visual), and this compact `.md` is the **fast textual summary** for quick or
machine reading. Update both when the schema changes.
