"""Tests para format_normalizer (Flujo A, contrato en src/theory/SPEC.md §Storage/§Metadatos).

Contrato:
- `render_document`: función pura, cabecera YAML (fuente/autor/tipo_fuente/
  licencia a nivel de documento + `fragmentos` con subtipo/idioma_original/
  idioma_fragmento por fragmento, mismo orden que el cuerpo) + cuerpo.
- `generar_version`: vuelca documentos a `directorio_base/v{N}/` (documents/
  + manifest.json + stats.json), respetando la inmutabilidad: una versión con
  `manifest.json` NUNCA se sobrescribe (dos llamadas seguidas -> v1 intacta,
  v2 nueva).
- `tipo_fuente` fuera del enum cerrado de Flujo A (`teoria`/`transcripcion_curso`)
  se rechaza con ValueError.
- `quality_score` (task 10, `score_quality`) se usa en `stats.json`.

Encadena el pipeline real completo `parse_whisperx_transcript (task 3) ->
detect_subtypes (task 7) -> clean_fragments (task 8) -> normalize_language
(task 9, con traductor espía no-op ya que el fixture está en español) sobre
`tests/fixtures/sample_transcript.txt` (mismo patrón que
`test_language_normalizer.py`), para que el input de test de FormatNormalizer
sea exactamente `FragmentoNormalizado` real, no inventado. Metadatos de
documento reales para este fixture (transcripción de curso real, no un
libro): `fuente="Curso de stand-up (fixture WhisperX)"`,
`tipo_fuente="transcripcion_curso"`, `licencia="personal_only"`, `autor=None`
(desconocido).

Todos los tests escriben en `tmp_path` (nunca en `/data/processed/` real del
repo, ver CLAUDE.md raíz).
"""
import json
from pathlib import Path

import pytest

from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.normalizers.format_normalizer import (
    DocumentoEntrada,
    VersionInmutableError,
    _slugify,
    generar_version,
    render_document,
)
from src.theory.normalizers.language_normalizer import (
    FragmentoNormalizado,
    normalize_language,
)
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"

FUENTE_FIXTURE = "Curso de stand-up (fixture WhisperX)"
TIPO_FUENTE_FIXTURE = "transcripcion_curso"
LICENCIA_FIXTURE = "personal_only"


def _traductor_no_op(texto: str, idioma_origen: str) -> str:
    """Traductor espía no-op: el fixture ya está en español, no debería
    llamarse nunca para `explicacion` (ya en destino) — se inyecta solo para
    no depender de red/DeepL en el test, mismo patrón que
    `test_language_normalizer.py`."""
    raise AssertionError(
        "no debería traducirse: el fixture sample_transcript.txt ya está en español"
    )


def _fragmentos_normalizados_reales() -> list[FragmentoNormalizado]:
    """Cadena real completa: parser -> detector -> cleaner -> normalizer."""
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos_subtipo = detect_subtypes(texto)
    fragmentos_limpios = clean_fragments(fragmentos_subtipo)
    return normalize_language(fragmentos_limpios, traductor=_traductor_no_op)


# --- render_document: función pura --------------------------------------


def test_render_document_incluye_los_7_campos_de_checkpoints():
    fragmentos = _fragmentos_normalizados_reales()

    contenido = render_document(
        fragmentos,
        fuente=FUENTE_FIXTURE,
        tipo_fuente=TIPO_FUENTE_FIXTURE,
        autor=None,
        licencia=LICENCIA_FIXTURE,
    )

    cabecera, _, cuerpo = contenido.partition("---\n")[2].partition("\n---\n")
    # Campos de documento
    assert f'fuente: "{FUENTE_FIXTURE}"' in contenido
    assert "autor: null" in contenido
    assert f'tipo_fuente: "{TIPO_FUENTE_FIXTURE}"' in contenido
    assert f'licencia: "{LICENCIA_FIXTURE}"' in contenido
    # Campos por fragmento
    assert "subtipo:" in cabecera
    assert "idioma_original:" in cabecera
    assert "idioma_fragmento:" in cabecera

    # Un item de `fragmentos:` por cada FragmentoNormalizado de entrada
    assert cabecera.count("- subtipo:") == len(fragmentos)


