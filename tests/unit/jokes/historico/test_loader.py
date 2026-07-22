"""Tests para Loader — Flujo C (Histórico).

Lee ficheros .md marcados con [REMATE] y [CHISTOIDE], con idempotencia por
hash MD5 del documento.
"""
import json
from pathlib import Path

import pytest

from src.jokes.historico.loader import Loader


@pytest.fixture
def sample_md_file(tmp_path):
    """Copia del fixture real Freskito-Informático.md a tmp_path."""
    fixture_path = Path(__file__).parent.parent.parent.parent / "fixtures" / "Freskito-Informático.md"
    content = fixture_path.read_text()
    md_file = tmp_path / "Freskito-Informático.md"
    md_file.write_text(content)
    return md_file


@pytest.fixture
def loader(tmp_path, sample_md_file):
    """Loader con carpeta y estado en tmp_path."""
    state_path = tmp_path / "state.json"
    return Loader(folder=tmp_path, state_path=state_path)


def test_loader_reads_md_files(loader, tmp_path):
    """Loader lee ficheros .md de la carpeta."""
    docs = loader.load()
    assert len(docs) == 1
    assert docs[0]["name"] == "Freskito-Informático.md"
    assert "[REMATE]" in docs[0]["content"]


def test_loader_idempotence_same_content(loader, tmp_path):
    """Segunda llamada sin cambios → devuelve lista vacía (idempotencia)."""
    docs1 = loader.load()
    assert len(docs1) == 1

    docs2 = loader.load()
    assert len(docs2) == 0  # No hay cambios, idempotencia


def test_loader_detects_modified_file(loader, tmp_path):
    """Modificar el fichero → Loader lo detecta como pendiente."""
    docs1 = loader.load()
    assert len(docs1) == 1

    # Modificar el fichero
    md_file = tmp_path / "Freskito-Informático.md"
    content = md_file.read_text()
    md_file.write_text(content + "\nNueva línea agregada.")

    docs2 = loader.load()
    assert len(docs2) == 1
    assert "Nueva línea agregada" in docs2[0]["content"]


def test_loader_state_persistence(tmp_path):
    """Estado persiste en JSON entre instancias del Loader."""
    state_path = tmp_path / "state.json"
    fixture_path = Path(__file__).parent.parent.parent.parent / "fixtures" / "Freskito-Informático.md"
    content = fixture_path.read_text()
    md_file = tmp_path / "Freskito-Informático.md"
    md_file.write_text(content)

    # Primera instancia
    loader1 = Loader(folder=tmp_path, state_path=state_path)
    docs1 = loader1.load()
    assert len(docs1) == 1

    # Segunda instancia (nuevo Loader) → debe ver el archivo como ya procesado
    loader2 = Loader(folder=tmp_path, state_path=state_path)
    docs2 = loader2.load()
    assert len(docs2) == 0


def test_loader_state_file_format(loader, tmp_path):
    """El fichero de estado tiene formato JSON correcto con hash MD5."""
    loader.load()

    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text())

    assert "Freskito-Informático.md" in state
    assert "md5" in state["Freskito-Informático.md"]
    # MD5 debe ser un string hexadecimal de 32 caracteres
    assert len(state["Freskito-Informático.md"]["md5"]) == 32


def test_loader_ignores_non_md_files(tmp_path):
    """Loader ignora ficheros que no sean .md."""
    # Crear ficheros de prueba
    fixture_path = Path(__file__).parent.parent.parent.parent / "fixtures" / "Freskito-Informático.md"
    content = fixture_path.read_text()
    md_file = tmp_path / "test.md"
    md_file.write_text(content)

    txt_file = tmp_path / "ignore.txt"
    txt_file.write_text("Este fichero debe ignorarse")

    state_path = tmp_path / "state.json"
    loader = Loader(folder=tmp_path, state_path=state_path)
    docs = loader.load()

    # Solo debe procesar .md
    assert len(docs) == 1
    assert docs[0]["name"] == "test.md"


def test_loader_multiple_md_files(tmp_path):
    """Loader procesa múltiples ficheros .md."""
    fixture_path = Path(__file__).parent.parent.parent.parent / "fixtures" / "Freskito-Informático.md"
    content = fixture_path.read_text()

    # Crear varios ficheros
    for i in range(3):
        md_file = tmp_path / f"doc_{i}.md"
        md_file.write_text(content + f"\n\n<!-- id: {i} -->")

    state_path = tmp_path / "state.json"
    loader = Loader(folder=tmp_path, state_path=state_path)
    docs = loader.load()

    assert len(docs) == 3
    names = {doc["name"] for doc in docs}
    assert names == {"doc_0.md", "doc_1.md", "doc_2.md"}
