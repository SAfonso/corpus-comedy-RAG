"""DriveSyncTeoria — Flujo A (Teoría).

Contrato completo en `src/theory/SPEC.md` §"Fuente de entrada — Drive real y
modo dual (P23, 2026-07-27)" > "`src/theory/drive_sync.py` — especialización
de teoría (task 44)". Sincroniza la carpeta de Google Drive real de teoría
(`DRIVE_FOLDER_ID`) a un *staging* local (`data/staging/theory/`), listo para
que `DriveMonitor` lo procese exactamente igual que el modo solo-local
(`data/raw/books/` + `data/raw/notes/`) — su interfaz no cambia (task 45 hace
el *wiring*, no este módulo).

`DriveSyncTeoria` es una **subclase fina** de `src.utils.drive_sync.DriveSync`
(task 43, núcleo compartido con el Flujo C) — igual patrón que
`src.jokes.historico.drive_source.DriveSource`. Todo el mecanismo (auth por
cuenta de servicio, listado paginado, idempotencia por `fileId` +
`modifiedTime`, staging, solo lectura) vive en el núcleo; lo único que fija
esta subclase es **qué MIMEs acepta y a qué formato aterriza cada uno**:

- `application/pdf`, DOCX, `application/epub+zip` y `text/plain` se
  **descargan directos** (`get_media`): ya vienen en un formato que el Parser
  de teoría sabe leer (`pdf_parser`, `docx_parser`, `epub_parser`,
  `whisperx_parser`). Nada que convertir.
- **Google Docs nativos (`application/vnd.google-apps.document`) se exportan a
  `.docx`, NO a texto plano.** Motivo propio de teoría (no el del histórico,
  que conserva `w:color` para `marcar_remates` — aquí no aplica): el `.docx`
  cae en `docx_parser`/markitdown, que conserva estructura útil para
  `SubtypeDetector`/Cleaner. `.txt` está reservado en teoría a las
  transcripciones WhisperX — `theory/pipeline.py` deriva Parser y
  `tipo_fuente` por extensión, así que un Google Doc exportado a `.txt`
  acabaría mal enrutado (parser de WhisperX + `tipo_fuente` incorrecto).
- Cualquier otro MIME se ignora (la query de Drive ya filtra).

`desde_entorno()` es la factory que construye un `DriveSyncTeoria` leyendo
`DRIVE_FOLDER_ID` del entorno (obligatoria: `RuntimeError` explícito si falta,
antes de tocar red o credenciales — "nunca arranca a medias"). No lee
`GOOGLE_APPLICATION_CREDENTIALS` ella misma: si no se pasa `credentials_path`,
lo resuelve `DriveSync._get_service()` (capa de abajo), igual que hace
`DriveSource`/`run_historico_pipeline` con la suya.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from src.utils.drive_sync import DriveSync

MIME_PDF = "application/pdf"
MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_EPUB = "application/epub+zip"
MIME_TXT = "text/plain"
MIME_GDOC = "application/vnd.google-apps.document"

MIMES_TEORIA: dict[str, Optional[str]] = {
    MIME_PDF: None,  # libro PDF               -> get_media -> .pdf
    MIME_DOCX: None,  # DOCX subido            -> get_media -> .docx
    MIME_EPUB: None,  # libro EPUB             -> get_media -> .epub
    MIME_TXT: None,  # transcripción WhisperX  -> get_media -> .txt
    MIME_GDOC: MIME_DOCX,  # Google Doc nativo -> export    -> .docx
}

STAGING_DIR_POR_DEFECTO = Path("data/staging/theory")
RUTA_ESTADO_DRIVE_POR_DEFECTO = Path("data/state/theory_drive.json")

ENV_FOLDER_ID = "DRIVE_FOLDER_ID"


class DriveSyncTeoria(DriveSync):
    """Sincroniza la carpeta de Drive de teoría `folder_id` a `staging_dir`.

    Descarga SOLO los ficheros nuevos/modificados desde el último `sync()`
    cuyo MIME esté en `MIMES_TEORIA` (idempotencia por `fileId` +
    `modifiedTime`, persistida en `state_path`) y devuelve sus paths locales.
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
            mimes_aceptados=MIMES_TEORIA,
            credentials_path=credentials_path,
            service=service,
        )


def desde_entorno(
    folder_id: Optional[str] = None,
    staging_dir: Optional[Path] = None,
    state_path: Optional[Path] = None,
    credentials_path: Optional[Path] = None,
    service=None,
) -> DriveSyncTeoria:
    """Construye el `DriveSyncTeoria` de teoría desde el entorno.

    `folder_id` cae a la variable de entorno `DRIVE_FOLDER_ID` si no se pasa
    explícito; si tampoco está en el entorno, `RuntimeError` inmediato —
    nunca arranca a medias ni intenta tocar red/credenciales sin carpeta.
    `staging_dir`/`state_path` caen a `STAGING_DIR_POR_DEFECTO`/
    `RUTA_ESTADO_DRIVE_POR_DEFECTO` si no se pasan. `credentials_path` se pasa
    tal cual (puede ser `None`): la resolución de
    `GOOGLE_APPLICATION_CREDENTIALS` vive en `DriveSync._get_service()`, no
    aquí.
    """
    folder_id = folder_id or os.environ.get(ENV_FOLDER_ID)
    if not folder_id:
        raise RuntimeError(
            "theory.drive_sync.desde_entorno sin folder_id: pásalo o define "
            f"{ENV_FOLDER_ID} (carpeta de Drive de teoría)."
        )

    staging_dir = Path(staging_dir) if staging_dir is not None else STAGING_DIR_POR_DEFECTO
    state_path = (
        Path(state_path) if state_path is not None else RUTA_ESTADO_DRIVE_POR_DEFECTO
    )

    return DriveSyncTeoria(
        folder_id=folder_id,
        staging_dir=staging_dir,
        state_path=state_path,
        credentials_path=credentials_path,
        service=service,
    )
