"""DriveSource — Flujo C (Histórico).

Contrato completo en `src/jokes/historico/SPEC.md` §"Fuente de entrada —
carpeta Drive real" (P19, 2026-07-24). Lista una carpeta REAL de Google Drive
y descarga a un *staging* local solo los `.docx` nuevos/modificados, listos
para `scripts/marcar_remates.procesar_docx(ruta_docx, carpeta_salida)` (cuya
firma no cambia — DriveSource la envuelve por fuera).

- **Idempotencia por metadata de Drive** (`fileId` + `modifiedTime` RFC3339),
  NO por MD5 de contenido: `modifiedTime` se lee de la metadata sin
  descargar el fichero, evitando así la descarga misma cuando no ha
  cambiado. Capa independiente de la idempotencia MD5 del `Loader` — cada
  una vigila su propio `state_path` (ver §Idempotencia en capas).
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

import json
import os
from pathlib import Path
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_GDOC = "application/vnd.google-apps.document"


class DriveSource:
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
        self.folder_id = folder_id
        self.staging_dir = Path(staging_dir)
        self.state_path = Path(state_path)
        self.credentials_path = Path(credentials_path) if credentials_path else None
        self._service = service  # inyectable para tests; si None, se construye perezosamente

    # --- construcción perezosa del cliente de Drive (solo en producción) ---

    def _get_service(self):
        if self._service is not None:
            return self._service

        cred_path = self.credentials_path or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )
        if not cred_path:
            raise RuntimeError(
                "DriveSource sin credenciales: pasa credentials_path o define "
                "GOOGLE_APPLICATION_CREDENTIALS (JSON de cuenta de servicio, "
                "scope drive.readonly)."
            )

        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            str(cred_path), scopes=SCOPES
        )
        self._service = build(
            "drive", "v3", credentials=credentials, cache_discovery=False
        )
        return self._service

    # --- estado de idempotencia (metadata de Drive) ---

    def _cargar_estado(self) -> dict:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text())

    def _guardar_estado(self, estado: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(estado, indent=2, ensure_ascii=False))

    # --- listado y descarga ---

    def _listar_archivos(self, service) -> list[dict]:
        """Lista los ficheros de tipo documento (`.docx` o Google Doc nativo)
        de `folder_id`, paginando con `nextPageToken`. Ignora cualquier otro
        MIME (hojas, PDFs, imágenes, ...) y los ficheros en la papelera."""
        query = (
            f"'{self.folder_id}' in parents and trashed = false and "
            f"(mimeType = '{MIME_DOCX}' or mimeType = '{MIME_GDOC}')"
        )
        archivos: list[dict] = []
        page_token = None
        while True:
            respuesta = (
                service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                    pageToken=page_token,
                )
                .execute()
            )
            archivos.extend(respuesta.get("files", []))
            page_token = respuesta.get("nextPageToken")
            if not page_token:
                break
        return sorted(archivos, key=lambda a: a["name"])  # orden determinista

    def _descargar_contenido(self, service, archivo: dict) -> bytes:
        """Descarga el contenido del `.docx` (directo) o lo exporta a `.docx`
        (Google Doc nativo, conservando `w:color` a nivel de run). Nunca
        modifica el fichero original en Drive (solo lectura)."""
        file_id = archivo["id"]
        if archivo["mimeType"] == MIME_GDOC:
            return service.files().export(fileId=file_id, mimeType=MIME_DOCX).execute()
        return service.files().get_media(fileId=file_id).execute()

    @staticmethod
    def _nombre_local(nombre_drive: str) -> str:
        if nombre_drive.lower().endswith(".docx"):
            return nombre_drive
        return f"{nombre_drive}.docx"

    def sync(self) -> list[Path]:
        """Lista la carpeta de Drive, descarga a `staging_dir` SOLO los
        `.docx` nuevos/modificados (idempotencia por metadata: `fileId` +
        `modifiedTime`) y devuelve sus paths locales. Los ficheros sin
        cambios desde el último `sync()` no se descargan ni se devuelven.
        Nunca modifica los ficheros en Drive (solo lectura)."""
        service = self._get_service()
        estado = self._cargar_estado()
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        pendientes: list[Path] = []
        for archivo in self._listar_archivos(service):
            file_id = archivo["id"]
            modified_time = archivo["modifiedTime"]
            registro_previo = estado.get(file_id)
            if (
                registro_previo is not None
                and registro_previo.get("modifiedTime") == modified_time
            ):
                continue  # sin cambios en Drive desde el último sync: idempotencia

            contenido = self._descargar_contenido(service, archivo)
            destino = self.staging_dir / self._nombre_local(archivo["name"])
            destino.write_bytes(contenido)

            pendientes.append(destino)
            estado[file_id] = {"name": archivo["name"], "modifiedTime": modified_time}

        self._guardar_estado(estado)
        return pendientes
