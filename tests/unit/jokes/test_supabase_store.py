"""Tests unitarios de la lógica PURA de `src/jokes/supabase_store.py`.

Contrato (task 12): igual que `_necesita_ocr_fallback`/`_necesita_traduccion`
en tareas anteriores, se testea solo la lógica de decisión (validación de
enums + construcción de payloads) sin ninguna dependencia de red ni mock
frágil de `supabase-py`. El acceso real a Supabase (`SupabaseStore` contra
la API real) se cubre en
`tests/integration/test_supabase_store_live.py`.
"""
import pytest

from src.jokes.supabase_store import (
    ESTADOS_CANDIDATO_TAXONOMIA,
    ESTADOS_CHISTE,
    SCHEMA_MODE_DEFAULT,
    TIPOS_CANDIDATO_TAXONOMIA,
    TIPOS_FUENTE_CHISTE,
    SupabaseStore,
    _SCHEMA_TABLAS,
    _build_candidato_payload,
    _build_candidato_update_payload,
    _build_chiste_payload,
    _build_chiste_update_payload,
    _build_mensaje_telegram_bronze_payload,
    _build_telegram_bronze_procesado_payload,
    _build_revision_payload,
    _normalizar_tipo_fuente_candidatos,
    _parsear_embedding,
    _schema_mode,
    _tabla,
    _validar_estado_candidato,
    _validar_estado_chiste,
    _validar_tipo_candidato,
    _validar_tipo_fuente_chiste,
)


# ---------------------------------------------------------------------------
# Validación de enums (§Storage) — valores permitidos exactos de SPEC.md
# ---------------------------------------------------------------------------

def test_tipos_fuente_chiste_coinciden_con_spec():
    assert TIPOS_FUENTE_CHISTE == ("propio", "propio_historico")


def test_estados_chiste_coinciden_con_spec():
    assert ESTADOS_CHISTE == ("idea_suelta", "con_estructura", "rematado")


def test_tipos_candidato_taxonomia_coinciden_con_spec():
    assert TIPOS_CANDIDATO_TAXONOMIA == ("tema", "tecnica")


def test_estados_candidato_taxonomia_coinciden_con_spec():
    assert ESTADOS_CANDIDATO_TAXONOMIA == ("pendiente", "aceptado", "rechazado")


@pytest.mark.parametrize("tipo_fuente", TIPOS_FUENTE_CHISTE)
def test_validar_tipo_fuente_chiste_acepta_valores_validos(tipo_fuente):
    assert _validar_tipo_fuente_chiste(tipo_fuente) == tipo_fuente


def test_validar_tipo_fuente_chiste_acepta_none():
    assert _validar_tipo_fuente_chiste(None) is None


def test_validar_tipo_fuente_chiste_rechaza_valor_invalido():
    with pytest.raises(ValueError, match="tipo_fuente inválido"):
        _validar_tipo_fuente_chiste("ajeno")


@pytest.mark.parametrize("estado", ESTADOS_CHISTE)
def test_validar_estado_chiste_acepta_valores_validos(estado):
    assert _validar_estado_chiste(estado) == estado


def test_validar_estado_chiste_rechaza_valor_invalido():
    with pytest.raises(ValueError, match="estado inválido"):
        _validar_estado_chiste("terminado")


def test_validar_tipo_candidato_rechaza_valor_invalido():
    with pytest.raises(ValueError, match="tipo inválido"):
        _validar_tipo_candidato("fuente")


@pytest.mark.parametrize("tipo", TIPOS_CANDIDATO_TAXONOMIA)
def test_validar_tipo_candidato_acepta_valores_validos(tipo):
    assert _validar_tipo_candidato(tipo) == tipo


def test_validar_estado_candidato_rechaza_valor_invalido():
    with pytest.raises(ValueError, match="estado inválido"):
        _validar_estado_candidato("en_revision")


@pytest.mark.parametrize("estado", ESTADOS_CANDIDATO_TAXONOMIA)
def test_validar_estado_candidato_acepta_valores_validos(estado):
    assert _validar_estado_candidato(estado) == estado


# ---------------------------------------------------------------------------
# _build_chiste_payload
# ---------------------------------------------------------------------------

