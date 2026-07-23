"""Tests unitarios de `src/theory/teoria_store.py` — lógica pura, sin red.

Cubre (§Storage, task 21) la construcción del payload de upsert de
`teoria_chunks`. El acceso real a Supabase (`TeoriaStore` contra la API
real) se cubre en `tests/integration/test_ingest_teoria_live.py`.
"""
import pytest

from src.theory.teoria_store import _build_chunk_payload


def test_build_chunk_payload_minimo_omite_opcionales():
    payload = _build_chunk_payload(
        doc_id="documents/foo.txt",
        version_corpus="v1",
        chunk_index=0,
        contenido="un fragmento de teoria",
        embedding=[0.1, 0.2],
        tipo_fuente="teoria",
    )
    assert payload == {
        "doc_id": "documents/foo.txt",
        "version_corpus": "v1",
        "chunk_index": 0,
        "contenido": "un fragmento de teoria",
        "embedding": [0.1, 0.2],
        "tipo_fuente": "teoria",
    }


def test_build_chunk_payload_completo():
    payload = _build_chunk_payload(
        doc_id="documents/foo.txt",
        version_corpus="v1",
        chunk_index=2,
        contenido="un fragmento de teoria",
        embedding=[0.1, 0.2],
        tipo_fuente="transcripcion_curso",
        fuente_id=7,
        licencia="personal_only",
    )
    assert payload == {
        "doc_id": "documents/foo.txt",
        "version_corpus": "v1",
        "chunk_index": 2,
        "contenido": "un fragmento de teoria",
        "embedding": [0.1, 0.2],
        "tipo_fuente": "transcripcion_curso",
        "fuente_id": 7,
        "licencia": "personal_only",
    }


def test_build_chunk_payload_requiere_campos_obligatorios():
    with pytest.raises(TypeError):
        _build_chunk_payload(doc_id="documents/foo.txt", version_corpus="v1")
