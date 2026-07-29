"""Tests para pipeline (Flujo A, orquestador — task 22).

Contrato (`src/theory/SPEC.md` §Cadena de componentes/§Idempotencia y
versionado, `CHECKPOINTS.md`, ver también `pipeline.py` docstring):
`run_pipeline` encadena `DriveMonitor -> Parser -> SubtypeDetector ->
Cleaner -> LanguageNormalizer -> generar_version` (QualityScorer ya está
embebido en `generar_version`, ver docstring de `pipeline.py`) sobre
ficheros nuevos/modificados, y marca cada fichero en `processed_files.json`
SOLO tras completar la cadena entera con éxito.

Fixtures reales usadas (nunca inventadas): `tests/fixtures/sample_transcript.txt`
(WhisperX, español) y `tests/fixtures/sample_transcript.pdf` (PDF nativo,
español, 2 páginas). Aunque ambos documentos están en español, algunos
fragmentos muy cortos del PDF ("¿Por qué?", "No.", "2.") hacen que
`detect_language` (basado en n-gramas, ver `src/utils/language_detector.py`)
detecte un idioma distinto por falta de señal — comportamiento real y ya
documentado de un componente aprobado, no un bug de este módulo — así que
`normalize_language` sí invoca al traductor para esos fragmentos. Se inyecta
un traductor falso sin red (pass-through, mismo patrón que `_TraductorEspia`
de `test_language_normalizer.py`) en vez de uno que lance al ser llamado.

Los escenarios de fallo a mitad de cadena/reanudación se simulan con
`monkeypatch` sobre `pipeline._procesar_fichero`/`pipeline.generar_version`
(mismo patrón que `_traductor_que_no_debe_llamarse` en otros tests de Flujo
A: inyectar un fallo controlado, no fabricar un fixture corrupto) — el
correcto funcionamiento de cada etapa ya está cubierto por sus propios tests
unitarios; aquí se testea la ORQUESTACIÓN (marcado/reanudación), no las
etapas en sí.
"""
import json
import shutil
from pathlib import Path

import pytest

from src.theory import pipeline as pipeline_mod
from src.theory.normalizers.format_normalizer import LICENCIA_POR_DEFECTO
from src.theory.pipeline import run_pipeline
from src.utils.drive_sync import ArchivoSincronizado

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"
SAMPLE_PDF = FIXTURES_DIR / "sample_transcript.pdf"


def _traductor_fake(texto: str, idioma_origen: str) -> str:
    """Traductor sin red: pass-through (ver docstring del módulo)."""
    return texto


@pytest.fixture
def carpeta_books(tmp_path):
    carpeta = tmp_path / "books"
    carpeta.mkdir()
    shutil.copy(SAMPLE_TXT, carpeta / "sample_transcript.txt")
    shutil.copy(SAMPLE_PDF, carpeta / "sample_transcript.pdf")
    return carpeta


@pytest.fixture
def entorno(tmp_path, carpeta_books):
    return {
        "carpetas": [carpeta_books],
        "directorio_procesado": tmp_path / "processed",
        "ruta_estado": tmp_path / "state" / "processed_files.json",
    }


def _run(entorno, **kwargs):
    kwargs.setdefault("traductor", _traductor_fake)
    return run_pipeline(
        entorno["carpetas"],
        directorio_procesado=entorno["directorio_procesado"],
        ruta_estado=entorno["ruta_estado"],
        **kwargs,
    )


# --- Cadena completa feliz ---------------------------------------------------


def test_cadena_completa_feliz_procesa_los_dos_ficheros(entorno):
    resultado = _run(entorno)

    assert resultado.ok
    assert resultado.version_dir == entorno["directorio_procesado"] / "v1"
    assert {p.name for p in resultado.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }
    assert resultado.fallidos == []
    assert resultado.ignorados == []

    manifest = json.loads((resultado.version_dir / "manifest.json").read_text())
    assert manifest["version"] == 1
    assert len(manifest["documents"]) == 2

    por_tipo = {doc["tipo_fuente"]: doc for doc in manifest["documents"]}

    # tipo_fuente inferido por extensión (ver docstring de pipeline.py)
    assert "transcripcion_curso" in por_tipo  # .txt (WhisperX)
    assert "teoria" in por_tipo  # .pdf

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert set(estado.keys()) == {"sample_transcript.txt", "sample_transcript.pdf"}


