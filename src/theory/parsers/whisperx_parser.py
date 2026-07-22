"""whisperx_parser — Flujo A (Teoría), Parser para transcripciones WhisperX.

Contrato (src/theory/SPEC.md §Cadena de componentes): elimina timestamps y
speaker tags del texto; conserva el speaker dominante como metadato del
documento; une líneas consecutivas del mismo speaker; preserva el contenido,
nunca lo interpreta.

Formato de entrada esperado (una línea por frase, generado en Colab):
    [12.10s] SPEAKER_00: texto de la frase.

No toca el fichero de origen (sagrado, ver CLAUDE.md) — solo lo lee.
"""
import re
from dataclasses import dataclass
from pathlib import Path

_LINEA_RE = re.compile(r"^\[[\d.]+s\]\s+(SPEAKER_\d+):\s*(.*)$")


@dataclass
class TranscripcionParseada:
    """Salida mínima del parser: texto limpio + metadato de speaker dominante."""

    texto: str
    speaker_dominante: str


def parse_whisperx_transcript(path: Path) -> TranscripcionParseada:
    """Parsea un `.txt` de WhisperX y devuelve texto limpio + speaker dominante.

    - Quita `[timestamp]` y `SPEAKER_XX:` del texto de salida.
    - Une líneas consecutivas del mismo speaker en un solo bloque (con espacio).
    - Bloques de speakers distintos se separan con una línea en blanco (`\n\n`).
    - El speaker dominante es el que acumula más texto (caracteres) en todo
      el documento.
    """
    lineas = Path(path).read_text(encoding="utf-8").splitlines()

    # bloques: lista de (speaker, [frases...])
    bloques: list[tuple[str, list[str]]] = []
    texto_por_speaker: dict[str, int] = {}

    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue

        match = _LINEA_RE.match(linea)
        if not match:
            raise ValueError(
                f"Línea no cumple el formato WhisperX esperado "
                f"'[timestamp] SPEAKER_XX: texto': {linea!r}"
            )

        speaker, frase = match.group(1), match.group(2).strip()
        texto_por_speaker[speaker] = texto_por_speaker.get(speaker, 0) + len(frase)

        if bloques and bloques[-1][0] == speaker:
            bloques[-1][1].append(frase)
        else:
            bloques.append((speaker, [frase]))

    if not texto_por_speaker:
        raise ValueError(f"Transcripción vacía o sin líneas válidas: {path}")

    speaker_dominante = max(texto_por_speaker, key=texto_por_speaker.get)

    texto = "\n\n".join(" ".join(frases) for _, frases in bloques)

    return TranscripcionParseada(texto=texto, speaker_dominante=speaker_dominante)
