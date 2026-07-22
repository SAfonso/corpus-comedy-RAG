"""Tests para language_detector (utils, contrato en src/utils/SPEC.md).

Contrato:
- `detect_language(texto)` devuelve el código ISO 639-1 del idioma detectado.
- Determinista (semilla fija de `langdetect`, ver docstring del módulo).
- Texto vacío/en blanco, o sin señal lingüística suficiente -> `ValueError`
  (nunca un idioma "por defecto" silencioso).

Usa los dos fixtures reales es/en ya disponibles en el repo (regla del
proyecto: nunca inventar fixtures):
- `tests/fixtures/sample_transcript.txt` (español real, vía
  `parse_whisperx_transcript`).
- `tests/fixtures/comedy_bible_excerpt.docx` (inglés real, prosa de
  "The Comedy Bible", vía `parse_docx`).
"""
from pathlib import Path

import pytest

from src.theory.parsers.docx_parser import parse_docx
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript
from src.utils.language_detector import detect_language

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"
COMEDY_BIBLE_EXCERPT = FIXTURES_DIR / "comedy_bible_excerpt.docx"


def test_detecta_espanol_en_transcripcion_real():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    assert detect_language(texto) == "es"


def test_detecta_ingles_en_excerpt_real():
    texto = parse_docx(COMEDY_BIBLE_EXCERPT).texto
    assert detect_language(texto) == "en"


def test_detecta_espanol_en_frase_suelta_real_del_fixture():
    # Frase suelta real del fixture (no inventada): ejercita el caso de un
    # fragmento corto, más parecido a lo que le llega desde el Cleaner que
    # el documento completo.
    frase = "Y hoy vamos a hablar de la regla de tres."
    assert detect_language(frase) == "es"


def test_texto_vacio_lanza_value_error():
    with pytest.raises(ValueError):
        detect_language("")


def test_texto_solo_espacios_lanza_value_error():
    with pytest.raises(ValueError):
        detect_language("   \n\t  ")


def test_es_deterministico_entre_llamadas():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    resultados = {detect_language(texto) for _ in range(5)}
    assert resultados == {"es"}
