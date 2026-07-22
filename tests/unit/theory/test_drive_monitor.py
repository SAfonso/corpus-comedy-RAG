"""Tests para DriveMonitor (Flujo A, P18: carpeta local en vez de Drive API real).

Usa fixtures reales de tests/fixtures/ (sample_transcript.pdf, sample_transcript.txt)
copiadas a tmp_path para simular una carpeta vigilada. Nunca contenido inventado.
"""
import shutil
from pathlib import Path

import pytest

from src.theory.drive_monitor import DriveMonitor

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_PDF = FIXTURES_DIR / "sample_transcript.pdf"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"


@pytest.fixture
def watched_folder(tmp_path):
    """Carpeta vigilada con las fixtures reales copiadas dentro."""
    folder = tmp_path / "books"
    folder.mkdir()
    shutil.copy(SAMPLE_PDF, folder / "sample_transcript.pdf")
    shutil.copy(SAMPLE_TXT, folder / "sample_transcript.txt")
    return folder


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "state" / "processed_files.json"


def test_ficheros_nuevos_se_reportan_y_quedan_registrados(watched_folder, state_file):
    monitor = DriveMonitor(folder=watched_folder, state_path=state_file)
    pendientes = monitor.scan()

    nombres = {p.name for p in pendientes}
    assert nombres == {"sample_transcript.pdf", "sample_transcript.txt"}

    assert state_file.exists()
    import json
    registro = json.loads(state_file.read_text())
    assert set(registro.keys()) == {"sample_transcript.pdf", "sample_transcript.txt"}
    # hash MD5 real del fichero, no un placeholder
    import hashlib
    hash_pdf_real = hashlib.md5(SAMPLE_PDF.read_bytes()).hexdigest()
    assert registro["sample_transcript.pdf"]["md5"] == hash_pdf_real


def test_segunda_ejecucion_sin_cambios_no_reporta_nada(watched_folder, state_file):
    monitor = DriveMonitor(folder=watched_folder, state_path=state_file)
    monitor.scan()

    monitor2 = DriveMonitor(folder=watched_folder, state_path=state_file)
    pendientes = monitor2.scan()

    assert pendientes == []


def test_fichero_modificado_se_reporta_de_nuevo(watched_folder, state_file):
    monitor = DriveMonitor(folder=watched_folder, state_path=state_file)
    monitor.scan()

    # modifica contenido real (sigue siendo texto de transcripción, no basura)
    txt_path = watched_folder / "sample_transcript.txt"
    contenido_original = txt_path.read_text()
    txt_path.write_text(contenido_original + "\n[00:99:99] SPEAKER_00: linea anadida\n")

    monitor2 = DriveMonitor(folder=watched_folder, state_path=state_file)
    pendientes = monitor2.scan()

    nombres = {p.name for p in pendientes}
    assert nombres == {"sample_transcript.txt"}


def test_fichero_original_nunca_se_modifica(watched_folder, state_file):
    pdf_path = watched_folder / "sample_transcript.pdf"
    contenido_antes = pdf_path.read_bytes()
    mtime_antes = pdf_path.stat().st_mtime

    monitor = DriveMonitor(folder=watched_folder, state_path=state_file)
    monitor.scan()
    monitor.scan()  # segunda pasada tampoco debe tocar el original

    assert pdf_path.read_bytes() == contenido_antes
    assert pdf_path.stat().st_mtime == mtime_antes


def test_reanudacion_tras_interrupcion_no_reprocesa_lo_marcado(watched_folder, state_file):
    """Simula una interrupción: solo un fichero queda marcado como procesado en
    processed_files.json (p.ej. el proceso murió tras el primero). El siguiente
    run debe retomar solo con el que falta, sin reprocesar el ya marcado."""
    import hashlib
    import json

    pdf_path = watched_folder / "sample_transcript.pdf"
    md5_pdf = hashlib.md5(pdf_path.read_bytes()).hexdigest()

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "sample_transcript.pdf": {"md5": md5_pdf}
    }))

    monitor = DriveMonitor(folder=watched_folder, state_path=state_file)
    pendientes = monitor.scan()

    nombres = {p.name for p in pendientes}
    assert nombres == {"sample_transcript.txt"}
