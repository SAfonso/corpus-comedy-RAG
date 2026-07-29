"""Tests para pipeline (Flujo A, orquestador — task 22).

Contrato (`src/theory/SPEC.md` §Cadena de componentes/§Idempotencia y
versionado, `CHECKPOINTS.md`, ver también `pipeline.py` docstring):
`run_pipeline` encadena `DriveMonitor -> Parser -> SubtypeDetector ->
Cleaner -> LanguageNormalizer` (QualityScorer ya está embebido en
`_persistir_silver`, ver docstring de `pipeline.py`) sobre ficheros
nuevos/modificados, y marca cada fichero en `processed_files.json` SOLO tras
completar con éxito su propia `_procesar_fichero`.

**Task 63, P25 — retirada de `/data/processed/v{N}/`**: `run_pipeline` YA NO
invoca `generar_version` (ver `src/theory/pipeline.py` docstring, §"Task 63,
P25"). Los tests que dependían del batch `generar_version`/`documentos_listos`
(comprobar `manifest.json`, o "si `generar_version` falla, ningún fichero del
batch se marca como procesado") ya NO APLICAN — se han retirado o
reescrito para el modelo por-fichero-independiente: cada fichero se marca en
`processed_files.json` y persiste su Silver en cuanto termina su propia
`_procesar_fichero`, sin esperar al resto del batch. `resultado.version_dir`
es SIEMPRE `None` desde esta tarea (se mantiene el atributo solo por
compatibilidad del contrato de CLI, ver `ResultadoPipeline`).

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
`monkeypatch` sobre `pipeline._procesar_fichero` (mismo patrón que
`_traductor_que_no_debe_llamarse` en otros tests de Flujo A: inyectar un
fallo controlado, no fabricar un fixture corrupto) — el correcto
funcionamiento de cada etapa ya está cubierto por sus propios tests
unitarios; aquí se testea la ORQUESTACIÓN (marcado/reanudación), no las
etapas en sí.
"""
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.theory import pipeline as pipeline_mod
from src.theory.normalizers.format_normalizer import DocumentoEntrada, LICENCIA_POR_DEFECTO
from src.theory.normalizers.language_normalizer import FragmentoNormalizado
from src.theory.pipeline import run_pipeline
from src.utils.drive_sync import ArchivoSincronizado
from src.utils.quality_scorer import score_quality

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
        "ruta_estado": tmp_path / "state" / "processed_files.json",
    }


def _run(entorno, **kwargs):
    kwargs.setdefault("traductor", _traductor_fake)
    return run_pipeline(
        entorno["carpetas"],
        ruta_estado=entorno["ruta_estado"],
        **kwargs,
    )


# --- Cadena completa feliz ---------------------------------------------------


def test_cadena_completa_feliz_procesa_los_dos_ficheros(entorno):
    resultado = _run(entorno)

    assert resultado.ok
    assert resultado.version_dir is None  # SIEMPRE None desde la task 63
    assert {p.name for p in resultado.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }
    assert resultado.fallidos == []
    assert resultado.ignorados == []

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert set(estado.keys()) == {"sample_transcript.txt", "sample_transcript.pdf"}


def test_generar_version_ya_no_se_invoca_desde_pipeline(entorno):
    """Guardrail de regresión (task 63): `generar_version` ya no se importa
    en `pipeline.py` — si alguna vez se reintrodujera la llamada, este test
    dejaría de encontrar el símbolo ausente y fallaría al comprobarlo. La
    cadena sigue funcionando (Drive/legacy -> Bronze/Silver opcional) sin
    escribir ningún `v{N}/` en disco."""
    assert not hasattr(pipeline_mod, "generar_version")

    resultado = _run(entorno)
    assert resultado.ok
    assert {p.name for p in resultado.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }


def test_tipo_fuente_se_infiere_por_extension_de_origen(carpeta_books):
    """Unidad aislada de `_procesar_fichero` (sin pasar por `run_pipeline`,
    ya que `tipo_fuente` no viaja a ningún `manifest.json` desde la task 63 —
    se verifica directamente en el `DocumentoEntrada` devuelto)."""
    txt = carpeta_books / "sample_transcript.txt"
    pdf = carpeta_books / "sample_transcript.pdf"

    documento_txt = pipeline_mod._procesar_fichero(txt, traductor=_traductor_fake)
    documento_pdf = pipeline_mod._procesar_fichero(pdf, traductor=_traductor_fake)

    assert documento_txt.tipo_fuente == "transcripcion_curso"  # .txt (WhisperX)
    assert documento_pdf.tipo_fuente == "teoria"  # .pdf


