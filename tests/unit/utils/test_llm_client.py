"""Tests unitarios de `src/utils/llm/client.py` — lógica pura, sin red.

Cubre la resolución de credenciales (prioridad argumento > entorno, error
explícito si faltan) y el parseo de la respuesta JSON del LLM. La llamada
real a Gemini (`generar_json`) se ejercita de verdad, sin mocks, desde
`tests/integration/test_silver_live.py` (task 13) — aquí no se toca la red.
"""
import pytest

from src.utils.llm.client import (
    LLMClientError,
    _parsear_json_respuesta,
    _resolver_credenciales,
)


class TestResolverCredenciales:
    def test_usa_argumentos_explicitos_si_se_dan(self):
        api_key, model = _resolver_credenciales(
            "clave-explicita", "modelo-explicito", entorno={}
        )
        assert api_key == "clave-explicita"
        assert model == "modelo-explicito"

    def test_cae_a_variables_de_entorno_si_no_hay_argumentos(self):
        entorno = {"LLM_API_KEY": "clave-env", "LLM_MODEL": "modelo-env"}
        api_key, model = _resolver_credenciales(None, None, entorno=entorno)
        assert api_key == "clave-env"
        assert model == "modelo-env"

    def test_argumento_explicito_tiene_prioridad_sobre_entorno(self):
        entorno = {"LLM_API_KEY": "clave-env", "LLM_MODEL": "modelo-env"}
        api_key, model = _resolver_credenciales(
            "clave-explicita", None, entorno=entorno
        )
        assert api_key == "clave-explicita"
        assert model == "modelo-env"

    def test_lanza_error_si_falta_api_key(self):
        with pytest.raises(LLMClientError):
            _resolver_credenciales(None, "modelo-env", entorno={"LLM_MODEL": "modelo-env"})

    def test_lanza_error_si_falta_model(self):
        with pytest.raises(LLMClientError):
            _resolver_credenciales(
                None, None, entorno={"LLM_API_KEY": "clave-env"}
            )

    def test_lanza_error_si_entorno_vacio(self):
        with pytest.raises(LLMClientError):
            _resolver_credenciales(None, None, entorno={})


class TestParsearJsonRespuesta:
    def test_parsea_json_valido(self):
        resultado = _parsear_json_respuesta('{"tema": "prueba", "estado": "rematado"}')
        assert resultado == {"tema": "prueba", "estado": "rematado"}

    def test_lanza_llm_client_error_si_no_es_json(self):
        with pytest.raises(LLMClientError):
            _parsear_json_respuesta("esto no es json")

    def test_lanza_llm_client_error_si_json_truncado(self):
        with pytest.raises(LLMClientError):
            _parsear_json_respuesta('{"tema": "prueba"')
