"""Test de integración: `ingestar_documentos_pendientes` contra Supabase y Gemini reales.

Contrato (task 21, actualizado en la task 61 para leer de
`silver.teoria_documentos` en vez de `manifest.json`, `src/theory/SPEC.md`
§Task 61): sin mocks, sube un `.md` real (mismo `render_document` que usa la
task 60 para poblar Silver) a una fila de `silver.teoria_documentos` de test
y la ingesta de verdad contra `TeoriaStore` (Supabase) y `generar_embedding`
(Gemini). Si `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` no están disponibles, o si
las tablas todavía no tienen el esquema aplicado a mano en Supabase (P25:
`silver.teoria_documentos`/`gold.teoria_chunks` con `doc_id`/`version_corpus`
reexpresados sobre la fila Silver, ver `src/jokes/schema.sql`), hace
`pytest.skip` con instrucciones explícitas — mismo patrón que
`tests/integration/test_supabase_store_live.py`/`test_telegram_bot_live.py`
(`PGRST205`, `src/jokes/KNOWN_ERRORS.md`).

Usa un `fuente`/`nombre` claramente marcados como de test y los borra al
terminar (fila Silver + objeto de Storage + chunks Gold + fila de fuentes) —
no deja basura en Supabase.
"""
import hashlib
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.ingest_teoria import ResultadoIngesta, ingestar_documentos_pendientes
from src.theory.normalizers.format_normalizer import DocumentoEntrada, render_document
from src.theory.normalizers.language_normalizer import normalize_language
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript
from src.theory.teoria_store import TeoriaStore, TeoriaStoreError

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src" / "jokes" / "schema.sql"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"

# Marcadores únicos para poder identificar y limpiar las filas/objetos de test.
_FUENTE_TEST = "test_task61_ingest_teoria_live"
_BUCKET_TEST = "silver-teoria"

_CODIGO_TABLA_INEXISTENTE = "PGRST205"
_CODIGO_COLUMNA_INEXISTENTE = "PGRST204"


def _traductor_no_op(texto: str, idioma_origen: str) -> str:
    raise AssertionError("no debería traducirse: el fixture ya está en español")


def _construir_store_o_skip() -> TeoriaStore:
    try:
        return TeoriaStore()
    except TeoriaStoreError as exc:
        pytest.skip(f"SUPABASE_URL/SUPABASE_SERVICE_KEY no disponibles en este entorno: {exc}")


def _limpiar_datos_de_test(store: TeoriaStore) -> None:
    fuentes = store.client.table("fuentes").select("id").eq("nombre", _FUENTE_TEST).execute()
    for fila in fuentes.data:
        store.client.table("teoria_chunks").delete().eq("fuente_id", fila["id"]).execute()
        store.client.table("fuentes").delete().eq("id", fila["id"]).execute()

    documentos = (
        store.client.table("teoria_documentos").select("id,object_path").eq("fuente", _FUENTE_TEST).execute()
    )
    for fila in documentos.data:
        store.client.storage.from_(_BUCKET_TEST).remove([fila["object_path"]])
        store.client.table("teoria_documentos").delete().eq("id", fila["id"]).execute()


