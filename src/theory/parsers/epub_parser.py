"""epub_parser — Flujo A (Teoría), Parser de EPUBs vía markitdown (P17).

Contrato (src/theory/SPEC.md §Parser — decisión markitdown P17):
- Conversión con `markitdown` (determinista, sin LLM: no se activa ningún
  plugin de markitdown, en particular el captioning de imágenes vía LLM —
  ver `docs/specs/llm-policy.md`, regla "sin LLM" para Flujo A).
- markitdown extrae texto presente en EPUBs con estructura de contenido
  legible (incluye metadata del libro si está disponible).
- No toca el fichero de origen (sagrado, ver CLAUDE.md) — solo lo lee.
- A diferencia de pdf_parser, no hay OCR fallback para EPUBs: la decisión
  y evaluación ya se hizo en P17 (markitdown cubre EPUB con calidad
  suficiente en los 3 libros reales del corpus).
"""
from dataclasses import dataclass
from pathlib import Path

from markitdown import MarkItDown


@dataclass
class EpubParseado:
    """Salida del parser: texto Markdown extraído del EPUB."""

    texto: str


def parse_epub(path: Path) -> EpubParseado:
    """Parsea un EPUB a texto Markdown.

    Camino único: `markitdown` (determinista, sin LLM, sin plugins) sobre
    el fichero original. markitdown extrae la metadata del EPUB (título,
    autores si está disponible) como frontmatter Markdown, seguido del
    contenido textual.

    No modifica `path` (fichero de origen sagrado, ver CLAUDE.md) — solo lo lee.
    """
    path = Path(path)

    # enable_plugins=False explícito: sin captioning de imágenes vía LLM ni
    # ningún otro plugin de markitdown — regla "sin LLM" del Flujo A.
    conversor = MarkItDown(enable_plugins=False)
    resultado = conversor.convert(str(path))
    texto = resultado.text_content

    return EpubParseado(texto=texto)
