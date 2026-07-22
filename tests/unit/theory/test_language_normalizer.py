"""Tests para language_normalizer (Flujo A, contrato en src/theory/SPEC.md §Idioma).

Contrato:
- `subtipo="ejemplo"`: NUNCA se traduce, sin importar el idioma detectado.
- `subtipo="explicacion"` en español: NO se traduce (ya está en destino).
- `subtipo="explicacion"` en otro idioma: SÍ se traduce a español.
- La decisión (`_necesita_traduccion`) es una función pura, sin red,
  testeable con inputs directos — no depende de `detect_language` ni de
  ningún traductor real.
- El traductor es inyectable (`normalize_language(..., traductor=...)`) para
  poder testear la orquestación completa sin llamar a DeepL de verdad.

Encadena el pipeline real `parse_whisperx_transcript/parse_docx ->
detect_subtypes -> clean_fragments -> normalize_language` sobre los dos
fixtures reales es/en del repo (mismo patrón que `test_transcript_cleaner.py`),
para que el input de test sea el que vería el LanguageNormalizer en la cadena
real. Los tests de la función pura `_necesita_traduccion` usan valores
directos (idiomas/subtipos) escritos a mano — mismo patrón ya usado en
`test_subtype_detector.py`/`test_transcript_cleaner.py` para aislar casos de
borde de una función pura.
"""
from pathlib import Path

import pytest

from src.theory.cleaners.transcript_cleaner import FragmentoLimpio, clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.normalizers.language_normalizer import (
    FragmentoNormalizado,
    _necesita_traduccion,
    normalize_language,
)
from src.theory.parsers.docx_parser import parse_docx
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"
COMEDY_BIBLE_EXCERPT = FIXTURES_DIR / "comedy_bible_excerpt.docx"


def _fragmentos_limpios_es() -> list[FragmentoLimpio]:
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    return clean_fragments(detect_subtypes(texto))


def _fragmentos_limpios_en() -> list[FragmentoLimpio]:
    texto = parse_docx(COMEDY_BIBLE_EXCERPT).texto
    return clean_fragments(detect_subtypes(texto))


class _TraductorEspia:
    """Traductor falso (sin red) que registra las llamadas recibidas.

    Devuelve un marcador fijo en vez de una traducción real, para poder
    comprobar tanto SI se llamó como con qué argumentos, sin depender de
    DeepL.
    """

    def __init__(self):
        self.llamadas: list[tuple[str, str]] = []

    def __call__(self, texto: str, idioma_origen: str) -> str:
        self.llamadas.append((texto, idioma_origen))
        return f"[TRADUCIDO:{idioma_origen}] {texto}"


def _traductor_que_no_debe_llamarse(texto: str, idioma_origen: str) -> str:
    raise AssertionError(
        "el traductor no debía invocarse para este fragmento (subtipo=ejemplo "
        "o explicacion ya en español)"
    )


# --- _necesita_traduccion: función pura, sin red -----------------------------


@pytest.mark.parametrize("idioma", ["en", "es", "fr", "und"])
def test_ejemplo_nunca_necesita_traduccion_sin_importar_idioma(idioma):
    assert _necesita_traduccion("ejemplo", idioma) is False


def test_explicacion_en_espanol_no_necesita_traduccion():
    assert _necesita_traduccion("explicacion", "es") is False


@pytest.mark.parametrize("idioma", ["en", "fr", "de", "und"])
def test_explicacion_en_otro_idioma_necesita_traduccion(idioma):
    assert _necesita_traduccion("explicacion", idioma) is True


def test_subtipo_desconocido_lanza_value_error():
    with pytest.raises(ValueError):
        _necesita_traduccion("no_es_un_subtipo", "en")


# --- normalize_language: orquestación con traductor inyectado (sin red) ------


def test_explicacion_en_espanol_no_traduce_fixture_real():
    fragmentos = [f for f in _fragmentos_limpios_es() if f.subtipo == "explicacion"]
    assert fragmentos  # hay al menos una explicacion real en el fixture

    resultado = normalize_language(fragmentos, traductor=_traductor_que_no_debe_llamarse)

    for r in resultado:
        assert isinstance(r, FragmentoNormalizado)
        assert r.subtipo == "explicacion"
        assert r.traducido is False
        assert r.idioma_original == "es"
        assert r.idioma_fragmento == "es"
        # Texto intacto: no se llamó a ningún traductor.
        original = next(f.texto for f in fragmentos if f.texto == r.texto)
        assert r.texto == original


def test_ejemplo_nunca_se_traduce_ni_estando_en_ingles():
    # Fragmento "ejemplo" real, pero forzado a inglés real del otro fixture:
    # confirma que el subtipo manda por encima del idioma, sin llamar jamás
    # al traductor.
    texto_en_ingles_real = parse_docx(COMEDY_BIBLE_EXCERPT).texto[:200]
    fragmento = FragmentoLimpio(texto=texto_en_ingles_real, subtipo="ejemplo")

    resultado = normalize_language([fragmento], traductor=_traductor_que_no_debe_llamarse)

    assert len(resultado) == 1
    r = resultado[0]
    assert r.subtipo == "ejemplo"
    assert r.traducido is False
    assert r.idioma_original == "en"
    assert r.idioma_fragmento == "en"  # se conserva en su idioma original
    assert r.texto == texto_en_ingles_real  # ni un carácter tocado


def test_explicacion_en_ingles_se_traduce_via_traductor_inyectado():
    fragmentos_en = [f for f in _fragmentos_limpios_en() if f.subtipo == "explicacion"]
    assert fragmentos_en

    fragmento = fragmentos_en[0]
    espia = _TraductorEspia()

    resultado = normalize_language([fragmento], traductor=espia)

    assert len(resultado) == 1
    r = resultado[0]
    assert r.subtipo == "explicacion"
    assert r.traducido is True
    assert r.idioma_original == "en"
    assert r.idioma_fragmento == "es"
    assert r.texto == f"[TRADUCIDO:en] {fragmento.texto}"
    # El traductor se invocó exactamente una vez, con el texto e idioma
    # detectado correctos.
    assert espia.llamadas == [(fragmento.texto, "en")]


def test_normalize_language_preserva_orden_y_acepta_tuplas():
    espia = _TraductorEspia()
    fragmentos_mixtos = [
        ("Hola, bienvenidas y bienvenidos a este curso.", "explicacion"),
        FragmentoLimpio(texto="Por ejemplo, imagina que dices algo gracioso.", subtipo="ejemplo"),
        ("This is real English theory text about comedy writing.", "explicacion"),
    ]

    resultado = normalize_language(fragmentos_mixtos, traductor=espia)

    assert len(resultado) == 3
    # Orden preservado.
    assert resultado[0].subtipo == "explicacion"
    assert resultado[0].traducido is False  # ya en español
    assert resultado[1].subtipo == "ejemplo"
    assert resultado[1].traducido is False  # nunca se traduce
    assert resultado[2].subtipo == "explicacion"
    assert resultado[2].traducido is True  # inglés -> se traduce
    assert resultado[2].idioma_fragmento == "es"
    # Solo se llamó al traductor para el tercer fragmento (inglés/explicacion).
    assert len(espia.llamadas) == 1
