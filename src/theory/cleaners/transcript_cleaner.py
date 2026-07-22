"""transcript_cleaner — Flujo A (Teoría), Cleaner agresivo para subtipo=explicacion.

Contrato (`src/theory/SPEC.md` §Limpieza, `docs/CORPUS_INVENTORY.md` Bloque 2):
se ejecuta DESPUÉS de `subtype_detector.py` en la cadena
`DriveMonitor -> Parser -> SubtypeDetector -> Cleaner -> ...` y consume su
salida: una lista de `FragmentoSubtipo(texto, subtipo)` (o directamente tuplas
`(texto, subtipo)`, para poder testear/usar la limpieza sin pasar por el
detector).

- `subtipo="explicacion"`: limpieza AGRESIVA — elimina muletillas (ver
  `_MULETILLAS` abajo), elimina repeticiones de palabras y de frases (cláusulas
  separadas por coma) consecutivas, y normaliza espacios/puntuación/mayúscula
  inicial del resultado.
- `subtipo="ejemplo"`: se conserva TAL CUAL, carácter a carácter — ni siquiera
  se tocan sus muletillas. Es una excepción explícita de la spec para no
  romper el estilo oral de los ejemplos (docs/CORPUS_INVENTORY.md, Bloque 5).
- Determinista, SIN LLM (regla de Flujo A, ver `docs/specs/llm-policy.md`).

## Muletillas — criterio de esta tarea

Lista cerrada y determinista (sin heurística estadística/LLM), basada en las
muletillas reales que aparecen en el fixture
`tests/fixtures/sample_transcript.txt` más variantes de la misma familia
documentadas en `docs/CORPUS_INVENTORY.md`: "o sea", "bueno,", "digamos",
"esto es" (uso de relleno tipo "es decir", no la construcción "eso es"),
"eh", "básicamente," (como inciso), y las coletillas interrogativas de cierre
"¿vale?" / "¿no?". Cada patrón exige límite de palabra (`\b`) para no comerse
palabras que las contienen o que coinciden por casualidad (p.ej. "o Deni" no
coincide con "o sea"; "Eso es" no coincide con "esto es").

## Repeticiones — criterio de esta tarea

Dos niveles, ambos "consecutivos" (nunca se borra una repetición separada por
contenido intermedio, para no alterar el sentido):
- Palabra repetida consecutiva (típico de disfluencias de transcripción:
  "el el gato" -> "el gato"), comparación case-insensitive.
- Frase/cláusula repetida consecutiva, delimitada por comas dentro del mismo
  fragmento ("vamos a verlo, vamos a verlo, con calma" -> "vamos a verlo, con
  calma"), comparación case-insensitive tras recortar espacios.

## Párrafos coherentes

`agrupar_en_parrafos` junta fragmentos consecutivos de `explicacion` en un
único párrafo (ya limpios), y aísla cada fragmento `ejemplo` como su propio
párrafo — refleja que un ejemplo suele ser una cita/frase suelta dentro de una
explicación más larga (mismo criterio de granularidad documentado en
`subtype_detector.py`).

## Gap de scope documentado: "corrige errores obvios de transcripción"

`docs/CORPUS_INVENTORY.md` (Bloque 2) pide también "corregir errores obvios de
transcripción" como parte de la limpieza agresiva. Esta tarea NO lo
implementa: hacerlo de forma determinista y sin LLM exigiría, o bien un
diccionario de correcciones ad-hoc por palabra/frase, o bien una heurística
gramatical/ortográfica que es frágil y fácil de dejar rota fuera de los casos
vistos. No existe ningún fixture real con errores de transcripción
identificados sobre los que basar esa heurística sin inventarla (violaría la
regla del proyecto de nunca inventar fixtures). Se documenta aquí explícitamente
en vez de omitirse en silencio — mismo patrón que el umbral de OCR sin fixture
real de `pdf_parser` (aprobado por el reviewer en la task 6). Queda como tarea
futura si aparecen errores reales y recurrentes en el corpus sobre los que
construir el diccionario/heurística con fixtures reales.
"""
import re
from dataclasses import dataclass

from src.theory.detectors.subtype_detector import FragmentoSubtipo

# Muletillas a eliminar en subtipo=explicacion (ver docstring del módulo para
# el criterio). Cada patrón incluye la coma final opcional para no dejar un
# rastro de puntuación colgando ("Bueno, yo..." -> elimina "Bueno," entero).
_MULETILLAS = (
    r"\bo sea\b,?",
    r"\bbueno\b,?",
    r"\bdigamos\b,?",
    r"\besto es\b,?",
    r"\beh\b,?",
    r"\bbásicamente\b,?",
    r"¿\s*vale\s*\?",
    r"¿\s*no\s*\?",
)
_MULETILLA_PATTERNS = [re.compile(patron, re.IGNORECASE) for patron in _MULETILLAS]

# Palabra repetida consecutiva (case-insensitive vía backreference bajo
# re.IGNORECASE): "el el gato Gato" -> "el gato".
_PALABRA_REPETIDA_RE = re.compile(r"\b(\w+)\b(\s+\1\b)+", re.IGNORECASE)

_SUBTIPOS_VALIDOS = ("explicacion", "ejemplo")


