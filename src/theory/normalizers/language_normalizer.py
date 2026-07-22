"""language_normalizer — Flujo A (Teoría), LanguageNormalizer (corpus bilingüe).

Contrato (`src/theory/SPEC.md` §Idioma, §Cadena de componentes): se ejecuta
DESPUÉS de `LanguageDetector` en la cadena
`... -> Cleaner -> LanguageDetector -> LanguageNormalizer -> QualityScorer -> ...`
y consume la salida del Cleaner (`transcript_cleaner.py`): una lista de
`FragmentoLimpio(texto, subtipo)` (o tuplas `(texto, subtipo)`, mismo patrón
de aceptación que ya usa `clean_fragment`).

Regla del corpus bilingüe (§Idioma): la teoría (`subtipo="explicacion"`) se
traduce a español; los ejemplos (`subtipo="ejemplo"`) se conservan SIEMPRE en
su idioma original, nunca se traducen, pase lo que pase — es una excepción
explícita de la spec, igual que el Cleaner conserva el estilo oral de los
ejemplos.

`LanguageDetector` en este módulo es `detect_language` de
`src/utils/language_detector.py` (código genérico compartido, ver su
docstring) — este módulo NO reimplementa detección de idioma, solo la usa
para decidir traducción.

Determinismo de la lógica de decisión vs. servicio externo de traducción:
qué se traduce y qué no es 100% determinista y depende solo de
`subtipo` + idioma detectado (`_necesita_traduccion`, función pura, sin red,
testeable con inputs directos — mismo patrón que `_necesita_ocr_fallback` de
`pdf_parser.py`). La traducción EN SÍ depende de un servicio externo (DeepL,
vía `deep_translator`), pero DeepL es traducción determinista de terceros,
NO un LLM generativo — no viola la regla "sin LLM" de Flujo A (ver
`docs/specs/llm-policy.md`).
"""
import os
from dataclasses import dataclass
from typing import Callable, Optional

from dotenv import load_dotenv

from src.theory.cleaners.transcript_cleaner import FragmentoLimpio
from src.utils.language_detector import detect_language

# Carga `.env` (si existe) para poblar `DEEPL_API_KEY` en variables de
# entorno antes de que `_traducir_con_deepl` la lea. No falla si `.env` no
# existe (p.ej. en CI): `load_dotenv()` es un no-op silencioso en ese caso.
load_dotenv()

IDIOMA_DESTINO = "es"
"""Idioma destino de la teoría del corpus (ver `src/theory/SPEC.md` §Idioma)."""

_IDIOMA_INDETERMINADO = "und"
"""Código ISO 639-2 "undetermined": fallback cuando `detect_language` no
puede determinar el idioma de un fragmento (p.ej. muy corto). Ver
`_detectar_idioma_seguro`."""

_SUBTIPOS_VALIDOS = ("explicacion", "ejemplo")

Traductor = Callable[[str, str], str]
"""Firma del traductor inyectable: `(texto, idioma_origen) -> texto_traducido`.
Se inyecta para poder testear `normalize_language` sin red (ver
`test_language_normalizer.py`) y para poder swappear DeepL por otro backend
(LibreTranslate, ver `src/theory/SPEC.md` §Stack) sin tocar la lógica de
decisión."""


@dataclass
class FragmentoNormalizado:
    """Un fragmento tras pasar por el LanguageNormalizer.

    - `idioma_original`: idioma detectado en el texto de ENTRADA (antes de
      traducir), o `_IDIOMA_INDETERMINADO` si no se pudo detectar.
    - `idioma_fragmento`: idioma del texto de SALIDA (`texto`) — `es` si se
      tradujo, o `idioma_original` si se conservó tal cual (ejemplo, o
      explicacion ya en español).
    - `traducido`: True solo si se invocó al traductor.
    """

    texto: str
    subtipo: str
    idioma_original: str
    idioma_fragmento: str
    traducido: bool


def _texto_y_subtipo(fragmento) -> tuple[str, str]:
    """Normaliza la entrada: acepta `FragmentoLimpio` o tupla `(texto, subtipo)`.

    Mismo patrón que `_texto_y_subtipo` de `transcript_cleaner.py`, para
    encadenar directamente la salida real del Cleaner.
    """
    if isinstance(fragmento, FragmentoLimpio):
        return fragmento.texto, fragmento.subtipo
    texto, subtipo = fragmento
    return texto, subtipo