def test_build_chiste_payload_minimo_aplica_defaults_y_omite_opcionales():
    payload = _build_chiste_payload(
        texto_normalizado="por qué los pollos cruzan la calle",
        hash_normalizado="abc123",
        tipo_fuente="propio",
    )
    assert payload == {
        "texto_normalizado": "por qué los pollos cruzan la calle",
        "hash_normalizado": "abc123",
        "tipo_fuente": "propio",
        "estado": "idea_suelta",
        "version_actual": 1,
    }
    # Opcionales no proporcionados (incl. licencia) se omiten del payload
    # para que aplique el default de la DDL, no se envía None a Supabase.
    for clave in ("embedding", "tema_id", "tecnica_id", "fuente_id", "chiste_origen_id", "licencia"):
        assert clave not in payload


def test_build_chiste_payload_completo_incluye_todos_los_campos():
    payload = _build_chiste_payload(
        texto_normalizado="texto",
        hash_normalizado="hash",
        tipo_fuente="propio_historico",
        embedding=[0.1, 0.2, 0.3],
        tema_id=1,
        tecnica_id=2,
        fuente_id=3,
        estado="rematado",
        version_actual=4,
        chiste_origen_id="11111111-1111-1111-1111-111111111111",
        licencia="personal_only",
    )
    assert payload == {
        "texto_normalizado": "texto",
        "hash_normalizado": "hash",
        "tipo_fuente": "propio_historico",
        "estado": "rematado",
        "version_actual": 4,
        "embedding": [0.1, 0.2, 0.3],
        "tema_id": 1,
        "tecnica_id": 2,
        "fuente_id": 3,
        "chiste_origen_id": "11111111-1111-1111-1111-111111111111",
        "licencia": "personal_only",
    }


def test_build_chiste_payload_rechaza_tipo_fuente_invalido():
    with pytest.raises(ValueError, match="tipo_fuente inválido"):
        _build_chiste_payload(
            texto_normalizado="x", hash_normalizado="h", tipo_fuente="ajeno"
        )


def test_build_chiste_payload_rechaza_estado_invalido():
    with pytest.raises(ValueError, match="estado inválido"):
        _build_chiste_payload(
            texto_normalizado="x",
            hash_normalizado="h",
            tipo_fuente="propio",
            estado="terminado",
        )


def test_build_chiste_payload_requiere_campos_obligatorios():
    with pytest.raises(TypeError):
        _build_chiste_payload(texto_normalizado="x")  # falta hash_normalizado, tipo_fuente


# ---------------------------------------------------------------------------
# _build_chiste_update_payload
# ---------------------------------------------------------------------------

def test_build_chiste_update_payload_incluye_updated_at():
    payload = _build_chiste_update_payload({"estado": "rematado"})
    assert payload["estado"] == "rematado"
    assert "updated_at" in payload
    assert isinstance(payload["updated_at"], str) and payload["updated_at"]


def test_build_chiste_update_payload_rechaza_columna_no_actualizable():
    with pytest.raises(ValueError, match="no actualizables"):
        _build_chiste_update_payload({"id": "algo"})


def test_build_chiste_update_payload_rechaza_columna_inventada():
    with pytest.raises(ValueError, match="no actualizables"):
        _build_chiste_update_payload({"columna_inventada": "x"})


def test_build_chiste_update_payload_valida_tipo_fuente_si_presente():
    with pytest.raises(ValueError, match="tipo_fuente inválido"):
        _build_chiste_update_payload({"tipo_fuente": "ajeno"})


def test_build_chiste_update_payload_valida_estado_si_presente():
    with pytest.raises(ValueError, match="estado inválido"):
        _build_chiste_update_payload({"estado": "terminado"})


# ---------------------------------------------------------------------------
# _build_revision_payload (append-only, §Versionado)
# ---------------------------------------------------------------------------

def test_build_revision_payload_minimo():
    payload = _build_revision_payload(
        chiste_id="11111111-1111-1111-1111-111111111111",
        version=1,
        contenido="primera version del chiste",
    )
    assert payload == {
        "chiste_id": "11111111-1111-1111-1111-111111111111",
        "version": 1,
        "contenido": "primera version del chiste",
    }


def test_build_revision_payload_completo():
    payload = _build_revision_payload(
        chiste_id="11111111-1111-1111-1111-111111111111",
        version=2,
        contenido="version revisada",
        estructura_detectada={"tipo": "setup_punchline"},
        estado="con_estructura",
        sugerencias_mejora="acorta el setup",
    )
    assert payload == {
        "chiste_id": "11111111-1111-1111-1111-111111111111",
        "version": 2,
        "contenido": "version revisada",
        "estructura_detectada": {"tipo": "setup_punchline"},
        "estado": "con_estructura",
        "sugerencias_mejora": "acorta el setup",
    }


