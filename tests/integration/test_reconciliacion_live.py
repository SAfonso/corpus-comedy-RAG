"""Test de integración: `reconciliar_chiste`/`generar_embedding` contra Gemini real.

Contrato (task 15, `src/jokes/SPEC.md` §Reconciliación): sin mocks, llama de
verdad a `generar_embedding` (`src/utils/llm/embeddings.py`) vía
`reconciliar_chiste` (sin `generar_embedding_fn` inyectado) con el chiste real
y completo de `tests/fixtures/Freskito-Informático.md` (task 17). Salta con
`pytest.skip` si `EMBEDDINGS_API_KEY`/`LLM_API_KEY` no están configuradas en
este entorno (mismo patrón que `test_silver_live.py`).

No se asume el valor del vector (es generativo/determinado por el modelo del
proveedor) — solo su forma (lista de floats no vacía) y que, sin candidatos,
la decisión es NUEVO (caso trivial y verificable sin depender de datos ya
insertados en Supabase).
"""
from pathlib import Path

import pytest

from src.jokes.reconciliacion import ResultadoReconciliacion, reconciliar_chiste
from src.utils.llm.embeddings import EmbeddingsClientError

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "Freskito-Informático.md"
)

# Tercera unidad de chiste real y completa del fixture: copiada literal del
# .md (nunca inventada, regla del proyecto).
CHISTE_REAL = (
    "Soy malísimo con los idiomas, incluso con el mio, tengo faltas de "
    "ortografía [REMATE]hasta en los exámenes orales[/REMATE]."
)


def _confirmar_chiste_en_fixture_real() -> None:
    contenido = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "Soy malísimo con los idiomas" in contenido, (
        "El chiste usado en este test de integración debe copiarse literal "
        f"de {FIXTURE_PATH} (regla del proyecto: nunca inventar fixtures)."
    )


def test_reconciliar_chiste_contra_gemini_real_sin_candidatos():
    _confirmar_chiste_en_fixture_real()

    try:
        resultado = reconciliar_chiste(CHISTE_REAL, [])
    except EmbeddingsClientError as exc:
        pytest.skip(f"EMBEDDINGS_API_KEY/LLM_API_KEY no disponibles en este entorno: {exc}")

    assert isinstance(resultado, ResultadoReconciliacion)
    # Sin candidatos existentes contra los que comparar, la decisión es
    # siempre NUEVO — esto sí es determinista, no depende del contenido
    # generativo del embedding.
    assert resultado.decision == "NUEVO"
    assert resultado.chiste_id is None

    # El embedding real: forma (lista de floats no vacía), sin asumir valores.
    assert isinstance(resultado.embedding, list)
    assert len(resultado.embedding) > 0
    assert all(isinstance(valor, float) for valor in resultado.embedding)
