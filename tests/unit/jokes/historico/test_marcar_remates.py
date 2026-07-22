"""Tests para scripts/marcar_remates.py (Flujo C — Histórico, P15).

Contrato: `src/jokes/historico/SPEC.md` §"Preprocesado de marcado
(`scripts/marcar_remates.py`)`". Migración TDD del prototipo YA VALIDADO en
`notebooks/marcar_remates_colab.ipynb` sobre documentos reales del histórico.

Fixture real (regla del proyecto: nunca inventar fixtures):
- `tests/fixtures/Freskito-Informático.docx`: monólogo real con remates
  marcados en rojo (`#FF0000`) y burdeos (`#980000`).
- `tests/fixtures/Freskito-Informático.md`: salida real generada por el
  propio `notebooks/marcar_remates_colab.ipynb` sobre ese `.docx` — el
  script migrado debe reproducirla exactamente (misma lógica).

Gap de cobertura documentado: el fixture real (`Freskito-Informático.docx`)
NO contiene tablas ni hyperlinks (verificado inspeccionando `word/document.xml`
— no aparecen los tags `w:tbl` ni `w:hyperlink`). El contrato exige cubrir
también esos casos (el iterador ingenuo de `python-docx` no los recorre);
la implementación cubre el requisito operando sobre `root.iter(...)` del XML
crudo, que por construcción no distingue si un `w:p`/`w:r` cuelga de un
`w:tbl` o de un `w:hyperlink` — se recorren igual que cualquier otro. No hay,
sin embargo, un fixture real con tablas/hyperlinks para verificarlo
end-to-end; si aparece uno en el futuro, añadir un test dedicado.
"""
from __future__ import annotations

import copy

import pytest

from scripts.marcar_remates import (
    clasificar_color,
    construir_markdown,
    contar_alfanum,
    emitir_span,
    extraer_parrafos,
    fusionar_tokens,
    marcar_remates,
    procesar_docx,
    validar_roundtrip,
)

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"
FIXTURE_DOCX = FIXTURES_DIR / "Freskito-Informático.docx"
FIXTURE_MD = FIXTURES_DIR / "Freskito-Informático.md"


# --- clasificar_color: mapa de tono con margen (contrato del SPEC) ---


def test_clasificar_color_rojo_puro_es_remate():
    assert clasificar_color("FF0000") == "REMATE"


def test_clasificar_color_burdeos_es_chistoide():
    assert clasificar_color("980000") == "CHISTOIDE"


def test_clasificar_color_tono_cercano_al_rojo_puro_tambien_es_remate():
    # Margen de tono, no igualdad hex exacta (spec: "el color puede variar
    # un par de digitos entre documentos").
    assert clasificar_color("FE0101") == "REMATE"


def test_clasificar_color_negro_no_es_remate_ni_chistoide():
    assert clasificar_color("000000") is None


def test_clasificar_color_auto_es_texto_normal():
    assert clasificar_color("auto") is None


def test_clasificar_color_none_es_texto_normal():
    assert clasificar_color(None) is None


def test_clasificar_color_azul_es_texto_normal():
    assert clasificar_color("0000FF") is None


# --- extraer_parrafos: lee el XML crudo del .docx real ---


def test_extraer_parrafos_del_fixture_real_encuentra_tokens_remate():
    parrafos = extraer_parrafos(FIXTURE_DOCX)
    textos_remate = [
        tx for parrafo in parrafos for et, tx in parrafo if et == "REMATE"
    ]
    texto_completo = "".join(textos_remate)
    assert "venenzolano con asma" in texto_completo


def test_extraer_parrafos_del_fixture_real_encuentra_tokens_chistoide():
    parrafos = extraer_parrafos(FIXTURE_DOCX)
    textos_chistoide = [
        tx for parrafo in parrafos for et, tx in parrafo if et == "CHISTOIDE"
    ]
    texto_completo = "".join(textos_chistoide)
    assert "dermatólogo" in texto_completo


def test_extraer_parrafos_conserva_tambien_texto_sin_etiquetar():
    parrafos = extraer_parrafos(FIXTURE_DOCX)
    textos_normales = [tx for parrafo in parrafos for et, tx in parrafo if et is None]
    assert any("Me llamo Sergio Afonso" in tx for tx in textos_normales)