def test_build_revision_payload_rechaza_estado_invalido():
    with pytest.raises(ValueError, match="estado inválido"):
        _build_revision_payload(
            chiste_id="x", version=1, contenido="c", estado="terminado"
        )


# ---------------------------------------------------------------------------
# _build_candidato_payload / _build_candidato_update_payload (§Taxonomías)
# ---------------------------------------------------------------------------

def test_build_candidato_payload_minimo_default_pendiente():
    payload = _build_candidato_payload(tipo="tema", texto="crisis existencial")
    assert payload == {"tipo": "tema", "texto": "crisis existencial", "estado": "pendiente"}


def test_build_candidato_payload_con_propuesto_por():
    payload = _build_candidato_payload(
        tipo="tecnica", texto="callback", propuesto_por="silver_llm"
    )
    assert payload == {
        "tipo": "tecnica",
        "texto": "callback",
        "estado": "pendiente",
        "propuesto_por": "silver_llm",
    }


def test_build_candidato_payload_rechaza_tipo_invalido():
    with pytest.raises(ValueError, match="tipo inválido"):
        _build_candidato_payload(tipo="fuente", texto="x")


def test_build_candidato_payload_rechaza_estado_invalido():
    with pytest.raises(ValueError, match="estado inválido"):
        _build_candidato_payload(tipo="tema", texto="x", estado="en_revision")


def test_build_candidato_update_payload_valido():
    assert _build_candidato_update_payload("aceptado") == {"estado": "aceptado"}


def test_build_candidato_update_payload_rechaza_estado_invalido():
    with pytest.raises(ValueError, match="estado inválido"):
        _build_candidato_update_payload("en_revision")


# ---------------------------------------------------------------------------
# _build_mensaje_telegram_bronze_payload (Flujo B, task 16, §Bronze)
# ---------------------------------------------------------------------------

def test_build_mensaje_telegram_bronze_payload_minimo_omite_opcionales():
    payload = _build_mensaje_telegram_bronze_payload(
        telegram_update_id=100, texto_raw="un chiste"
    )
    assert payload == {"telegram_update_id": 100, "texto_raw": "un chiste"}


def test_build_mensaje_telegram_bronze_payload_completo():
    payload = _build_mensaje_telegram_bronze_payload(
        telegram_update_id=100,
        texto_raw="un chiste",
        chat_id=555,
        timestamp_telegram="2023-07-22T04:26:40+00:00",
    )
    assert payload == {
        "telegram_update_id": 100,
        "texto_raw": "un chiste",
        "chat_id": 555,
        "timestamp_telegram": "2023-07-22T04:26:40+00:00",
    }


def test_build_mensaje_telegram_bronze_payload_requiere_campos_obligatorios():
    with pytest.raises(TypeError):
        _build_mensaje_telegram_bronze_payload(texto_raw="un chiste")  # falta telegram_update_id


# ---------------------------------------------------------------------------
# _build_telegram_bronze_procesado_payload (task 35, telegram/SPEC.md
# §Recuperación de fallos — paso 11, marcado de `procesado_at`)
# ---------------------------------------------------------------------------

def test_build_telegram_bronze_procesado_payload_solo_tiene_procesado_at():
    payload = _build_telegram_bronze_procesado_payload()
    assert set(payload.keys()) == {"procesado_at"}


def test_build_telegram_bronze_procesado_payload_no_toca_texto_raw_ni_otras_columnas():
    """Bronze sagrado (CLAUDE.md): el UPDATE del paso 11 SOLO puede tocar
    `procesado_at` — nunca `texto_raw`, `chat_id` ni ninguna otra columna del
    mensaje original."""
    payload = _build_telegram_bronze_procesado_payload()
    assert "texto_raw" not in payload
    assert "chat_id" not in payload
    assert "telegram_update_id" not in payload


def test_build_telegram_bronze_procesado_payload_es_timestamp_iso():
    payload = _build_telegram_bronze_procesado_payload()
    # No lanza al parsear — es un ISO 8601 válido (mismo formato que
    # `_timestamp_actual`, usado también en `_build_chiste_update_payload`).
    from datetime import datetime

    datetime.fromisoformat(payload["procesado_at"])


