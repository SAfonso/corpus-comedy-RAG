"""Tests unitarios de `src/jokes/silver.py` — lógica pura, sin red.

Cubre (§Silver, task 13): limpieza del marcado de Histórico, validación
estricta del enum `estado`, construcción de prompt/schema, y parseo de una
respuesta ya deserializada a `dict` (simulando lo que devolvería
`generar_json` tras parsear el JSON de Gemini) — nunca se llama a la red
aquí, `estructurar_chiste` recibe `llamar_llm` inyectado con un doble fijo.
El chiste real usado como fixture viene literal de
`tests/fixtures/Freskito-Informático.md` (task 17), no se inventa.
"""
from pathlib import Path

import pytest

from src.jokes.silver import (
    CAMPOS_SILVER,
    ChisteEstructurado,
    SilverError,
    _build_prompt,
    _build_schema,
    _limpiar_marcado_historico,
    _parsear_respuesta,
    _validar_estado,
    estructurar_chiste,
)
from src.jokes.supabase_store import ESTADOS_CHISTE

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "Freskito-Informático.md"
)

# Primera unidad de chiste real y completa del fixture (línea 1), copiada
# literal (con sus etiquetas [REMATE]/[/REMATE] tal cual están en el .md).
CHISTE_REAL_CON_MARCADO = (
    "Me llamo Sergio Afonso, y si, efectivamente no soy de aquí, lo habrán "
    "notado por mi acento y porque aspiro las S, eso es porque soy canario, "
    "vale? [REMATE]No un venenzolano con asma[/REMATE]."
)

_RESPUESTA_LLM_VALIDA = {
    "tema": "acento y origen canario",
    "estructura_detectada": "setup/punchline con comparación absurda",
    "estado": "rematado",
    "sugerencias_mejora": "Alargar la pausa antes del remate.",
    "chiste_normalizado": (
        "Me llamo Sergio Afonso, y sí, efectivamente no soy de aquí, lo "
        "habrán notado por mi acento y porque aspiro las eses, eso es "
        "porque soy canario, vale? No un venezolano con asma."
    ),
}


def test_fixture_real_contiene_el_chiste_usado_en_los_tests():
    """Confirma que el fragmento no se inventó: está literal en el .md real."""
    contenido = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "Me llamo Sergio Afonso" in contenido
    assert "[REMATE]No un venenzolano con asma[/REMATE]" in contenido


# ---------------------------------------------------------------------------
# _limpiar_marcado_historico
# ---------------------------------------------------------------------------

class TestLimpiarMarcadoHistorico:
    def test_quita_etiquetas_remate(self):
        limpio = _limpiar_marcado_historico(CHISTE_REAL_CON_MARCADO)
        assert "[REMATE]" not in limpio
        assert "[/REMATE]" not in limpio
        assert "No un venenzolano con asma" in limpio

    def test_quita_etiquetas_chistoide(self):
        texto = "¿A que tu no le pides a un dermatólogo [CHISTOIDE]que te opere un menisco[/CHISTOIDE]?"
        limpio = _limpiar_marcado_historico(texto)
        assert "[CHISTOIDE]" not in limpio
        assert "[/CHISTOIDE]" not in limpio

    def test_no_op_si_no_hay_etiquetas(self):
        texto = "Un chiste de Telegram sin marcado alguno."
        assert _limpiar_marcado_historico(texto) == texto


# ---------------------------------------------------------------------------
# _validar_estado
# ---------------------------------------------------------------------------

class TestValidarEstado:
    @pytest.mark.parametrize("estado", list(ESTADOS_CHISTE))
    def test_acepta_los_tres_valores_del_enum(self, estado):
        assert _validar_estado(estado) == estado

    def test_rechaza_valor_fuera_de_enum(self):
        with pytest.raises(SilverError):
            _validar_estado("gracioso")

    def test_rechaza_valor_no_string(self):
        with pytest.raises(SilverError):
            _validar_estado(None)

    def test_rechaza_string_vacio(self):
        with pytest.raises(SilverError):
            _validar_estado("")


# ---------------------------------------------------------------------------
# _build_prompt / _build_schema
# ---------------------------------------------------------------------------

