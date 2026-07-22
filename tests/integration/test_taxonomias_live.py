"""Test de integración: `resolver_taxonomia` contra Supabase y Gemini reales.

Contrato (task 14, `src/jokes/SPEC.md` §Taxonomías): `SUPABASE_URL`/
`SUPABASE_SERVICE_KEY` (task 12) y `LLM_API_KEY`/`LLM_MODEL` (task 13) ya
están verificadas en `.env` — este test llama de verdad a ambas, sin mocks,
usando `SupabaseStore()` real (sin `client` inyectado) y `resolver_taxonomia`
sin `llamar_llm` inyectado (usa `generar_json` real de
`src/utils/llm/client.py`). Mismo patrón de `skip` explícito que
`test_supabase_store_live.py`/`test_silver_live.py` si las credenciales o las
tablas no están disponibles en este entorno — nunca falla en silencio ni
mockea la ausencia de infraestructura.

## CASO DE ÉXITO (match, intento 1 o 2)

Las tablas `temas`/`tecnicas` están vacías en un proyecto Supabase recién
creado (`schema.sql` sin filas todavía) — sin al menos una fila real no hay
forma de testear el camino de "match" del loop contra la API real. Se siembra
UNA fila real de referencia en `tecnicas` (`nombre="setup/punchline"`, el
mismo string real que produjo Silver como `estructura_detectada` sobre el
chiste real de `Freskito-Informático.md`, task 13) usando
`SupabaseStore.crear_tecnica` (ya existente, sin reimplementar acceso), y se
limpia esa fila al final del test pase o falle — mismo espíritu que la
limpieza ya hecha en `test_supabase_store_live.py` (task 12): no es un
fixture inventado, es sembrar la tabla de referencia real con un dato real de
prueba, limpiándolo después.

## CASO DE FALLO (agotamiento de los 3 intentos → cola)

Se usa un string real pero deliberadamente inventado/raro que no coincide con
nada sembrado (`"paradoja del abuelo cuántico en el probador de una tienda de
disfraces de flamenco"`) — no hay ninguna fila real de `temas` con ese
nombre, así que el LLM no debería encontrar match ni en el intento 1 ni con
el contexto real de los intentos 2/3, y el loop debe agotar los 3 intentos y
encolar en `candidatos_taxonomia`. Se limpia esa fila encolada al final.
"""
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from src.jokes.supabase_store import SupabaseStore, SupabaseStoreError
from src.jokes.taxonomias import MAX_INTENTOS, resolver_taxonomia
from src.utils.llm.client import LLMClientError

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src" / "jokes" / "schema.sql"

_CODIGO_TABLA_INEXISTENTE = "PGRST205"

# String real producido por Silver (task 13) sobre el chiste real de
# `Freskito-Informático.md` — usado tal cual como seed y como input del caso
# de éxito (nunca inventado, ver docstring del módulo y CLAUDE.md).
_TECNICA_SEED = "setup/punchline"
_MARCADOR_TEST = "test_task14_taxonomias_live"

# String real pero deliberadamente inventado/raro para el caso de fallo — no
# corresponde a ninguna fila sembrada ni real de `temas`.
_TEMA_SIN_MATCH = (
    "paradoja del abuelo cuántico en el probador de una tienda de disfraces "
    "de flamenco"
)


def _construir_store_o_skip() -> SupabaseStore:
    try:
        return SupabaseStore()
    except SupabaseStoreError as exc:
        pytest.skip(f"SUPABASE_URL/SUPABASE_SERVICE_KEY no disponibles en este entorno: {exc}")


def _skip_si_tabla_no_existe(exc: APIError, tabla: str):
    if exc.code == _CODIGO_TABLA_INEXISTENTE:
        pytest.skip(
            f"La tabla '{tabla}' no existe todavía en Supabase. Aplica {SCHEMA_SQL} "
            "en el SQL Editor del dashboard de Supabase antes de correr este test "
            "de integración."
        )
    raise exc


def _limpiar_tecnicas_seed(store: SupabaseStore) -> None:
    store.client.table("tecnicas").delete().eq("nombre", _TECNICA_SEED).execute()


def _limpiar_candidatos_de_test(store: SupabaseStore) -> None:
    store.client.table("candidatos_taxonomia").delete().eq(
        "propuesto_por", _MARCADOR_TEST
    ).execute()


def test_resolver_taxonomia_encuentra_match_con_seed_real():
    store = _construir_store_o_skip()

    try:
        _limpiar_tecnicas_seed(store)  # por si quedó basura de una corrida previa
    except APIError as exc:
        _skip_si_tabla_no_existe(exc, "tecnicas")

    seed = None
    try:
        seed = store.crear_tecnica(_TECNICA_SEED)
        assert seed["nombre"] == _TECNICA_SEED

        try:
            resultado = resolver_taxonomia(_TECNICA_SEED, "tecnica", store)
        except LLMClientError as exc:
            pytest.skip(f"LLM_API_KEY/LLM_MODEL no disponibles en este entorno: {exc}")

        assert resultado.match is True, (
            "el loop debería encontrar la fila sembrada en intento 1 o 2 "
            f"(intentos usados: {resultado.intentos})"
        )
        assert resultado.fila["id"] == seed["id"]
        assert resultado.intentos in (1, 2, MAX_INTENTOS)
        assert resultado.candidato is None
    finally:
        _limpiar_tecnicas_seed(store)


def test_resolver_taxonomia_agota_intentos_y_encola_candidato_real():
    store = _construir_store_o_skip()

    try:
        _limpiar_candidatos_de_test(store)
    except APIError as exc:
        _skip_si_tabla_no_existe(exc, "candidatos_taxonomia")

    try:
        try:
            resultado = resolver_taxonomia(
                _TEMA_SIN_MATCH, "tema", store, propuesto_por=_MARCADOR_TEST
            )
        except LLMClientError as exc:
            pytest.skip(f"LLM_API_KEY/LLM_MODEL no disponibles en este entorno: {exc}")

        assert resultado.match is False
        assert resultado.fila is None
        assert resultado.intentos == MAX_INTENTOS
        assert resultado.candidato is not None
        assert resultado.candidato["tipo"] == "tema"
        assert resultado.candidato["texto"] == _TEMA_SIN_MATCH
        assert resultado.candidato["estado"] == "pendiente"

        pendientes = store.listar_candidatos_taxonomia(estado="pendiente")
        assert any(c["id"] == resultado.candidato["id"] for c in pendientes)
    finally:
        _limpiar_candidatos_de_test(store)
