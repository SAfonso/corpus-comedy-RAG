"""Tests para quality_scorer (utils, contrato en src/utils/SPEC.md).

Contrato:
- `score_quality(texto)` devuelve un float en [0.0, 1.0]: densidad de
  contenido útil de un fragmento YA LIMPIO (salida de
  `transcript_cleaner.clean_fragment`), ver docstring del módulo para la
  fórmula (media ponderada de longitud, diversidad léxica y ratio de
  palabras de contenido).
- Determinista, sin LLM, función pura.
- Texto vacío/en blanco/sin palabras tokenizables -> 0.0 (nunca error).

No se verifica un valor "mágico" exacto (la fórmula es una heurística, no una
referencia externa) sino ORDEN RELATIVO razonable entre fragmentos reales de
densidad claramente distinta, y los límites del rango [0.0, 1.0].

Usa el fixture real de WhisperX ya disponible (regla del proyecto: nunca
inventar fixtures), pasado por la cadena real de componentes ya aprobados
hasta este punto: `parse_whisperx_transcript -> detect_subtypes ->
clean_fragment` (ver `src/theory/SPEC.md` §Cadena de componentes):
- Fragmento sustancioso: el párrafo de explicación teórica real sobre "la
  regla de tres" (`agrupar_en_parrafos` sobre los fragmentos `explicacion`
  limpios del fixture).
- Fragmento pobre/corto: la frase de saludo inicial del mismo fixture
  ("Hola, bienvenidas y bienvenidos a este curso."), limpia con
  `clean_fragment` — la frase real más corta y sin carga informativa
  disponible en el corpus (ver "Gap de scope documentado" en el docstring del
  módulo: no hay en el corpus un caso real de relleno más extremo, p.ej.
  vacío o de una sola palabra repetida, sobre el que construir un caso
  adicional sin inventar contenido).
"""
from pathlib import Path

from src.theory.cleaners.transcript_cleaner import (
    agrupar_en_parrafos,
    clean_fragment,
    clean_fragments,
)
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript
from src.utils.quality_scorer import score_quality

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"


def _parrafo_explicacion_real() -> str:
    """Párrafo sustancioso real: explicación teórica de "la regla de tres",
    tras pasar por la cadena real Parser -> SubtypeDetector -> Cleaner."""
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = detect_subtypes(texto)
    limpios = clean_fragments(fragmentos)
    parrafos = agrupar_en_parrafos(limpios)
    # Primer párrafo: bloque de explicación antes del primer ejemplo.
    return parrafos[0]


def _saludo_real_limpio() -> str:
    """Frase real más corta y trivial del fixture (saludo de apertura), tras
    pasar por `clean_fragment` como `subtipo=explicacion`."""
    return clean_fragment(
        ("Hola, bienvenidas y bienvenidos a este curso.", "explicacion")
    ).texto


def test_fragmento_sustancioso_puntua_mas_que_fragmento_pobre_real():
    sustancioso = _parrafo_explicacion_real()
    pobre = _saludo_real_limpio()
    assert score_quality(sustancioso) > score_quality(pobre)


def test_fragmento_sustancioso_puntua_por_encima_de_la_mitad():
    sustancioso = _parrafo_explicacion_real()
    assert score_quality(sustancioso) > 0.5


def test_documento_completo_real_da_score_alto():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = detect_subtypes(texto)
    limpios = clean_fragments(fragmentos)
    parrafos = agrupar_en_parrafos(limpios)
    score = score_quality(" ".join(parrafos))
    assert 0.0 <= score <= 1.0
    assert score > 0.5


def test_texto_vacio_da_score_cero():
    assert score_quality("") == 0.0


def test_texto_solo_espacios_da_score_cero():
    assert score_quality("   \n\t  ") == 0.0


def test_texto_solo_puntuacion_sin_palabras_da_score_cero():
    # Real en el sentido de "no se inventa contenido": son únicamente los
    # signos de puntuación que ya aparecen en el propio fixture (¿, ?, ...),
    # sin ninguna palabra.
    assert score_quality("¿? ¡! ...") == 0.0


def test_score_siempre_en_rango_valido_para_fragmentos_reales():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = detect_subtypes(texto)
    limpios = clean_fragments(fragmentos)
    for fragmento in limpios:
        score = score_quality(fragmento.texto)
        assert 0.0 <= score <= 1.0


def test_es_deterministico_entre_llamadas():
    sustancioso = _parrafo_explicacion_real()
    resultados = {score_quality(sustancioso) for _ in range(5)}
    assert len(resultados) == 1
