"""Tests para subtype_detector (Flujo A, contrato en src/theory/SPEC.md).

Contrato: clasifica el texto ya parseado por un Parser en fragmentos (frases)
con su `subtipo` (`explicacion` | `ejemplo`), heurística determinista basada
en los marcadores lingüísticos de `docs/CORPUS_INVENTORY.md` (Bloque 5):
"un ejemplo", "por ejemplo", "algo así como" (case-insensitive).

Usa el fixture real tests/fixtures/sample_transcript.txt (vía
`parse_whisperx_transcript`, para obtener el texto de entrada real ya sin
timestamps/speaker tags — mismo texto que vería el SubtypeDetector en la
cadena real DriveMonitor -> Parser -> SubtypeDetector -> Cleaner).
"""
from pathlib import Path

from src.theory.detectors.subtype_detector import FragmentoSubtipo, detect_subtypes
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"


def test_frase_con_por_ejemplo_se_clasifica_como_ejemplo():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = detect_subtypes(texto)

    ejemplos = [f for f in fragmentos if f.subtipo == "ejemplo"]

    assert len(ejemplos) == 1
    assert "por ejemplo" in ejemplos[0].texto.lower()
    assert "Me gustan los perros" in ejemplos[0].texto


def test_resto_de_frases_se_clasifican_como_explicacion():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = detect_subtypes(texto)

    explicaciones = {f.texto for f in fragmentos if f.subtipo == "explicacion"}

    # Frases sin marcador de ejemplo (regla de tres, presentación) -> explicacion.
    assert any("Hola, bienvenidas y bienvenidos a este curso" in f for f in explicaciones)
    assert any("Y hoy vamos a hablar de la regla de tres" in f for f in explicaciones)
    assert any(
        "una técnica donde presentas dos elementos que siguen un patrón" in f
        for f in explicaciones
    )
    # Ninguna frase de pura explicación contiene el marcador de ejemplo.
    assert not any("por ejemplo" in f.lower() for f in explicaciones)


def test_subtipos_devueltos_son_siempre_validos():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = detect_subtypes(texto)

    assert fragmentos  # no vacío
    assert all(isinstance(f, FragmentoSubtipo) for f in fragmentos)
    assert all(f.subtipo in ("explicacion", "ejemplo") for f in fragmentos)


def test_marcador_case_insensitive_y_variante_sin_tilde():
    assert detect_subtypes("Esto es POR EJEMPLO una prueba.")[0].subtipo == "ejemplo"
    assert detect_subtypes("Algo asi como lo que decíamos antes.")[0].subtipo == "ejemplo"
    assert detect_subtypes("Algo así como lo que decíamos antes.")[0].subtipo == "ejemplo"
    assert detect_subtypes("Un ejemplo claro de esto sería.")[0].subtipo == "ejemplo"


def test_no_reinterpreta_el_contenido_solo_trocea():
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = detect_subtypes(texto)

    # Concatenar los fragmentos reconstruye el contenido original (salvo
    # espacios de separación entre frases, que no forman parte del contenido).
    reconstruido = " ".join(f.texto for f in fragmentos)
    for palabra_clave in ("Denis", "stand-up comedy", "regla de tres", "impuestos"):
        assert palabra_clave in reconstruido
