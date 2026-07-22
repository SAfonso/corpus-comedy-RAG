"""subtype_detector — Flujo A (Teoría), SubtypeDetector (explicacion|ejemplo).

Contrato (src/theory/SPEC.md §Cadena de componentes): clasifica el texto ya
producido por un Parser (`whisperx_parser`/`pdf_parser`/`docx_parser`/
`epub_parser` — texto plano o Markdown de UN documento completo, ya sin
timestamps ni marcado de formato de origen) en una lista de fragmentos, cada
uno con su `subtipo`. Se ejecuta ANTES del Cleaner porque este aplica reglas
de limpieza distintas según `subtipo`: agresiva para `explicacion`, conserva
el estilo oral para `ejemplo` (ver `src/theory/SPEC.md` §Limpieza).

Heurística (determinista, SIN LLM — regla de Flujo A, ver
`docs/specs/llm-policy.md`): basada en los marcadores lingüísticos ya
documentados en `docs/CORPUS_INVENTORY.md` (Bloque 5) — "un ejemplo", "por
ejemplo", "algo así como" (con variante sin tilde "algo asi como", ya que la
transcripción de origen no siempre acentúa correctamente). Un fragmento es
`ejemplo` si contiene alguno de estos marcadores (case-insensitive);
`explicacion` en caso contrario. No se reinterpreta ni resume el contenido:
solo se trocea el texto de entrada y se clasifica cada trozo tal cual.

Granularidad — decisión de esta tarea (documentada aquí igual que el umbral
OCR de `pdf_parser`): se clasifica por FRASE, no por documento completo ni
por párrafo. Los ejemplos suelen aparecer como una única frase suelta dentro
de un bloque de explicación más largo (ver fixture
`tests/fixtures/sample_transcript.txt`: "Por ejemplo, imagina que dices:
..." es una frase suelta en medio de la explicación de la regla de tres).
Clasificar por documento completo o por párrafo perdería esa granularidad y
el Cleaner acabaría aplicando limpieza agresiva también al ejemplo (o
conservando estilo oral en explicaciones), rompiendo el contrato de
`src/theory/SPEC.md` §Limpieza. Frase = texto entre delimitadores de fin de
frase ('.', '!', '?') seguidos de espacio en blanco; los puntos suspensivos
("...") NO cuentan como fin de frase (ver `_DIVISOR_FRASES`), para no cortar
a mitad frases con elipsis como la del propio fixture.
"""
import re
from dataclasses import dataclass

# Marcadores lingüísticos de ejemplo (docs/CORPUS_INVENTORY.md, Bloque 5),
# en minúsculas para comparación case-insensitive. Se incluyen ambas formas
# (con/sin tilde) de "así" porque las transcripciones de origen no siempre
# acentúan correctamente.
_MARCADORES_EJEMPLO = (
    "un ejemplo",
    "por ejemplo",
    "algo así como",
    "algo asi como",
)

# Divide en frases por '.', '!' o '?' seguido de espacio en blanco, salvo
# cuando el punto forma parte de puntos suspensivos ("..."): el lookbehind
# negativo comprueba que los dos caracteres previos a la posición de corte
# no sean ambos '.', para no partir frases con elipsis a mitad.
_DIVISOR_FRASES = re.compile(r"(?<!\.\.)(?<=[.!?])\s+")


@dataclass
class FragmentoSubtipo:
    """Una frase del documento clasificada con su `subtipo`.

    `subtipo` es `"explicacion"` o `"ejemplo"`.
    """

    texto: str
    subtipo: str


def _es_ejemplo(frase: str) -> bool:
    """True si `frase` contiene algún marcador lingüístico de ejemplo."""
    frase_lower = frase.lower()
    return any(marcador in frase_lower for marcador in _MARCADORES_EJEMPLO)


def detect_subtypes(texto: str) -> list[FragmentoSubtipo]:
    """Trocea `texto` en frases y clasifica cada una en `explicacion`/`ejemplo`.

    Entrada: texto plano/Markdown de un documento completo, ya parseado por
    uno de los Parsers de Flujo A (p.ej. `TranscripcionParseada.texto`).
    Salida: lista de `FragmentoSubtipo` en el mismo orden en que aparecen en
    el texto de entrada, sin frases vacías. No modifica el contenido de cada
    frase (solo recorta espacio en blanco sobrante de los bordes al trocear).
    """
    frases = [f.strip() for f in _DIVISOR_FRASES.split(texto.strip()) if f.strip()]
    return [
        FragmentoSubtipo(
            texto=frase,
            subtipo="ejemplo" if _es_ejemplo(frase) else "explicacion",
        )
        for frase in frases
    ]
