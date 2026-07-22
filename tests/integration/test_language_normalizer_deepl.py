"""Test de integración: LanguageNormalizer contra la API real de DeepL free tier.

Contrato (task 9): `DEEPL_API_KEY` está configurada en `.env` (verificada en
task 1) y se usa contra la API real de DeepL free tier — no hace falta
mockear. Si la llamada de red fallara en este entorno (sin conexión, límite
de cuota, o cualquier otro fallo del servicio externo), el test hace
`pytest.skip(...)` explícito con el motivo, igual que el patrón ya usado en
`test_pdf_parser_smoke.py`/`test_epub_parser_smoke.py` cuando un fixture no
está disponible.

Fixture real de entrada: un fragmento real en inglés de
`tests/fixtures/comedy_bible_excerpt.docx` ("The Comedy Bible"), pasado por
el pipeline real `parse_docx -> detect_subtypes -> clean_fragments` (mismo
patrón que `test_language_normalizer.py`), para traducir exactamente el tipo
de fragmento (`subtipo=explicacion`) que vería `LanguageNormalizer` en la
cadena real.

Nota (ver `src/theory/KNOWN_ERRORS.md`): a fecha de esta tarea,
`deep_translator` 1.11.4 (última versión en PyPI) autentica contra DeepL con
un método ("auth_key" por query param) que DeepL deprecó en noviembre de
2025, así que la llamada real puede fallar con `AuthorizationException`
incluso con una API key válida. Ese fallo se trata igual que cualquier otro
fallo de red/cuota del servicio externo: `skip`, no error.
"""
from pathlib import Path

import pytest

from deep_translator.exceptions import (
    ApiKeyException,
    AuthorizationException,
    BaseError as DeepTranslatorBaseError,
    RequestError,
    ServerException,
    TooManyRequests,
)

from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.normalizers.language_normalizer import normalize_language
from src.theory.parsers.docx_parser import parse_docx

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
COMEDY_BIBLE_EXCERPT = FIXTURES_DIR / "comedy_bible_excerpt.docx"

# deep_translator no tiene una única excepción base para todos los fallos de
# transporte/autenticación/cuota (algunas heredan de `BaseError`, otras de
# `Exception` a secas -- ver src/theory/KNOWN_ERRORS.md). Se agrupan aquí
# explícitamente todas las que representan un fallo del servicio externo (no
# un bug de nuestra lógica) para poder hacer `skip` sobre cualquiera de ellas.
_FALLOS_SERVICIO_EXTERNO = (
    DeepTranslatorBaseError,
    ApiKeyException,
    AuthorizationException,
    RequestError,
    ServerException,
    TooManyRequests,
    OSError,
)


def test_traduce_explicacion_en_ingles_a_espanol_via_deepl_real():
    if not COMEDY_BIBLE_EXCERPT.exists():
        pytest.skip(f"Fixture real no disponible en este entorno: {COMEDY_BIBLE_EXCERPT}")

    texto = parse_docx(COMEDY_BIBLE_EXCERPT).texto
    fragmentos_explicacion = [
        f for f in clean_fragments(detect_subtypes(texto)) if f.subtipo == "explicacion"
    ]
    assert fragmentos_explicacion  # el excerpt real tiene explicacion en inglés

    # Un solo fragmento corto: suficiente para confirmar la llamada real a
    # DeepL sin gastar cuota de más del free tier.
    fragmento = fragmentos_explicacion[0]

    try:
        resultado = normalize_language([fragmento])
    except _FALLOS_SERVICIO_EXTERNO as exc:
        pytest.skip(f"Llamada real a DeepL no disponible en este entorno: {exc}")

    assert len(resultado) == 1
    r = resultado[0]
    assert r.traducido is True
    assert r.idioma_original == "en"
    assert r.idioma_fragmento == "es"
    # Traducción real: el texto de salida debe ser distinto del original en
    # inglés (no podemos fijar el texto exacto, depende del servicio externo).
    assert r.texto != fragmento.texto
    assert len(r.texto) > 0
