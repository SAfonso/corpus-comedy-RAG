"""DriveSource — Flujo C (Histórico).

Contrato completo en `src/jokes/historico/SPEC.md` §"Fuente de entrada —
carpeta Drive real" (P19, 2026-07-24). Lista una carpeta REAL de Google Drive
y descarga a un *staging* local solo los `.docx` nuevos/modificados, listos
para `scripts/marcar_remates.procesar_docx(ruta_docx, carpeta_salida)` (cuya
firma no cambia — DriveSource la envuelve por fuera).

Desde la task 43 (P23, 2026-07-27), `DriveSource` es una **subclase fina** de
`src.utils.drive_sync.DriveSync` — el núcleo de sincronización (auth por
cuenta de servicio, listado paginado, idempotencia por `fileId` +
`modifiedTime`, staging, solo lectura) vive ahí y se comparte con el Flujo A
(Teoría). Lo único que `DriveSource` fija es **qué MIMEs acepta y a qué
formato aterriza cada uno**:

- **Qué se descarga:** `.docx` ya subidos
  (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`)
  vía `files().get_media(fileId=...)`; Google Docs nativos
  (`application/vnd.google-apps.document`) se **exportan** a `.docx` vía
  `files().export(fileId=..., mimeType=...)` porque el export a `.docx`
  conserva el color de fuente a nivel de run (`w:rPr/w:color`) que
  `marcar_remates` necesita — un export a texto plano/Markdown lo perdería.
  Cualquier otro MIME se ignora.
- **`staging_dir`** es una caché local reconstruible (NO material sagrado);
  si se borra `state_path`, el siguiente `sync()` re-descarga todo.
- **Auth desatendida:** cuenta de servicio (`GOOGLE_APPLICATION_CREDENTIALS`
  por defecto, o `credentials_path`), scope `drive.readonly`. Nunca OAuth
  interactivo — tiene que poder correr en un runner de CI (task 31).
- Nunca modifica ni borra nada en Drive: solo lectura (`files().list`,
  `files().get_media`, `files().export`).

Contrato de regresión (task 43, ver `src/utils/SPEC.md` §DriveSync): el
constructor conserva exactamente los mismos parámetros/orden que antes de la
extracción — `mimes_aceptados` NO se añade aquí, `DriveSource` lo fija
internamente — y `MIME_DOCX`/`MIME_GDOC` siguen siendo importables desde este
módulo. `tests/unit/jokes/historico/test_drive_source.py` (task 30) sigue en
verde sin modificarse.

Patrón de inyección de dependencias: `folder_id`, `staging_dir`, `state_path`
y `credentials_path` son exactamente los parámetros fijados por el contrato
de la spec (nunca hardcodeados). Para testear sin red, el cliente de Drive
(el objeto que devuelve `googleapiclient.discovery.build("drive", "v3", ...)`)
se puede inyectar vía el parámetro adicional `service` (por defecto `None`):
en producción no se pasa y se construye de forma perezosa, en el primer uso,
a partir de las credenciales; en tests se inyecta un doble de prueba con la
misma interfaz (`files().list/get_media/export`) y `sync()` nunca toca la red.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.utils.drive_sync import DriveSync

MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_GDOC = "application/vnd.google-apps.document"


class DriveSource(DriveSync):
    """Sincroniza la carpeta de Drive `folder_id` a `staging_dir`.

    Descarga SOLO los `.docx` nuevos/modificados desde el último `sync()`
    (idempotencia por `fileId` + `modifiedTime`, persistida en `state_path`)
    y devuelve sus paths locales — exactamente lo que hay que volver a pasar
    por `marcar_remates.procesar_docx(...)`.
    """

    def __init__(
        self,
        folder_id: str,
        staging_dir: Path,
        state_path: Path,
        credentials_path: Optional[Path] = None,
        service=None,
    ):
        super().__init__(
            folder_id=folder_id,
            staging_dir=staging_dir,
            state_path=state_path,
            mimes_aceptados={MIME_DOCX: None, MIME_GDOC: MIME_DOCX},
            credentials_path=credentials_path,
            service=service,
        )
