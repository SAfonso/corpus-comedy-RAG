"""Tests para whisperx_parser (Flujo A, contrato en src/theory/SPEC.md).

Contrato: elimina timestamps y speaker tags del texto; conserva el speaker
dominante como metadato del documento; une líneas consecutivas del mismo
speaker; preserva el contenido, nunca lo interpreta.

Usa fixtures reales:
- tests/fixtures/sample_transcript.txt (un solo speaker, 8 líneas).
- Un extracto real de data/raw/transcriptions/Tomas/TOMASFUENTES_3_ELTEMA_transcripcion.txt
  (dos speakers, para probar la lógica de "dominante"), copiado a tmp_path
  sin modificar el fichero original (material sagrado, solo lectura).
"""
from pathlib import Path

import pytest

from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"

REPO_ROOT = Path(__file__).resolve().parents[3]
MULTI_SPEAKER_SOURCE = (
    REPO_ROOT
    / "data"
    / "raw"
    / "transcriptions"
    / "Tomas"
    / "TOMASFUENTES_3_ELTEMA_transcripcion.txt"
)


@pytest.fixture
def multi_speaker_txt(tmp_path):
    """Extracto real (líneas 55-90) del fichero de Tomas, copiado a tmp_path.

    No se edita el original: se lee (solo lectura) y el extracto textual se
    escribe en un fichero nuevo dentro de tmp_path. El rango se eligió porque
    contiene dos cambios reales de speaker (SPEAKER_00 -> SPEAKER_01 -> SPEAKER_00
    -> SPEAKER_01 -> SPEAKER_00), con SPEAKER_00 claramente dominante en texto.
    """
    lineas = MULTI_SPEAKER_SOURCE.read_text(encoding="utf-8").splitlines()
    extracto = "\n".join(lineas[54:90])  # líneas 55-90 (1-indexado)
    destino = tmp_path / "multi_speaker_extracto.txt"
    destino.write_text(extracto, encoding="utf-8")
    return destino


def test_un_solo_speaker_texto_limpio_y_unido():
    resultado = parse_whisperx_transcript(SAMPLE_TXT)

    assert resultado.speaker_dominante == "SPEAKER_00"
    # Todas las frases del fixture deben estar presentes, unidas con espacio.
    assert "Hola, bienvenidas y bienvenidos a este curso." in resultado.texto
    assert "Y hoy vamos a hablar de la regla de tres." in resultado.texto
    assert "\n" not in resultado.texto  # un solo bloque: mismo speaker todo el doc
    # Unión con espacio entre frases consecutivas del mismo speaker.
    assert "este curso. Bueno, yo soy Denis" in resultado.texto


def test_timestamps_y_tags_desaparecen_del_texto():
    resultado = parse_whisperx_transcript(SAMPLE_TXT)

    assert "[" not in resultado.texto
    assert "]" not in resultado.texto
    assert "SPEAKER_" not in resultado.texto


def test_multi_speaker_dominante_es_el_de_mas_texto_no_el_primero(multi_speaker_txt):
    resultado = parse_whisperx_transcript(multi_speaker_txt)

    # SPEAKER_00 aparece primero Y tiene mucho más texto/líneas que SPEAKER_01
    # en el extracto real -> debe ganar por cantidad de contenido, no por orden.
    assert resultado.speaker_dominante == "SPEAKER_00"

    assert "[" not in resultado.texto
    assert "SPEAKER_" not in resultado.texto

    # El contenido de ambos speakers se conserva literal (no se interpreta).
    assert "no fumadores están aquí esta noche" in resultado.texto
    assert "Trombo." in resultado.texto

    # Bloques de speakers distintos separados (no todo fusionado en una frase).
    assert "\n\n" in resultado.texto


def test_lineas_consecutivas_mismo_speaker_se_unen_en_un_bloque(multi_speaker_txt):
    resultado = parse_whisperx_transcript(multi_speaker_txt)

    # Las dos líneas consecutivas de SPEAKER_01 al inicio de su primera
    # intervención deben quedar unidas en el mismo bloque/frase.
    assert (
        "no fumadores están aquí esta noche? No fumadores, un aplauso"
        in resultado.texto
    )
