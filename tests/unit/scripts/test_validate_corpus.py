"""Tests para `scripts/validate_corpus.py` (task 20, gate de validación de
`/data/processed/v{N}/`, contrato en `ROADMAP_DATA_PIPELINE.md` §"Checks de
Validación del Corpus Final" + `CHECKPOINTS.md`).

Regla del proyecto (nunca fixtures inventadas): la estructura `v{N}/` de
partida se genera con la cadena real YA APROBADA — `parse_whisperx_transcript`
(task 3) -> `detect_subtypes` (task 7) -> `clean_fragments` (task 8) ->
`normalize_language` (task 9, traductor espía no-op) -> `generar_version`
(task 11) — sobre `tests/fixtures/sample_transcript.txt`, exactamente el
mismo patrón que `tests/unit/theory/test_format_normalizer.py`.

El fixture real (9 fragmentos, 88 palabras totales) es más corto que
`MIN_PALABRAS`, así que cada documento del caso feliz repite la lista de
fragmentos reales dos veces (mismo texto real, ningún contenido inventado)
para superar el umbral de 100 palabras sin necesitar un fixture más largo.
Los casos de FALLO de cada check se construyen mutando una COPIA de esa
estructura real ya generada (borra un campo, trunca/repite texto, cambia un
valor de un campo real) en vez de inventar ficheros nuevos desde cero — mismo
enfoque que la task 17 (caso de fallo del round-trip).
"""
import json
import shutil
from pathlib import Path

import pytest

from scripts.validate_corpus import (
    DocumentoLeido,
    cargar_documentos,
    cargar_manifest,
    check_cabecera_completa,
    check_idiomas_permitidos,
    check_manifest_sincronizado,
    check_max_palabras,
    check_min_palabras,
    check_sin_duplicados,
    check_sin_speaker_tags,
    check_sin_timestamps,
    encontrar_ultima_version,
    leer_documento,
    validar_version,
)
from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.normalizers.format_normalizer import DocumentoEntrada, generar_version
from src.theory.normalizers.language_normalizer import FragmentoNormalizado, normalize_language
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"


def _traductor_no_op(texto: str, idioma_origen: str) -> str:
    raise AssertionError(
        "no debería traducirse: el fixture sample_transcript.txt ya está en español"
    )


def _fragmentos_reales() -> list[FragmentoNormalizado]:
    """Cadena real completa (mismo patrón que test_format_normalizer.py):
    9 fragmentos reales, 88 palabras totales."""
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos_subtipo = detect_subtypes(texto)
    fragmentos_limpios = clean_fragments(fragmentos_subtipo)
    return normalize_language(fragmentos_limpios, traductor=_traductor_no_op)


def _documento_real(fuente: str, repeticiones: int = 2) -> DocumentoEntrada:
    """Un `DocumentoEntrada` real cuyo cuerpo repite los fragmentos reales
    `repeticiones` veces (176 palabras con `repeticiones=2`, > MIN_PALABRAS)
    — mismo texto real, sin inventar contenido nuevo."""
    fragmentos = _fragmentos_reales() * repeticiones
    return DocumentoEntrada(
        fragmentos=fragmentos,
        fuente=fuente,
        tipo_fuente="transcripcion_curso",
        autor=None,
        licencia="personal_only",
    )


def _generar_v1_real(tmp_path: Path) -> Path:
    """Estructura `v1/` real y válida: 2 documentos reales, distinta `fuente`
    (headers distintos -> contenido de fichero distinto, no hay duplicados),
    cada uno por encima de MIN_PALABRAS y por debajo de MAX_PALABRAS."""
    doc1 = _documento_real("Curso de stand-up (fixture WhisperX) - Sesión 1")
    doc2 = _documento_real("Curso de stand-up (fixture WhisperX) - Sesión 2")
    return generar_version([doc1, doc2], tmp_path)


