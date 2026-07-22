"""pdf_parser — Flujo A (Teoría), Parser de PDFs vía markitdown (P17).

Contrato (src/theory/SPEC.md §Parser — decisión markitdown P17):
- Conversión PRIMARIA con `markitdown` (determinista, sin LLM: no se activa
  ningún plugin de markitdown, en particular el captioning de imágenes vía
  LLM — ver `docs/specs/llm-policy.md`, regla "sin LLM" para Flujo A).
- markitdown solo re-extrae texto YA PRESENTE en el PDF (no hace OCR). Para
  páginas realmente escaneadas (sin capa de texto) hace falta Tesseract como
  *fallback*, invocado cuando markitdown no extrae texto útil.
- No toca el fichero de origen (sagrado, ver CLAUDE.md) — solo lo lee.

Criterio de fallback (MVP, sin granularidad perfecta por página): se compara
la densidad media de caracteres extraídos por markitdown (caracteres totales
/ número de páginas del PDF) contra un umbral mínimo. Si la densidad es
demasiado baja, se asume que el documento (o una parte sustancial de él) está
escaneado sin capa de texto útil, y se rasteriza CADA página con `pdf2image`
aplicando OCR con `pytesseract`, sustituyendo el texto de markitdown por el
texto OCR completo del documento. No se aísla el fallback página a página
(no hace falta para el MVP: ver tarea del backlog) — la decisión y la unidad
de sustitución son a nivel de documento completo.
"""
from dataclasses import dataclass
from pathlib import Path

from markitdown import MarkItDown
from pdf2image import convert_from_path, pdfinfo_from_path
import pytesseract

UMBRAL_CHARS_POR_PAGINA = 30
"""Densidad mínima (caracteres útiles / página) por debajo de la cual se
considera que markitdown NO extrajo texto útil y se dispara el fallback OCR.
Valor conservador: una página con contenido real de teoría/curso tiene
cientos de caracteres; una página escaneada sin capa de texto devuelve 0
caracteres (o ruido residual insignificante)."""


@dataclass
class PdfParseado:
    """Salida del parser: texto Markdown extraído + metadatos de la extracción."""

    texto: str
    num_paginas: int
    ocr_aplicado: bool


def _necesita_ocr_fallback(
    texto: str,
    num_paginas: int,
    umbral_chars_por_pagina: float = UMBRAL_CHARS_POR_PAGINA,
) -> bool:
    """Decide si el texto extraído por markitdown es insuficiente y hace falta OCR.

    Función pura (sin I/O, sin dependencias de markitdown/tesseract): compara
    la densidad de caracteres por página contra `umbral_chars_por_pagina`.
    Aislada así para poder testear la lógica de decisión con inputs directos,
    sin necesitar un PDF escaneado real de por medio.
    """
    if num_paginas <= 0:
        return False  # no hay páginas que rasterizar; nada que decidir
    densidad = len(texto.strip()) / num_paginas
    return densidad < umbral_chars_por_pagina


def _ocr_documento(path: Path) -> str:
    """Rasteriza todas las páginas del PDF (`pdf2image`) y aplica OCR (`pytesseract`).

    Fallback para PDFs sin capa de texto extraíble por markitdown. Un bloque
    de texto por página, páginas separadas por línea en blanco doble.
    """
    paginas = convert_from_path(str(path))
    textos_por_pagina = [pytesseract.image_to_string(pagina).strip() for pagina in paginas]
    return "\n\n".join(texto for texto in textos_por_pagina if texto)


def parse_pdf(
    path: Path, umbral_chars_por_pagina: float = UMBRAL_CHARS_POR_PAGINA
) -> PdfParseado:
    """Parsea un PDF a texto Markdown.

    Camino primario: `markitdown` (determinista, sin LLM, sin plugins) sobre
    el fichero original. Si el texto extraído es insuficiente (ver
    `_necesita_ocr_fallback`), recurre a Tesseract OCR sobre las páginas
    rasterizadas con `pdf2image`, sustituyendo el texto completo.

    No modifica `path` (fichero de origen sagrado, ver CLAUDE.md) — solo lo lee.
    """
    path = Path(path)

    info = pdfinfo_from_path(str(path))
    num_paginas = info.get("Pages", 0)

    # enable_plugins=False explícito: sin captioning de imágenes vía LLM ni
    # ningún otro plugin de markitdown — regla "sin LLM" del Flujo A.
    conversor = MarkItDown(enable_plugins=False)
    resultado = conversor.convert(str(path))
    texto = resultado.text_content

    ocr_aplicado = False
    if _necesita_ocr_fallback(texto, num_paginas, umbral_chars_por_pagina):
        texto_ocr = _ocr_documento(path)
        if texto_ocr:
            texto = texto_ocr
            ocr_aplicado = True

    return PdfParseado(texto=texto, num_paginas=num_paginas, ocr_aplicado=ocr_aplicado)
