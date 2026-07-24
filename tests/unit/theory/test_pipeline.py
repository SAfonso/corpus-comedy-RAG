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
from src.theory.pipeline import run_pipeline

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
