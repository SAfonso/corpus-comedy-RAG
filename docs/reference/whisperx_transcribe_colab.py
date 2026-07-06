"""
Transcripción vídeo → .txt con WhisperX (referencia).
=====================================================

Origen: notebook de Google Colab usado en la fase previa de preparación para
generar las transcripciones del corpus (Videos_RAGComedy: Demy / Pinol / Tomas).
Guardado aquí como REFERENCIA del proceso que produjo `data/raw/transcriptions/`.

NO forma parte del pipeline determinista (`src/`). Es el paso de captación previo
(vídeo → texto) que corre en Colab con GPU. El parser del Flujo A
(`src/theory/parsers/whisperx_parser.py`) consume la salida de este script.

Contrato de salida (una línea por segmento):
    [<start>s] SPEAKER_XX: <texto>
Ej.:
    [0.19s] SPEAKER_00: Hola, bienvenidas y bienvenidos a este curso.

Cambios respecto al notebook original:
  - El token de Hugging Face NO va hardcodeado: se lee de la variable de entorno
    HF_TOKEN. (El token original quedó expuesto en un notebook compartido y debe
    revocarse en https://huggingface.co/settings/tokens.)
  - La carpeta a procesar se parametriza (el notebook estaba fijado a "Demy/").

Requisitos (en Colab):
    !pip install git+https://github.com/m-bain/whisperX.git
El token de HF debe tener aceptados los términos de los modelos gated de
pyannote (segmentation + speaker-diarization).
"""

import os
import shutil

import torch
import whisperx
from whisperx.diarize import DiarizationPipeline

# --- Credenciales -----------------------------------------------------------
# Nunca hardcodear. En Colab: os.environ["HF_TOKEN"] = "..." en una celda que no
# se comparta, o usar los "Secrets" de Colab (google.colab.userdata).
HF_TOKEN = os.environ["HF_TOKEN"]

# --- Hardware ---------------------------------------------------------------
use_gpu = torch.cuda.is_available()
device = "cuda" if use_gpu else "cpu"
batch_size = 16  # Reduce si da error de memoria
compute_type = "float16" if device == "cuda" else "int8"

# --- Rutas ------------------------------------------------------------------
# Parametriza el cómico/curso a procesar. En el notebook original estaba fijo a
# "Videos_RAGComedy/Demy/"; hay que repetir por cada subcarpeta (Demy/Pinol/Tomas).
CARPETA_DRIVE = os.environ.get(
    "CARPETA_DRIVE", "/content/drive/MyDrive/Videos_RAGComedy/Demy/"
)
CARPETA_PROCESADOS = os.path.join(CARPETA_DRIVE, "Processed")
CARPETA_TRANS = os.path.join(CARPETA_DRIVE, "Transcript")

EXTENSIONES_VIDEO = (".mp4", ".mkv", ".avi", ".mov", ".mp3")
IDIOMA = "es"


def main():
    os.makedirs(CARPETA_PROCESADOS, exist_ok=True)
    os.makedirs(CARPETA_TRANS, exist_ok=True)

    print("Cargando modelos...")
    model = whisperx.load_model("large-v3", device, compute_type=compute_type)
    model_a, metadata = whisperx.load_align_model(language_code=IDIOMA, device=device)
    diarize_model = DiarizationPipeline(token=HF_TOKEN, device=device)

    for archivo in os.listdir(CARPETA_DRIVE):
        if not archivo.lower().endswith(EXTENSIONES_VIDEO):
            continue

        ruta_completa = os.path.join(CARPETA_DRIVE, archivo)
        print(f"\n--- Procesando: {archivo} ---")

        try:
            # A. Transcripción
            audio = whisperx.load_audio(ruta_completa)
            result = model.transcribe(audio, batch_size=batch_size, language=IDIOMA)

            # B. Alineación + diarización de hablantes
            result = whisperx.align(
                result["segments"], model_a, metadata, audio, device
            )
            diarize_segments = diarize_model(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)

            # C. Guardar TXT  ->  [<start>s] SPEAKER_XX: texto
            nombre_txt = os.path.splitext(archivo)[0] + "_transcripcion.txt"
            ruta_txt = os.path.join(CARPETA_TRANS, nombre_txt)
            with open(ruta_txt, "w", encoding="utf-8") as f:
                for segment in result["segments"]:
                    speaker = segment.get("speaker", "DESCONOCIDO")
                    f.write(
                        f"[{segment['start']:.2f}s] {speaker}: "
                        f"{segment['text'].strip()}\n"
                    )
            print(f"OK Transcripción guardada: {nombre_txt}")

            # D. Mover el vídeo ya procesado
            shutil.move(ruta_completa, os.path.join(CARPETA_PROCESADOS, archivo))
            print("OK Vídeo movido a Processed/")

        except Exception as e:  # noqa: BLE001 - log y continuar con el siguiente
            print(f"X Error procesando {archivo}: {e}")

    print("\n--- PROCESO FINALIZADO ---")


# --- Utilidad suelta del notebook -------------------------------------------
# Reparar el audio de un vídeo problemático extrayendo solo la pista de audio:
#   ffmpeg -i entrada.mp4 -vn -acodec libmp3lame -q:a 2 salida.mp3


if __name__ == "__main__":
    main()