# ---------------------------------------------------------------------------
# _normalizar_tipo_fuente_candidatos (task 25, §Reconciliación) — acepta
# str o Sequence[str], valida cada valor contra TIPOS_FUENTE_CHISTE.
# ---------------------------------------------------------------------------

def test_normalizar_tipo_fuente_candidatos_acepta_str_unico():
    assert _normalizar_tipo_fuente_candidatos("propio_historico") == ["propio_historico"]


def test_normalizar_tipo_fuente_candidatos_acepta_secuencia():
    assert _normalizar_tipo_fuente_candidatos(["propio", "propio_historico"]) == [
        "propio",
        "propio_historico",
    ]


def test_normalizar_tipo_fuente_candidatos_rechaza_valor_invalido():
    with pytest.raises(ValueError, match="tipo_fuente inválido"):
        _normalizar_tipo_fuente_candidatos("ajeno")


def test_normalizar_tipo_fuente_candidatos_rechaza_valor_invalido_dentro_de_secuencia():
    with pytest.raises(ValueError, match="tipo_fuente inválido"):
        _normalizar_tipo_fuente_candidatos(["propio", "ajeno"])


def test_normalizar_tipo_fuente_candidatos_rechaza_secuencia_vacia():
    with pytest.raises(ValueError, match="al menos un valor"):
        _normalizar_tipo_fuente_candidatos([])


# ---------------------------------------------------------------------------
# _parsear_embedding (task 25, §Reconciliación) — adapta la representación
# de pgvector (`list[float]` ya deserializada o `text` serializado, según
# el driver de PostgREST) al `list[float]` que `similitud_coseno` espera.
# ---------------------------------------------------------------------------

def test_parsear_embedding_none_se_mantiene_none():
    assert _parsear_embedding(None) is None


def test_parsear_embedding_lista_vacia_se_normaliza_a_none():
    assert _parsear_embedding([]) is None


def test_parsear_embedding_cadena_vacia_se_normaliza_a_none():
    assert _parsear_embedding("") is None


def test_parsear_embedding_ya_lista_de_floats_se_devuelve_igual():
    assert _parsear_embedding([0.1, 0.2, 0.3]) == [0.1, 0.2, 0.3]


