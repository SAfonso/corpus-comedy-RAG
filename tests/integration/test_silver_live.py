"""Test de integración: `estructurar_chiste` contra la API real de Gemini.

Contrato (task 13, `src/jokes/SPEC.md` §Silver): `LLM_API_KEY`/`LLM_MODEL`
ya están verificadas en `.env` (el leader confirmó una llamada real a
`generateContent` que responde "OK" antes de esta task). Este test llama de
verdad, sin mocks, a `estructurar_chiste` (sin `llamar_llm` inyectado, así
que usa el `generar_json` real de `src/utils/llm/client.py`) con el chiste
real y completo de `tests/fixtures/Freskito-Informático.md` (task 17).

Es generativo por naturaleza (P16, `docs/specs/llm-policy.md`): NO se asume
contenido exacto de `tema`/`estructura_detectada`/`sugerencias_mejora`/
`chiste_normalizado` (varían de una llamada a otra) — solo se verifica forma
(string no vacío) y, para `estado`, PERTENENCIA al enum de 3 valores (no un
valor fijo). Una sola llamada real, sin reintento.
"""
from pathlib import Path

import pytest

from src.jokes.silver import ChisteEstructurado, estructurar_chiste
from src.jokes.supabase_store import ESTADOS_CHISTE
from src.utils.llm.client import LLMClientError

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "Freskito-Informático.md"
)

# Segunda unidad de chiste real y completa del fixture (línea 3): copiada
# literal del .md, incluye su etiqueta [REMATE] tal cual está en el fichero.
CHISTE_REAL = (
    "Soy malísimo con los idiomas, incluso con el mio, tengo faltas de "
    "ortografía [REMATE]hasta en los exámenes orales[/REMATE]. La razón que "
    "tenian mis padres cuando me decían “Estudia INglés…en el "
    "futuro si no tienes idiomas no te vas a comer nada…” y joder "
    "tenían razón. [REMATE]La cantidad de guiris que hay en Tinder[/REMATE]…"
)


def _confirmar_chiste_en_fixture_real() -> None:
    contenido = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "Soy malísimo con los idiomas" in contenido, (
        "El chiste usado en este test de integración debe copiarse literal "
        f"de {FIXTURE_PATH} (regla del proyecto: nunca inventar fixtures)."
    )


def test_estructurar_chiste_contra_gemini_real():
    _confirmar_chiste_en_fixture_real()

    try:
        resultado = estructurar_chiste(CHISTE_REAL)
    except LLMClientError as exc:
        pytest.skip(f"LLM_API_KEY/LLM_MODEL no disponibles en este entorno: {exc}")

    assert isinstance(resultado, ChisteEstructurado)

    # estado: enum estricto — pertenencia, no valor exacto (generativo).
    assert resultado.estado in ESTADOS_CHISTE

    # Resto de campos: string no vacío, sin asumir contenido exacto.
    for campo in ("tema", "estructura_detectada", "sugerencias_mejora", "chiste_normalizado"):
        valor = getattr(resultado, campo)
        assert isinstance(valor, str)
        assert valor.strip(), f"'{campo}' no debe venir vacío"