def _necesita_traduccion(
    subtipo: str, idioma_detectado: str, idioma_destino: str = IDIOMA_DESTINO
) -> bool:
    """Decide si un fragmento debe traducirse. Función pura, sin red ni I/O.

    Contrato (`src/theory/SPEC.md` §Idioma):
    - `subtipo="ejemplo"` -> NUNCA se traduce, sin importar el idioma
      detectado (incluido `_IDIOMA_INDETERMINADO`).
    - `subtipo="explicacion"` -> se traduce solo si el idioma detectado NO es
      ya el idioma destino (evita traducir teoría que ya está en español).

    Aislada así (igual que `_necesita_ocr_fallback` de `pdf_parser.py`) para
    poder testear la decisión con inputs directos, sin depender de
    `detect_language` ni de ningún traductor real.
    """
    if subtipo not in _SUBTIPOS_VALIDOS:
        raise ValueError(
            f"subtipo desconocido {subtipo!r}: se esperaba uno de {_SUBTIPOS_VALIDOS}"
        )
    if subtipo == "ejemplo":
        return False
    return idioma_detectado != idioma_destino


def _detectar_idioma_seguro(texto: str) -> str:
    """Detecta el idioma de `texto`, o `_IDIOMA_INDETERMINADO` si no es posible.

    Fragmentos muy cortos (frecuente en `subtipo=ejemplo`: una interjección o
    frase suelta) pueden no darle a `detect_language` señal suficiente para
    decidir. Un fragmento sin idioma detectable nunca debe romper la
    normalización de todo el documento: se marca como indeterminado.
    `_necesita_traduccion` trata `_IDIOMA_INDETERMINADO` igual que cualquier
    idioma distinto del destino (fallback conservador para `explicacion`: si
    no se puede confirmar que el texto ya está en español, se traduce en vez
    de arriesgarse a dejar teoría sin traducir silenciosamente); para
    `ejemplo` es irrelevante, porque nunca se traduce.
    """
    try:
        return detect_language(texto)
    except ValueError:
        return _IDIOMA_INDETERMINADO


def _traducir_con_deepl(
    texto: str, idioma_origen: str, idioma_destino: str = IDIOMA_DESTINO
) -> str:
    """Traductor por defecto: DeepL free tier vía `deep_translator.DeeplTranslator`.

    Lee `DEEPL_API_KEY` de variables de entorno (pobladas desde `.env` por
    `load_dotenv()` al importar este módulo). DeepL es traducción determinista
    de terceros, no un LLM generativo (ver docstring del módulo).

    Importa `deep_translator` de forma perezosa (dentro de la función) para
    que el resto del módulo -incluida `_necesita_traduccion`, la lógica de
    decisión- sea importable y testeable sin esa dependencia si hiciera falta.
    """
    from deep_translator import DeeplTranslator

    api_key = os.getenv("DEEPL_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPL_API_KEY no configurada: necesaria para traducir subtipo=explicacion "
            "a español (ver .env.example / src/theory/SPEC.md §Stack)"
        )

    traductor = DeeplTranslator(
        api_key=api_key,
        source=idioma_origen,
        target=idioma_destino,
        use_free_api=True,
    )
    return traductor.translate(texto)


def normalize_language(
    fragmentos,
    traductor: Optional[Traductor] = None,
    idioma_destino: str = IDIOMA_DESTINO,
) -> list[FragmentoNormalizado]:
    """Aplica el LanguageNormalizer a una lista de fragmentos del Cleaner.

    Para cada fragmento: detecta su idioma (`_detectar_idioma_seguro`) y
    decide si traducirlo (`_necesita_traduccion`, pura). Si hace falta
    traducir, invoca `traductor(texto, idioma_original)` (por defecto,
    `_traducir_con_deepl`); si no, conserva `texto` tal cual.

    `traductor` es inyectable (ver `Traductor`) para testear la orquestación
    sin red — el traductor por defecto solo se resuelve en el momento de la
    llamada, así que este módulo es importable sin `DEEPL_API_KEY` configurada
    mientras no se traduzca nada de verdad.

    Acepta `FragmentoLimpio` (salida real de `transcript_cleaner.py`) o
    tuplas `(texto, subtipo)`, preservando el orden de entrada.
    """
    if traductor is None:
        traductor = _traducir_con_deepl

    resultado: list[FragmentoNormalizado] = []
    for fragmento in fragmentos:
        texto, subtipo = _texto_y_subtipo(fragmento)
        idioma_original = _detectar_idioma_seguro(texto)

        if _necesita_traduccion(subtipo, idioma_original, idioma_destino):
            texto_final = traductor(texto, idioma_original)
            traducido = True
        else:
            texto_final = texto
            traducido = False

        idioma_fragmento = idioma_destino if traducido else idioma_original

        resultado.append(
            FragmentoNormalizado(
                texto=texto_final,
                subtipo=subtipo,
                idioma_original=idioma_original,
                idioma_fragmento=idioma_fragmento,
                traducido=traducido,
            )
        )

    return resultado