def test_fuente_se_deriva_del_nombre_de_fichero(carpeta_books):
    txt = carpeta_books / "sample_transcript.txt"
    documento = pipeline_mod._procesar_fichero(txt, traductor=_traductor_fake)
    assert documento.fuente == "sample transcript"


def test_no_toca_el_fichero_original(entorno):
    carpeta = entorno["carpetas"][0]
    txt_path = carpeta / "sample_transcript.txt"
    contenido_antes = txt_path.read_bytes()

    _run(entorno)

    assert txt_path.read_bytes() == contenido_antes


# --- Segunda ejecución sin cambios -------------------------------------------


def test_segunda_ejecucion_sin_cambios_no_reprocesa_nada(entorno):
    _run(entorno)
    resultado2 = _run(entorno)

    assert resultado2.version_dir is None
    assert resultado2.procesados == []
    assert resultado2.fallidos == []


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

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert set(estado.keys()) == {"sample_transcript.txt", "sample_transcript.pdf"}


# --- Independencia por fichero: un fallo de Silver no afecta al otro fichero -


def test_fichero_independiente_no_afecta_a_los_demas_del_batch(entorno, monkeypatch):
    """Task 63: ya no hay una fase batch (`generar_version`) que agrupe el
    run entero — el fichero que falla en `_procesar_fichero` no impide que
    el resto del batch complete su propia cadena y quede marcado, ni
    viceversa. Este es el escenario que antes cubría
    `test_fallo_en_generar_version_no_marca_ningun_fichero_del_batch`, que ya
    no aplica (no hay ningún paso batch que pueda tumbar el grupo entero)."""
    original = pipeline_mod._procesar_fichero
    llamados = []

    def _procesar_registrando(path, traductor):
        llamados.append(path.name)
        if path.name == "sample_transcript.pdf":
            raise RuntimeError("fallo simulado en un fichero del batch")
        return original(path, traductor)

    monkeypatch.setattr(pipeline_mod, "_procesar_fichero", _procesar_registrando)

    resultado = _run(entorno)

    # Ambos ficheros se intentaron (el fallo de uno no corta el bucle).
    assert set(llamados) == {"sample_transcript.txt", "sample_transcript.pdf"}
    # El que tuvo éxito queda marcado — no depende del que falló.
    assert {p.name for p in resultado.procesados} == {"sample_transcript.txt"}
    assert {f.path.name for f in resultado.fallidos} == {"sample_transcript.pdf"}


# --- Ficheros sin Parser conocido (p.ej. .gitkeep) ---------------------------