def test_parsear_embedding_texto_serializado_pgvector_se_parsea():
    assert _parsear_embedding("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]


def test_parsear_embedding_texto_serializado_con_espacios_se_parsea():
    assert _parsear_embedding("[0.1, 0.2, 0.3]") == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# SupabaseStore.listar_candidatos_reconciliacion (task 25, §Reconciliación)
# — doble de cliente en memoria, sin red, siguiendo el patrón de
# `tests/integration/test_supabase_store_live.py` pero sin conectar de verdad.
# ---------------------------------------------------------------------------

class _FakeResultado:
    def __init__(self, data):
        self.data = data


class _FakeConsultaChistes:
    """Doble mínimo de la interfaz fluida de `supabase-py` usada por el método:
    `.table("chistes").select(...).eq(...)` o `.in_(...)`, luego `.execute()`.
    """

    def __init__(self, filas):
        self._filas = filas
        self._columnas_seleccionadas = None
        self._filtro = None

    def select(self, columnas):
        self._columnas_seleccionadas = columnas
        return self

    def eq(self, columna, valor):
        self._filtro = (columna, [valor])
        return self

    def in_(self, columna, valores):
        self._filtro = (columna, list(valores))
        return self

    def execute(self):
        assert self._columnas_seleccionadas == "id, hash_normalizado, embedding", (
            "el método debe pedir SOLO id, hash_normalizado, embedding (no select('*'))"
        )
        columna, valores = self._filtro
        filas = [f for f in self._filas if f.get(columna) in valores]
        return _FakeResultado(filas)


class _FakeClienteChistes:
    def __init__(self, filas):
        self._filas = filas

    def table(self, nombre_tabla):
        assert nombre_tabla == "chistes"
        return _FakeConsultaChistes(self._filas)


def test_listar_candidatos_reconciliacion_filtra_por_tipo_fuente_unico():
    filas = [
        {
            "id": "1",
            "hash_normalizado": "h1",
            "embedding": [0.1, 0.2],
            "tipo_fuente": "propio",
        },
        {
            "id": "2",
            "hash_normalizado": "h2",
            "embedding": [0.3, 0.4],
            "tipo_fuente": "propio_historico",
        },
    ]
    store = SupabaseStore(client=_FakeClienteChistes(filas))

    candidatos = store.listar_candidatos_reconciliacion("propio_historico")

    assert candidatos == [
        {"id": "2", "hash_normalizado": "h2", "embedding": [0.3, 0.4]}
    ]


def test_listar_candidatos_reconciliacion_acepta_secuencia_de_tipo_fuente():
    filas = [
        {"id": "1", "hash_normalizado": "h1", "embedding": None, "tipo_fuente": "propio"},
        {
            "id": "2",
            "hash_normalizado": "h2",
            "embedding": [0.3, 0.4],
            "tipo_fuente": "propio_historico",
        },
        {"id": "3", "hash_normalizado": "h3", "embedding": None, "tipo_fuente": "ajeno_no_deberia_estar"},
    ]
    store = SupabaseStore(client=_FakeClienteChistes(filas))

    candidatos = store.listar_candidatos_reconciliacion(["propio", "propio_historico"])

    assert {c["id"] for c in candidatos} == {"1", "2"}


def test_listar_candidatos_reconciliacion_devuelve_solo_las_tres_claves():
    filas = [
        {
            "id": "1",
            "hash_normalizado": "h1",
            "embedding": [0.1, 0.2],
            "tipo_fuente": "propio",
            "estado": "rematado",
            "version_actual": 3,
        }
    ]
    store = SupabaseStore(client=_FakeClienteChistes(filas))

    candidatos = store.listar_candidatos_reconciliacion("propio")

    assert candidatos == [{"id": "1", "hash_normalizado": "h1", "embedding": [0.1, 0.2]}]
    assert set(candidatos[0].keys()) == {"id", "hash_normalizado", "embedding"}


def test_listar_candidatos_reconciliacion_incluye_variantes_chiste_origen_id():
    filas = [
        {
            "id": "2",
            "hash_normalizado": "h2",
            "embedding": [0.5, 0.5],
            "tipo_fuente": "propio_historico",
            "chiste_origen_id": "1",
        }
    ]
    store = SupabaseStore(client=_FakeClienteChistes(filas))

    candidatos = store.listar_candidatos_reconciliacion("propio_historico")

    assert len(candidatos) == 1
    assert candidatos[0]["id"] == "2"


def test_listar_candidatos_reconciliacion_parsea_embedding_texto_pgvector():
    filas = [
        {
            "id": "1",
            "hash_normalizado": "h1",
            "embedding": "[0.1,0.2,0.3]",
            "tipo_fuente": "propio",
        }
    ]
    store = SupabaseStore(client=_FakeClienteChistes(filas))

    candidatos = store.listar_candidatos_reconciliacion("propio")

    assert candidatos[0]["embedding"] == [0.1, 0.2, 0.3]


def test_listar_candidatos_reconciliacion_embedding_ausente_es_none():
    filas = [
        {"id": "1", "hash_normalizado": "h1", "embedding": None, "tipo_fuente": "propio"}
    ]
    store = SupabaseStore(client=_FakeClienteChistes(filas))

    candidatos = store.listar_candidatos_reconciliacion("propio")

    assert candidatos[0]["embedding"] is None


def test_listar_candidatos_reconciliacion_rechaza_tipo_fuente_invalido():
    store = SupabaseStore(client=_FakeClienteChistes([]))

    with pytest.raises(ValueError, match="tipo_fuente inválido"):
        store.listar_candidatos_reconciliacion("ajeno")


# ---------------------------------------------------------------------------
# SupabaseStore.marcar_telegram_bronze_procesado (task 35, telegram/SPEC.md
# §Recuperación de fallos — paso 11) — doble de cliente en memoria, mismo
# patrón que `_FakeClienteChistes`/`_FakeConsultaChistes` de arriba, adaptado
# a la interfaz fluida de UPDATE (`.table(...).update(...).eq(...).execute()`).
# ---------------------------------------------------------------------------

class _FakeConsultaBronzeUpdate:
    def __init__(self, filas):
        self._filas = filas
        self._payload = None
        self._filtro_id = None

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, columna, valor):
        assert columna == "id", "el UPDATE debe filtrar por id de la fila Bronze"
        self._filtro_id = valor
        return self

    def execute(self):
        assert set(self._payload.keys()) == {"procesado_at"}, (
            "el UPDATE de chistes_telegram_bronze solo puede tocar procesado_at "
            "(Bronze sagrado, CLAUDE.md)"
        )
        fila = next(f for f in self._filas if f["id"] == self._filtro_id)
        fila.update(self._payload)
        return _FakeResultado([fila])


