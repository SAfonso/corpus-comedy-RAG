"""Tests para pdf_parser (Flujo A, contrato en src/theory/SPEC.md §Parser — P17).

Contrato: conversión PRIMARIA con markitdown (determinista, sin LLM, sin
plugins); fallback OCR con pdf2image + pytesseract cuando markitdown no
extrae texto útil de un PDF.

Fixtures:
- Camino primario: tests/fixtures/sample_transcript.pdf (fixture real, PDF
  con capa de texto nativa, 2 páginas). Se comprueba que se extrae el texto
  real y no se dispara el fallback OCR.
- Camino de fallback OCR: NO existe en el repo ningún PDF escaneado real
  (los 3 libros de data/raw/books/ extraen 395k-749k caracteres con
  markitdown — ninguno está escaneado). Por la regla del proyecto de no
  inventar fixtures, el fallback se testea de forma aislada mediante
  `_necesita_ocr_fallback`, una función pura de decisión (sin I/O) que recibe
  texto e info de páginas directamente — no se mockea pytesseract/pdf2image
  simulando que "funcionan" sobre contenido inventado.
"""
from pathlib import Path

from src.theory.parsers.pdf_parser import (
    UMBRAL_CHARS_POR_PAGINA,
    _necesita_ocr_fallback,
    parse_pdf,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_PDF = FIXTURES_DIR / "sample_transcript.pdf"


def test_camino_primario_extrae_texto_real_del_pdf():
    resultado = parse_pdf(SAMPLE_PDF)

    assert resultado.num_paginas == 2
    assert "Rutina de stand-up" in resultado.texto
    assert "el botón sí" in resultado.texto
    assert "la puerta del" in resultado.texto


def test_camino_primario_no_dispara_fallback_ocr_con_texto_nativo():
    resultado = parse_pdf(SAMPLE_PDF)

    # El PDF fixture tiene capa de texto nativa (no escaneado) -> markitdown
    # solo debe bastar, sin necesidad de Tesseract.
    assert resultado.ocr_aplicado is False


def test_necesita_ocr_fallback_con_texto_vacio_y_paginas():
    # 0 caracteres en un documento de varias páginas -> claramente insuficiente.
    assert _necesita_ocr_fallback("", num_paginas=5) is True


def test_necesita_ocr_fallback_con_densidad_por_debajo_del_umbral():
    # 10 páginas, 50 caracteres en total -> 5 chars/página, por debajo del
    # umbral por defecto (30).
    texto_pobre = "x" * 50
    assert _necesita_ocr_fallback(texto_pobre, num_paginas=10) is True


def test_no_necesita_ocr_fallback_con_texto_suficiente():
    # 2 páginas, texto largo -> densidad muy por encima del umbral.
    texto_suficiente = "contenido real de teoría " * 50  # bastante más de 60 chars/página
    assert _necesita_ocr_fallback(texto_suficiente, num_paginas=2) is False


def test_necesita_ocr_fallback_umbral_personalizable():
    texto = "x" * 100  # 20 chars/página en 5 páginas
    assert _necesita_ocr_fallback(texto, num_paginas=5, umbral_chars_por_pagina=10) is False
    assert _necesita_ocr_fallback(texto, num_paginas=5, umbral_chars_por_pagina=30) is True


def test_necesita_ocr_fallback_sin_paginas_no_revienta():
    # num_paginas=0 (p.ej. pdfinfo falló en detectar páginas): no hay nada
    # que rasterizar, no debe intentar disparar OCR sobre "0 páginas".
    assert _necesita_ocr_fallback("", num_paginas=0) is False


def test_umbral_por_defecto_es_el_esperado():
    # Valor documentado en el módulo — si cambia, este test debe actualizarse
    # deliberadamente (no un valor mágico duplicado a ciegas).
    assert UMBRAL_CHARS_POR_PAGINA == 30