# --- fusionar_tokens: runs contiguos del mismo color se fusionan ---


def test_fusionar_tokens_funde_runs_contiguos_del_mismo_color():
    tokens = [("REMATE", "No un "), ("REMATE", "venenzolano"), (None, " con asma")]
    segs = fusionar_tokens(tokens)
    assert segs == [["REMATE", "No un venenzolano"], [None, " con asma"]]


def test_fusionar_tokens_no_funde_colores_distintos():
    tokens = [("REMATE", "toma"), ("CHISTOIDE", "ya")]
    segs = fusionar_tokens(tokens)
    assert segs == [["REMATE", "toma"], ["CHISTOIDE", "ya"]]


def test_fusionar_tokens_absorbe_separador_de_solo_espacios_entre_mismo_color():
    tokens = [("REMATE", "toma"), (None, " "), ("REMATE", "ya")]
    segs = fusionar_tokens(tokens)
    assert segs == [["REMATE", "toma ya"]]


# --- emitir_span: espacios/puntuación fuera de la etiqueta ---


def test_emitir_span_deja_puntuacion_final_fuera():
    assert emitir_span("REMATE", "hasta en los exámenes orales.") == (
        "[REMATE]hasta en los exámenes orales[/REMATE]."
    )


def test_emitir_span_deja_espacios_en_los_bordes_fuera():
    assert emitir_span("REMATE", " toma ya ") == " [REMATE]toma ya[/REMATE] "


def test_emitir_span_con_texto_vacio_tras_quitar_bordes_no_etiqueta():
    assert emitir_span("REMATE", " ... ") == " ... "


# --- construir_markdown + comparación con la salida real de referencia ---


def test_construir_markdown_reproduce_exactamente_el_md_de_referencia():
    parrafos = extraer_parrafos(FIXTURE_DOCX)
    md = construir_markdown(parrafos)
    referencia = FIXTURE_MD.read_text(encoding="utf-8")
    assert md == referencia


def test_marcar_remates_reproduce_exactamente_el_md_de_referencia():
    md = marcar_remates(FIXTURE_DOCX)
    referencia = FIXTURE_MD.read_text(encoding="utf-8")
    assert md == referencia


def test_marcar_remates_contiene_los_remates_reales_esperados():
    md = marcar_remates(FIXTURE_DOCX)
    assert "[REMATE]No un venenzolano con asma[/REMATE]" in md
    assert "[REMATE]en argentino[/REMATE]" in md
    assert "[CHISTOIDE]" in md and "[/CHISTOIDE]" in md


def test_marcar_remates_no_solapa_etiquetas():
    md = marcar_remates(FIXTURE_DOCX)
    # No debe haber un [REMATE] abierto sin cerrar antes del siguiente [REMATE]/[CHISTOIDE]
    import re

    aperturas = re.findall(r"\[(REMATE|CHISTOIDE)\]", md)
    cierres = re.findall(r"\[/(REMATE|CHISTOIDE)\]", md)
    assert aperturas == cierres  # cada apertura tiene su cierre en el mismo orden


# --- validar_roundtrip: caso feliz sobre el fixture real ---


def test_validar_roundtrip_pasa_en_el_documento_real():
    parrafos = extraer_parrafos(FIXTURE_DOCX)
    md = construir_markdown(parrafos)
    ok, esperado, obtenido = validar_roundtrip(parrafos, md)
    assert ok is True
    assert esperado == obtenido
    assert esperado["REMATE"] > 0
    assert esperado["CHISTOIDE"] > 0


def test_contar_alfanum_ignora_puntuacion_y_espacios():
    assert contar_alfanum("hola, ¿qué tal?") == len("holaquétal")


# --- validar_roundtrip: DEBE fallar si se pierden runs (caso obligatorio) ---
#
# Enfoque: en vez de inventar un .docx nuevo, se parte de los `parrafos` REALES
# extraídos del fixture y se construye una versión corrupta que simula el
# fallo real que puede producir el parser (un run con color perdido, por
# ejemplo porque vivía dentro de una tabla/hyperlink y el recorrido del XML
# no lo alcanzó). Se valida esa versión corrupta contra el recuento verdadero
# (derivado de los parrafos originales, íntegros) y se comprueba que la
# guarda round-trip detecta el descuadre.


