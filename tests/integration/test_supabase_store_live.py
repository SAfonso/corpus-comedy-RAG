"""Test de integración: `SupabaseStore` contra el proyecto Supabase real.

Contrato (task 12): `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` ya están
verificadas en `.env` (conexión probada por el leader contra
`{SUPABASE_URL}/rest/v1/` → 200 OK, task 1). Este test se conecta de verdad,
sin mocks, y hace una operación real.

El DDL de `src/jokes/schema.sql` NO puede aplicarse desde este cliente (la
API REST/PostgREST no ejecuta `CREATE TABLE`, ver docstring de
`src/jokes/supabase_store.py`) — hay que pegarlo a mano en el SQL Editor del
dashboard de Supabase. Si las tablas aún no existen en este entorno (caso
esperado hasta que el usuario aplique el `.sql`), el test hace `pytest.skip`
con instrucciones explícitas, mismo patrón que el skip de DeepL
(`tests/integration/test_language_normalizer_deepl.py`) — NUNCA falla en
silencio ni mockea la ausencia de tablas.

Si las tablas SÍ existen, el test inserta una fila de prueba claramente
marcada (`propuesto_por="test_task12_supabase_store_live"`) en
`candidatos_taxonomia` (tabla de menor riesgo: cola de revisión humana, no
toca `chistes`/taxonomías reales) y la borra al terminar — no deja basura.
"""
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from src.jokes.supabase_store import SupabaseStore, SupabaseStoreError

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src" / "jokes" / "schema.sql"

# Marcador único para poder identificar y limpiar la fila de test sin
# ambigüedad, incluso si el test se interrumpe a medias en una corrida previa.
_MARCADOR_TEST = "test_task12_supabase_store_live"

# Código de error propio de PostgREST (no el código Postgres "42P01") para
# "tabla no encontrada en el cache de esquema de PostgREST" — es lo que
# devuelve la API REST tanto si la tabla no existe como si existe pero
# PostgREST todavía no ha refrescado su cache de esquema tras el CREATE
# TABLE. Confirmado empíricamente contra el proyecto Supabase real de esta
# task (ver `src/jokes/KNOWN_ERRORS.md`): PostgREST nunca deja pasar el
# código Postgres nativo a través de la API REST, solo el suyo (PGRSTxxx).
_CODIGO_TABLA_INEXISTENTE = "PGRST205"


def _construir_store_o_skip() -> SupabaseStore:
    try:
        return SupabaseStore()
    except SupabaseStoreError as exc:
        pytest.skip(f"SUPABASE_URL/SUPABASE_SERVICE_KEY no disponibles en este entorno: {exc}")


def _limpiar_candidatos_de_test(store: SupabaseStore) -> None:
    """Borra cualquier fila de test que hubiera quedado de una corrida previa."""
    store.client.table("candidatos_taxonomia").delete().eq(
        "propuesto_por", _MARCADOR_TEST
    ).execute()


def test_supabase_store_contra_candidatos_taxonomia_real():
    store = _construir_store_o_skip()

    try:
        _limpiar_candidatos_de_test(store)
    except APIError as exc:
        if exc.code == _CODIGO_TABLA_INEXISTENTE:
            pytest.skip(
                "La tabla 'candidatos_taxonomia' no existe todavía en Supabase. "
                f"Aplica {SCHEMA_SQL} en el SQL Editor del dashboard de Supabase "
                "(Project -> SQL Editor -> pegar y ejecutar) antes de correr este "
                "test de integración."
            )
        raise

    creado = None
    try:
        creado = store.crear_candidato_taxonomia(
            tipo="tema",
            texto="tema de prueba (task 12, borrar si se ve en producción)",
            propuesto_por=_MARCADOR_TEST,
        )
        assert creado["tipo"] == "tema"
        assert creado["estado"] == "pendiente"
        assert "id" in creado

        pendientes = store.listar_candidatos_taxonomia(estado="pendiente")
        assert any(c["id"] == creado["id"] for c in pendientes)

        actualizado = store.actualizar_candidato_taxonomia(creado["id"], estado="rechazado")
        assert actualizado["estado"] == "rechazado"
    finally:
        # Limpieza real: no dejar basura en la tabla, corra el test en verde
        # o falle a medias.
        _limpiar_candidatos_de_test(store)


def test_supabase_store_listar_temas_real():
    store = _construir_store_o_skip()

    try:
        temas = store.listar_temas()
    except APIError as exc:
        if exc.code == _CODIGO_TABLA_INEXISTENTE:
            pytest.skip(
                "La tabla 'temas' no existe todavía en Supabase. "
                f"Aplica {SCHEMA_SQL} en el SQL Editor del dashboard de Supabase "
                "antes de correr este test de integración."
            )
        raise

    assert isinstance(temas, list)
