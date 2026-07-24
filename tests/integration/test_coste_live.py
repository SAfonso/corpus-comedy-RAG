"""Test de integración: `contar_tokens_oficial` contra la API real de Gemini.

Contrato (task 26, `src/jokes/historico/coste.py`): `contar_tokens_oficial`
usa `client.models.count_tokens(...)` del SDK `google-genai` de verdad, contra
el endpoint `countTokens` (conteo, NO generación). Mismo patrón que
`test_segmentador_live.py`/`test_silver_live.py`: si `LLM_API_KEY`/`LLM_MODEL`
no están configuradas en el entorno, el test se salta en vez de fallar.
"""
from src.jokes.historico.coste import contar_tokens_oficial, estimar_tokens_heuristica
from src.utils.llm.client import LLMClientError

import pytest


def test_contar_tokens_oficial_contra_gemini_real():
    texto = "Eres un editor de comedia. ¿Dónde empieza el remate de este chiste?"

    try:
        total = contar_tokens_oficial(texto)
    except LLMClientError as exc:
        pytest.skip(f"LLM_API_KEY/LLM_MODEL no disponibles en este entorno: {exc}")

    assert isinstance(total, int)
    assert total > 0

    # La heurística offline (~4 caracteres/token) debe quedar en el mismo
    # orden de magnitud que el conteo real del tokenizador (margen documentado
    # ±20-30% en `coste.py`, con un colchón adicional aquí por robustez del test).
    aproximado = estimar_tokens_heuristica(texto)
    assert aproximado == pytest.approx(total, rel=0.6)
