"""Tests unitarios de `src/jokes/historico/segmentador.py` — lógica pura, sin red.

Cubre (§Segmentador, task 19): detección determinista de `[REMATE]`/
`[CHISTOIDE]`, construcción de ventanas candidatas, limpieza de etiquetas,
localización del fragmento de inicio (incluida la tolerancia de espaciado y el
caso de alucinación), construcción de prompt/schema, parseo de una respuesta
ya deserializada a `dict`, y la orquestación `segmentar_documento` con
`llamar_llm` inyectado (doble fijo, nunca se toca la red).

El texto real usado como fixture viene literal de
`tests/fixtures/Freskito-Informático.md` (task 17), no se inventa — mismo
patrón que `tests/unit/jokes/test_silver.py`.
"""
from pathlib import Path

import pytest

from src.jokes.historico.segmentador import (
    ChisteSegmentado,
    SegmentadorError,
    VentanaCandidata,
    _build_prompt,
    _build_schema,
    _construir_ventanas,
    _localizar_inicio,
    _parsear_respuesta,
    _quitar_etiquetas,
    segmentar_documento,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "fixtures"
    / "Freskito-Informático.md"
)
FIXTURE_CONTENT = FIXTURE_PATH.read_text(encoding="utf-8")

# Párrafos del .md real, extraídos LITERAL (no se copian a mano ni se inventan:
# se seleccionan del contenido real por una subcadena marcadora — así lo que se
# pasa al segmentador es texto verbatim del fixture, misma regla que test_silver).
_PARRAFOS = [p.strip() for p in FIXTURE_CONTENT.split("\n\n") if p.strip()]


def _parrafo_con(marcador: str) -> str:
    coincidencias = [p for p in _PARRAFOS if marcador in p]
    assert coincidencias, f"marcador {marcador!r} no está en ningún párrafo del fixture"
    return coincidencias[0]


# - un chiste de un solo remate (línea 1),
CHISTE_UN_REMATE = _parrafo_con("Me llamo Sergio Afonso")
# - un tramo con DOS remates seguidos (línea 13),
CHISTE_DOS_REMATES = _parrafo_con("¿Sabéis a cuanto se cotizan")
# - un chiste con un [CHISTOIDE] interno (línea 19).
CHISTE_CON_CHISTOIDE = _parrafo_con("[CHISTOIDE]")


def test_fixture_real_contiene_los_fragmentos_usados():
    """Confirma que los fragmentos no se inventaron: están literal en el .md."""
    assert "[REMATE]No un venenzolano con asma[/REMATE]" in FIXTURE_CONTENT
    assert "[REMATE]las bragas usadas[/REMATE]" in FIXTURE_CONTENT
    assert "[REMATE]se las robo a la vecina[/REMATE]" in FIXTURE_CONTENT
    assert (
        "¿[CHISTOIDE]A que tu no le pides a un dermatólogo que te opere un "
        "menisco[/CHISTOIDE]?" in FIXTURE_CONTENT
    )


# ---------------------------------------------------------------------------
# _construir_ventanas — detección determinista (sin LLM)
# ---------------------------------------------------------------------------

class TestConstruirVentanas:
    def test_una_ventana_por_remate_en_el_documento_real(self):
        ventanas = _construir_ventanas(FIXTURE_CONTENT)
        # El .md real tiene 20 [REMATE] (fin determinista de chiste) → 20 ventanas.
        assert len(ventanas) == 20
        assert all(isinstance(v, VentanaCandidata) for v in ventanas)

    def test_remate_guarda_el_contenido_literal(self):
        ventanas = _construir_ventanas(CHISTE_UN_REMATE)
        assert len(ventanas) == 1
        assert ventanas[0].remate == "No un venenzolano con asma"

    def test_primera_ventana_arranca_al_inicio_del_documento(self):
        ventanas = _construir_ventanas(CHISTE_UN_REMATE)
        assert ventanas[0].texto.startswith("Me llamo Sergio Afonso")

    def test_dos_remates_seguidos_producen_dos_ventanas_sin_solaparse(self):
        ventanas = _construir_ventanas(CHISTE_DOS_REMATES)
        assert len(ventanas) == 2
        primera, segunda = ventanas
        assert primera.remate == "las bragas usadas"
        assert segunda.remate == "se las robo a la vecina"
        # La segunda ventana empieza DESPUÉS del cierre de la primera: no
        # vuelve a contener el remate ya consumido.
        assert "[REMATE]las bragas usadas[/REMATE]" not in segunda.texto
        assert "[REMATE]las bragas usadas[/REMATE]" in primera.texto

    def test_chistoide_se_conserva_como_metadato_no_como_frontera(self):
        ventanas = _construir_ventanas(CHISTE_CON_CHISTOIDE)
        # Un solo remate → una sola ventana: el [CHISTOIDE] NO parte el chiste.
        assert len(ventanas) == 1
        assert ventanas[0].chistoides == (
            "A que tu no le pides a un dermatólogo que te opere un menisco",
        )

    def test_documento_sin_remates_no_produce_ventanas(self):
        ventanas = _construir_ventanas("Un texto sin ningún marcado de color.")
        assert ventanas == []

    def test_texto_despues_del_ultimo_remate_no_genera_ventana(self):
        texto = CHISTE_UN_REMATE + " Esta coletilla final ya no tiene remate."
        ventanas = _construir_ventanas(texto)
        assert len(ventanas) == 1
        assert "coletilla final" not in ventanas[0].texto