def _copiar_version(v1_dir: Path, destino: Path) -> Path:
    """Copia la versión real generada a un directorio nuevo, para mutarla sin
    tocar el original (cada test de fallo parte de su propia copia)."""
    shutil.copytree(v1_dir, destino)
    return destino


# --- Caso feliz: la estructura real pasa los 8 checks -----------------------


def test_validar_version_caso_feliz_todos_los_checks_pasan(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)

    resultados = validar_version(v1_dir)

    nombres = {r.nombre for r in resultados}
    assert nombres == {
        "sin_timestamps",
        "sin_speaker_tags",
        "cabecera_completa",
        "min_palabras",
        "max_palabras",
        "sin_duplicados",
        "idiomas_permitidos",
        "manifest_sincronizado",
    }
    for resultado in resultados:
        assert resultado.ok, f"{resultado.nombre} falló inesperadamente: {resultado.detalles}"


def test_cargar_documentos_lee_los_dos_ficheros_reales(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    assert len(documentos) == 2
    assert all(isinstance(d, DocumentoLeido) for d in documentos)


def test_leer_documento_parsea_cabecera_y_cuerpo_reales(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    doc = documentos[0]

    assert doc.metadatos["tipo_fuente"] == "transcripcion_curso"
    assert doc.metadatos["licencia"] == "personal_only"
    assert doc.metadatos["autor"] is None
    assert len(doc.fragmentos_meta) == 18  # 9 fragmentos reales x 2 repeticiones
    assert doc.fragmentos_meta[0]["subtipo"] in ("explicacion", "ejemplo")
    assert "Hola, bienvenidas y bienvenidos a este curso." in doc.cuerpo


# --- Check 1: timestamps ----------------------------------------------------


def test_check_sin_timestamps_falla_si_queda_un_timestamp(tmp_path):
    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    ruta_doc = next((v1_dir / "documents").glob("*.txt"))
    contenido = ruta_doc.read_text(encoding="utf-8")
    ruta_doc.write_text(contenido + "\n[7.42s] texto colado sin limpiar\n", encoding="utf-8")

    documentos = cargar_documentos(v1_dir)
    resultado = check_sin_timestamps(documentos)

    assert not resultado.ok
    assert any("timestamp" in d for d in resultado.detalles)


# --- Check 2: speaker tags ---------------------------------------------------


def test_check_sin_speaker_tags_falla_si_queda_un_speaker_tag(tmp_path):
    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    ruta_doc = next((v1_dir / "documents").glob("*.txt"))
    contenido = ruta_doc.read_text(encoding="utf-8")
    ruta_doc.write_text(contenido + "\nSPEAKER_00: texto colado sin limpiar\n", encoding="utf-8")

    documentos = cargar_documentos(v1_dir)
    resultado = check_sin_speaker_tags(documentos)

    assert not resultado.ok
    assert any("SPEAKER" in d for d in resultado.detalles)


# --- Check 3: cabecera completa ----------------------------------------------


def test_check_cabecera_completa_falla_si_falta_licencia(tmp_path):
    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    ruta_doc = next((v1_dir / "documents").glob("*.txt"))
    contenido = ruta_doc.read_text(encoding="utf-8")
    lineas = [l for l in contenido.splitlines(keepends=True) if not l.startswith("licencia:")]
    ruta_doc.write_text("".join(lineas), encoding="utf-8")

    documentos = cargar_documentos(v1_dir)
    resultado = check_cabecera_completa(documentos)

    assert not resultado.ok
    assert any("licencia" in d for d in resultado.detalles)


def test_check_cabecera_completa_ok_con_cabecera_real_intacta(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    resultado = check_cabecera_completa(documentos)
    assert resultado.ok


# --- Check 4: mínimo de palabras ---------------------------------------------


def test_check_min_palabras_falla_si_el_cuerpo_se_trunca(tmp_path):
    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    ruta_doc = next((v1_dir / "documents").glob("*.txt"))
    contenido = ruta_doc.read_text(encoding="utf-8")
    _, _, cuerpo = contenido.rpartition("\n---\n")
    cabecera = contenido[: len(contenido) - len(cuerpo)]
    # trunca el cuerpo real a solo las primeras palabras (< MIN_PALABRAS)
    cuerpo_truncado = " ".join(cuerpo.split()[:10])
    ruta_doc.write_text(cabecera + cuerpo_truncado, encoding="utf-8")

    documentos = cargar_documentos(v1_dir)
    resultado = check_min_palabras(documentos)

    assert not resultado.ok
    assert any("palabras" in d for d in resultado.detalles)


def test_check_min_palabras_ok_con_documento_real_de_176_palabras(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    resultado = check_min_palabras(documentos)
    assert resultado.ok


# --- Check 5: máximo de palabras ---------------------------------------------


def test_check_max_palabras_falla_si_el_cuerpo_se_concatena_de_mas(tmp_path):
    # Documento real con las 9 fragmentos reales repetidos suficientes veces
    # para superar MAX_PALABRAS=50_000 (88 palabras/pasada * 600 > 50_000) —
    # mismo texto real, sin inventar contenido, simulando un error de
    # concatenación real (el mismo documento pegado muchas veces).
    doc = _documento_real("Curso con error de concatenación", repeticiones=600)
    v1_dir = generar_version([doc], tmp_path)

    documentos = cargar_documentos(v1_dir)
    resultado = check_max_palabras(documentos)

    assert not resultado.ok
    assert any("palabras" in d for d in resultado.detalles)


def test_check_max_palabras_ok_con_documento_real_de_176_palabras(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    resultado = check_max_palabras(documentos)
    assert resultado.ok


# --- Check 6: duplicados ------------------------------------------------------


def test_check_sin_duplicados_falla_si_dos_ficheros_son_idénticos(tmp_path):
    # Mismo DocumentoEntrada dos veces -> generar_version genera dos ficheros
    # con nombre distinto (colisión de slug resuelta con sufijo -2) pero
    # contenido byte a byte idéntico: duplicado real, no inventado.
    doc = _documento_real("Curso de stand-up (fixture WhisperX) - Sesión 1")
    v1_dir = generar_version([doc, doc], tmp_path)

    documentos = cargar_documentos(v1_dir)
    resultado = check_sin_duplicados(documentos)

    assert not resultado.ok
    assert len(documentos) == 2
    assert any("duplicado" in d for d in resultado.detalles)


def test_check_sin_duplicados_ok_con_los_dos_documentos_reales_distintos(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    resultado = check_sin_duplicados(documentos)
    assert resultado.ok


# --- Check 7: idiomas permitidos ----------------------------------------------


def test_check_idiomas_permitidos_falla_con_idioma_fuera_de_la_lista(tmp_path):
    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    ruta_doc = next((v1_dir / "documents").glob("*.txt"))
    contenido = ruta_doc.read_text(encoding="utf-8")
    # Muta un valor real de idioma_fragmento (es -> fr, fuera de {es, en})
    mutado = contenido.replace('idioma_fragmento: "es"', 'idioma_fragmento: "fr"', 1)
    assert mutado != contenido  # confirma que la sustitución encontró algo real
    ruta_doc.write_text(mutado, encoding="utf-8")

    documentos = cargar_documentos(v1_dir)
    resultado = check_idiomas_permitidos(documentos)

    assert not resultado.ok
    assert any("fr" in d for d in resultado.detalles)


def test_check_idiomas_permitidos_ok_con_los_documentos_reales_en_es(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    resultado = check_idiomas_permitidos(documentos)
    assert resultado.ok


# --- Check 8: manifest sincronizado -------------------------------------------


def test_check_manifest_sincronizado_falla_si_se_borra_un_fichero(tmp_path):
    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    ruta_doc = next((v1_dir / "documents").glob("*.txt"))
    ruta_doc.unlink()

    documentos = cargar_documentos(v1_dir)
    manifest = cargar_manifest(v1_dir)
    resultado = check_manifest_sincronizado(documentos, manifest)

    assert not resultado.ok
    assert any("inexistente" in d for d in resultado.detalles)


def test_check_manifest_sincronizado_falla_si_sobra_un_fichero_en_disco(tmp_path):
    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    (v1_dir / "documents" / "intruso.txt").write_text("contenido no manifestado", encoding="utf-8")

    documentos = cargar_documentos(v1_dir)
    manifest = cargar_manifest(v1_dir)
    resultado = check_manifest_sincronizado(documentos, manifest)

    assert not resultado.ok
    assert any("no referenciado" in d for d in resultado.detalles)


def test_check_manifest_sincronizado_ok_con_estructura_real(tmp_path):
    v1_dir = _generar_v1_real(tmp_path)
    documentos = cargar_documentos(v1_dir)
    manifest = cargar_manifest(v1_dir)
    resultado = check_manifest_sincronizado(documentos, manifest)
    assert resultado.ok


# --- validar_version: sin manifest.json (versión inexistente/incompleta) ----


def test_validar_version_lanza_filenotfound_claro_sin_manifest(tmp_path):
    directorio_vacio = tmp_path / "v1"
    directorio_vacio.mkdir()

    with pytest.raises(FileNotFoundError):
        validar_version(directorio_vacio)


# --- encontrar_ultima_version -------------------------------------------------


def test_encontrar_ultima_version_ninguna_disponible(tmp_path):
    assert encontrar_ultima_version(tmp_path / "no-existe") is None
    assert encontrar_ultima_version(tmp_path) is None  # existe pero vacío


def test_encontrar_ultima_version_elige_la_de_mayor_numero_finalizada(tmp_path):
    doc = _documento_real("Curso de stand-up (fixture WhisperX) - Sesión 1")
    generar_version([doc], tmp_path)  # v1
    generar_version([doc], tmp_path)  # v2

    ultima = encontrar_ultima_version(tmp_path)
    assert ultima == tmp_path / "v2"


def test_encontrar_ultima_version_ignora_version_no_finalizada(tmp_path):
    doc = _documento_real("Curso de stand-up (fixture WhisperX) - Sesión 1")
    generar_version([doc], tmp_path)  # v1, finalizada

    # v2 "a medias": directorio presente pero sin manifest.json todavía
    (tmp_path / "v2" / "documents").mkdir(parents=True)

    ultima = encontrar_ultima_version(tmp_path)
    assert ultima == tmp_path / "v1"


# --- main() (CLI) --------------------------------------------------------------


def test_main_devuelve_0_con_ruta_explicita_valida(tmp_path, capsys):
    from scripts.validate_corpus import main

    v1_dir = _generar_v1_real(tmp_path)
    codigo = main([str(v1_dir)])
    salida = capsys.readouterr().out

    assert codigo == 0
    assert "8/8 checks OK" in salida


def test_main_devuelve_1_si_un_check_falla(tmp_path, capsys):
    from scripts.validate_corpus import main

    v1_dir = _copiar_version(_generar_v1_real(tmp_path), tmp_path / "mutado")
    ruta_doc = next((v1_dir / "documents").glob("*.txt"))
    contenido = ruta_doc.read_text(encoding="utf-8")
    ruta_doc.write_text(contenido + "\n[7.42s] texto colado sin limpiar\n", encoding="utf-8")

    codigo = main([str(v1_dir)])

    assert codigo == 1


def test_main_devuelve_1_con_ruta_explicita_sin_manifest(tmp_path, capsys):
    from scripts.validate_corpus import main

    directorio_vacio = tmp_path / "v1"
    directorio_vacio.mkdir()

    codigo = main([str(directorio_vacio)])
    salida = capsys.readouterr().out

    assert codigo == 1
    assert "✗" in salida