def test_tipo_fuente_se_infiere_por_extension_de_origen(entorno):
    resultado = _run(entorno)
    manifest = json.loads((resultado.version_dir / "manifest.json").read_text())

    tipos_por_ruta = {doc["path"]: doc["tipo_fuente"] for doc in manifest["documents"]}
    # Ambos documentos derivan su `fuente` del mismo stem ("sample_transcript"),
    # format_normalizer desambigua el nombre de fichero de salida con sufijo.
    assert set(tipos_por_ruta.values()) == {"transcripcion_curso", "teoria"}


def test_fuente_se_deriva_del_nombre_de_fichero(entorno):
    resultado = _run(entorno)
    manifest = json.loads((resultado.version_dir / "manifest.json").read_text())

    fuentes = {doc["fuente"] for doc in manifest["documents"]}
    assert fuentes == {"sample transcript"}


def test_no_toca_el_fichero_original(entorno):
    carpeta = entorno["carpetas"][0]
    txt_path = carpeta / "sample_transcript.txt"
    contenido_antes = txt_path.read_bytes()

    _run(entorno)

    assert txt_path.read_bytes() == contenido_antes


# --- Segunda ejecución sin cambios -------------------------------------------


def test_segunda_ejecucion_sin_cambios_no_genera_version_nueva(entorno):
    _run(entorno)
    resultado2 = _run(entorno)

    assert resultado2.version_dir is None
    assert resultado2.procesados == []
    assert resultado2.fallidos == []
    assert not (entorno["directorio_procesado"] / "v2").exists()


# --- Fallo a mitad de cadena: NO debe marcar el fichero ----------------------


def test_fallo_a_mitad_de_cadena_no_marca_el_fichero_fallido(entorno, monkeypatch):
    original = pipeline_mod._procesar_fichero

    def _procesar_con_fallo(path, traductor):
        if path.name == "sample_transcript.pdf":
            raise RuntimeError("fallo simulado a mitad de cadena")
        return original(path, traductor)

    monkeypatch.setattr(pipeline_mod, "_procesar_fichero", _procesar_con_fallo)

    resultado = _run(entorno)

    assert not resultado.ok
    assert {p.name for p in resultado.procesados} == {"sample_transcript.txt"}
    assert len(resultado.fallidos) == 1
    assert resultado.fallidos[0].path.name == "sample_transcript.pdf"
    assert "fallo simulado" in resultado.fallidos[0].error

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert "sample_transcript.pdf" not in estado
    assert "sample_transcript.txt" in estado

    # El fichero que sí completó la cadena queda en una v1 válida.
    assert resultado.version_dir == entorno["directorio_procesado"] / "v1"
    manifest = json.loads((resultado.version_dir / "manifest.json").read_text())
    assert len(manifest["documents"]) == 1


# --- Reanudación tras fallo previo -------------------------------------------


def test_reanudacion_tras_fallo_previo_reprocesa_solo_lo_pendiente(entorno, monkeypatch):
    original = pipeline_mod._procesar_fichero

    def _procesar_con_fallo(path, traductor):
        if path.name == "sample_transcript.pdf":
            raise RuntimeError("fallo simulado")
        return original(path, traductor)

    monkeypatch.setattr(pipeline_mod, "_procesar_fichero", _procesar_con_fallo)
    resultado1 = _run(entorno)
    assert {p.name for p in resultado1.procesados} == {"sample_transcript.txt"}
    assert {f.path.name for f in resultado1.fallidos} == {"sample_transcript.pdf"}

    # Se levanta el fallo simulado (mismo comportamiento que un redeploy que
    # corrige el bug) y se relanza el pipeline: NO debe reprocesar
    # sample_transcript.txt (ya marcado), solo el que faltaba.
    monkeypatch.undo()
    resultado2 = _run(entorno)

    assert resultado2.ok
    assert {p.name for p in resultado2.procesados} == {"sample_transcript.pdf"}
    assert resultado2.fallidos == []
    assert resultado2.version_dir == entorno["directorio_procesado"] / "v2"

    manifest_v2 = json.loads((resultado2.version_dir / "manifest.json").read_text())
    assert len(manifest_v2["documents"]) == 1

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert set(estado.keys()) == {"sample_transcript.txt", "sample_transcript.pdf"}

    # v1 (del primer run) sigue intacta.
    assert (entorno["directorio_procesado"] / "v1" / "manifest.json").exists()