def _neutralizar_primer_run_remate(parrafos):
    """Devuelve una copia de `parrafos` donde el primer token REMATE
    encontrado pierde su etiqueta (simula un run cuyo color no se detectó)."""
    corruptos = copy.deepcopy(parrafos)
    for parrafo in corruptos:
        for i, (etiqueta, texto) in enumerate(parrafo):
            if etiqueta == "REMATE":
                parrafo[i] = (None, texto)
                return corruptos
    raise AssertionError("el fixture no contiene ningún token REMATE para corromper")


def test_validar_roundtrip_falla_si_se_pierde_un_run_remate():
    parrafos_reales = extraer_parrafos(FIXTURE_DOCX)
    parrafos_corruptos = _neutralizar_primer_run_remate(parrafos_reales)

    # El .md se genera a partir de la extracción CORRUPTA (como haría un
    # parser con un bug que no ve ese run), pero se valida contra el recuento
    # de los parrafos REALES (el .docx de verdad, íntegro) — debe descuadrar.
    md_corrupto = construir_markdown(parrafos_corruptos)
    ok, esperado, obtenido = validar_roundtrip(parrafos_reales, md_corrupto)

    assert ok is False
    assert esperado["REMATE"] > obtenido["REMATE"]


def test_validar_roundtrip_falla_tambien_con_chistoide_perdido():
    parrafos_reales = extraer_parrafos(FIXTURE_DOCX)
    corruptos = copy.deepcopy(parrafos_reales)
    encontrado = False
    for parrafo in corruptos:
        for i, (etiqueta, texto) in enumerate(parrafo):
            if etiqueta == "CHISTOIDE":
                parrafo[i] = (None, texto)
                encontrado = True
                break
        if encontrado:
            break
    assert encontrado, "el fixture no contiene ningún token CHISTOIDE para corromper"

    md_corrupto = construir_markdown(corruptos)
    ok, esperado, obtenido = validar_roundtrip(parrafos_reales, md_corrupto)

    assert ok is False
    assert esperado["CHISTOIDE"] > obtenido["CHISTOIDE"]


# --- procesar_docx: la validación round-trip debe impedir la escritura ---


def test_procesar_docx_escribe_el_md_cuando_el_roundtrip_pasa(tmp_path):
    ruta_md, msg = procesar_docx(FIXTURE_DOCX, tmp_path)
    assert ruta_md.exists()
    assert ruta_md.read_text(encoding="utf-8") == FIXTURE_MD.read_text(encoding="utf-8")
    assert msg.startswith("OK")


def test_procesar_docx_no_escribe_nada_si_el_roundtrip_falla(tmp_path, monkeypatch):
    # La extracción (`extraer_parrafos`) ve el color correctamente (esperado
    # correcto, derivado del .docx real), pero simulamos que el paso de
    # construcción del markdown pierde uno de esos runs al emitirlo (p.ej. un
    # bug futuro que no cubra bien un caso de tabla/hyperlink): el texto
    # queda sin etiquetar en el .md aunque el .docx real sí lo tenía en rojo.
    # Esto reproduce exactamente el descuadre que `validar_roundtrip` debe
    # detectar, usando datos derivados del .docx real (nunca inventados).
    import re

    import scripts.marcar_remates as mod

    construir_original = mod.construir_markdown

    def construir_con_run_perdido(parrafos):
        md = construir_original(parrafos)
        # Quita la primera etiqueta [REMATE]...[/REMATE] dejando el texto
        # plano, como si ese run se hubiese perdido al construir el .md.
        return re.sub(r"\[REMATE\](.*?)\[/REMATE\]", r"\1", md, count=1)

    monkeypatch.setattr(mod, "construir_markdown", construir_con_run_perdido)

    ruta_md_esperada = tmp_path / (FIXTURE_DOCX.stem + ".md")
    with pytest.raises(ValueError, match="round-trip FALLIDO"):
        mod.procesar_docx(FIXTURE_DOCX, tmp_path)

    assert not ruta_md_esperada.exists()


def test_procesar_docx_no_sobrescribe_el_docx_original():
    original_antes = FIXTURE_DOCX.read_bytes()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        procesar_docx(FIXTURE_DOCX, tmp)
    original_despues = FIXTURE_DOCX.read_bytes()
    assert original_antes == original_despues
