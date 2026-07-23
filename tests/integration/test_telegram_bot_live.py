"""Test de integración: `procesar_mensaje_telegram` contra Supabase real.

Contrato (task 16, `src/jokes/telegram/SPEC.md` §Bronze): sin mocks, llama de
verdad a `SupabaseStore.guardar_mensaje_telegram_bronze` (vía
`procesar_mensaje_telegram`) contra la tabla `chistes_telegram_bronze`. La
tabla es NUEVA en `src/jokes/schema.sql` (esta task) — si el entorno de
Supabase todavía no tiene el `schema.sql` actualizado aplicado a mano (ver
docstring de `supabase_store.py`), hace `pytest.skip` con instrucciones
explícitas, mismo patrón que `test_supabase_store_live.py`
(`src/jokes/KNOWN_ERRORS.md` — PostgREST devuelve `PGRST205`, no `42P01`,
cuando falta una tabla).

Inserta un update de prueba con un `telegram_update_id` claramente fuera de
rango real (marcador negativo, imposible de colisionar con un update real de
Telegram, que siempre son positivos) y lo borra al terminar — no deja basura.
"""
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from src.jokes.supabase_store import SupabaseStore, SupabaseStoreError
from src.jokes.telegram.telegram_bot import ResultadoProcesamiento, procesar_mensaje_telegram

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src" / "jokes" / "schema.sql"

# Marcador negativo: Telegram nunca emite update_id negativos, así que este
# valor no puede colisionar con un evento real.
_UPDATE_ID_TEST = -161616

_CODIGO_TABLA_INEXISTENTE = "PGRST205"


def _construir_store_o_skip() -> SupabaseStore:
    try:
        return SupabaseStore()
    except SupabaseStoreError as exc:
        pytest.skip(f"SUPABASE_URL/SUPABASE_SERVICE_KEY no disponibles en este entorno: {exc}")


def _limpiar_mensaje_de_test(store: SupabaseStore) -> None:
    store.client.table("chistes_telegram_bronze").delete().eq(
        "telegram_update_id", _UPDATE_ID_TEST
    ).execute()


def test_procesar_mensaje_telegram_contra_supabase_real():
    store = _construir_store_o_skip()

    try:
        _limpiar_mensaje_de_test(store)
    except APIError as exc:
        if exc.code == _CODIGO_TABLA_INEXISTENTE:
            pytest.skip(
                "La tabla 'chistes_telegram_bronze' no existe todavía en Supabase. "
                f"Aplica {SCHEMA_SQL} (actualizado en la task 16) en el SQL Editor "
                "del dashboard de Supabase antes de correr este test de integración."
            )
        raise

    update = {
        "update_id": _UPDATE_ID_TEST,
        "message": {
            "chat": {"id": 999, "type": "private"},
            "date": 1690000000,
            "text": "chiste de prueba de integración (task 16, borrar si se ve en producción)",
        },
    }

    try:
        resultado = procesar_mensaje_telegram(update, store)
        assert isinstance(resultado, ResultadoProcesamiento)
        assert resultado.es_duplicado is False
        assert resultado.fila_bronze["telegram_update_id"] == _UPDATE_ID_TEST

        # Reenvío del mismo update_id: idempotencia real contra Supabase.
        resultado_repetido = procesar_mensaje_telegram(update, store)
        assert resultado_repetido.es_duplicado is True
        assert resultado_repetido.fila_bronze is None
    finally:
        _limpiar_mensaje_de_test(store)