# --- Fallo en el último paso (generar_version): nada se marca ---------------


def test_fallo_en_generar_version_no_marca_ningun_fichero_del_batch(entorno, monkeypatch):
    def _generar_version_con_fallo(documentos, directorio_base, version=None):
        raise RuntimeError("fallo simulado en generar_version")

    monkeypatch.setattr(pipeline_mod, "generar_version", _generar_version_con_fallo)

    resultado = _run(entorno)

    assert resultado.version_dir is None
    assert resultado.procesados == []
    assert len(resultado.fallidos) == 1
    assert resultado.fallidos[0].path == Path("<generar_version>")
    assert "fallo simulado" in resultado.fallidos[0].error

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert estado == {}
    assert not entorno["directorio_procesado"].exists() or not any(
        entorno["directorio_procesado"].iterdir()
    )

    # Siguiente run, sin el fallo: reprocesa AMBOS ficheros desde cero.
    monkeypatch.undo()
    resultado2 = _run(entorno)
    assert resultado2.ok
    assert {p.name for p in resultado2.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }
    assert resultado2.version_dir == entorno["directorio_procesado"] / "v1"


# --- Ficheros sin Parser conocido (p.ej. .gitkeep) ---------------------------


def test_fichero_sin_parser_conocido_se_ignora_y_no_se_reintenta(tmp_path):
    carpeta = tmp_path / "books"
    carpeta.mkdir()
    shutil.copy(SAMPLE_TXT, carpeta / "sample_transcript.txt")
    (carpeta / ".gitkeep").write_text("")

    ruta_estado = tmp_path / "state" / "processed_files.json"
    directorio_procesado = tmp_path / "processed"

    resultado1 = run_pipeline(
        [carpeta],
        directorio_procesado=directorio_procesado,
        ruta_estado=ruta_estado,
        traductor=_traductor_fake,
    )
    assert {p.name for p in resultado1.ignorados} == {".gitkeep"}
    assert resultado1.fallidos == []
    assert {p.name for p in resultado1.procesados} == {"sample_transcript.txt"}

    resultado2 = run_pipeline(
        [carpeta],
        directorio_procesado=directorio_procesado,
        ruta_estado=ruta_estado,
        traductor=_traductor_fake,
    )
    # Nada pendiente: ni el .txt (ya procesado) ni el .gitkeep (ya marcado
    # como visto pese a no tener Parser) se vuelven a reportar.
    assert resultado2.ignorados == []
    assert resultado2.procesados == []
    assert resultado2.version_dir is None


# --- Carpetas inexistentes ----------------------------------------------------


def test_carpeta_inexistente_se_ignora_sin_error(tmp_path):
    resultado = run_pipeline(
        [tmp_path / "no_existe"],
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
    )
    assert resultado.version_dir is None
    assert resultado.procesados == []
    assert resultado.fallidos == []
    assert resultado.ignorados == []


def test_run_pipeline_sin_ficheros_pendientes_no_falla(tmp_path):
    carpeta = tmp_path / "books"
    carpeta.mkdir()

    resultado = run_pipeline(
        [carpeta],
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
    )
    assert resultado.ok
    assert resultado.version_dir is None