# ---------------------------------------------------------------------------
# _quitar_etiquetas
# ---------------------------------------------------------------------------

class TestQuitarEtiquetas:
    def test_quita_remate_y_chistoide(self):
        limpio = _quitar_etiquetas(CHISTE_CON_CHISTOIDE)
        assert "[REMATE]" not in limpio
        assert "[/REMATE]" not in limpio
        assert "[CHISTOIDE]" not in limpio
        assert "[/CHISTOIDE]" not in limpio
        assert "hablando con mi ansiedad" in limpio

    def test_no_op_si_no_hay_etiquetas(self):
        assert _quitar_etiquetas("Texto plano.") == "Texto plano."


# ---------------------------------------------------------------------------
# _localizar_inicio — verificación barata, NO criterio de loop
# ---------------------------------------------------------------------------

class TestLocalizarInicio:
    def test_coincidencia_exacta_devuelve_offset(self):
        ventana = "aaa bbb ccc"
        assert _localizar_inicio(ventana, "bbb") == 4

    def test_tolerancia_a_espaciado(self):
        ventana = "hola   mundo\ncruel"
        # El LLM copió "hola mundo" con espaciado normalizado; debe localizarse.
        assert _localizar_inicio(ventana, "hola mundo") == 0

    def test_fragmento_que_no_aparece_devuelve_none(self):
        assert _localizar_inicio("aaa bbb ccc", "zzz inventado") is None

    def test_fragmento_vacio_devuelve_none(self):
        assert _localizar_inicio("aaa bbb", "   ") is None


# ---------------------------------------------------------------------------
# _build_prompt / _build_schema
# ---------------------------------------------------------------------------

class TestBuildPromptYSchema:
    def test_prompt_incluye_la_ventana(self):
        prompt = _build_prompt("Un tramo de prueba.")
        assert "Un tramo de prueba." in prompt

    def test_prompt_pide_el_fragmento_literal_de_inicio(self):
        prompt = _build_prompt("x")
        assert "texto_inicio_setup" in prompt
        assert "LITERAL" in prompt

    def test_prompt_conserva_las_etiquetas_de_la_ventana(self):
        # A diferencia de Silver, aquí SÍ se le pasan las etiquetas al LLM.
        prompt = _build_prompt(CHISTE_UN_REMATE)
        assert "[REMATE]No un venenzolano con asma[/REMATE]" in prompt

    def test_schema_tiene_el_campo_requerido(self):
        schema = _build_schema()
        assert schema["type"] == "OBJECT"
        assert schema["required"] == ["texto_inicio_setup"]
        assert schema["properties"]["texto_inicio_setup"]["type"] == "STRING"


# ---------------------------------------------------------------------------
# _parsear_respuesta
# ---------------------------------------------------------------------------

class TestParsearRespuesta:
    def test_respuesta_valida_devuelve_el_fragmento(self):
        assert _parsear_respuesta({"texto_inicio_setup": "hola"}) == "hola"

    def test_string_vacio_no_es_error(self):
        # Vacío se propaga: la alucinación es de negocio (fallback), no de estructura.
        assert _parsear_respuesta({"texto_inicio_setup": ""}) == ""

    def test_falta_el_campo_lanza_segmentador_error(self):
        with pytest.raises(SegmentadorError):
            _parsear_respuesta({"otra_cosa": "x"})

    def test_campo_no_string_lanza_segmentador_error(self):
        with pytest.raises(SegmentadorError):
            _parsear_respuesta({"texto_inicio_setup": 123})


# ---------------------------------------------------------------------------
# segmentar_documento — orquestación con `llamar_llm` inyectado (sin red)
# ---------------------------------------------------------------------------

