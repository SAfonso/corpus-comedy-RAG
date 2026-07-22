"""quality_scorer — utils, QualityScorer (densidad de contenido útil), Flujo A (Teoría).

Contrato (`src/theory/SPEC.md` §Cadena de componentes; `docs/CORPUS_INVENTORY.md`
Bloque 1, "Densidad de contenido útil": "Mucho relleno — el QualityScorer es
imprescindible, no nice-to-have"): puntúa de 0.0 a 1.0 la densidad de
contenido útil de un fragmento de texto YA LIMPIO, es decir la salida de
`transcript_cleaner.clean_fragment` (cadena real: `... -> Cleaner ->
LanguageDetector -> LanguageNormalizer -> QualityScorer -> FormatNormalizer ->
...`, ver `src/theory/SPEC.md`). Sirve para distinguir fragmentos sustanciosos
(teoría real, ejemplos con contenido) de fragmentos pobres/triviales (relleno,
saludos, coletillas residuales) dentro del "mucho relleno" que reporta el
inventario.

Determinista, SIN LLM (regla de Flujo A, ver `docs/specs/llm-policy.md`).
Función pura: sin E/S ni estado, mismo texto de entrada siempre produce el
mismo score. No importa nada de `theory/` ni `jokes/` (regla de dependencias,
ver CLAUDE.md raíz) — código genérico reutilizable, sin conocimiento de
`subtipo` ni de ningún concepto propio de un flujo.

## Fórmula — decisión de esta tarea

La spec (`src/theory/SPEC.md`) exige el score pero no fija una fórmula
exacta; se documenta aquí la elegida, mismo patrón que el umbral OCR de
`pdf_parser` (`UMBRAL_CHARS_POR_PAGINA`) o la granularidad por frase de
`subtype_detector`. Media ponderada de tres señales independientes, cada una
normalizada por separado a [0, 1] — cada una capta una forma distinta en la
que un fragmento puede ser "relleno" sin serlo en las otras dos:

1. **Longitud** (peso 0.4) — un fragmento de una sola frase suelta ("Hola,
   bienvenidos a este curso.") rara vez aporta contenido sustancial por
   gramaticalmente correcta que sea; las explicaciones sustanciosas del
   corpus real (`tests/fixtures/sample_transcript.txt`) se extienden varias
   frases. Se mide en palabras (tokenizadas con `_PALABRA_RE`, ver abajo) con
   una función de saturación `longitud / (longitud + K)` en vez de un tope
   duro: crece rápido al principio y se aplana, para no premiar sin límite un
   fragmento arbitrariamente largo ni penalizar con un corte brusco un
   fragmento de, p.ej., 24 palabras frente a uno de 26. `K=25` es una
   elección conservadora sin fixture de calibración exacta con la que fijar
   un valor "correcto" (igual que `UMBRAL_CHARS_POR_PAGINA` en `pdf_parser`):
   con `K=25`, una frase de ~25 palabras alcanza 0.5 en esta subseñal.
2. **Diversidad léxica (type-token ratio)** (peso 0.3) — palabras únicas /
   palabras totales, comparación case-insensitive. Un fragmento con mucha
   repetición de las mismas palabras (relleno, muletillas residuales que se
   le hayan escapado al Cleaner) tiene TTR bajo; una explicación con
   vocabulario variado, alto.
3. **Ratio de palabras de contenido** (peso 0.3) — `1 - (palabras
   funcionales / palabras totales)`, contra una lista cerrada de palabras
   funcionales españolas de alta frecuencia (artículos, preposiciones,
   conjunciones, pronombres, verbos copulativos — ver
   `_PALABRAS_FUNCIONALES`). Un fragmento dominado por palabras funcionales
   (saludos, coletillas, frases hechas) aporta menos densidad informativa que
   uno con muchos sustantivos/verbos/adjetivos de contenido.

`score = 0.4 * longitud + 0.3 * diversidad + 0.3 * contenido`, resultado
recortado a `[0.0, 1.0]` (cada subseñal ya cae en ese rango por construcción;
el recorte final es solo defensivo). Texto vacío o solo espacio en blanco, o
sin ninguna palabra tokenizable -> `0.0`: a diferencia de `detect_language`,
este componente no necesita distinguir "vacío" de "no se pudo decidir" con un
error — el contrato pedido es más simple (float en `[0.0, 1.0]` siempre), y
"sin contenido" es, por definición, densidad de contenido útil cero.

## Gap de scope documentado

No hay en el corpus real disponible ningún fixture de fragmento verdaderamente
"vacío" o de una sola palabra repetida (ver `tests/fixtures/sample_transcript.txt`
completo) — los tests de este módulo verifican orden relativo entre un
fragmento sustancioso real y el fragmento más corto/trivial real disponible
(una frase de saludo sin carga informativa), no un caso de relleno extremo
inventado, siguiendo la regla del proyecto de nunca inventar fixtures.
"""
import re