# --- Drive real, etapa 0 y resolución de `carpetas` (task 45, P23) ----------
#
# Contrato exacto en `src/theory/SPEC.md` §"Fuente de entrada — Drive real y
# modo dual (P23)" > "Activación (task 45)":
#   - drive_sync=None + carpetas=None -> defaults locales (comportamiento de
#     hoy, sin cambios).
#   - drive_sync presente + carpetas=None -> [drive_sync.staging_dir] (el
#     default local NO se añade).
#   - carpetas explícito -> se respeta tal cual; si drive_sync está presente
#     y su staging_dir no está ya en la lista, se añade.
# `drive_sync` es duck-typed (`.sync_con_metadata() -> list[ArchivoSincronizado]`,
# `.staging_dir: Path`, ampliado en la task 59, P25) — los dobles de abajo no
# heredan de `DriveSyncTeoria` a propósito, para mantener estos tests sin
# ninguna dependencia de red/credenciales de `src.utils.drive_sync`
# (google-api-python-client incluido); sí importan `ArchivoSincronizado`, que
# es un `dataclass` puro sin esas dependencias.


class _MonitorEspia:
    """Doble de `DriveMonitor` que solo registra la carpeta con la que se
    instancia y no encuentra nunca nada pendiente — para tests que solo
    verifican la RESOLUCIÓN de `carpetas`, no el resto de la cadena (que ya
    cubren los tests de arriba)."""

    instancias: list = []

    def __init__(self, folder, state_path):
        type(self).instancias.append(folder)

    def scan(self):
        return []


class _DriveSyncFake:
    """Doble mínimo de `DriveSyncTeoria`: registra si `.sync_con_metadata()`
    se llamó y expone `.staging_dir`. No descarga nada por defecto
    (subclases/usos concretos abajo sobrescriben `sync_con_metadata()` cuando
    el test necesita dejar ficheros reales en el staging)."""

    def __init__(self, staging_dir: Path):
        self.staging_dir = staging_dir
        self.sync_llamado = False

    def sync_con_metadata(self) -> list[ArchivoSincronizado]:
        self.sync_llamado = True
        return []


def test_drive_sync_none_usa_los_defaults_locales_sin_cambios(tmp_path, monkeypatch):
    """`drive_sync=None` (el caso de hoy, ni pasado ni inyectado) termina
    vigilando exactamente `CARPETA_BOOKS_POR_DEFECTO`/`CARPETA_NOTES_POR_DEFECTO`
    — comportamiento bit a bit igual que antes de esta tarea."""
    books = tmp_path / "books"
    notes = tmp_path / "notes"
    books.mkdir()
    notes.mkdir()
    monkeypatch.setattr(pipeline_mod, "CARPETA_BOOKS_POR_DEFECTO", books)
    monkeypatch.setattr(pipeline_mod, "CARPETA_NOTES_POR_DEFECTO", notes)
    _MonitorEspia.instancias = []
    monkeypatch.setattr(pipeline_mod, "DriveMonitor", _MonitorEspia)

    resultado = run_pipeline(
        None,
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
    )

    assert resultado.ok
    assert _MonitorEspia.instancias == [books, notes]


def test_drive_sync_presente_y_carpetas_none_vigila_solo_el_staging_dir(tmp_path, monkeypatch):
    """`drive_sync` presente + `carpetas=None` -> `[drive_sync.staging_dir]`;
    el default local NO se añade (tabla de la SPEC)."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _MonitorEspia.instancias = []
    monkeypatch.setattr(pipeline_mod, "DriveMonitor", _MonitorEspia)

    fake = _DriveSyncFake(staging_dir)

    resultado = run_pipeline(
        None,
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
        drive_sync=fake,
    )

    assert resultado.ok
    assert fake.sync_llamado is True
    assert _MonitorEspia.instancias == [staging_dir]


def test_carpetas_explicitas_incluyen_staging_dir_si_no_estaba(tmp_path, monkeypatch):
    """`carpetas` explícito se respeta tal cual; el `staging_dir` de
    `drive_sync` se añade al final si no estaba ya en la lista."""
    carpeta_explicita = tmp_path / "custom"
    carpeta_explicita.mkdir()
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _MonitorEspia.instancias = []
    monkeypatch.setattr(pipeline_mod, "DriveMonitor", _MonitorEspia)

    fake = _DriveSyncFake(staging_dir)

    resultado = run_pipeline(
        [carpeta_explicita],
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
        drive_sync=fake,
    )

    assert resultado.ok
    assert fake.sync_llamado is True
    assert _MonitorEspia.instancias == [carpeta_explicita, staging_dir]


def test_carpetas_explicitas_no_duplican_staging_dir_si_ya_estaba(tmp_path, monkeypatch):
    """Si `carpetas` explícito YA contiene el `staging_dir` de `drive_sync`,
    no se duplica en la lista (un solo `DriveMonitor` para esa carpeta)."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _MonitorEspia.instancias = []
    monkeypatch.setattr(pipeline_mod, "DriveMonitor", _MonitorEspia)

    fake = _DriveSyncFake(staging_dir)

    resultado = run_pipeline(
        [staging_dir],
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
        drive_sync=fake,
    )

    assert resultado.ok
    assert fake.sync_llamado is True
    assert _MonitorEspia.instancias == [staging_dir]