class TestBuildPromptYSchema:
    def test_prompt_incluye_el_texto_del_chiste(self):
        prompt = _build_prompt("Un chiste de prueba.")
        assert "Un chiste de prueba." in prompt

    def test_prompt_menciona_los_tres_valores_del_enum(self):
        prompt = _build_prompt("Un chiste de prueba.")
        for estado in ESTADOS_CHISTE:
            assert estado in prompt

    def test_prompt_instruye_conservar_timing_y_muletillas(self):
        prompt = _build_prompt("Un chiste de prueba.")
        assert "timing" in prompt.lower()
        assert "muletillas" in prompt.lower()

    def test_schema_tiene_los_5_campos_requeridos(self):
        schema = _build_schema()
        assert schema["type"] == "OBJECT"
        assert set(schema["required"]) == set(CAMPOS_SILVER)
        assert set(schema["properties"].keys()) == set(CAMPOS_SILVER)

    def test_schema_restringe_estado_al_enum(self):
        schema = _build_schema()
        assert schema["properties"]["estado"]["enum"] == list(ESTADOS_CHISTE)


# ---------------------------------------------------------------------------
# _parsear_respuesta
# ---------------------------------------------------------------------------

class TestParsearRespuesta:
    def test_respuesta_valida_produce_chiste_estructurado(self):
        resultado = _parsear_respuesta(_RESPUESTA_LLM_VALIDA)
        assert isinstance(resultado, ChisteEstructurado)
        assert resultado.tema == _RESPUESTA_LLM_VALIDA["tema"]
        assert resultado.estado == "rematado"
        assert resultado.chiste_normalizado == _RESPUESTA_LLM_VALIDA["chiste_normalizado"]

    def test_falta_un_campo_lanza_silver_error(self):
        incompleta = dict(_RESPUESTA_LLM_VALIDA)
        del incompleta["sugerencias_mejora"]
        with pytest.raises(SilverError):
            _parsear_respuesta(incompleta)

    def test_estado_fuera_de_enum_lanza_silver_error(self):
        invalida = dict(_RESPUESTA_LLM_VALIDA)
        invalida["estado"] = "buenisimo"
        with pytest.raises(SilverError):
            _parsear_respuesta(invalida)

    def test_campo_no_string_lanza_silver_error(self):
        invalida = dict(_RESPUESTA_LLM_VALIDA)
        invalida["tema"] = 123
        with pytest.raises(SilverError):
            _parsear_respuesta(invalida)


# ---------------------------------------------------------------------------
# estructurar_chiste — orquestación con `llamar_llm` inyectado (sin red)
# ---------------------------------------------------------------------------

class TestEstructurarChisteSinRed:
    def test_llama_una_sola_vez_y_devuelve_el_resultado_tal_cual(self):
        llamadas = []

        def llamar_llm_fake(prompt: str, schema: dict) -> dict:
            llamadas.append((prompt, schema))
            return dict(_RESPUESTA_LLM_VALIDA)

        resultado = estructurar_chiste(CHISTE_REAL_CON_MARCADO, llamar_llm=llamar_llm_fake)

        assert len(llamadas) == 1, "Silver no debe reintentar (P16): una sola llamada al LLM"
        assert isinstance(resultado, ChisteEstructurado)
        assert resultado.estado in ESTADOS_CHISTE

    def test_limpia_el_marcado_antes_de_construir_el_prompt(self):
        prompts_vistos = []

        def llamar_llm_fake(prompt: str, schema: dict) -> dict:
            prompts_vistos.append(prompt)
            return dict(_RESPUESTA_LLM_VALIDA)

        estructurar_chiste(CHISTE_REAL_CON_MARCADO, llamar_llm=llamar_llm_fake)

        assert "[REMATE]" not in prompts_vistos[0]
        assert "No un venenzolano con asma" in prompts_vistos[0]

    def test_no_reintenta_si_la_respuesta_es_invalida(self):
        llamadas = []

        def llamar_llm_fake(prompt: str, schema: dict) -> dict:
            llamadas.append(1)
            respuesta = dict(_RESPUESTA_LLM_VALIDA)
            respuesta["estado"] = "fuera_de_enum"
            return respuesta

        with pytest.raises(SilverError):
            estructurar_chiste(CHISTE_REAL_CON_MARCADO, llamar_llm=llamar_llm_fake)

        assert len(llamadas) == 1, "Un fallo de validación no debe disparar un reintento (P16)"

    def test_texto_vacio_lanza_silver_error_sin_llamar_al_llm(self):
        def llamar_llm_fake(prompt: str, schema: dict) -> dict:
            raise AssertionError("no debería llamarse al LLM con texto vacío")

        with pytest.raises(SilverError):
            estructurar_chiste("   ", llamar_llm=llamar_llm_fake)
