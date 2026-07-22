"""Tests para docx_parser (Flujo A, contrato en src/theory/SPEC.md §Parser — P17).

Contrato: conversión con markitdown (determinista, sin LLM, sin plugins).
A diferencia de pdf_parser, no hay OCR fallback para .docx: es un formato
nativo (no escaneado), así que markitdown debe bastar siempre.

Fixtures:
- tests/fixtures/comedy_bible_excerpt.docx (fixture real, .docx con un excerpt
  de "The Comedy Bible" de Judy Carter, re-empaquetado a .docx desde el PDF
  original). Se comprueba que se extrae el texto real y contiene frases
  reconocibles del excerpt.
"""
from pathlib import Path

from src.theory.parsers.docx_parser import parse_docx

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
COMEDY_BIBLE_EXCERPT = FIXTURES_DIR / "comedy_bible_excerpt.docx"


def test_extrae_texto_real_del_docx():
    resultado = parse_docx(COMEDY_BIBLE_EXCERPT)

    # Verificamos que se extrajo texto real del fixture.
    assert len(resultado.texto) > 0
    # El fixture contiene un excerpt de "The Comedy Bible", así que el título
    # debe estar presente.
    assert "Comedy Bible" in resultado.texto or "comedy" in resultado.texto.lower()


def test_extrae_contenido_de_parrafos():
    resultado = parse_docx(COMEDY_BIBLE_EXCERPT)

    # El fixture tiene párrafos reales de prosa. Verificamos que se extrajo
    # algo más que solo metadata (> 100 chars es una ballpark razonable).
    assert len(resultado.texto) > 100