def test_etapa_0_sync_puebla_el_staging_antes_de_que_drivemonitor_escanee(tmp_path):
    """La etapa 0 (`drive_sync.sync_con_metadata()`) debe completarse ANTES
    del loop de `DriveMonitor` — se comprueba end-to-end (sin dobles de
    `DriveMonitor`): `staging_dir` NO existe hasta que `sync_con_metadata()`
    lo crea y deja el fichero; si el wiring llamara a `sync_con_metadata()`
    DESPUÉS de construir/escanear `DriveMonitor`, la carpeta no existiría
    todavía en ese punto (`carpeta.exists()` sería False) y el fichero nunca
    se procesaría."""

    class _DriveSyncQueDejaFichero:
        def __init__(self, staging_dir: Path):
            self.staging_dir = staging_dir
            self.sync_llamado = False

        def sync_con_metadata(self) -> list[ArchivoSincronizado]:
            self.sync_llamado = True
            assert not self.staging_dir.exists()  # todavía no la creó nadie
            self.staging_dir.mkdir(parents=True, exist_ok=True)
            destino = self.staging_dir / "sample_transcript.txt"
            shutil.copy(SAMPLE_TXT, destino)
            return [
                ArchivoSincronizado(
                    path=destino,
                    file_id="fake-file-id",
                    name="sample_transcript.txt",
                    modified_time="2026-07-29T00:00:00.000Z",
                    mime_type="text/plain",
                    mime_salida="text/plain",
                )
            ]

    staging_dir = tmp_path / "staging"
    fake = _DriveSyncQueDejaFichero(staging_dir)

    resultado = run_pipeline(
        None,
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        drive_sync=fake,
    )

    assert fake.sync_llamado is True
    assert resultado.ok
    assert {p.name for p in resultado.procesados} == {"sample_transcript.txt"}
    assert resultado.version_dir == tmp_path / "processed" / "v1"


# --- Captura Bronze, etapa 0.5 (task 59, P25) --------------------------------
#
# Contrato exacto en `src/theory/SPEC.md` §"Captura Bronze — todo lo que
# entra se guarda antes de tocarlo (P25, task 59)": `document_store=None`
# (por defecto) -> cero llamadas nuevas; con `document_store` inyectado, cada
# fichero (Drive o legacy) se captura ANTES de `_procesar_fichero`, salvo un
# fichero bajo `drive_sync.staging_dir` en modo legacy (ya se captura, o se
# capturará, en modo Drive). Un fallo de `capturar()` no tumba el batch: el
# fichero afectado se excluye de esta corrida (queda en `fallidos`, no se
# marca en `ruta_estado`, se reintenta en el siguiente run).


class _DocumentStoreEspia:
    """Doble de `DocumentStore`: registra cada llamada a `capturar()` (sin
    tocar red) y, opcionalmente, lanza una excepción simulada para las rutas
    en `fallos` (mismo patrón que `_procesar_con_fallo` de arriba: fallo
    controlado por monkeypatch/inyección, no un fixture corrupto)."""

    def __init__(self, fallos: set = frozenset()):
        self.llamadas: list[dict] = []
        self._fallos = fallos

    def capturar(
        self,
        ruta,
        capa,
        flujo,
        drive_file_id=None,
        modified_time=None,
        nombre=None,
        mime_type=None,
        extra=None,
    ):
        self.llamadas.append(
            {
                "ruta": ruta,
                "capa": capa,
                "flujo": flujo,
                "drive_file_id": drive_file_id,
                "modified_time": modified_time,
                "nombre": nombre,
                "mime_type": mime_type,
                "extra": extra,
            }
        )
        if ruta in self._fallos:
            raise RuntimeError(f"fallo simulado capturando {ruta}")


