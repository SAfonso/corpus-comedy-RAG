"""Tests para DriveSource — Flujo C (Histórico), P19.

Contrato: `src/jokes/historico/SPEC.md` §"Fuente de entrada — carpeta Drive
real". Idempotencia por metadata de Drive (`fileId` + `modifiedTime`), NO por
MD5 de contenido. `.docx` subidos -> `files().get_media`; Google Docs nativos
-> `files().export(..., mimeType=.../wordprocessingml.document)`.

Sin red: se inyecta un doble de `googleapiclient.discovery.build("drive",
"v3", ...)` vía el parámetro `service` del constructor (ver docstring de
`DriveSource._get_service`). El contenido devuelto por el doble de Drive es
el fixture REAL `tests/fixtures/Freskito-Informático.docx` (regla del
proyecto: nunca fixtures inventadas), así que el `.docx` que aterriza en
`staging_dir` es contenido real y válido, no bytes placeholder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.jokes.historico.drive_source import DriveSource, MIME_DOCX, MIME_GDOC

FIXTURE_DOCX = Path(__file__).parent.parent.parent.parent / "fixtures" / "Freskito-Informático.docx"
DOCX_BYTES = FIXTURE_DOCX.read_bytes()


class _FakeExecute:
    """Envuelve un resultado para imitar `<request>.execute()`."""

    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeFilesResource:
    """Doble de `service.files()` con la misma interfaz mínima que usa
    DriveSource: `list`, `get_media`, `export`. Registra las llamadas para
    que los tests puedan verificar qué se pidió (y qué NO)."""

    def __init__(self, paginas: list[list[dict]], media_content: dict[str, bytes]):
        self._paginas = paginas  # lista de páginas; cada página es list[dict] de ficheros
        self._media_content = media_content  # fileId -> bytes
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
        assert mimeType == MIME_DOCX
        return _FakeExecute(self._media_content[fileId])


class FakeDriveService:
    def __init__(self, archivos: list[dict], media_content: dict[str, bytes] | None = None, paginas=None):
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
    return tmp_path / "state" / "drive_state.json"


def _archivo(file_id, name, mime_type, modified_time):
    return {"id": file_id, "name": name, "mimeType": mime_type, "modifiedTime": modified_time}


def test_sync_downloads_new_docx_and_records_state(staging_dir, state_path):
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})

    source = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = source.sync()

    assert resultado == [staging_dir / "chiste1.docx"]
    assert (staging_dir / "chiste1.docx").read_bytes() == DOCX_BYTES
    assert ("get_media", "file1") in service.files().calls

    estado = json.loads(state_path.read_text())
    assert estado == {"file1": {"name": "chiste1.docx", "modifiedTime": "2026-07-20T10:00:00Z"}}


def test_sync_exports_google_doc_native_to_docx(staging_dir, state_path):
    archivo = _archivo("file2", "Chiste Google Doc", MIME_GDOC, "2026-07-20T11:00:00Z")
    service = FakeDriveService([archivo], {"file2": DOCX_BYTES})

    source = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = source.sync()

    # Google Doc nativo (sin extensión .docx en el nombre) aterriza como .docx
    assert resultado == [staging_dir / "Chiste Google Doc.docx"]
    assert (staging_dir / "Chiste Google Doc.docx").read_bytes() == DOCX_BYTES
    assert ("export", "file2", MIME_DOCX) in service.files().calls
    # nunca se llama a get_media para un Google Doc nativo
    assert not any(c[0] == "get_media" for c in service.files().calls)


def test_sync_second_run_no_changes_skips_download(staging_dir, state_path):
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    source = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    source.sync()

    # segunda sync, mismo servicio (misma metadata) -> no debe volver a descargar
    resultado2 = source.sync()

    assert resultado2 == []
    descargas = [c for c in service.files().calls if c[0] in ("get_media", "export")]
    assert len(descargas) == 1  # solo la primera vez


def test_sync_redownloads_when_modified_time_changes(staging_dir, state_path):
    archivo_v1 = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service1 = FakeDriveService([archivo_v1], {"file1": DOCX_BYTES})
    source1 = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service1
    )
    source1.sync()

    # nueva instancia (simula un nuevo run) con el mismo fileId pero modifiedTime distinto
    archivo_v2 = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-21T09:00:00Z")
    contenido_v2 = DOCX_BYTES + b"\x00"  # contenido "nuevo" simulado
    service2 = FakeDriveService([archivo_v2], {"file1": contenido_v2})
    source2 = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service2
    )
    resultado2 = source2.sync()

    assert resultado2 == [staging_dir / "chiste1.docx"]
    assert (staging_dir / "chiste1.docx").read_bytes() == contenido_v2

    estado = json.loads(state_path.read_text())
    assert estado["file1"]["modifiedTime"] == "2026-07-21T09:00:00Z"


def test_sync_rename_in_drive_without_content_change_does_not_redownload(staging_dir, state_path):
    """fileId es la clave estable: renombrar en Drive no fuerza redescarga
    (solo editar contenido cambia modifiedTime), tal como fija la spec."""
    archivo_v1 = _archivo("file1", "chiste-viejo-nombre.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service1 = FakeDriveService([archivo_v1], {"file1": DOCX_BYTES})
    source1 = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service1
    )
    source1.sync()

    archivo_renombrado = _archivo("file1", "chiste-nuevo-nombre.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service2 = FakeDriveService([archivo_renombrado], {"file1": DOCX_BYTES})
    source2 = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service2
    )
    resultado2 = source2.sync()

    assert resultado2 == []
    assert not any(c[0] in ("get_media", "export") for c in service2.files().calls)


def test_sync_state_persists_across_instances(staging_dir, state_path):
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service1 = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service1
    ).sync()

    service2 = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    resultado2 = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service2
    ).sync()

    assert resultado2 == []


def test_sync_query_scopes_to_folder_and_relevant_mime_types(staging_dir, state_path):
    service = FakeDriveService([])
    source = DriveSource(
        folder_id="folder-xyz", staging_dir=staging_dir, state_path=state_path, service=service
    )
    source.sync()

    llamada_list = next(c for c in service.files().calls if c[0] == "list")
    query = llamada_list[1]
    assert "'folder-xyz' in parents" in query
    assert "trashed = false" in query
    assert MIME_DOCX in query
    assert MIME_GDOC in query


def test_sync_paginates_through_all_results(staging_dir, state_path):
    archivo1 = _archivo("file1", "a.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    archivo2 = _archivo("file2", "b.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService(
        archivos=None,
        media_content={"file1": DOCX_BYTES, "file2": DOCX_BYTES},
        paginas=[[archivo1], [archivo2]],
    )
    source = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    resultado = source.sync()

    nombres = {p.name for p in resultado}
    assert nombres == {"a.docx", "b.docx"}


def test_sync_never_writes_to_drive(staging_dir, state_path):
    """DriveSource es solo lectura: el doble de servicio no expone ningún
    método de escritura (create/update/delete), y sync() nunca los invoca
    (si lo hiciera, el doble lanzaría AttributeError)."""
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    source = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    source.sync()  # si intentase escribir, el FakeFilesResource no tiene esos métodos

    assert all(c[0] in ("list", "get_media", "export") for c in service.files().calls)


def test_missing_credentials_raises_without_touching_network(staging_dir, state_path, monkeypatch):
    """Sin `service` inyectado ni credenciales (ni credentials_path ni
    GOOGLE_APPLICATION_CREDENTIALS), sync() falla explícitamente en vez de
    intentar construir un cliente real / tocar la red."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    source = DriveSource(folder_id="folder123", staging_dir=staging_dir, state_path=state_path)

    with pytest.raises(RuntimeError):
        source.sync()


def test_sync_ignores_state_file_missing_creates_it(staging_dir, state_path):
    """Si no existe state_path (primer run), no falla y lo crea."""
    assert not state_path.exists()
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    source = DriveSource(
        folder_id="folder123", staging_dir=staging_dir, state_path=state_path, service=service
    )
    source.sync()

    assert state_path.exists()