class TestSegmentarDocumentoSinRed:
    def test_recorta_desde_el_inicio_del_setup_y_limpia_etiquetas(self):
        # El LLM devuelve un fragmento que SÍ está literal en la ventana.
        def llamar_llm_fake(prompt, schema):
            return {"texto_inicio_setup": "eso es porque soy canario"}

        chistes = segmentar_documento(CHISTE_UN_REMATE, llamar_llm=llamar_llm_fake)

        assert len(chistes) == 1
        chiste = chistes[0]
        assert isinstance(chiste, ChisteSegmentado)
        assert chiste.inicio_localizado is True
        # Recortado desde donde dijo el LLM (se descarta la intro anterior).
        assert chiste.texto.startswith("eso es porque soy canario")
        assert "Me llamo Sergio Afonso" not in chiste.texto
        # Etiquetas consumidas en el texto que va a Silver.
        assert "[REMATE]" not in chiste.texto
        assert chiste.texto.endswith("No un venenzolano con asma")
        # Remate conservado como metadato.
        assert chiste.remate == "No un venenzolano con asma"
        # texto_marcado conserva las etiquetas para la revisión humana.
        assert "[REMATE]No un venenzolano con asma[/REMATE]" in chiste.texto_marcado

    def test_conserva_chistoide_como_metadato(self):
        def llamar_llm_fake(prompt, schema):
            return {"texto_inicio_setup": "Que esa es otra"}

        chistes = segmentar_documento(CHISTE_CON_CHISTOIDE, llamar_llm=llamar_llm_fake)

        assert len(chistes) == 1
        chiste = chistes[0]
        assert chiste.contiene_chistoide is True
        assert chiste.chistoides == (
            "A que tu no le pides a un dermatólogo que te opere un menisco",
        )
        # El chistoide NO es frontera: su contenido sigue dentro del texto.
        assert "dermatólogo" in chiste.texto

    def test_varios_remates_una_llamada_por_remate(self):
        llamadas = []

        def llamar_llm_fake(prompt, schema):
            llamadas.append(prompt)
            return {"texto_inicio_setup": ""}  # fuerza fallback, da igual aquí

        chistes = segmentar_documento(CHISTE_DOS_REMATES, llamar_llm=llamar_llm_fake)

        assert len(chistes) == 2
        assert len(llamadas) == 2, "una sola llamada por remate, sin reintento (P16)"
        assert chistes[0].remate == "las bragas usadas"
        assert chistes[1].remate == "se las robo a la vecina"

    def test_alucinacion_cae_a_fallback_conservador_sin_reintentar(self):
        llamadas = []

        def llamar_llm_fake(prompt, schema):
            llamadas.append(1)
            # Fragmento que NO aparece en la ventana candidata (alucinación).
            return {"texto_inicio_setup": "Érase una vez un texto inventado"}

        chistes = segmentar_documento(CHISTE_UN_REMATE, llamar_llm=llamar_llm_fake)

        assert len(llamadas) == 1, "una alucinación NO dispara un reintento (P16)"
        assert len(chistes) == 1
        chiste = chistes[0]
        # Fallback conservador: no se descarta el chiste, se marca para revisión.
        assert chiste.inicio_localizado is False
        # Se conserva la ventana COMPLETA (no se pierde contenido SAGRADO).
        assert chiste.texto.startswith("Me llamo Sergio Afonso")
        assert chiste.texto.endswith("No un venenzolano con asma")

    def test_documento_sin_remates_devuelve_lista_vacia_sin_llamar_al_llm(self):
        def llamar_llm_fake(prompt, schema):
            raise AssertionError("no debe llamarse al LLM si no hay remates")

        assert segmentar_documento("Texto sin marcado.", llamar_llm=llamar_llm_fake) == []

    def test_respuesta_estructuralmente_invalida_propaga_error(self):
        def llamar_llm_fake(prompt, schema):
            return {"campo_incorrecto": "x"}

        with pytest.raises(SegmentadorError):
            segmentar_documento(CHISTE_UN_REMATE, llamar_llm=llamar_llm_fake)

    def test_documento_real_completo_produce_20_chistes(self):
        def llamar_llm_fake(prompt, schema):
            return {"texto_inicio_setup": ""}  # fallback en todos, da igual

        chistes = segmentar_documento(FIXTURE_CONTENT, llamar_llm=llamar_llm_fake)
        assert len(chistes) == 20
        # Exactamente uno de los 20 chistes lleva el [CHISTOIDE] del .md real.
        con_chistoide = [c for c in chistes if c.contiene_chistoide]
        assert len(con_chistoide) == 1