class _FakeClienteBronze:
    def __init__(self, filas):
        self._filas = filas

    def table(self, nombre_tabla):
        assert nombre_tabla == "chistes_telegram_bronze"
        return _FakeConsultaBronzeUpdate(self._filas)


def test_marcar_telegram_bronze_procesado_fija_timestamp():
    filas = [{"id": 1, "texto_raw": "un chiste", "procesado_at": None}]
    store = SupabaseStore(client=_FakeClienteBronze(filas))

    resultado = store.marcar_telegram_bronze_procesado(1)

    assert resultado["procesado_at"] is not None
    assert resultado["id"] == 1


def test_marcar_telegram_bronze_procesado_no_toca_texto_raw():
    """Bronze sagrado (CLAUDE.md): el único apunte que vuelve a la tabla es
    `procesado_at` — `texto_raw` (y cualquier otra columna del mensaje
    original) queda intacto."""
    filas = [{"id": 1, "texto_raw": "un chiste literal", "procesado_at": None}]
    store = SupabaseStore(client=_FakeClienteBronze(filas))

    store.marcar_telegram_bronze_procesado(1)

    assert filas[0]["texto_raw"] == "un chiste literal"


def test_marcar_telegram_bronze_procesado_filtra_por_id_correcto():
    filas = [
        {"id": 1, "texto_raw": "chiste 1", "procesado_at": None},
        {"id": 2, "texto_raw": "chiste 2", "procesado_at": None},
    ]
    store = SupabaseStore(client=_FakeClienteBronze(filas))

    store.marcar_telegram_bronze_procesado(2)

    assert filas[0]["procesado_at"] is None  # la fila 1 no se tocó
    assert filas[1]["procesado_at"] is not None


# ---------------------------------------------------------------------------
# SupabaseStore.listar_telegram_bronze_pendientes (task 47, telegram/SPEC.md
# §"Recuperación de fallos" — script de reproceso) — doble de cliente en
# memoria, mismo patrón de estilo que `_FakeConsultaChistes`
# (`listar_candidatos_reconciliacion`, task 25): select mínimo, nunca
# `select('*')`, filtrado por `procesado_at IS NULL`.
# ---------------------------------------------------------------------------

class _FakeConsultaBronzePendientes:
    """Doble mínimo de la interfaz fluida de `supabase-py` usada por el
    método: `.table("chistes_telegram_bronze").select(...).is_("procesado_at",
    "null").execute()`."""

    def __init__(self, filas):
        self._filas = filas
        self._columnas_seleccionadas = None
        self._filtro_is_null = None

    def select(self, columnas):
        self._columnas_seleccionadas = columnas
        return self

    def is_(self, columna, valor):
        assert valor == "null", "el filtro debe pedir procesado_at IS NULL"
        self._filtro_is_null = columna
        return self

    def execute(self):
        assert self._columnas_seleccionadas == "id, texto_raw", (
            "listar_telegram_bronze_pendientes debe pedir SOLO id, texto_raw "
            "(no select('*'))"
        )
        assert self._filtro_is_null == "procesado_at"
        filas = [f for f in self._filas if f.get("procesado_at") is None]
        return _FakeResultado(filas)


class _FakeClienteBronzePendientes:
    def __init__(self, filas):
        self._filas = filas

    def table(self, nombre_tabla):
        assert nombre_tabla == "chistes_telegram_bronze"
        return _FakeConsultaBronzePendientes(self._filas)


def test_listar_telegram_bronze_pendientes_filtra_por_procesado_at_null():
    filas = [
        {"id": 1, "texto_raw": "chiste 1", "procesado_at": None},
        {"id": 2, "texto_raw": "chiste 2", "procesado_at": "2024-01-01T00:00:00+00:00"},
        {"id": 3, "texto_raw": "chiste 3", "procesado_at": None},
    ]
    store = SupabaseStore(client=_FakeClienteBronzePendientes(filas))

    pendientes = store.listar_telegram_bronze_pendientes()

    assert {p["id"] for p in pendientes} == {1, 3}


