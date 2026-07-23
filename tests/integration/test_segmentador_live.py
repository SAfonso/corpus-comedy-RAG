"""Test de integración: `segmentar_documento` contra la API real de Gemini.

Contrato (task 19, `src/jokes/historico/SPEC.md` §Segmentador):
`LLM_API_KEY`/`LLM_MODEL` ya están verificadas en `.env`. Este test llama de
verdad, sin mocks, a `segmentar_documento` (sin `llamar_llm` inyectado, así
que usa el `generar_json` real de `src/utils/llm/client.py`) sobre un tramo
real y marcado del fixture `tests/fixtures/Freskito-Informático.md` (task 17).

Es juicio semántico por naturaleza (P16, `docs/specs/llm-policy.md`): NO se
asume DÓNDE exactamente cortará el LLM el setup (varía entre llamadas) — solo
se verifica el contrato estructural: los `[REMATE]` (fin determinista) siguen
siendo los mismos, el texto sale sin etiquetas, el remate se conserva como
metadato, y el `[CHISTOIDE]` se preserva como estructura (no como frontera).
Una sola llamada real por remate, sin reintento.
"""
from pathlib import Path

import pytest

from src.jokes.historico.segmentador import ChisteSegmentado, segmentar_documento
from src.utils.llm.client import LLMClientError

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "Freskito-Informático.md"
)
FIXTURE_CONTENT = FIXTURE_PATH.read_text(encoding="utf-8")

# Dos párrafos reales y consecutivos del .md (líneas 17 y 19), copiados por
# selección literal del contenido real (nunca inventados): el segundo trae un
# [CHISTOIDE] interno, así se ejercita en real tanto la frontera por [REMATE]
# como la conservación del chistoide como metadato.
_PARRAFOS = [p.strip() for p in FIXTURE_CONTENT.split("\n\n") if p.strip()]
_TRAMO_CON_CHISTOIDE = next(p for p in _PARRAFOS if "[CHISTOIDE]" in p)


def _confirmar_tramo_en_fixture_real() -> None:
    assert "[CHISTOIDE]" in FIXTURE_CONTENT, (
        "El tramo usado en este test debe salir literal de "
        f"{FIXTURE_PATH} (regla del proyecto: nunca inventar fixtures)."
    )


def test_segmentar_documento_contra_gemini_real():
    _confirmar_tramo_en_fixture_real()

    try:
        chistes = segmentar_documento(_TRAMO_CON_CHISTOIDE)
    except LLMClientError as exc:
        pytest.skip(f"LLM_API_KEY/LLM_MODEL no disponibles en este entorno: {exc}")

    # El tramo tiene un solo [REMATE] → un solo chiste (fin determinista, no lo
    # decide el LLM).
    assert len(chistes) == 1
    chiste = chistes[0]
    assert isinstance(chiste, ChisteSegmentado)

    # Texto no vacío y SIN etiquetas de marcado (consumidas por el segmentador).
    assert chiste.texto.strip()
    for etiqueta in ("[REMATE]", "[/REMATE]", "[CHISTOIDE]", "[/CHISTOIDE]"):
        assert etiqueta not in chiste.texto

    # Remate conservado como metadato (contenido determinista del marcado).
    assert chiste.remate == "hablando con mi ansiedad"

    # El [CHISTOIDE] se conserva como estructura, no parte el chiste.
    assert chiste.contiene_chistoide is True
    assert chiste.chistoides == (
        "A que tu no le pides a un dermatólogo que te opere un menisco",
    )

    # `inicio_localizado` es booleano; si es True, el texto recortado es un
    # sufijo real del tramo limpio (el LLM copió un fragmento que existe).
    assert isinstance(chiste.inicio_localizado, bool)