@dataclass
class FragmentoLimpio:
    """Un fragmento tras pasar por el Cleaner, con su `subtipo` preservado.

    Para `subtipo="ejemplo"`, `texto` es idéntico al fragmento de entrada.
    Para `subtipo="explicacion"`, `texto` es el resultado de la limpieza
    agresiva (ver docstring del módulo).
    """

    texto: str
    subtipo: str


def _texto_y_subtipo(fragmento) -> tuple[str, str]:
    """Normaliza la entrada: acepta `FragmentoSubtipo` o tupla `(texto, subtipo)`."""
    if isinstance(fragmento, FragmentoSubtipo):
        return fragmento.texto, fragmento.subtipo
    texto, subtipo = fragmento
    return texto, subtipo


def _eliminar_muletillas(texto: str) -> str:
    """Elimina todas las muletillas de `_MULETILLAS` (case-insensitive)."""
    for patron in _MULETILLA_PATTERNS:
        texto = patron.sub("", texto)
    return texto


def _eliminar_frases_repetidas(texto: str) -> str:
    """Colapsa cláusulas (separadas por coma) repetidas de forma consecutiva.

    Compara cada cláusula recortada de espacios, en minúsculas, contra la
    cláusula inmediatamente anterior; si coinciden, descarta la repetición.
    No reordena ni toca cláusulas no consecutivas.
    """
    clausulas = texto.split(",")
    resultado: list[str] = []
    anterior_normalizada = None
    for clausula in clausulas:
        normalizada = clausula.strip().lower()
        if normalizada and normalizada == anterior_normalizada:
            continue
        resultado.append(clausula)
        anterior_normalizada = normalizada
    return ",".join(resultado)


def _eliminar_palabras_repetidas(texto: str) -> str:
    """Colapsa palabras repetidas de forma consecutiva (case-insensitive)."""
    return _PALABRA_REPETIDA_RE.sub(lambda m: m.group(1), texto)


def _normalizar_espacios_y_puntuacion(texto: str) -> str:
    """Limpia artefactos de espacios/puntuación dejados por las eliminaciones.

    Colapsa comas y espacios duplicados, recorta comas/espacios sobrantes al
    inicio y al final, capitaliza la primera letra y asegura un signo de
    puntuación final si no queda ninguno.
    """
    texto = re.sub(r",\s*,", ",", texto)
    texto = re.sub(r"\s{2,}", " ", texto)
    texto = re.sub(r"\s+([,.!?])", r"\1", texto)
    texto = re.sub(r"^[,\s]+", "", texto)
    texto = re.sub(r"[,\s]+$", "", texto)
    texto = texto.strip()

    if texto and texto[0].isalpha():
        texto = texto[0].upper() + texto[1:]
    if texto and texto[-1] not in ".!?":
        texto += "."
    return texto


def _limpiar_agresivo(texto: str) -> str:
    """Pipeline completo de limpieza agresiva para `subtipo=explicacion`."""
    texto = _eliminar_muletillas(texto)
    texto = _eliminar_frases_repetidas(texto)
    texto = _eliminar_palabras_repetidas(texto)
    texto = _normalizar_espacios_y_puntuacion(texto)
    return texto


def clean_fragment(fragmento) -> FragmentoLimpio:
    """Limpia un fragmento según su `subtipo`.

    `explicacion` -> limpieza agresiva (`_limpiar_agresivo`).
    `ejemplo` -> se devuelve tal cual, sin ninguna modificación (excepción
    explícita de la spec).

    Acepta `FragmentoSubtipo` (salida real de `subtype_detector.py`) o una
    tupla `(texto, subtipo)`.
    """
    texto, subtipo = _texto_y_subtipo(fragmento)

    if subtipo not in _SUBTIPOS_VALIDOS:
        raise ValueError(
            f"subtipo desconocido {subtipo!r}: se esperaba uno de {_SUBTIPOS_VALIDOS}"
        )

    if subtipo == "ejemplo":
        return FragmentoLimpio(texto=texto, subtipo=subtipo)

    return FragmentoLimpio(texto=_limpiar_agresivo(texto), subtipo=subtipo)


def clean_fragments(fragmentos) -> list[FragmentoLimpio]:
    """Aplica `clean_fragment` a una lista de fragmentos, preservando el orden."""
    return [clean_fragment(fragmento) for fragmento in fragmentos]


def agrupar_en_parrafos(fragmentos_limpios: list[FragmentoLimpio]) -> list[str]:
    """Agrupa `FragmentoLimpio` consecutivos en párrafos coherentes.

    Fragmentos consecutivos de `subtipo="explicacion"` se unen en un único
    párrafo (join con espacio). Cada fragmento `subtipo="ejemplo"` se aísla
    como su propio párrafo, sin fundirse con la explicación circundante (ver
    docstring del módulo, §Párrafos coherentes).
    """
    parrafos: list[str] = []
    buffer_explicacion: list[str] = []

    for fragmento in fragmentos_limpios:
        if fragmento.subtipo == "ejemplo":
            if buffer_explicacion:
                parrafos.append(" ".join(buffer_explicacion))
                buffer_explicacion = []
            parrafos.append(fragmento.texto)
        else:
            buffer_explicacion.append(fragmento.texto)

    if buffer_explicacion:
        parrafos.append(" ".join(buffer_explicacion))

    return parrafos