def test_listar_telegram_bronze_pendientes_devuelve_solo_id_y_texto_raw():
    filas = [
        {
            "id": 1,
            "texto_raw": "chiste 1",
            "procesado_at": None,
            "chat_id": 555,
            "telegram_update_id": 999,
        }
    ]
    store = SupabaseStore(client=_FakeClienteBronzePendientes(filas))

    pendientes = store.listar_telegram_bronze_pendientes()

    assert pendientes == [{"id": 1, "texto_raw": "chiste 1"}]
    assert set(pendientes[0].keys()) == {"id", "texto_raw"}


def test_listar_telegram_bronze_pendientes_sin_pendientes_devuelve_lista_vacia():
    filas = [
        {"id": 1, "texto_raw": "chiste 1", "procesado_at": "2024-01-01T00:00:00+00:00"},
    ]
    store = SupabaseStore(client=_FakeClienteBronzePendientes(filas))

    assert store.listar_telegram_bronze_pendientes() == []


# ---------------------------------------------------------------------------
# Acceso por schema (task 54, `src/jokes/SPEC.md` §"Acceso por schema con
# supabase-py") — `_tabla()` es el único punto de resolución tabla -> builder
# PostgREST. Estos tests verifican, para CADA método de `SupabaseStore`:
#   1. en modo por defecto (`SUPABASE_SCHEMA_MODE` no fijada / "public"),
#      invoca EXACTAMENTE `client.table(nombre_logico)` — cero `.schema(...)`
#      de por medio, el criterio duro de "sin cambio de comportamiento
#      observable" de la propia task;
#   2. en modo "p25" (env var fijada, simula el cutover post-tasks 55/56),
#      invoca `client.schema(schema).table(tabla)` con el schema/tabla exacto
#      del contrato de `SPEC.md` (tabla método -> schema -> tabla).
#
# Usa un doble de cliente genérico (`_RecordingClient`) que solo REGISTRA la
# llamada a `.table`/`.schema(...).table`, sin validar payload — el payload
# ya está cubierto arriba por los tests de `_build_*_payload` y por los
# dobles de cliente más estrictos (`_FakeClienteChistes`, `_FakeClienteBronze`,
# etc.), que además siguen pasando sin tocarlos: la prueba viviente de que el
# modo por defecto no cambió el call site.
# ---------------------------------------------------------------------------

class _RecordingResultado:
    def __init__(self, data):
        self.data = data


