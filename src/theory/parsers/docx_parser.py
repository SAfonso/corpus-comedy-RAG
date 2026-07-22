"""docx_parser — Flujo A (Teoría), Parser de DOCXs vía markitdown (P17).

Contrato (src/theory/SPEC.md §Parser — decisión markitdown P17):
- Conversión con `markitdown` (determinista, sin LLM: no se activa ningún
  plugin de markitdown, en particular el captioning de imágenes vía LLM —
  ver `docs/specs/llm-policy.md`, regla "sin LLM" para Flujo A).
- markitdown extrae texto presente en DOCXs con estructura de contenido
  legible (incluye metadata del documento si está disponible).
- No toca el fichero de origen (sagrado, ver CLAUDE.md) — solo lo lee.
- A diferencia de pdf_parser, no hay OCR fallback para DOCXs: un .docx es un
  formato nativo (no escaneado), así que markitdown siempre debe extraer
  texto útil sin necesidad de Tesseract. La decisión se alinea con la de
  epub_parser (P17): markitdown es suficiente para formatos no escaneados.
"""
from dataclasses import dataclass
from pathlib import Path

from markitdown import MarkItDown


@dataclass
class DocxParseado:
    """Salida del parser: texto Markdown extraído del DOCX."""

    texto: str


def parse_docx(path: Path) -> DocxParseado:
    """Parsea un DOCX a texto Markdown.

    Camino único: `markitdown` (determinista, sin LLM, sin plugins) sobre
    el fichero original. markitdown extrae la metadata del DOCX si está
    disponible, seguido del contenido textual (headings, párrafos, listas, etc.).

    No modifica `path` (fichero de origen sagrado, ver CLAUDE.md) — solo lo lee.
    """
    path = Path(path)

    # enable_plugins=False explícito: sin captioning de imágenes vía LLM ni
    # ningún otro plugin de markitdown — regla "sin LLM" del Flujo A.
    conversor = MarkItDown(enable_plugins=False)
    resultado = conversor.convert(str(path))
    texto = resultado.text_content

    return DocxParseado(texto=texto)
