"""Tests unitarios de `src/utils/llm/embeddings.py` — lógica pura, sin red.

Cubre la resolución de credenciales (prioridad argumento > EMBEDDINGS_* del
entorno > LLM_* del entorno, error explícito si no se puede resolver ninguna)
y la selección/validación del vector de una respuesta ya extraída (simulando
lo que `generar_embedding` construye a partir de `response.embeddings`). La
llamada real a Gemini se ejercita de verdad, sin mocks, desde
`tests/integration/test_reconciliacion_live.py` (task 15) — aquí no se toca
la red.
"""
import pytest

from src.utils.llm.embeddings import (
    EmbeddingsClientError,
    _resolver_credenciales_embeddings,
    _seleccionar_embedding,
)


class TestResolverCredencialesEmbeddings:
    def test_usa_argumentos_explicitos_si_se_dan(self):
        api_key, model = _resolver_credenciales_embeddings(
            "clave-explicita", "modelo-explicito", entorno={}
        )
        assert api_key == "clave-explicita"
        assert model == "modelo-explicito"

    def test_cae_a_variables_embeddings_del_entorno(self):
        entorno = {"EMBEDDINGS_API_KEY": "clave-emb", "EMBEDDINGS_MODEL": "modelo-emb"}
        api_key, model = _resolver_credenciales_embeddings(None, None, entorno=entorno)
        assert api_key == "clave-emb"
        assert model == "modelo-emb"

    def test_cae_a_variables_llm_si_no_hay_variables_de_embeddings(self):
        entorno = {"LLM_API_KEY": "clave-llm", "LLM_MODEL": "modelo-llm"}
        api_key, model = _resolver_credenciales_embeddings(None, None, entorno=entorno)
        assert api_key == "clave-llm"
        assert model == "modelo-llm"

    def test_variables_de_embeddings_tienen_prioridad_sobre_llm(self):
        entorno = {
            "EMBEDDINGS_API_KEY": "clave-emb",
            "EMBEDDINGS_MODEL": "modelo-emb",
            "LLM_API_KEY": "clave-llm",
            "LLM_MODEL": "modelo-llm",
        }
        api_key, model = _resolver_credenciales_embeddings(None, None, entorno=entorno)
        assert api_key == "clave-emb"
        assert model == "modelo-emb"

    def test_argumento_explicito_tiene_prioridad_sobre_todo_el_entorno(self):
        entorno = {"EMBEDDINGS_API_KEY": "clave-emb", "EMBEDDINGS_MODEL": "modelo-emb"}
        api_key, model = _resolver_credenciales_embeddings(
            "clave-explicita", None, entorno=entorno
        )
        assert api_key == "clave-explicita"
        assert model == "modelo-emb"

    def test_lanza_error_si_entorno_vacio(self):
        with pytest.raises(EmbeddingsClientError):
            _resolver_credenciales_embeddings(None, None, entorno={})

    def test_lanza_error_si_falta_model_en_todo_el_entorno(self):
        with pytest.raises(EmbeddingsClientError):
            _resolver_credenciales_embeddings(
                None, None, entorno={"EMBEDDINGS_API_KEY": "clave-emb"}
            )


class TestSeleccionarEmbedding:
    def test_devuelve_el_unico_vector(self):
        assert _seleccionar_embedding([[0.1, 0.2, 0.3]]) == [0.1, 0.2, 0.3]

    def test_lanza_error_si_no_hay_vectores(self):
        with pytest.raises(EmbeddingsClientError):
            _seleccionar_embedding([])

    def test_lanza_error_si_hay_mas_de_un_vector(self):
        with pytest.raises(EmbeddingsClientError):
            _seleccionar_embedding([[0.1, 0.2], [0.3, 0.4]])

    def test_lanza_error_si_el_vector_esta_vacio(self):
        with pytest.raises(EmbeddingsClientError):
            _seleccionar_embedding([[]])