class _RecordingQuery:
    """Doble fluido genérico: acepta cualquier método de la cadena PostgREST
    (`select`/`insert`/`update`/`upsert`/`eq`/`in_`/`is_`/`order`) sin
    validar argumentos y siempre se devuelve a sí mismo, hasta `.execute()`.
    """

    def select(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def upsert(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def is_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        # Una fila con todas las claves que cualquier método de
        # SupabaseStore pueda leer del resultado (id, hash_normalizado,
        # embedding, texto_raw) — estos tests no verifican el payload de
        # vuelta, solo qué tabla/schema se invocó.
        return _RecordingResultado(
            [{"id": "x", "hash_normalizado": "h", "embedding": None, "texto_raw": "t"}]
        )


class _RecordingSchema:
    def __init__(self, client, nombre_schema):
        self._client = client
        self._nombre_schema = nombre_schema

    def table(self, nombre_tabla):
        self._client.llamadas.append({"schema": self._nombre_schema, "tabla": nombre_tabla})
        return _RecordingQuery()


class _RecordingClient:
    """Doble de cliente que registra cada `.table(...)` /
    `.schema(...).table(...)` invocado, en orden — usado para verificar el
    contrato schema/tabla de cada método de `SupabaseStore` (task 54)."""

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
    """`_SCHEMA_TABLAS` es exactamente la tabla método -> schema -> tabla que
    fija `src/jokes/SPEC.md` §"Acceso por schema con supabase-py"."""
    assert _SCHEMA_TABLAS == {
        "chistes": ("silver", "chistes"),
        "chistes_revisiones": ("silver", "chistes_revisiones"),
        "temas": ("silver", "temas"),
        "tecnicas": ("silver", "tecnicas"),
        "fuentes": ("silver", "fuentes"),
        "candidatos_taxonomia": ("silver", "candidatos_taxonomia"),
        "chistes_telegram_bronze": ("bronze", "chistes_telegram"),
    }


def test_tabla_modo_public_no_antepone_schema(monkeypatch):
    monkeypatch.delenv("SUPABASE_SCHEMA_MODE", raising=False)
    client = _RecordingClient()

    _tabla(client, "chistes")

    assert client.llamadas == [{"schema": None, "tabla": "chistes"}]


def test_tabla_modo_p25_antepone_schema_segun_contrato(monkeypatch):
    monkeypatch.setenv("SUPABASE_SCHEMA_MODE", "p25")
    client = _RecordingClient()

    _tabla(client, "chistes_telegram_bronze")

    assert client.llamadas == [{"schema": "bronze", "tabla": "chistes_telegram"}]


# Nombre lógico de tabla (clave de `_SCHEMA_TABLAS`) esperado por método —
# mismo mapeo que la tabla de `SPEC.md`.
_TABLA_LOGICA_POR_METODO = {
    "crear_chiste": "chistes",
    "obtener_chiste": "chistes",
    "actualizar_chiste": "chistes",
    "listar_candidatos_reconciliacion": "chistes",
    "crear_revision": "chistes_revisiones",
    "listar_revisiones": "chistes_revisiones",
    "listar_temas": "temas",
    "crear_tema": "temas",
    "listar_tecnicas": "tecnicas",
    "crear_tecnica": "tecnicas",
    "listar_fuentes": "fuentes",
    "crear_fuente": "fuentes",
    "crear_candidato_taxonomia": "candidatos_taxonomia",
    "listar_candidatos_taxonomia": "candidatos_taxonomia",
    "actualizar_candidato_taxonomia": "candidatos_taxonomia",
    "guardar_mensaje_telegram_bronze": "chistes_telegram_bronze",
    "marcar_telegram_bronze_procesado": "chistes_telegram_bronze",
    "listar_telegram_bronze_pendientes": "chistes_telegram_bronze",
}

# Cómo invocar cada método con argumentos mínimos válidos (solo para
# ejercitar el call site — el payload/resultado no se verifica aquí).
_INVOCACIONES = {
    "crear_chiste": lambda store: store.crear_chiste(
        texto_normalizado="t", hash_normalizado="h", tipo_fuente="propio"
    ),
    "obtener_chiste": lambda store: store.obtener_chiste("id-1"),
    "actualizar_chiste": lambda store: store.actualizar_chiste("id-1", estado="rematado"),
    "listar_candidatos_reconciliacion": lambda store: store.listar_candidatos_reconciliacion("propio"),
    "crear_revision": lambda store: store.crear_revision(chiste_id="id-1", version=1, contenido="c"),
    "listar_revisiones": lambda store: store.listar_revisiones("id-1"),
    "listar_temas": lambda store: store.listar_temas(),
    "crear_tema": lambda store: store.crear_tema("nombre"),
    "listar_tecnicas": lambda store: store.listar_tecnicas(),
    "crear_tecnica": lambda store: store.crear_tecnica("nombre"),
    "listar_fuentes": lambda store: store.listar_fuentes(),
    "crear_fuente": lambda store: store.crear_fuente("nombre"),
    "crear_candidato_taxonomia": lambda store: store.crear_candidato_taxonomia(tipo="tema", texto="x"),
    "listar_candidatos_taxonomia": lambda store: store.listar_candidatos_taxonomia(),
    "actualizar_candidato_taxonomia": lambda store: store.actualizar_candidato_taxonomia(1, "aceptado"),
    "guardar_mensaje_telegram_bronze": lambda store: store.guardar_mensaje_telegram_bronze(
        telegram_update_id=1, texto_raw="x"
    ),
    "marcar_telegram_bronze_procesado": lambda store: store.marcar_telegram_bronze_procesado(1),
    "listar_telegram_bronze_pendientes": lambda store: store.listar_telegram_bronze_pendientes(),
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
    store = SupabaseStore(client=client)

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
    store = SupabaseStore(client=client)

    _INVOCACIONES[nombre_metodo](store)

    nombre_logico = _TABLA_LOGICA_POR_METODO[nombre_metodo]
    schema, tabla = _SCHEMA_TABLAS[nombre_logico]
    assert client.llamadas[-1] == {"schema": schema, "tabla": tabla}