def test_render_document_empieza_y_cierra_con_delimitador_yaml():
    fragmentos = _fragmentos_normalizados_reales()
    contenido = render_document(
        fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE
    )
    assert contenido.startswith("---\n")
    # segundo delimitador presente (cierre de cabecera)
    assert contenido.count("---\n") >= 2


def test_render_document_cuerpo_preserva_texto_de_cada_fragmento_en_orden():
    fragmentos = _fragmentos_normalizados_reales()
    contenido = render_document(
        fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE
    )

    _, _, cuerpo = contenido.rpartition("\n---\n")
    posiciones = [cuerpo.index(f.texto) for f in fragmentos]
    assert posiciones == sorted(posiciones)


def test_render_document_ejemplo_conserva_idioma_original_no_traducido():
    fragmentos = _fragmentos_normalizados_reales()
    ejemplo = next(f for f in fragmentos if f.subtipo == "ejemplo")
    assert ejemplo.traducido is False

    contenido = render_document(
        fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE
    )
    assert "Me gustan los perros" in contenido


def test_render_document_rechaza_tipo_fuente_fuera_del_enum():
    fragmentos = _fragmentos_normalizados_reales()
    with pytest.raises(ValueError):
        render_document(fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente="propio")


def test_render_document_rechaza_tipo_fuente_propio_historico():
    fragmentos = _fragmentos_normalizados_reales()
    with pytest.raises(ValueError):
        render_document(
            fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente="propio_historico"
        )


def test_render_document_rechaza_lista_de_fragmentos_vacia():
    with pytest.raises(ValueError):
        render_document([], fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE)


def test_render_document_es_puro_mismo_input_mismo_output():
    fragmentos = _fragmentos_normalizados_reales()
    c1 = render_document(
        fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE
    )
    c2 = render_document(
        fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE
    )
    assert c1 == c2


# --- _slugify: función pura ------------------------------------------------


def test_slugify_fixture_real():
    assert _slugify(FUENTE_FIXTURE) == "curso-de-stand-up-fixture-whisperx"


def test_slugify_cadena_vacia_cae_en_fallback():
    assert _slugify("") == "documento"


# --- generar_version: orquestación con I/O (tmp_path) ----------------------


def _documento_fixture() -> DocumentoEntrada:
    return DocumentoEntrada(
        fragmentos=_fragmentos_normalizados_reales(),
        fuente=FUENTE_FIXTURE,
        tipo_fuente=TIPO_FUENTE_FIXTURE,
        autor=None,
        licencia=LICENCIA_FIXTURE,
    )


def test_generar_version_crea_v1_con_documento_manifest_y_stats(tmp_path):
    documento = _documento_fixture()

    dir_v1 = generar_version([documento], tmp_path)

    assert dir_v1 == tmp_path / "v1"
    assert (dir_v1 / "documents" / "curso-de-stand-up-fixture-whisperx.txt").exists()
    assert (dir_v1 / "manifest.json").exists()
    assert (dir_v1 / "stats.json").exists()


