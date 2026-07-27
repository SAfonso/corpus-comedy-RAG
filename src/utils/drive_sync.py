"""DriveSync — núcleo compartido de sincronización con una carpeta de Google
Drive real (P23, 2026-07-27).

Contrato completo en `src/utils/SPEC.md` §"DriveSync — núcleo compartido de
sincronización con Drive". Generaliza el `DriveSource` original del Flujo C
(`src/jokes/historico/drive_source.py`, task 30, P19) a un núcleo
**parametrizable por MIMEs** para que el Flujo A (Teoría) pueda reutilizarlo
sin reimplementar auth, listado paginado, idempotencia ni staging. Lo único
que varía por consumidor es qué MIMEs se aceptan y a qué formato aterriza cada
uno en local (`mimes_aceptados`, `extension_por_mime`) — todo lo demás (auth
por cuenta de servicio, listado paginado, idempotencia por `fileId` +
`modifiedTime`, staging reconstruible, solo lectura) se conserva tal cual.

- **`mimes_aceptados`**: `dict[mime_origen, Optional[mime_export]]`. Un valor
  `None` significa descarga directa (`files().get_media`); un valor `str`
  significa exportar a ese MIME (`files().export(..., mimeType=valor)`, para
  tipos nativos de Google que no se pueden descargar en crudo). Las claves se
  inyectan literalmente en la query de `files().list`, en su **orden de
  inserción** — determinismo exigido por los consumidores (mismo orden en
  cada corrida).
- **Nombrado local**: el MIME de salida efectivo es
  `mimes_aceptados[mime_origen] or mime_origen`; su extensión sale de
  `EXTENSION_POR_MIME` (ampliable/sobrescribible vía `extension_por_mime`,
  sin tocar este módulo). Si el nombre de Drive ya termina en esa extensión
  (case-insensitive) se respeta tal cual; si no, se le añade — un Google Doc
  nativo llamado `Notas.txt` exportado a `.docx` aterriza como
  `Notas.txt.docx`: **deliberado**, el nombre local tiene que reflejar la
  extensión de su contenido real porque aguas abajo hay consumidores que
  enrutan por extensión.
- **Validación fail-fast en `__init__`**: si algún MIME de salida efectivo no
  tiene extensión conocida, `ValueError` inmediato. La ausencia de
  credenciales, en cambio, **no** se valida en construcción — sigue fallando
  en `sync()` (`RuntimeError`), igual que hoy, para no romper patrones de uso
  donde el objeto se construye sin intención inmediata de sincronizar.
- **Idempotencia**: estado JSON `{fileId: {"name": ..., "modifiedTime": ...}}`
  en `state_path` — mismo formato que hoy, sin migración.
- **`staging_dir`**: caché local reconstruible, NO material sagrado.
- **Auth desatendida**: cuenta de servicio (`credentials_path` o
  `GOOGLE_APPLICATION_CREDENTIALS`), scope `drive.readonly`, cliente
  construido perezosamente en el primer uso. `service` inyectable para tests
  sin red (misma interfaz `files().list/get_media/export`).
- **Solo lectura**: únicamente `files().list`, `files().get_media` y
  `files().export`. Nunca `create`/`update`/`delete`.
- **Colisión de nombres**: dos ficheros cuyo nombre local resuelto coincide se
  pisan en `staging_dir` — limitación conocida y heredada. La idempotencia
  sigue siendo correcta (clave = `fileId`); lo que se pierde es un fichero en
  el staging.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

EXTENSION_POR_MIME: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/pdf": ".pdf",
    "application/epub+zip": ".epub",
    "text/plain": ".txt",
}
"""MIME de SALIDA -> extensión del fichero local. Ampliable por consumidor vía
`extension_por_mime` sin tocar este módulo."""


class DriveSync:
    """Sincroniza la carpeta de Drive `folder_id` a `staging_dir`.

    Descarga SOLO los ficheros nuevos/modificados desde el último `sync()`
    cuyo MIME esté en `mimes_aceptados` (idempotencia por `fileId` +
    `modifiedTime`, persistida en `state_path`) y devuelve sus paths locales.
    """

    def __init__(
        self,
        folder_id: str,
        staging_dir: Path,
        state_path: Path,
        mimes_aceptados: dict[str, Optional[str]],
        credentials_path: Optional[Path] = None,
        service=None,
        extension_por_mime: Optional[dict[str, str]] = None,
    ):
        self.folder_id = folder_id
        self.staging_dir = Path(staging_dir)
        self.state_path = Path(state_path)
        self.mimes_aceptados = mimes_aceptados
        self.credentials_path = Path(credentials_path) if credentials_path else None
        self._service = service  # inyectable para tests; si None, se construye perezosamente

        self._extension_por_mime = dict(EXTENSION_POR_MIME)
        if extension_por_mime:
            self._extension_por_mime.update(extension_por_mime)

        # Validación fail-fast: todo MIME de salida efectivo debe tener
        # extensión conocida. Las credenciales, en cambio, NO se validan
        # aquí — eso falla en sync() (RuntimeError), no en el constructor.
        for mime_origen, mime_export in self.mimes_aceptados.items():
            mime_salida = mime_export or mime_origen
            if mime_salida not in self._extension_por_mime:
                raise ValueError(
                    f"DriveSync: MIME de salida '{mime_salida}' (de origen "
                    f"'{mime_origen}') sin extensión conocida en "
                    "EXTENSION_POR_MIME ni en extension_por_mime."
                )

    # --- construcción perezosa del cliente de Drive (solo en producción) ---

    def _get_service(self):
        if self._service is not None:
            return self._service

        cred_path = self.credentials_path or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )
        if not cred_path:
            raise RuntimeError(
                "DriveSync sin credenciales: pasa credentials_path o define "
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
        """Lista los ficheros cuyo MIME esté en `mimes_aceptados` dentro de
        `folder_id`, paginando con `nextPageToken`. Ignora cualquier otro
        MIME y los ficheros en la papelera."""
        condiciones_mime = " or ".join(
            f"mimeType = '{mime}'" for mime in self.mimes_aceptados
        )
        query = (
            f"'{self.folder_id}' in parents and trashed = false and "
            f"({condiciones_mime})"
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
        """Descarga el contenido directamente (`get_media`) o lo exporta al
        MIME configurado en `mimes_aceptados`, según corresponda. Nunca
        modifica el fichero original en Drive (solo lectura)."""
        file_id = archivo["id"]
        mime_export = self.mimes_aceptados.get(archivo["mimeType"])
        if mime_export is not None:
            return service.files().export(fileId=file_id, mimeType=mime_export).execute()
        return service.files().get_media(fileId=file_id).execute()

    def _nombre_local(self, archivo: dict) -> str:
        nombre_drive = archivo["name"]
        mime_export = self.mimes_aceptados.get(archivo["mimeType"])
        mime_salida = mime_export or archivo["mimeType"]
        extension = self._extension_por_mime[mime_salida]
        if nombre_drive.lower().endswith(extension.lower()):
            return nombre_drive
        return f"{nombre_drive}{extension}"

    def sync(self) -> list[Path]:
        """Lista la carpeta de Drive, descarga a `staging_dir` SOLO los
        ficheros nuevos/modificados (idempotencia por metadata: `fileId` +
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
            destino = self.staging_dir / self._nombre_local(archivo)
            destino.write_bytes(contenido)

            pendientes.append(destino)
            estado[file_id] = {"name": archivo["name"], "modifiedTime": modified_time}

        self._guardar_estado(estado)
        return pendientes
