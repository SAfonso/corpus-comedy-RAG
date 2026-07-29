"""Tests unitarios de `src/theory/teoria_store.py` — lógica pura, sin red.

Cubre (§Storage, task 21) la construcción del payload de upsert de
`teoria_chunks`. El acceso real a Supabase (`TeoriaStore` contra la API
real) se cubre en `tests/integration/test_ingest_teoria_live.py`.
"""
import pytest

from src.theory.teoria_store import (
    SCHEMA_MODE_DEFAULT,
    TeoriaStore,
    _SCHEMA_TABLAS,
    _build_chunk_payload,
    _schema_mode,
    _tabla,
)


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


# ---------------------------------------------------------------------------
# Acceso por schema (task 54, `src/jokes/SPEC.md` §"Acceso por schema con
# supabase-py" — mismo contrato que `src/jokes/supabase_store.py`, duplicado
# aquí porque `theory/` no importa `jokes/`, ver docstring del módulo).
# `_tabla()` es el único punto de resolución tabla -> builder PostgREST.
# ---------------------------------------------------------------------------

class _RecordingResultado:
    def __init__(self, data):
        self.data = data


class _RecordingQuery:
    """Doble fluido genérico (mismo patrón que
    `tests/unit/jokes/test_supabase_store.py`): acepta cualquier método de la
    cadena PostgREST sin validar argumentos, hasta `.execute()`."""

    def select(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    def upsert(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        return _RecordingResultado([{"id": 7}])


class _RecordingSchema:
    def __init__(self, client, nombre_schema):
        self._client = client
        self._nombre_schema = nombre_schema

    def table(self, nombre_tabla):
        self._client.llamadas.append({"schema": self._nombre_schema, "tabla": nombre_tabla})
        return _RecordingQuery()


class _RecordingClient:
    """Doble de cliente que registra cada `.table(...)` /
    `.schema(...).table(...)` invocado, en orden."""

    def __init__(self):
        self.llamadas: list[dict] = []

    def table(self, nombre_tabla):
        self.llamadas.append({"schema": None, "tabla": nombre_tabla})
        return _RecordingQuery()

    def schema(self, nombre_schema):
        return _RecordingSchema(self, nombre_schema)


def test_schema_mode_default_es_public():
    """Restricción dura de la task 54: el valor por defecto sigue apuntando
    a `public` hasta el cutover (tasks 55/56)."""
    assert SCHEMA_MODE_DEFAULT == "public"


def test_schema_mode_lee_env_var(monkeypatch):
    monkeypatch.delenv("SUPABASE_SCHEMA_MODE", raising=False)
    assert _schema_mode() == "public"

    monkeypatch.setenv("SUPABASE_SCHEMA_MODE", "p25")
    assert _schema_mode() == "p25"


def test_schema_tablas_coincide_con_contrato_de_spec():
    """`_SCHEMA_TABLAS` es exactamente el mapeo que fija `src/jokes/SPEC.md`
    §"Acceso por schema con supabase-py" para `teoria_chunks`/`fuentes`."""
    assert _SCHEMA_TABLAS == {
        "teoria_chunks": ("gold", "teoria_chunks"),
        "fuentes": ("silver", "fuentes"),
    }


def test_tabla_modo_public_no_antepone_schema(monkeypatch):
    monkeypatch.delenv("SUPABASE_SCHEMA_MODE", raising=False)
    client = _RecordingClient()

    _tabla(client, "teoria_chunks")

    assert client.llamadas == [{"schema": None, "tabla": "teoria_chunks"}]


def test_tabla_modo_p25_antepone_schema_segun_contrato(monkeypatch):
    monkeypatch.setenv("SUPABASE_SCHEMA_MODE", "p25")
    client = _RecordingClient()

    _tabla(client, "teoria_chunks")

    assert client.llamadas == [{"schema": "gold", "tabla": "teoria_chunks"}]


_TABLA_LOGICA_POR_METODO = {
    "guardar_chunk": "teoria_chunks",
    "buscar_o_crear_fuente": "fuentes",
}

_INVOCACIONES = {
    "guardar_chunk": lambda store: store.guardar_chunk(
        doc_id="documents/foo.txt",
        version_corpus="v1",
        chunk_index=0,
        contenido="un fragmento",
        embedding=[0.1, 0.2],
        tipo_fuente="teoria",
    ),
    "buscar_o_crear_fuente": lambda store: store.buscar_o_crear_fuente("nombre"),
}

assert set(_INVOCACIONES) == set(_TABLA_LOGICA_POR_METODO), (
    "cada método cubierto por el contrato de schema debe tener también su "
    "invocación mínima — lista desincronizada"
)


@pytest.mark.parametrize("nombre_metodo", sorted(_TABLA_LOGICA_POR_METODO))
def test_metodo_usa_tabla_publica_sin_schema_por_defecto(nombre_metodo, monkeypatch):
    """Con `SUPABASE_SCHEMA_MODE` no fijada (default `"public"`), cada método
    invoca `client.table(nombre_logico)` — nunca `client.schema(...)` — el
    mismo call site que existía antes de la task 54."""
    monkeypatch.delenv("SUPABASE_SCHEMA_MODE", raising=False)
    client = _RecordingClient()
    store = TeoriaStore(client=client)

    _INVOCACIONES[nombre_metodo](store)

    assert client.llamadas[-1] == {
        "schema": None,
        "tabla": _TABLA_LOGICA_POR_METODO[nombre_metodo],
    }


@pytest.mark.parametrize("nombre_metodo", sorted(_TABLA_LOGICA_POR_METODO))
def test_metodo_usa_schema_del_contrato_tras_cutover(nombre_metodo, monkeypatch):
    """Con `SUPABASE_SCHEMA_MODE=p25` (simula el cutover de las tasks 55/56),
    cada método invoca `client.schema(schema).table(tabla)` con el par exacto
    que fija `SPEC.md` §"Acceso por schema con supabase-py"."""
    monkeypatch.setenv("SUPABASE_SCHEMA_MODE", "p25")
    client = _RecordingClient()
    store = TeoriaStore(client=client)

    _INVOCACIONES[nombre_metodo](store)

    nombre_logico = _TABLA_LOGICA_POR_METODO[nombre_metodo]
    schema, tabla = _SCHEMA_TABLAS[nombre_logico]
    assert client.llamadas[-1] == {"schema": schema, "tabla": tabla}
