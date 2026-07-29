"""Tests para DriveSync — núcleo compartido de sincronización con Drive (P23).

Contrato: `src/utils/SPEC.md` §"DriveSync — núcleo compartido de sincronización
con Drive (P23, 2026-07-27)". Generaliza el `DriveSource` del Flujo C (task 30,
P19) a un núcleo parametrizable por `mimes_aceptados` (mime origen -> mime de
export, o `None` para descarga directa) y `extension_por_mime` (mime de salida
-> extensión local).

Mismo patrón de doble de servicio que
`tests/unit/jokes/historico/test_drive_source.py` (sin red: se inyecta un
doble de `googleapiclient.discovery.build("drive", "v3", ...)` vía el
parámetro `service`). El contenido de prueba es el fixture REAL
`tests/fixtures/Freskito-Informático.docx` (regla del proyecto: nunca
fixtures inventadas).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.drive_sync import EXTENSION_POR_MIME, ArchivoSincronizado, DriveSync

FIXTURE_DOCX = Path(__file__).parent.parent.parent / "fixtures" / "Freskito-Informático.docx"
DOCX_BYTES = FIXTURE_DOCX.read_bytes()

MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_GDOC = "application/vnd.google-apps.document"
MIME_PDF = "application/pdf"
MIME_TXT = "text/plain"

MIMES_HISTORICO = {MIME_DOCX: None, MIME_GDOC: MIME_DOCX}


class _FakeExecute:
    """Envuelve un resultado para imitar `<request>.execute()`."""

    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeFilesResource:
    """Doble de `service.files()` con la misma interfaz mínima que usa
    DriveSync: `list`, `get_media`, `export`. Registra las llamadas para que
    los tests puedan verificar qué se pidió (y qué NO)."""

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


# --- listado paginado ---


def test_sync_paginates_through_all_results(staging_dir, state_path):
    archivo1 = _archivo("file1", "a.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    archivo2 = _archivo("file2", "b.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService(
        archivos=None,
        media_content={"file1": DOCX_BYTES, "file2": DOCX_BYTES},
        paginas=[[archivo1], [archivo2]],
    )
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync()

    nombres = {p.name for p in resultado}
    assert nombres == {"a.docx", "b.docx"}


def test_sync_query_mime_order_follows_dict_insertion_order(staging_dir, state_path):
    """El orden de los MIMEs en la query es el orden de inserción de
    `mimes_aceptados`, no un orden alfabético ni arbitrario."""
    service = FakeDriveService([])
    mimes = {MIME_PDF: None, MIME_TXT: None, MIME_DOCX: None}
    sync = DriveSync(
        folder_id="folder-xyz",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=mimes,
        service=service,
    )
    sync.sync()

    llamada_list = next(c for c in service.files().calls if c[0] == "list")
    query = llamada_list[1]
    assert "'folder-xyz' in parents" in query
    assert "trashed = false" in query

    pos_pdf = query.index(MIME_PDF)
    pos_txt = query.index(MIME_TXT)
    pos_docx = query.index(MIME_DOCX)
    assert pos_pdf < pos_txt < pos_docx


# --- descarga directa vs export según mimes_aceptados ---


def test_sync_downloads_directly_when_mime_maps_to_none(staging_dir, state_path):
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})

    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "chiste1.docx"]
    assert (staging_dir / "chiste1.docx").read_bytes() == DOCX_BYTES
    assert ("get_media", "file1") in service.files().calls


def test_sync_exports_when_mime_maps_to_export_target(staging_dir, state_path):
    archivo = _archivo("file2", "Chiste Google Doc", MIME_GDOC, "2026-07-20T11:00:00Z")
    service = FakeDriveService([archivo], {"file2": DOCX_BYTES})

    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "Chiste Google Doc.docx"]
    assert (staging_dir / "Chiste Google Doc.docx").read_bytes() == DOCX_BYTES
    assert ("export", "file2", MIME_DOCX) in service.files().calls
    assert not any(c[0] == "get_media" for c in service.files().calls)


# --- nombrado local con extensión ---


def test_sync_local_name_keeps_existing_matching_extension_case_insensitive(staging_dir, state_path):
    archivo = _archivo("file1", "chiste1.DOCX", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync()

    # ya termina en una extensión .docx (case-insensitive): se respeta tal cual
    assert resultado == [staging_dir / "chiste1.DOCX"]


def test_sync_local_name_appends_extension_when_missing_txt_exported_to_docx(staging_dir, state_path):
    """Caso deliberado de la spec: un Google Doc llamado `Notas.txt`
    exportado a `.docx` aterriza como `Notas.txt.docx` (el nombre local tiene
    que reflejar la extensión de su contenido REAL)."""
    archivo = _archivo("file1", "Notas.txt", MIME_GDOC, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    mimes = {MIME_GDOC: MIME_DOCX}
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=mimes,
        service=service,
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "Notas.txt.docx"]
    assert (staging_dir / "Notas.txt.docx").read_bytes() == DOCX_BYTES


def test_sync_local_name_uses_extension_por_mime_override(staging_dir, state_path):
    archivo = _archivo("file1", "libro", MIME_PDF, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": b"%PDF-fake"})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados={MIME_PDF: None},
        service=service,
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "libro.pdf"]


def test_sync_local_name_uses_custom_extension_por_mime(staging_dir, state_path):
    """`extension_por_mime` amplía/sobrescribe `EXTENSION_POR_MIME` sin tocar
    el módulo."""
    mime_custom = "application/x-custom"
    archivo = _archivo("file1", "cosa", mime_custom, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": b"contenido"})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados={mime_custom: None},
        service=service,
        extension_por_mime={mime_custom: ".custom"},
    )
    resultado = sync.sync()

    assert resultado == [staging_dir / "cosa.custom"]


# --- idempotencia ---


def test_sync_second_run_no_changes_skips_download(staging_dir, state_path):
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    sync.sync()

    resultado2 = sync.sync()

    assert resultado2 == []
    descargas = [c for c in service.files().calls if c[0] in ("get_media", "export")]
    assert len(descargas) == 1


def test_sync_redownloads_when_modified_time_changes(staging_dir, state_path):
    archivo_v1 = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service1 = FakeDriveService([archivo_v1], {"file1": DOCX_BYTES})
    sync1 = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service1,
    )
    sync1.sync()

    archivo_v2 = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-21T09:00:00Z")
    contenido_v2 = DOCX_BYTES + b"\x00"
    service2 = FakeDriveService([archivo_v2], {"file1": contenido_v2})
    sync2 = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service2,
    )
    resultado2 = sync2.sync()

    assert resultado2 == [staging_dir / "chiste1.docx"]
    assert (staging_dir / "chiste1.docx").read_bytes() == contenido_v2

    estado = json.loads(state_path.read_text())
    assert estado["file1"]["modifiedTime"] == "2026-07-21T09:00:00Z"


def test_sync_new_file_id_is_downloaded_even_if_other_state_exists(staging_dir, state_path):
    archivo1 = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service1 = FakeDriveService([archivo1], {"file1": DOCX_BYTES})
    sync1 = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service1,
    )
    sync1.sync()

    archivo2 = _archivo("file2", "chiste2.docx", MIME_DOCX, "2026-07-22T10:00:00Z")
    service2 = FakeDriveService([archivo1, archivo2], {"file1": DOCX_BYTES, "file2": DOCX_BYTES})
    sync2 = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service2,
    )
    resultado2 = sync2.sync()

    assert resultado2 == [staging_dir / "chiste2.docx"]


# --- validación fail-fast en __init__ ---


def test_init_raises_valueerror_on_unknown_output_mime_extension(staging_dir, state_path):
    with pytest.raises(ValueError):
        DriveSync(
            folder_id="folder123",
            staging_dir=staging_dir,
            state_path=state_path,
            mimes_aceptados={"application/x-unknown": None},
        )


def test_init_does_not_raise_when_extension_por_mime_covers_unknown_mime(staging_dir, state_path):
    # no debe lanzar: la extensión desconocida está cubierta por extension_por_mime
    DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados={"application/x-unknown": None},
        extension_por_mime={"application/x-unknown": ".xyz"},
    )


# --- credenciales ausentes: falla en sync(), no en __init__ ---


def test_missing_credentials_raises_in_sync_not_in_init(staging_dir, state_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    # el constructor no debe lanzar
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
    )

    with pytest.raises(RuntimeError):
        sync.sync()


# --- state_path inexistente no rompe ---


def test_sync_missing_state_path_does_not_fail_and_creates_it(staging_dir, state_path):
    assert not state_path.exists()
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    sync.sync()

    assert state_path.exists()


# --- colisión de nombres: no crashea ---


def test_sync_name_collision_does_not_crash(staging_dir, state_path):
    """Dos ficheros cuyo nombre local resuelto coincide se pisan en
    staging_dir (limitación conocida, heredada) — lo único que se exige es
    que no crashee y que el estado quede correcto para ambos fileId."""
    archivo1 = _archivo("file1", "a.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    archivo2 = _archivo("file2", "a", MIME_GDOC, "2026-07-20T10:00:00Z")
    service = FakeDriveService(
        [archivo1, archivo2], {"file1": DOCX_BYTES, "file2": DOCX_BYTES + b"\x00"}
    )
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync()  # no debe lanzar

    assert len(resultado) == 2
    estado = json.loads(state_path.read_text())
    assert "file1" in estado and "file2" in estado


# --- EXTENSION_POR_MIME expuesto y correcto ---


def test_extension_por_mime_contains_expected_defaults():
    assert EXTENSION_POR_MIME[MIME_DOCX] == ".docx"
    assert EXTENSION_POR_MIME[MIME_PDF] == ".pdf"
    assert EXTENSION_POR_MIME["application/epub+zip"] == ".epub"
    assert EXTENSION_POR_MIME[MIME_TXT] == ".txt"


# --- sync_con_metadata (P25, task 57) ---
# Contrato: src/utils/SPEC.md §"Ampliación: metadata por fichero (P25,
# 2026-07-28 — task 57)". Estos tests son nuevos y no tocan ninguno de los
# de arriba (contrato de regresión de las tasks 43/44).


def test_sync_con_metadata_returns_archivo_sincronizado_with_six_fields(staging_dir, state_path):
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync_con_metadata()

    assert resultado == [
        ArchivoSincronizado(
            path=staging_dir / "chiste1.docx",
            file_id="file1",
            name="chiste1.docx",
            modified_time="2026-07-20T10:00:00Z",
            mime_type=MIME_DOCX,
            mime_salida=MIME_DOCX,
        )
    ]


def test_sync_con_metadata_name_differs_from_path_name_when_extension_added(staging_dir, state_path):
    """Caso deliberado de la spec: `name` es el nombre lógico de Drive
    (`Notas.txt`), distinto de `path.name` (nombre local con la extensión
    añadida por el nombrado local, `Notas.txt.docx`)."""
    archivo = _archivo("file1", "Notas.txt", MIME_GDOC, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados={MIME_GDOC: MIME_DOCX},
        service=service,
    )
    resultado = sync.sync_con_metadata()

    assert len(resultado) == 1
    archivo_sincronizado = resultado[0]
    assert archivo_sincronizado.name == "Notas.txt"
    assert archivo_sincronizado.path.name == "Notas.txt.docx"
    assert archivo_sincronizado.name != archivo_sincronizado.path.name
    assert archivo_sincronizado.mime_type == MIME_GDOC
    assert archivo_sincronizado.mime_salida == MIME_DOCX


def test_sync_con_metadata_mime_salida_for_direct_download(staging_dir, state_path):
    """Descarga directa (mime_export=None): mime_salida == mime_type de origen."""
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync_con_metadata()

    assert resultado[0].mime_type == MIME_DOCX
    assert resultado[0].mime_salida == MIME_DOCX


def test_sync_con_metadata_mime_salida_for_export(staging_dir, state_path):
    """Export (Google Doc -> DOCX): mime_type es el de origen (Google Doc),
    mime_salida es el MIME real del contenido descargado (DOCX)."""
    archivo = _archivo("file2", "Chiste Google Doc", MIME_GDOC, "2026-07-20T11:00:00Z")
    service = FakeDriveService([archivo], {"file2": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado = sync.sync_con_metadata()

    assert resultado[0].mime_type == MIME_GDOC
    assert resultado[0].mime_salida == MIME_DOCX


def test_sync_returns_exactly_paths_of_sync_con_metadata(staging_dir, state_path):
    """`sync()` sigue siendo `[a.path for a in sync_con_metadata()]` —
    verificado comparando, sobre el MISMO `staging_dir`/`state_path` (para que
    los paths resultantes sean comparables), el resultado de `sync_con_metadata()`
    contra el de `sync()` invocado después de resetear el estado a cero."""
    archivo1 = _archivo("file1", "a.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    archivo2 = _archivo("file2", "Chiste Google Doc", MIME_GDOC, "2026-07-20T11:00:00Z")

    service1 = FakeDriveService(
        [archivo1, archivo2], {"file1": DOCX_BYTES, "file2": DOCX_BYTES}
    )
    sync1 = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service1,
    )
    resultado_metadata = sync1.sync_con_metadata()
    assert {a.path.name for a in resultado_metadata} == {"a.docx", "Chiste Google Doc.docx"}

    # resetea el estado de idempotencia a cero para comparar sync() en igualdad
    # de condiciones (mismo staging_dir/state_path, ficheros "nuevos" otra vez)
    state_path.unlink()

    service2 = FakeDriveService(
        [archivo1, archivo2], {"file1": DOCX_BYTES, "file2": DOCX_BYTES}
    )
    sync2 = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service2,
    )
    resultado_sync = sync2.sync()

    assert resultado_sync == [a.path for a in resultado_metadata]


def test_sync_con_metadata_shares_idempotency_state_with_sync(staging_dir, state_path):
    """`sync()` y `sync_con_metadata()` comparten el mismo estado de
    idempotencia: llamar a la segunda tras la primera (mismos ficheros, sin
    cambios) no vuelve a stagear nada — igual que llamar a `sync()` dos veces."""
    archivo = _archivo("file1", "chiste1.docx", MIME_DOCX, "2026-07-20T10:00:00Z")
    service = FakeDriveService([archivo], {"file1": DOCX_BYTES})
    sync = DriveSync(
        folder_id="folder123",
        staging_dir=staging_dir,
        state_path=state_path,
        mimes_aceptados=MIMES_HISTORICO,
        service=service,
    )
    resultado1 = sync.sync()
    assert resultado1 == [staging_dir / "chiste1.docx"]

    resultado2 = sync.sync_con_metadata()
    assert resultado2 == []

    descargas = [c for c in service.files().calls if c[0] in ("get_media", "export")]
    assert len(descargas) == 1
