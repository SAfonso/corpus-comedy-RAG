"""Tests para transcript_cleaner (Flujo A, contrato en src/theory/SPEC.md §Limpieza).

Contrato:
- subtipo="explicacion": limpieza AGRESIVA (elimina muletillas, elimina
  repeticiones de palabras/frases consecutivas, separa en párrafos coherentes).
- subtipo="ejemplo": se conserva TAL CUAL, sin tocar ni siquiera las muletillas
  (excepción explícita de la spec).
- Determinista, sin LLM.

Encadena el pipeline real `parse_whisperx_transcript -> detect_subtypes ->
clean_fragment(s)` sobre el fixture real `tests/fixtures/sample_transcript.txt`
(mismo patrón que `test_subtype_detector.py`), para que el input de test sea
exactamente el que vería el Cleaner en la cadena real
DriveMonitor -> Parser -> SubtypeDetector -> Cleaner.

Las pruebas de eliminación de repeticiones usan cadenas construidas
directamente (no hay repeticiones literales en el fixture real) — mismo
patrón que `test_subtype_detector.test_marcador_case_insensitive_y_variante_sin_tilde`,
que también prueba casos de borde con strings escritos a mano en vez del
fixture, para aislar el comportamiento de una función pura.
"""
from pathlib import Path

from src.theory.cleaners.transcript_cleaner import (
    FragmentoLimpio,
    agrupar_en_parrafos,
    clean_fragment,
    clean_fragments,
)
from src.theory.detectors.subtype_detector import FragmentoSubtipo, detect_subtypes
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"


def _fragmentos_reales() -> list[FragmentoSubtipo]:
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    return detect_subtypes(texto)


def test_explicacion_elimina_muletillas_reales_del_fixture():
    fragmentos = _fragmentos_reales()
    limpios = clean_fragments(fragmentos)

    explicaciones = [f for f in limpios if f.subtipo == "explicacion"]
    assert explicaciones  # hay al menos una

    texto_conjunto = " ".join(f.texto for f in explicaciones).lower()

    # Muletillas reales del fixture: no deben sobrevivir en explicacion.
    for muletilla in ("bueno,", "o sea", "¿vale?", "¿no?", "básicamente,"):
        assert muletilla not in texto_conjunto, f"muletilla {muletilla!r} no eliminada"


def test_explicacion_conserva_el_contenido_util():
    fragmentos = _fragmentos_reales()
    limpios = clean_fragments(fragmentos)

    explicaciones = [f for f in limpios if f.subtipo == "explicacion"]
    texto_conjunto = " ".join(f.texto for f in explicaciones)

    # El contenido (sin las muletillas) debe seguir presente.
    assert "Denis" in texto_conjunto
    assert "stand-up comedy" in texto_conjunto
    assert "regla de tres" in texto_conjunto
    assert "el tercero rompe las expectativas" in texto_conjunto


def test_ejemplo_se_conserva_tal_cual_incluida_su_muletilla():
    fragmentos = _fragmentos_reales()
    limpios = clean_fragments(fragmentos)

    ejemplos_originales = [f for f in fragmentos if f.subtipo == "ejemplo"]
    ejemplos_limpios = [f for f in limpios if f.subtipo == "ejemplo"]

    assert len(ejemplos_originales) == 1
    assert len(ejemplos_limpios) == 1
    # Texto exactamente igual, carácter a carácter (ni siquiera se toca la
    # muletilla "¿Vale?" que forma parte de esta misma frase-ejemplo).
    assert ejemplos_limpios[0].texto == ejemplos_originales[0].texto
    assert "¿Vale?" in ejemplos_limpios[0].texto
    assert "Me gustan los perros" in ejemplos_limpios[0].texto


def test_clean_fragment_acepta_tupla_texto_subtipo():
    resultado = clean_fragment(("Bueno, esto es una prueba.", "explicacion"))

    assert isinstance(resultado, FragmentoLimpio)
    assert resultado.subtipo == "explicacion"
    assert "bueno" not in resultado.texto.lower()


def test_clean_fragment_ejemplo_no_modifica_nada_con_tupla():
    original = "Bueno, o sea, esto se deja tal cual, ¿vale?"
    resultado = clean_fragment((original, "ejemplo"))

    assert resultado.texto == original


def test_elimina_palabras_repetidas_consecutivas():
    resultado = clean_fragment(("El el gato gato se subió a la mesa.", "explicacion"))
    assert "gato gato" not in resultado.texto.lower()
    assert "el el" not in resultado.texto.lower()
    assert "gato" in resultado.texto.lower()


def test_elimina_frases_repetidas_consecutivas():
    resultado = clean_fragment(
        ("Vamos a verlo, vamos a verlo, con calma.", "explicacion")
    )
    texto_lower = resultado.texto.lower()
    assert texto_lower.count("vamos a verlo") == 1
    assert "con calma" in texto_lower


def test_agrupar_en_parrafos_separa_ejemplo_del_resto():
    fragmentos = _fragmentos_reales()
    limpios = clean_fragments(fragmentos)
    parrafos = agrupar_en_parrafos(limpios)

    # 3 párrafos: explicacion (1-5) | ejemplo (aislado) | explicacion (7-9).
    assert len(parrafos) == 3
    assert "por ejemplo" in parrafos[1].lower()
    assert "Denis" in parrafos[0]
    assert "el remate" in parrafos[2]


def test_subtipo_desconocido_lanza_valueerror():
    import pytest

    with pytest.raises(ValueError):
        clean_fragment(("texto cualquiera", "otro_subtipo"))
