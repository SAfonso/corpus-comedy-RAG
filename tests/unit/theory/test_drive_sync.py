"""Tests para DriveSyncTeoria — Flujo A (Teoría), especialización de Drive real (P23, task 44).

Contrato: `src/theory/SPEC.md` §"Fuente de entrada — Drive real y modo dual
(P23, 2026-07-27)" > "`src/theory/drive_sync.py` — especialización de teoría
(task 44)". `DriveSyncTeoria` es una subclase fina de
`src.utils.drive_sync.DriveSync` (task 43): fija `mimes_aceptados=MIMES_TEORIA`
y delega auth/listado/idempotencia/staging al núcleo compartido — mismo patrón
que `src.jokes.historico.drive_source.DriveSource` (task 30/43).

Sin red: se inyecta un doble de `googleapiclient.discovery.build("drive",
"v3", ...)` vía el parámetro `service` (mismo patrón que
`tests/unit/utils/test_drive_sync.py` y
`tests/unit/jokes/historico/test_drive_source.py`). El contenido de los tests
de PDF/DOCX/TXT usa los fixtures REALES `tests/fixtures/sample_transcript.pdf`,
`tests/fixtures/sample_transcript.txt` y `tests/fixtures/Freskito-Informático.docx`
(regla del proyecto: nunca fixtures inventadas). No existe fixture `.epub` en
el repo; para ese único caso se usan bytes placeholder, igual que ya hace
`tests/unit/utils/test_drive_sync.py::test_sync_local_name_uses_extension_por_mime_override`
para PDF (`b"%PDF-fake"`) — a esta capa no le importa el contenido real, solo
el round-trip de bytes vía el doble de servicio; el contenido real de PDF/DOCX
sí se parsea aguas abajo (Parser), pero eso queda fuera del scope de este
módulo.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.theory.drive_sync import (
    MIMES_TEORIA,
    RUTA_ESTADO_DRIVE_POR_DEFECTO,
    STAGING_DIR_POR_DEFECTO,
    DriveSyncTeoria,
    desde_entorno,
)
from src.utils.drive_sync import DriveSync

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"
PDF_BYTES = (FIXTURES_DIR / "sample_transcript.pdf").read_bytes()
TXT_BYTES = (FIXTURES_DIR / "sample_transcript.txt").read_bytes()
DOCX_BYTES = (FIXTURES_DIR / "Freskito-Informático.docx").read_bytes()

MIME_PDF = "application/pdf"
MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_EPUB = "application/epub+zip"
MIME_TXT = "text/plain"
MIME_GDOC = "application/vnd.google-apps.document"


class _FakeExecute:
    """Envuelve un resultado para imitar `<request>.execute()`."""

    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeFilesResource:
    """Doble de `service.files()` con la misma interfaz mínima que usa
    DriveSync: `list`, `get_media`, `export`. Registra las llamadas."""

    def __init__(self, paginas: list[list[dict]], media_content: dict[str, bytes]):
        self._paginas = paginas
        self._media_content = media_content
        self.calls: list[tuple] = []

    def list(self, q=None, fields=None, pageToken=None):
        idx = 0 if pageToken is None else int(pageToken)
        self.calls.append(("list", q, pageToken))
        pagina = self._paginas[idx]
        resultado = {"files": pagina}
        if idx + 1 < len(self._paginas):
            resultado["nextPageToken"] = str(idx + 1)
        return _FakeExecute(resultado)

    def get_media(self, fileId):
        self.calls.append(("get_media", fileId))
        return _FakeExecute(self._media_content[fileId])

    def export(self, fileId, mimeType):
        self.calls.append(("export", fileId, mimeType))
        return _FakeExecute(self._media_content[fileId])


class FakeDriveService:
    def __init__(self, archivos, media_content=None, paginas=None):
        self._files = FakeFilesResource(
            paginas if paginas is not None else [archivos],
            media_content or {},
        )

    def files(self):
        return self._files


@pytest.fixture
def staging_dir(tmp_path):
    return tmp_path / "staging"


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state" / "theory_drive.json"


def _archivo(file_id, name, mime_type, modified_time):
    return {"id": file_id, "name": name, "mimeType": mime_type, "modifiedTime": modified_time}


# --- MIMES_TEORIA: exactamente los 5 esperados, con los destinos correctos ---


def test_mimes_teoria_has_exactly_five_expected_mimes():
    assert set(MIMES_TEORIA) == {
        MIME_PDF,
        MIME_DOCX,
        MIME_EPUB,
        MIME_TXT,
        MIME_GDOC,
    }


def test_mimes_teoria_direct_download_mimes_map_to_none():
    assert MIMES_TEORIA[MIME_PDF] is None
    assert MIMES_TEORIA[MIME_DOCX] is None
    assert MIMES_TEORIA[MIME_EPUB] is None
    assert MIMES_TEORIA[MIME_TXT] is None


def test_mimes_teoria_google_doc_exports_to_docx():
    assert MIMES_TEORIA[MIME_GDOC] == MIME_DOCX


# --- DriveSyncTeoria: subclase fina de DriveSync, fija mimes_aceptados ---


def test_drive_sync_teoria_is_subclass_of_drive_sync(staging_dir, state_path):
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path
    )
    assert isinstance(sync, DriveSync)


def test_drive_sync_teoria_fixes_mimes_aceptados_without_caller_passing_it(
    staging_dir, state_path
):
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path
    )
    assert sync.mimes_aceptados == MIMES_TEORIA


# --- descarga directa de PDF/DOCX/EPUB/TXT vía el doble, sin red ---


def test_sync_downloads_pdf_directly(staging_dir, state_path):
    archivo = _archivo("file1", "libro.pdf", MIME_PDF, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": PDF_BYTES})
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "libro.pdf"]
    assert (staging_dir / "libro.pdf").read_bytes() == PDF_BYTES
    assert ("get_media", "file1") in service.files().calls


def test_sync_downloads_docx_directly(staging_dir, state_path):
    archivo = _archivo("file2", "apunte.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file2": DOCX_BYTES})
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "apunte.docx"]
    assert (staging_dir / "apunte.docx").read_bytes() == DOCX_BYTES
    assert ("get_media", "file2") in service.files().calls


def test_sync_downloads_epub_directly(staging_dir, state_path):
    archivo = _archivo("file3", "libro.epub", MIME_EPUB, "2026-07-20T10:00:00Z")
    contenido = b"epub-fake-content"  # sin fixture .epub real en el repo
    service = FakeDriveService([archivo], {"file3": contenido})
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "libro.epub"]
    assert (staging_dir / "libro.epub").read_bytes() == contenido
    assert ("get_media", "file3") in service.files().calls


def test_sync_downloads_txt_directly(staging_dir, state_path):
    archivo = _archivo(
        "file4", "transcripcion.txt", MIME_TXT, "2026-07-20T10:00:00Z"
    )
    service = FakeDriveService([archivo], {"file4": TXT_BYTES})
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "transcripcion.txt"]
    assert (staging_dir / "transcripcion.txt").read_bytes() == TXT_BYTES
    assert ("get_media", "file4") in service.files().calls


# --- export de Google Doc nativo a .docx (no a texto plano) ---


def test_sync_exports_native_google_doc_to_docx_not_plain_text(staging_dir, state_path):
    archivo = _archivo(
        "file5", "Apuntes de clase", MIME_GDOC, "2026-07-20T11:00:00Z"
    )
    service = FakeDriveService([archivo], {"file5": DOCX_BYTES})
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "Apuntes de clase.docx"]
    assert (staging_dir / "Apuntes de clase.docx").read_bytes() == DOCX_BYTES
    assert ("export", "file5", MIME_DOCX) in service.files().calls
    assert not any(c[0] == "get_media" for c in service.files().calls)


# --- desde_entorno(): RuntimeError explícito sin DRIVE_FOLDER_ID ---


def test_desde_entorno_raises_runtime_error_without_drive_folder_id(monkeypatch):
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)

    with pytest.raises(RuntimeError):
        desde_entorno()


def test_desde_entorno_does_not_touch_network_when_folder_id_missing(monkeypatch):
    """Falla explícito ANTES de tocar red ni construir nada a medias: ni
    siquiera intenta resolver credenciales/servicio."""
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    with pytest.raises(RuntimeError):
        desde_entorno()


# --- desde_entorno(): construcción con defaults correctos ---


def test_desde_entorno_builds_drive_sync_teoria_with_defaults(monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER_ID", "folder-desde-env")

    sync = desde_entorno(service=object())

    assert isinstance(sync, DriveSyncTeoria)
    assert sync.folder_id == "folder-desde-env"
    assert sync.staging_dir == STAGING_DIR_POR_DEFECTO
    assert sync.state_path == RUTA_ESTADO_DRIVE_POR_DEFECTO
    assert sync.mimes_aceptados == MIMES_TEORIA


def test_desde_entorno_respects_explicit_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("DRIVE_FOLDER_ID", "folder-desde-env")
    staging_dir = tmp_path / "mi_staging"
    state_path = tmp_path / "mi_estado.json"
    credentials_path = tmp_path / "creds.json"

    sync = desde_entorno(
        folder_id="folder-explicito",
        staging_dir=staging_dir,
        state_path=state_path,
        credentials_path=credentials_path,
        service=object(),
    )

    assert sync.folder_id == "folder-explicito"
    assert sync.staging_dir == staging_dir
    assert sync.state_path == state_path
    assert sync.credentials_path == credentials_path


# --- idempotencia end-to-end heredada de DriveSync ---


def test_sync_second_run_no_changes_skips_download(staging_dir, state_path):
    archivo = _archivo("file1", "libro.pdf", MIME_PDF, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": PDF_BYTES})
    sync = DriveSyncTeoria(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    sync.sync()

    resultado2 = sync.sync()

    assert resultado2 == []
    descargas = [c for c in service.files().calls if c[0] in ("get_media", "export")]
    assert len(descargas) == 1

    estado = json.loads(state_path.read_text())
    assert estado["file1"]["modifiedTime"] == "2026-07-20T10:00:00Z"