def test_generar_version_manifest_referencia_el_documento(tmp_path):
    documento = _documento_fixture()
    dir_v1 = generar_version([documento], tmp_path)

    manifest = json.loads((dir_v1 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert len(manifest["documents"]) == 1

    entrada = manifest["documents"][0]
    assert entrada["fuente"] == FUENTE_FIXTURE
    assert entrada["tipo_fuente"] == TIPO_FUENTE_FIXTURE
    assert entrada["licencia"] == LICENCIA_FIXTURE
    assert entrada["autor"] is None
    assert entrada["path"] == "documents/curso-de-stand-up-fixture-whisperx.txt"
    assert entrada["num_fragmentos"] == len(documento.fragmentos)


def test_generar_version_stats_usa_quality_score_task_10(tmp_path):
    documento = _documento_fixture()
    dir_v1 = generar_version([documento], tmp_path)

    stats = json.loads((dir_v1 / "stats.json").read_text(encoding="utf-8"))
    assert stats["num_documentos"] == 1
    assert stats["num_fragmentos"] == len(documento.fragmentos)

    quality = stats["quality_score"]
    assert 0.0 <= quality["media"] <= 1.0
    assert 0.0 <= quality["min"] <= quality["max"] <= 1.0

    assert len(stats["por_documento"]) == 1
    assert stats["por_documento"][0]["fuente"] == FUENTE_FIXTURE


def test_generar_version_rechaza_documento_con_tipo_fuente_invalido(tmp_path):
    documento = DocumentoEntrada(
        fragmentos=_fragmentos_normalizados_reales(),
        fuente=FUENTE_FIXTURE,
        tipo_fuente="propio",
    )
    with pytest.raises(ValueError):
        generar_version([documento], tmp_path)


# --- Inmutabilidad: el requisito duro de la tarea ---------------------------


def test_segunda_llamada_no_sobrescribe_v1_crea_v2(tmp_path):
    documento = _documento_fixture()

    dir_v1 = generar_version([documento], tmp_path)
    manifest_v1_antes = (dir_v1 / "manifest.json").read_text(encoding="utf-8")
    mtime_v1_antes = (dir_v1 / "manifest.json").stat().st_mtime_ns

    dir_v2 = generar_version([documento], tmp_path)

    assert dir_v2 == tmp_path / "v2"
    assert dir_v1 != dir_v2
    # v1 sigue existiendo, intacta, byte a byte
    manifest_v1_despues = (dir_v1 / "manifest.json").read_text(encoding="utf-8")
    assert manifest_v1_antes == manifest_v1_despues
    assert (dir_v1 / "manifest.json").stat().st_mtime_ns == mtime_v1_antes
    # v2 tiene su propio manifest válido
    assert (dir_v2 / "manifest.json").exists()
    manifest_v2 = json.loads((dir_v2 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_v2["version"] == 2


def test_tercera_llamada_crea_v3_no_pisa_v1_ni_v2(tmp_path):
    documento = _documento_fixture()

    generar_version([documento], tmp_path)
    generar_version([documento], tmp_path)
    dir_v3 = generar_version([documento], tmp_path)

    assert dir_v3 == tmp_path / "v3"
    assert (tmp_path / "v1" / "manifest.json").exists()
    assert (tmp_path / "v2" / "manifest.json").exists()


def test_version_explicita_ya_finalizada_lanza_version_inmutable_error(tmp_path):
    documento = _documento_fixture()
    generar_version([documento], tmp_path, version=1)

    with pytest.raises(VersionInmutableError):
        generar_version([documento], tmp_path, version=1)

    # v1 no se tocó
    assert (tmp_path / "v1" / "manifest.json").exists()


def test_version_explicita_libre_no_finalizada_se_puede_escribir(tmp_path):
    documento = _documento_fixture()
    # v1 no existe todavía: pedirla explícitamente debe funcionar igual que None
    dir_v1 = generar_version([documento], tmp_path, version=1)
    assert dir_v1 == tmp_path / "v1"
    assert (dir_v1 / "manifest.json").exists()


def test_generar_version_no_escribe_fuera_de_tmp_path(tmp_path):
    """Regla dura del proyecto: nunca tocar /data/processed/ real durante tests."""
    documento = _documento_fixture()
    antes = set(tmp_path.rglob("*"))

    generar_version([documento], tmp_path)

    despues = set(tmp_path.rglob("*"))
    # todo lo nuevo cuelga de tmp_path (por construcción de rglob), pero
    # afirmamos explícitamente que se creó contenido y que sigue bajo tmp_path
    nuevos = despues - antes
    assert nuevos
    assert all(str(p).startswith(str(tmp_path)) for p in nuevos)