def _subir_fila_silver_de_test(store: TeoriaStore) -> dict:
    """Genera el `.md` real (código de producción, no inventado) y lo sube a
    una fila de `silver.teoria_documentos` de test, devolviendo la fila tal
    cual la leería `listar_documentos_pendientes()`."""
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos = normalize_language(
        clean_fragments(detect_subtypes(texto)), traductor=_traductor_no_op
    )
    documento = DocumentoEntrada(
        fragmentos=fragmentos,
        fuente=_FUENTE_TEST,
        tipo_fuente="transcripcion_curso",
        licencia="personal_only",
    )
    contenido_md = render_document(
        documento.fragmentos,
        fuente=documento.fuente,
        tipo_fuente=documento.tipo_fuente,
        autor=documento.autor,
        licencia=documento.licencia,
    )
    contenido_bytes = contenido_md.encode("utf-8")
    hash_md5 = hashlib.md5(contenido_bytes).hexdigest()
    object_path = f"local_legacy/{hash_md5}/test_task61.md"

    store.client.storage.from_(_BUCKET_TEST).upload(
        object_path, contenido_bytes, {"content-type": "text/markdown"}
    )
    resultado = (
        store.client.table("teoria_documentos")
        .insert(
            {
                "bucket": _BUCKET_TEST,
                "object_path": object_path,
                "hash_md5": hash_md5,
                "origen": "local_legacy",
                "nombre": "test_task61.md",
                "origen_hash_md5": hash_md5,
                "fuente": documento.fuente,
                "tipo_fuente": documento.tipo_fuente,
                "licencia": documento.licencia,
            }
        )
        .execute()
    )
    return resultado.data[0]


def test_ingestar_documentos_pendientes_contra_supabase_y_gemini_reales():
    store = _construir_store_o_skip()

    try:
        _limpiar_datos_de_test(store)
    except APIError as exc:
        if exc.code in (_CODIGO_TABLA_INEXISTENTE, _CODIGO_COLUMNA_INEXISTENTE):
            pytest.skip(
                "'teoria_documentos'/'teoria_chunks'/'fuentes' no existen o no tienen el "
                f"esquema actualizado todavía en Supabase. Aplica {SCHEMA_SQL} (tasks 21/51/61: "
                "silver.teoria_documentos, gold.teoria_chunks con doc_id/version_corpus "
                "reexpresados sobre la fila Silver) en el SQL Editor del dashboard de "
                "Supabase antes de correr este test de integración."
            )
        raise

    try:
        try:
            fila_silver = _subir_fila_silver_de_test(store)
        except APIError as exc:
            if exc.code in (_CODIGO_TABLA_INEXISTENTE, _CODIGO_COLUMNA_INEXISTENTE):
                pytest.skip(
                    "'teoria_documentos' no tiene el esquema actualizado todavía en Supabase. "
                    f"Aplica {SCHEMA_SQL} en el SQL Editor del dashboard de Supabase antes de "
                    "correr este test de integración."
                )
            raise

        try:
            resultado = ingestar_documentos_pendientes(store, documentos=[fila_silver])
        except APIError as exc:
            if exc.code in (_CODIGO_TABLA_INEXISTENTE, _CODIGO_COLUMNA_INEXISTENTE):
                pytest.skip(
                    "'teoria_chunks' no tiene el esquema actualizado todavía en Supabase "
                    f"(falta 'doc_id'/'version_corpus' o su unique constraint). Aplica "
                    f"{SCHEMA_SQL} en el SQL Editor del dashboard de Supabase antes de correr "
                    "este test de integración."
                )
            raise

        assert isinstance(resultado, ResultadoIngesta)
        assert resultado.num_documentos == 1
        assert resultado.num_nuevos > 0
        assert resultado.num_duplicados == 0
        assert all(c.doc_id == str(fila_silver["id"]) for c in resultado.chunks)
        assert all(c.version_corpus == fila_silver["hash_md5"] for c in resultado.chunks)

        # Reingesta de la MISMA fila Silver: idempotencia real contra
        # Supabase (ON CONFLICT DO NOTHING), sin volver a listar pendientes
        # (la fila ya no aparecería en `listar_documentos_pendientes()`).
        resultado_repetido = ingestar_documentos_pendientes(store, documentos=[fila_silver])
        assert resultado_repetido.num_nuevos == 0
        assert resultado_repetido.num_duplicados == resultado.num_nuevos

        # `listar_documentos_pendientes()` ya no debe devolver esta fila
        # (tiene chunks en Gold).
        pendientes = store.listar_documentos_pendientes()
        assert str(fila_silver["id"]) not in {str(f["id"]) for f in pendientes}
    finally:
        _limpiar_datos_de_test(store)
