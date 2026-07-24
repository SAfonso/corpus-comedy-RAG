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
    TIPOS_CANDIDATO_TAXONOMIA,
    TIPOS_FUENTE_CHISTE,
    SupabaseStore,
    _build_candidato_payload,
    _build_candidato_update_payload,
    _build_chiste_payload,
    _build_chiste_update_payload,
    _build_mensaje_telegram_bronze_payload,
    _build_revision_payload,
    _normalizar_tipo_fuente_candidatos,
    _parsear_embedding,
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