def test_document_store_none_no_captura_nada_y_no_cambia_el_comportamiento(entorno):
    """Test de regresión explícito (criterio de aceptación más importante de
    la task 59): `document_store=None` (pasado explícitamente, no solo
    omitido) deja el pipeline bit a bit igual que antes de esta tarea."""
    resultado = _run(entorno, document_store=None)

    assert resultado.ok
    assert {p.name for p in resultado.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }
    assert resultado.fallidos == []


def test_captura_bronze_modo_drive_llama_a_document_store_con_kwargs_correctos(tmp_path):
    """Verifica los kwargs exactos de la captura en modo Drive: `mime_type`
    debe ser `archivo.mime_salida` (MIME real del contenido descargado), NUNCA
    `archivo.mime_type` (MIME de ORIGEN en Drive, aquí deliberadamente distinto
    para probar que no se confunden), y `tipo_fuente` se deriva de la
    extensión del `.txt` -> `transcripcion_curso`."""

    class _DriveSyncConArchivo:
        def __init__(self, staging_dir: Path):
            self.staging_dir = staging_dir

        def sync_con_metadata(self) -> list[ArchivoSincronizado]:
            self.staging_dir.mkdir(parents=True, exist_ok=True)
            destino = self.staging_dir / "sample_transcript.txt"
            shutil.copy(SAMPLE_TXT, destino)
            return [
                ArchivoSincronizado(
                    path=destino,
                    file_id="drive-file-123",
                    name="sample_transcript.txt",
                    modified_time="2026-07-29T10:15:00.000Z",
                    mime_type="application/vnd.google-apps.document",
                    mime_salida="text/plain",
                )
            ]

    staging_dir = tmp_path / "staging"
    fake_drive = _DriveSyncConArchivo(staging_dir)
    doc_store = _DocumentStoreEspia()

    resultado = run_pipeline(
        None,
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        drive_sync=fake_drive,
        document_store=doc_store,
    )

    assert resultado.ok
    assert len(doc_store.llamadas) == 1
    llamada = doc_store.llamadas[0]
    assert llamada["ruta"] == staging_dir / "sample_transcript.txt"
    assert llamada["capa"] == "bronze"
    assert llamada["flujo"] == "teoria"
    assert llamada["drive_file_id"] == "drive-file-123"
    assert llamada["modified_time"] == "2026-07-29T10:15:00.000Z"
    assert llamada["nombre"] == "sample_transcript.txt"
    assert llamada["mime_type"] == "text/plain"  # mime_salida, NO mime_type de origen
    assert llamada["extra"] == {
        "tipo_fuente": "transcripcion_curso",
        "licencia": LICENCIA_POR_DEFECTO,
        "ruta_relativa": None,
    }


