import enum


class ResourceStatus(str, enum.Enum):
    """Estado del ciclo de vida de un recurso (fichero o carpeta).

    Única fuente de verdad para las búsquedas: toda consulta filtra por `status`,
    nunca por una combinación de timestamps. Los timestamps (`trashed_at`,
    `deleted_at`) son metadata para analítica, no para decidir visibilidad.

    - ACTIVE  → visible en "My Drive". El binario existe en MinIO.
    - TRASHED → en la papelera, recuperable. El binario sigue en MinIO.
    - DELETED → borrado permanente: el binario se ha eliminado de MinIO, pero la
      fila se CONSERVA en la BD para analítica (subidas/borrados por mes, etc.).
    """

    ACTIVE = "active"
    TRASHED = "trashed"
    DELETED = "deleted"
