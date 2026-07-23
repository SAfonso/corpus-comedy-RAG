"""Test de integración: `ingestar_version` contra Supabase y Gemini reales.

Contrato (task 21, `src/theory/SPEC.md` §Storage): sin mocks, genera una
`v1/` real en `tmp_path` (misma cadena real que
`tests/unit/theory/test_ingest_teoria.py`, sobre
`tests/fixtures/sample_transcript.txt`) y la ingesta de verdad contra
`TeoriaStore` (Supabase) y `generar_embedding` (Gemini). Si
`SUPABASE_URL`/`SUPABASE_SERVICE_KEY` no están disponibles, o si la tabla
`teoria_chunks` todavía no tiene las columnas nuevas de esta task
(`chunk_index` + `unique(doc_id, version_corpus, chunk_index)`, ver
`src/jokes/schema.sql`) aplicadas a mano en Supabase, hace `pytest.skip` con
instrucciones explícitas — mismo patrón que
`tests/integration/test_supabase_store_live.py`/`test_telegram_bot_live.py`
(`PGRST205`, `src/jokes/KNOWN_ERRORS.md`).

Usa un `doc_id`/`fuente` claramente marcados como de test y los borra al
terminar — no deja basura en Supabase.
"""
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.ingest_teoria import ResultadoIngesta, ingestar_version
from src.theory.normalizers.format_normalizer import DocumentoEntrada, generar_version
from src.theory.normalizers.language_normalizer import normalize_language
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript
from src.theory.teoria_store import TeoriaStore, TeoriaStoreError

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src" / "jokes" / "schema.sql"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"

# Marcador único para poder identificar y limpiar las filas de test.
_FUENTE_TEST = "test_task21_ingest_teoria_live"

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


def test_ingestar_version_contra_supabase_y_gemini_reales(tmp_path):
    store = _construir_store_o_skip()

    try:
        _limpiar_datos_de_test(store)
    except APIError as exc:
        if exc.code in (_CODIGO_TABLA_INEXISTENTE, _CODIGO_COLUMNA_INEXISTENTE):
            pytest.skip(
                "'teoria_chunks'/'fuentes' no existen o no tienen el esquema actualizado "
                f"todavía en Supabase. Aplica {SCHEMA_SQL} (actualizado en la task 21: "
                "columna chunk_index + unique constraint) en el SQL Editor del dashboard "
                "de Supabase antes de correr este test de integración."
            )
        raise

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
    directorio_base = tmp_path / "processed"
    generar_version([documento], directorio_base)

    try:
        try:
            resultado = ingestar_version(directorio_base, store)
        except APIError as exc:
            if exc.code in (_CODIGO_TABLA_INEXISTENTE, _CODIGO_COLUMNA_INEXISTENTE):
                pytest.skip(
                    "'teoria_chunks' no tiene el esquema actualizado todavía en Supabase "
                    f"(falta 'chunk_index' o su unique constraint). Aplica {SCHEMA_SQL} "
                    "(actualizado en la task 21) en el SQL Editor del dashboard de Supabase "
                    "antes de correr este test de integración."
                )
            raise

        assert isinstance(resultado, ResultadoIngesta)
        assert resultado.num_nuevos > 0
        assert resultado.num_duplicados == 0

        # Reingesta: idempotencia real contra Supabase (ON CONFLICT DO NOTHING).
        resultado_repetido = ingestar_version(directorio_base, store)
        assert resultado_repetido.num_nuevos == 0
        assert resultado_repetido.num_duplicados == resultado.num_nuevos
    finally:
        _limpiar_datos_de_test(store)
