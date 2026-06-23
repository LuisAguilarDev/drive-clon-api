import enum


class DownloadJobStatus(str, enum.Enum):
    """Estado del job asíncrono que arma el ZIP de una carpeta.

    Ciclo de vida: QUEUED → PROCESSING → READY | FAILED, y EXPIRED como estado
    terminal cuando el objeto temporal ya caducó. La tabla `download_jobs` hace
    a la vez de cola: el worker reclama trabajo filtrando por `status='queued'`.

    - QUEUED     → en la cola, a la espera de un worker.
    - PROCESSING → un worker lo ha reclamado y está armando el ZIP.
    - READY      → ZIP disponible en el bucket; se sirve por URL prefirmada.
    - FAILED     → el armado falló (el motivo queda en `error`).
    - EXPIRED    → el ZIP temporal ya no está (caducó por el ciclo de vida).
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"
