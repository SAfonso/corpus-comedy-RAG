"""Smoke test de integración para epub_parser (Flujo A, P17) contra libros reales.

No es un test unitario formal con asserts exactos de contenido: es una
comprobación de cordura (sanity check) de solo lectura sobre 2 de los EPUBs
reales en data/raw/books/ — confirma que markitdown extrae una cantidad de
texto no trivial (> 50000 caracteres).

Solo lectura: nunca modifica los ficheros de data/raw/books/ (material
sagrado, ver CLAUDE.md).
"""
from pathlib import Path

import pytest

from src.theory.parsers.epub_parser import parse_epub

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOKS_DIR = REPO_ROOT / "data" / "raw" / "books"

LIBROS_REALES = [
    BOOKS_DIR / "dokumen.pub_step-by-step-to-stand-up-comedy-revised-edition.epub",
    BOOKS_DIR / "dokumen.pub_the-serious-guide-to-joke-writing-how-to-say-something-funny-about-anything.epub",
]


@pytest.mark.parametrize("libro", LIBROS_REALES, ids=lambda p: p.name)
def test_markitdown_extrae_texto_no_trivial_de_epub_real(libro):
    if not libro.exists():
        pytest.skip(f"Fixture real no disponible en este entorno: {libro}")

    resultado = parse_epub(libro)

    # Umbral de cordura, no un valor exacto: un libro real debe producir
    # bastante más que un puñado de caracteres.
    assert len(resultado.texto) > 50_000