def test_captura_bronze_legacy_asocia_ruta_relativa_a_su_propia_carpeta_de_origen(
    tmp_path,
):
    """Dos ficheros en DOS carpetas legacy distintas (p.ej. las subcarpetas
    por ponente de `data/raw/transcriptions/{Demy,Pinol}/` — `DriveMonitor` no
    recursa, así que cada subcarpeta se vigila como una entrada propia de
    `carpetas`, ver `src/theory/drive_monitor.py`): la `ruta_relativa` de cada
    uno debe calcularse contra SU PROPIA carpeta de origen, no contra
    `carpetas[0]` ni una carpeta ajena — es la asociación (carpeta, path) que
    exige la SPEC."""
    carpeta_demy = tmp_path / "transcriptions" / "Demy"
    carpeta_pinol = tmp_path / "transcriptions" / "Pinol"
    carpeta_demy.mkdir(parents=True)
    carpeta_pinol.mkdir(parents=True)
    shutil.copy(SAMPLE_TXT, carpeta_demy / "sample_transcript.txt")
    shutil.copy(SAMPLE_PDF, carpeta_pinol / "sample_transcript.pdf")

    doc_store = _DocumentStoreEspia()

    resultado = run_pipeline(
        [carpeta_demy, carpeta_pinol],
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        document_store=doc_store,
    )

    assert resultado.ok
    por_nombre = {llamada["nombre"]: llamada for llamada in doc_store.llamadas}
    assert set(por_nombre) == {"sample_transcript.txt", "sample_transcript.pdf"}

    llamada_txt = por_nombre["sample_transcript.txt"]
    assert llamada_txt["ruta"] == carpeta_demy / "sample_transcript.txt"
    assert llamada_txt["drive_file_id"] is None
    assert llamada_txt["modified_time"] is None
    assert llamada_txt["extra"]["ruta_relativa"] == "sample_transcript.txt"
    assert llamada_txt["extra"]["tipo_fuente"] == "transcripcion_curso"
    assert llamada_txt["extra"]["licencia"] == LICENCIA_POR_DEFECTO

    llamada_pdf = por_nombre["sample_transcript.pdf"]
    assert llamada_pdf["ruta"] == carpeta_pinol / "sample_transcript.pdf"
    assert llamada_pdf["extra"]["ruta_relativa"] == "sample_transcript.pdf"
    assert llamada_pdf["extra"]["tipo_fuente"] == "teoria"


def test_fichero_bajo_staging_dir_no_se_captura_en_modo_legacy(tmp_path):
    """Un fichero físicamente bajo `drive_sync.staging_dir` (p.ej. quedó ahí
    de una descarga previa y `DriveMonitor` lo reporta como pendiente, aunque
    `sync_con_metadata()` no lo devuelva EN ESTE run) nunca se captura en modo
    legacy — evita la fila duplicada que documenta la SPEC (dos índices
    únicos parciales que no se ven entre sí)."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    shutil.copy(SAMPLE_TXT, staging_dir / "sample_transcript.txt")

    class _DriveSyncSinNovedades:
        def __init__(self, staging_dir: Path):
            self.staging_dir = staging_dir

        def sync_con_metadata(self) -> list[ArchivoSincronizado]:
            return []  # nada nuevo/modificado en Drive en este run

    fake_drive = _DriveSyncSinNovedades(staging_dir)
    doc_store = _DocumentStoreEspia()

    resultado = run_pipeline(
        None,
        directorio_procesado=tmp_path / "processed",
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        drive_sync=fake_drive,
        document_store=doc_store,
    )

    assert resultado.ok
    assert doc_store.llamadas == []
    assert {p.name for p in resultado.procesados} == {"sample_transcript.txt"}


def test_fallo_captura_bronze_no_tumba_el_batch_y_excluye_solo_el_fichero_fallido(
    entorno,
):
    """Decisión de diseño de la task 59 (documentada también en
    `pipeline.py` §"Etapa 0.5 opcional"): un fallo de `capturar()` se trata
    igual que un fallo de `_procesar_fichero` — va a `fallidos`, el fichero no
    se marca en `ruta_estado` y el resto del batch sigue su curso normal."""
    carpeta = entorno["carpetas"][0]
    ruta_pdf = carpeta / "sample_transcript.pdf"
    doc_store = _DocumentStoreEspia(fallos={ruta_pdf})

    resultado = _run(entorno, document_store=doc_store)

    assert not resultado.ok
    assert {f.path for f in resultado.fallidos} == {ruta_pdf}
    assert "captura Bronze" in resultado.fallidos[0].error
    assert {p.name for p in resultado.procesados} == {"sample_transcript.txt"}
    assert resultado.version_dir == entorno["directorio_procesado"] / "v1"

    manifest = json.loads((resultado.version_dir / "manifest.json").read_text())
    assert len(manifest["documents"]) == 1

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert "sample_transcript.pdf" not in estado
    assert "sample_transcript.txt" in estado