# Palabras funcionales españolas de alta frecuencia (artículos, preposiciones,
# conjunciones, pronombres, verbos copulativos/auxiliares comunes y
# adverbios de uso muy general). Lista cerrada y determinista, no exhaustiva
# de la lengua española: cubre las categorías gramaticales que típicamente
# dominan el relleno conversacional (saludos, coletillas, frases hechas) sin
# intentar ser un lematizador completo.
_PALABRAS_FUNCIONALES = frozenset(
    {
        "el", "la", "los", "las", "un", "una", "unos", "unas",
        "de", "del", "a", "al", "en", "con", "por", "para", "sin", "sobre", "entre", "hacia",
        "y", "o", "u", "e", "ni", "pero", "que", "si", "no",
        "yo", "tú", "tu", "él", "ella", "nosotros", "vosotros", "ellos", "ellas",
        "me", "te", "se", "nos", "os", "le", "les", "lo",
        "su", "sus", "mi", "mis", "tus",
        "esto", "eso", "esta", "este", "estos", "estas", "esos", "esas",
        "es", "soy", "eres", "son", "somos", "sois", "era", "fue", "ser", "estar", "está", "están",
        "como", "más", "muy", "también", "ya", "aquí", "así",
    }
)

# Tokeniza en "palabras": secuencias de letras (Unicode, incluye acentos/ñ),
# excluyendo dígitos y guión bajo. La puntuación (comas, puntos, signos de
# interrogación) queda fuera de las palabras, tal y como los deja
# `transcript_cleaner` en su salida.
_PALABRA_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokenizar(texto: str) -> list[str]:
    """Extrae las palabras de `texto` en minúsculas, sin puntuación ni dígitos."""
    return [palabra.lower() for palabra in _PALABRA_RE.findall(texto)]


def _score_longitud(num_palabras: int, k: float = 25.0) -> float:
    """Subseñal de longitud: satura hacia 1.0, ver docstring del módulo."""
    if num_palabras <= 0:
        return 0.0
    return num_palabras / (num_palabras + k)


def _score_diversidad_lexica(palabras: list[str]) -> float:
    """Type-token ratio: palabras únicas / palabras totales."""
    if not palabras:
        return 0.0
    return len(set(palabras)) / len(palabras)


def _score_contenido(palabras: list[str]) -> float:
    """1 - proporción de palabras funcionales (`_PALABRAS_FUNCIONALES`)."""
    if not palabras:
        return 0.0
    funcionales = sum(1 for palabra in palabras if palabra in _PALABRAS_FUNCIONALES)
    return 1.0 - (funcionales / len(palabras))


def score_quality(texto: str) -> float:
    """Puntúa `texto` (ya limpio) de 0.0 a 1.0 según su densidad de contenido útil.

    Ver docstring del módulo para la fórmula exacta (media ponderada de
    longitud, diversidad léxica y ratio de palabras de contenido) y su
    justificación. Texto vacío/en blanco o sin palabras tokenizables -> 0.0.
    Función pura y determinista, sin LLM.
    """
    if not texto or not texto.strip():
        return 0.0

    palabras = _tokenizar(texto)
    if not palabras:
        return 0.0

    longitud = _score_longitud(len(palabras))
    diversidad = _score_diversidad_lexica(palabras)
    contenido = _score_contenido(palabras)

    score = 0.4 * longitud + 0.3 * diversidad + 0.3 * contenido
    return max(0.0, min(1.0, score))
