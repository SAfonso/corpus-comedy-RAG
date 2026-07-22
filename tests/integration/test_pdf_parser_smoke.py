"""Smoke test de integración para pdf_parser (Flujo A, P17) contra libros reales.

No es un test unitario formal con asserts exactos de contenido: es una
comprobación de cordura (sanity check) de solo lectura sobre 2 de los PDFs
reales en data/raw/books/ — confirma que markitdown extrae una cantidad de
texto no trivial y que, por tanto, el fallback OCR no se dispara para estos
libros concretos (ya investigado por el leader: ninguno de los 3 PDFs reales
tiene páginas escaneadas; extraen entre 395k y 749k caracteres cada uno).

Solo lectura: nunca modifica los ficheros de data/raw/books/ (material
sagrado, ver CLAUDE.md).
"""
from pathlib import Path

import pytest

from src.theory.parsers.pdf_parser import parse_pdf

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOKS_DIR = REPO_ROOT / "data" / "raw" / "books"

LIBROS_REALES = [
    BOOKS_DIR / "Judy_Carter_The_Comedy_Bible.pdf",
    BOOKS_DIR / "dokumen.pub_a-directors-guide-to-the-art-of-stand-up-9781350035522-9781350035553-9781350035546.pdf",
]


@pytest.mark.parametrize("libro", LIBROS_REALES, ids=lambda p: p.name)
def test_markitdown_extrae_texto_no_trivial_de_libro_real(libro):
    if not libro.exists():
        pytest.skip(f"Fixture real no disponible en este entorno: {libro}")

    resultado = parse_pdf(libro)

    # Umbral de cordura, no un valor exacto: un libro real de cientos de
    # páginas debe producir bastante más que un puñado de caracteres.
    assert len(resultado.texto) > 100_000
    assert resultado.num_paginas > 0
    # Ninguno de estos libros está escaneado (ver docstring) -> no debe
    # haberse disparado el fallback OCR.
    assert resultado.ocr_aplicado is False