def test_fichero_sin_parser_conocido_se_ignora_y_no_se_reintenta(tmp_path):
    carpeta = tmp_path / "books"
    carpeta.mkdir()
    shutil.copy(SAMPLE_TXT, carpeta / "sample_transcript.txt")
    (carpeta / ".gitkeep").write_text("")

    ruta_estado = tmp_path / "state" / "processed_files.json"

    resultado1 = run_pipeline(
        [carpeta],
        ruta_estado=ruta_estado,
        traductor=_traductor_fake,
    )
    assert {p.name for p in resultado1.ignorados} == {".gitkeep"}
    assert resultado1.fallidos == []
    assert {p.name for p in resultado1.procesados} == {"sample_transcript.txt"}

    resultado2 = run_pipeline(
        [carpeta],
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
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        drive_sync=fake,
    )

    assert fake.sync_llamado is True
    assert resultado.ok
    assert {p.name for p in resultado.procesados} == {"sample_transcript.txt"}
    assert resultado.version_dir is None


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
    en `fallos` o para toda una `capa` en `fallos_capa` (mismo patrón que
    `_procesar_con_fallo` de arriba: fallo controlado por monkeypatch/
    inyección, no un fixture corrupto).

    Devuelve un `ResultadoCaptura` doble (`SimpleNamespace` con `hash_md5`
    real del contenido leído, task 60, y `ya_existia` — parametrizable por
    ruta vía `existentes`, task 63, para testear los contadores
    `bronze_nuevos`/`bronze_existentes`/`silver_nuevos`/`silver_existentes`).
    """

    def __init__(
        self,
        fallos: set = frozenset(),
        fallos_capa: frozenset = frozenset(),
        existentes: set = frozenset(),
    ):
        self.llamadas: list[dict] = []
        self._fallos = fallos
        self._fallos_capa = fallos_capa
        self._existentes = existentes

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
        llamada = {
            "ruta": ruta,
            "capa": capa,
            "flujo": flujo,
            "drive_file_id": drive_file_id,
            "modified_time": modified_time,
            "nombre": nombre,
            "mime_type": mime_type,
            "extra": extra,
        }
        if capa == "silver":
            # Silver siempre sube texto (`.md`); Bronze puede ser binario
            # (p.ej. `.pdf`) y no se lee como texto aquí.
            llamada["contenido"] = Path(ruta).read_text(encoding="utf-8")
        self.llamadas.append(llamada)

        if ruta in self._fallos or capa in self._fallos_capa:
            raise RuntimeError(f"fallo simulado capturando {ruta} (capa={capa})")
        return SimpleNamespace(
            hash_md5=hashlib.md5(Path(ruta).read_bytes()).hexdigest(),
            ya_existia=ruta in self._existentes,
        )


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
    assert resultado.bronze_activo is False
    assert resultado.silver_activo is False
    assert resultado.bronze_nuevos == 0
    assert resultado.bronze_existentes == 0
    assert resultado.silver_nuevos == 0
    assert resultado.silver_existentes == 0


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
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        drive_sync=fake_drive,
        document_store=doc_store,
    )

    assert resultado.ok
    # Filtra a `capa == "bronze"`: con `document_store` inyectado, este mismo
    # doble también recibe la captura Silver (task 60) de este documento — no
    # es lo que este test verifica (ver tests de la §Persistencia Silver).
    llamadas_bronze = [l for l in doc_store.llamadas if l["capa"] == "bronze"]
    assert len(llamadas_bronze) == 1
    llamada = llamadas_bronze[0]
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

    assert resultado.bronze_activo is True
    assert resultado.bronze_nuevos == 1
    assert resultado.bronze_existentes == 0


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
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        document_store=doc_store,
    )

    assert resultado.ok
    # Filtra a `capa == "bronze"`: con `document_store` inyectado, este mismo
    # doble también recibe la captura Silver (task 60, otro `nombre` —
    # `.md` — que colisionaría en este dict si no se filtrara).
    llamadas_bronze = [l for l in doc_store.llamadas if l["capa"] == "bronze"]
    por_nombre = {llamada["nombre"]: llamada for llamada in llamadas_bronze}
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

    assert resultado.bronze_nuevos == 2
    assert resultado.bronze_existentes == 0


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
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        drive_sync=fake_drive,
        document_store=doc_store,
    )

    assert resultado.ok
    # Ninguna captura BRONZE ocurre para este fichero (ni Drive ni legacy, ver
    # docstring del test) — sí ocurre una captura Silver (task 60, §"caso raro
    # sin captura Bronze en este run": recalcula el hash directamente, ver
    # `pipeline._persistir_silver`), que no es lo que este test verifica.
    assert [l for l in doc_store.llamadas if l["capa"] == "bronze"] == []
    assert {p.name for p in resultado.procesados} == {"sample_transcript.txt"}
    assert resultado.bronze_nuevos == 0
    assert resultado.bronze_existentes == 0


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

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert "sample_transcript.pdf" not in estado
    assert "sample_transcript.txt" in estado


def test_bronze_nuevos_y_existentes_se_cuentan_segun_ya_existia(entorno):
    """Task 63: `ResultadoPipeline.bronze_nuevos`/`bronze_existentes` cuentan
    `ResultadoCaptura.ya_existia` de cada captura Bronze — un fichero cuya
    captura ya existía (segunda pasada, p.ej.) cuenta como "existente", no
    como "nuevo"."""
    carpeta = entorno["carpetas"][0]
    ruta_txt = carpeta / "sample_transcript.txt"
    doc_store = _DocumentStoreEspia(existentes={ruta_txt})

    resultado = _run(entorno, document_store=doc_store)

    assert resultado.ok
    assert resultado.bronze_activo is True
    assert resultado.bronze_nuevos == 1  # solo el .pdf
    assert resultado.bronze_existentes == 1  # el .txt, marcado "existente"


# --- Persistencia Silver, etapa 1 (task 60, P25) -----------------------------
#
# Contrato exacto en `src/theory/SPEC.md` §"`silver.teoria_documentos` — qué
# añade teoría vía `extra`" y §"La fila Silver no reutiliza el par de Drive de
# su Bronze — y por qué": con `document_store` inyectado (mismo interruptor
# que Bronze), cada documento que sobrevive a su propia `_procesar_fichero`
# con éxito se vuelca TAMBIÉN a `silver.teoria_documentos`, siempre en modo
# legacy (`drive_file_id=modified_time=None`), con el linaje hacia su Bronze
# de origen en `extra.origen_*`. Un fallo de esa captura no tumba el batch ni
# retira el fichero de `procesados`/`ruta_estado` (Bronze ya se completó con
# éxito para él).


def _llamadas_silver(doc_store: "_DocumentStoreEspia") -> list[dict]:
    return [llamada for llamada in doc_store.llamadas if llamada["capa"] == "silver"]


def test_persistir_silver_calcula_metadatos_correctos_de_documento_conocido(tmp_path):
    """Unidad aislada de `_persistir_silver` (sin pasar por todo `run_pipeline`):
    verifica que `idioma_original` (del PRIMER fragmento), `traducido` (True si
    ALGÚN fragmento se tradujo), `num_fragmentos` y `quality_score` (misma
    agregación que antes calculaba `generar_version` para `stats.json`, ahora
    calculada directamente aquí, ver `pipeline.py`) se calculan correctamente
    de un `DocumentoEntrada` con fragmentos conocidos, y que el contenido
    subido es exactamente el de `render_document`. También verifica que
    `_persistir_silver` DEVUELVE el `ResultadoCaptura` de su propia llamada
    (task 63, antes no devolvía nada)."""
    fragmentos = [
        FragmentoNormalizado(
            texto="Este es un fragmento de ejemplo bastante largo para tener buen quality score.",
            subtipo="explicacion",
            idioma_original="es",
            idioma_fragmento="es",
            traducido=False,
        ),
        FragmentoNormalizado(
            texto="This is another fragment, originally in English, later translated to Spanish.",
            subtipo="ejemplo",
            idioma_original="en",
            idioma_fragmento="es",
            traducido=True,
        ),
    ]
    documento = DocumentoEntrada(
        fragmentos=fragmentos,
        fuente="Fuente De Prueba",
        tipo_fuente="teoria",
        autor=None,
        licencia=LICENCIA_POR_DEFECTO,
    )

    path_original = tmp_path / "original.pdf"
    path_original.write_bytes(b"contenido binario original de prueba")
    hash_original = hashlib.md5(path_original.read_bytes()).hexdigest()

    doc_store = _DocumentStoreEspia()
    origen_bronze = {path_original: (hash_original, None, None)}

    resultado_silver = pipeline_mod._persistir_silver(
        doc_store, path_original, documento, origen_bronze
    )

    assert len(doc_store.llamadas) == 1
    llamada = doc_store.llamadas[0]
    assert llamada["capa"] == "silver"
    assert llamada["flujo"] == "teoria"
    assert llamada["drive_file_id"] is None
    assert llamada["modified_time"] is None
    assert llamada["nombre"] == "fuente-de-prueba.md"
    assert llamada["mime_type"] == "text/markdown"
    # El temporal se limpia al salir del `with` — ya no existe tras la llamada.
    assert not Path(llamada["ruta"]).exists()

    # `_persistir_silver` devuelve el `ResultadoCaptura` (task 63): su
    # `hash_md5` es el del `.md` renderizado (subido por la propia llamada),
    # no el del original.
    assert resultado_silver.ya_existia is False
    assert resultado_silver.hash_md5 == hashlib.md5(
        llamada["contenido"].encode("utf-8")
    ).hexdigest()

    from src.theory.normalizers.format_normalizer import render_document

    esperado_contenido = render_document(
        fragmentos, fuente="Fuente De Prueba", tipo_fuente="teoria", autor=None,
        licencia=LICENCIA_POR_DEFECTO,
    )
    assert llamada["contenido"] == esperado_contenido

    extra = llamada["extra"]
    assert extra["origen_hash_md5"] == hash_original
    assert extra["origen_drive_file_id"] is None
    assert extra["origen_modified_time"] is None
    assert extra["fuente"] == "Fuente De Prueba"
    assert extra["autor"] is None
    assert extra["tipo_fuente"] == "teoria"
    assert extra["licencia"] == LICENCIA_POR_DEFECTO
    assert extra["idioma_original"] == "es"  # del PRIMER fragmento, no el segundo (en)
    assert extra["traducido"] is True  # al menos un fragmento (el segundo) se tradujo
    assert extra["num_fragmentos"] == 2

    esperado_quality = (score_quality(fragmentos[0].texto) + score_quality(fragmentos[1].texto)) / 2
    assert extra["quality_score"] == pytest.approx(esperado_quality)


def test_persistencia_silver_legacy_sube_un_md_por_documento_con_linaje_correcto(entorno):
    """Extremo a extremo (modo legacy, `carpeta_books` con dos ficheros): cada
    documento que termina su `_procesar_fichero` con éxito genera UNA captura
    Silver, siempre con `drive_file_id=modified_time=None`, y
    `extra.origen_hash_md5` es el `hash_md5` del ORIGINAL (no el del `.md`)."""
    carpeta = entorno["carpetas"][0]
    txt_original = carpeta / "sample_transcript.txt"
    pdf_original = carpeta / "sample_transcript.pdf"
    doc_store = _DocumentStoreEspia()

    resultado = _run(entorno, document_store=doc_store)

    assert resultado.ok
    silver_llamadas = _llamadas_silver(doc_store)
    assert len(silver_llamadas) == 2

    por_tipo = {llamada["extra"]["tipo_fuente"]: llamada for llamada in silver_llamadas}
    assert set(por_tipo) == {"transcripcion_curso", "teoria"}

    llamada_txt = por_tipo["transcripcion_curso"]
    assert llamada_txt["capa"] == "silver"
    assert llamada_txt["flujo"] == "teoria"
    assert llamada_txt["drive_file_id"] is None
    assert llamada_txt["modified_time"] is None
    assert llamada_txt["nombre"] == "sample-transcript.md"
    assert llamada_txt["mime_type"] == "text/markdown"
    assert llamada_txt["extra"]["origen_hash_md5"] == hashlib.md5(
        txt_original.read_bytes()
    ).hexdigest()
    assert llamada_txt["extra"]["origen_drive_file_id"] is None
    assert llamada_txt["extra"]["origen_modified_time"] is None
    assert llamada_txt["extra"]["licencia"] == LICENCIA_POR_DEFECTO

    llamada_pdf = por_tipo["teoria"]
    assert llamada_pdf["extra"]["origen_hash_md5"] == hashlib.md5(
        pdf_original.read_bytes()
    ).hexdigest()
    assert llamada_pdf["extra"]["origen_drive_file_id"] is None
    assert llamada_pdf["extra"]["origen_modified_time"] is None

    # No afecta al resto del pipeline: el marcado sigue igual, sin v{N}.
    assert resultado.version_dir is None
    assert {p.name for p in resultado.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }
    assert resultado.silver_activo is True
    assert resultado.silver_nuevos == 2
    assert resultado.silver_existentes == 0


def test_persistencia_silver_modo_drive_usa_el_linaje_drive_en_extra(tmp_path):
    """Cuando el original viene de Drive, `extra.origen_drive_file_id`/
    `extra.origen_modified_time` de la fila Silver son los de ESE Bronze —
    aun cuando la propia fila Silver se escribe en modo legacy
    (`drive_file_id=modified_time=None` en la llamada de captura)."""

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
                    file_id="drive-file-456",
                    name="sample_transcript.txt",
                    modified_time="2026-07-29T11:00:00.000Z",
                    mime_type="application/vnd.google-apps.document",
                    mime_salida="text/plain",
                )
            ]

    staging_dir = tmp_path / "staging"
    fake_drive = _DriveSyncConArchivo(staging_dir)
    doc_store = _DocumentStoreEspia()

    resultado = run_pipeline(
        None,
        ruta_estado=tmp_path / "state" / "processed_files.json",
        traductor=_traductor_fake,
        drive_sync=fake_drive,
        document_store=doc_store,
    )

    assert resultado.ok
    silver_llamadas = _llamadas_silver(doc_store)
    assert len(silver_llamadas) == 1
    llamada = silver_llamadas[0]

    # La fila Silver en sí sigue en modo legacy (decisión task 51).
    assert llamada["drive_file_id"] is None
    assert llamada["modified_time"] is None

    extra = llamada["extra"]
    assert extra["origen_drive_file_id"] == "drive-file-456"
    assert extra["origen_modified_time"] == "2026-07-29T11:00:00.000Z"
    assert extra["origen_hash_md5"] == hashlib.md5(
        (staging_dir / "sample_transcript.txt").read_bytes()
    ).hexdigest()


def test_fallo_captura_silver_no_impide_marcar_el_fichero_como_procesado(entorno):
    """Decisión de esta task (documentada también en `pipeline.py` §"Etapa 1:
    persistencia Silver"): a diferencia de un fallo de captura Bronze, un
    fallo de captura Silver NO retira el fichero de `procesados` ni de
    `ruta_estado` — Bronze ya se completó con éxito para él, así que el
    siguiente run no debe reprocesarlo desde cero. Sí se registra en
    `fallidos`."""
    doc_store = _DocumentStoreEspia(fallos_capa={"silver"})

    resultado = _run(entorno, document_store=doc_store)

    assert not resultado.ok  # hay fallos (los de la captura Silver)
    assert {p.name for p in resultado.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }
    assert len(resultado.fallidos) == 2
    assert all("captura Silver fallida" in f.error for f in resultado.fallidos)

    estado = json.loads(entorno["ruta_estado"].read_text())
    assert set(estado.keys()) == {"sample_transcript.txt", "sample_transcript.pdf"}

    # Silver falló para ambos: ningún nuevo/existente se cuenta.
    assert resultado.silver_nuevos == 0
    assert resultado.silver_existentes == 0


def test_document_store_none_no_persiste_silver_tampoco(entorno):
    """Regresión explícita (además de `test_document_store_none_no_cambia_el_
    comportamiento`, que ya cubre Bronze): `document_store=None` deja también
    la persistencia Silver completamente desactivada — nada que capturar, sin
    doble ni llamada que verificar más allá de que el pipeline termine ok."""
    resultado = _run(entorno, document_store=None)

    assert resultado.ok
    assert {p.name for p in resultado.procesados} == {
        "sample_transcript.txt",
        "sample_transcript.pdf",
    }


def test_silver_nuevos_y_existentes_se_cuentan_segun_ya_existia(entorno):
    """Task 63: `ResultadoPipeline.silver_nuevos`/`silver_existentes` cuentan
    `ResultadoCaptura.ya_existia` de la captura Silver de `_persistir_silver`
    — independiente de los contadores Bronze (que pueden diferir, p.ej. un
    original Bronze nuevo cuyo `.md` renderizado ya existía en Silver de un
    run anterior con contenido idéntico)."""
    carpeta = entorno["carpetas"][0]
    # `_DocumentStoreEspia._existentes` compara por `ruta` — en la captura
    # Silver, `ruta` es el fichero TEMPORAL (nombre no determinista), así que
    # en su lugar forzamos "ya_existia" para TODAS las capturas Silver
    # marcando la propia carpeta de origen fuera de `_existentes` (Bronze
    # nuevo) y comprobando el conteo Silver por separado con un doble
    # dedicado que sí distingue por capa.

    class _DocumentStoreSilverExistente(_DocumentStoreEspia):
        def capturar(self, ruta, capa, flujo, **kwargs):
            resultado_base = super().capturar(ruta, capa, flujo, **kwargs)
            if capa == "silver":
                return SimpleNamespace(
                    hash_md5=resultado_base.hash_md5, ya_existia=True
                )
            return resultado_base

    doc_store = _DocumentStoreSilverExistente()

    resultado = _run(entorno, document_store=doc_store)

    assert resultado.ok
    assert resultado.silver_activo is True
    assert resultado.silver_nuevos == 0
    assert resultado.silver_existentes == 2
    # Bronze no se ve afectado por el `ya_existia` forzado de Silver.
    assert resultado.bronze_nuevos == 2
    assert resultado.bronze_existentes == 0
