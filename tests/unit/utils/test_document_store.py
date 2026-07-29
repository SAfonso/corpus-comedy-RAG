"""Tests para DocumentStore — captura durable de documentos (P25, task 58).

Contrato: `src/utils/SPEC.md` §"DocumentStore — captura durable de documentos
(P25, 2026-07-28)". Cero red: la resolución de `Destino`, la construcción del
`object_path`, el saneado del nombre, la derivación de `origen`, el cálculo
del `hash_md5` y la validación de argumentos son funciones puras de módulo,
testeadas directamente sin cliente; `DocumentStore.capturar()` se testea con
un doble de cliente inyectado que registra qué bucket/clave/schema/tabla/
payload recibió, con la interfaz de `supabase-py`
(`.schema().table().insert/select/eq/is_/execute` y
`.storage.from_().upload()`).

Ficheros de entrada: fixtures reales de `tests/fixtures/` (nunca inventadas):
`comedy_bible_excerpt.docx`, `sample_transcript.txt`, `sample_transcript.pdf`.
"""
from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from src.utils.document_store import (
    CAPAS,
    DESTINOS,
    FLUJOS,
    ORIGENES,
    DocumentStore,
    DocumentStoreError,
    Destino,
    ResultadoCaptura,
    _compactar_modified_time,
    _construir_object_path,
    _derivar_origen,
    _hash_md5,
    _sanear_nombre,
    _validar_argumentos,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
DOCX_FIXTURE = FIXTURES_DIR / "comedy_bible_excerpt.docx"
TXT_FIXTURE = FIXTURES_DIR / "sample_transcript.txt"
PDF_FIXTURE = FIXTURES_DIR / "sample_transcript.pdf"


# ---------------------------------------------------------------------------
# Funciones puras — sin cliente, sin red.
# ---------------------------------------------------------------------------


def test_destinos_tiene_los_cuatro_fijos_de_p25():
    assert set(DESTINOS.keys()) == {
        ("bronze", "teoria"),
        ("silver", "teoria"),
        ("bronze", "historico"),
        ("silver", "historico"),
    }
    assert DESTINOS[("bronze", "teoria")] == Destino(
        bucket="bronze-teoria", schema="bronze", tabla="teoria_documentos"
    )
    assert DESTINOS[("silver", "teoria")] == Destino(
        bucket="silver-teoria", schema="silver", tabla="teoria_documentos"
    )
    assert DESTINOS[("bronze", "historico")] == Destino(
        bucket="bronze-historico", schema="bronze", tabla="historico_documentos"
    )
    assert DESTINOS[("silver", "historico")] == Destino(
        bucket="silver-historico", schema="silver", tabla="historico_documentos"
    )


def test_capas_flujos_origenes_constantes():
    assert CAPAS == ("bronze", "silver")
    assert FLUJOS == ("teoria", "historico")
    assert ORIGENES == ("drive", "local_legacy")


@pytest.mark.parametrize(
    "nombre,esperado",
    [
        ("Curso_de_Demy.docx", "Curso_de_Demy.docx"),
        ("Bases de la comedia.pdf", "Bases_de_la_comedia.pdf"),
        ("ñoño (final)!!.txt", "_o_o__final___.txt"),
        ("a-b.c_d", "a-b.c_d"),
    ],
)
def test_sanear_nombre(nombre, esperado):
    assert _sanear_nombre(nombre) == esperado


def test_compactar_modified_time():
    assert _compactar_modified_time("2026-07-28T10:15:00.000Z") == "20260728T101500.000Z"


def test_derivar_origen_drive():
    assert _derivar_origen("1AbCxyz") == "drive"


def test_derivar_origen_legacy():
    assert _derivar_origen(None) == "local_legacy"


def test_hash_md5_coincide_con_hashlib_del_fixture_real():
    contenido = DOCX_FIXTURE.read_bytes()
    assert _hash_md5(contenido) == hashlib.md5(contenido).hexdigest()


def test_construir_object_path_modo_drive():
    path = _construir_object_path(
        "drive", "1AbCxyz", "2026-07-28T10:15:00.000Z", "deadbeef", "Curso_de_Demy.docx"
    )
    assert path == "drive/1AbCxyz/20260728T101500.000Z/Curso_de_Demy.docx"


def test_construir_object_path_modo_legacy():
    path = _construir_object_path("local_legacy", None, None, "deadbeef", "Bases.pdf")
    assert path == "local_legacy/deadbeef/Bases.pdf"


def test_validar_argumentos_destino_desconocido():
    with pytest.raises(DocumentStoreError):
        _validar_argumentos(DOCX_FIXTURE, "oro", "teoria", None, None, None)


def test_validar_argumentos_flujo_desconocido():
    with pytest.raises(DocumentStoreError):
        _validar_argumentos(DOCX_FIXTURE, "bronze", "telegram", None, None, None)


def test_validar_argumentos_ruta_no_existe():
    with pytest.raises(DocumentStoreError):
        _validar_argumentos(
            FIXTURES_DIR / "no_existe.docx", "bronze", "teoria", None, None, None
        )


def test_validar_argumentos_ruta_es_directorio():
    with pytest.raises(DocumentStoreError):
        _validar_argumentos(FIXTURES_DIR, "bronze", "teoria", None, None, None)


def test_validar_argumentos_drive_file_id_sin_modified_time():
    with pytest.raises(DocumentStoreError):
        _validar_argumentos(DOCX_FIXTURE, "bronze", "teoria", "1AbCxyz", None, None)


def test_validar_argumentos_modified_time_sin_drive_file_id():
    with pytest.raises(DocumentStoreError):
        _validar_argumentos(
            DOCX_FIXTURE, "bronze", "teoria", None, "2026-07-28T10:15:00.000Z", None
        )


@pytest.mark.parametrize(
    "columna",
    [
        "bucket",
        "object_path",
        "drive_file_id",
        "modified_time",
        "hash_md5",
        "origen",
        "nombre",
        "mime_type",
        "tamano_bytes",
    ],
)
def test_validar_argumentos_extra_pisa_columna_reservada(columna):
    with pytest.raises(DocumentStoreError):
        _validar_argumentos(
            DOCX_FIXTURE, "bronze", "teoria", None, None, {columna: "lo que sea"}
        )


def test_validar_argumentos_extra_con_columnas_propias_ok():
    destino = _validar_argumentos(
        DOCX_FIXTURE, "bronze", "teoria", None, None, {"tipo_fuente": "teoria"}
    )
    assert destino == DESTINOS[("bronze", "teoria")]


# ---------------------------------------------------------------------------
# Doble de cliente `supabase-py` — sin red. Simula un almacén en memoria por
# (schema, tabla), con los mismos dos índices únicos parciales que P25 (task
# 53): (drive_file_id, modified_time) WHERE drive_file_id IS NOT NULL, y
# (hash_md5) WHERE drive_file_id IS NULL.
# ---------------------------------------------------------------------------


class _FakeResultado:
    def __init__(self, data):
        self.data = data


class _FakeStorageBucket:
    def __init__(self, storage, bucket):
        self._storage = storage
        self._bucket = bucket

    def upload(self, path, file, file_options=None):
        self._storage.subidas.append(
            {
                "bucket": self._bucket,
                "path": path,
                "file": file,
                "file_options": file_options,
            }
        )
        return {"path": path}


class _FakeStorage:
    def __init__(self):
        self.subidas = []

    def from_(self, bucket):
        return _FakeStorageBucket(self, bucket)


class _FakeTabla:
    """Doble de `client.schema(s).table(t)`: registra select/insert y aplica
    los índices únicos parciales de P25 al insertar, tal y como lo haría
    Postgres — es lo que permite testear la carrera del paso FISCAL/§Carrera
    sin mockear la excepción a mano en cada test."""

    def __init__(self, filas, contador_id):
        self._filas = filas
        self._contador_id = contador_id
        self._select_cols = None
        self._filtros_eq = {}
        self._filtros_is_null = set()
        self._payload_insert = None

    def select(self, columnas):
        self._select_cols = columnas
        self._payload_insert = None
        return self

    def eq(self, columna, valor):
        self._filtros_eq[columna] = valor
        return self

    def is_(self, columna, valor):
        assert valor == "null"
        self._filtros_is_null.add(columna)
        return self

    def insert(self, payload):
        self._payload_insert = payload
        return self

    def execute(self):
        if self._payload_insert is not None:
            return self._ejecutar_insert()
        return self._ejecutar_select()

    def _conflicto_indice_unico(self, payload):
        if payload.get("drive_file_id") is not None:
            return any(
                f.get("drive_file_id") == payload["drive_file_id"]
                and f.get("modified_time") == payload["modified_time"]
                for f in self._filas
            )
        return any(
            f.get("drive_file_id") is None and f.get("hash_md5") == payload["hash_md5"]
            for f in self._filas
        )

    def _ejecutar_insert(self):
        payload = self._payload_insert
        if self._conflicto_indice_unico(payload):
            raise APIError(
                {
                    "code": "23505",
                    "message": "duplicate key value violates unique constraint",
                }
            )
        nueva = dict(payload)
        nueva["id"] = f"id-{next(self._contador_id)}"
        self._filas.append(nueva)
        return _FakeResultado([nueva])

    def _ejecutar_select(self):
        filas = self._filas
        for columna, valor in self._filtros_eq.items():
            filas = [f for f in filas if f.get(columna) == valor]
        for columna in self._filtros_is_null:
            filas = [f for f in filas if f.get(columna) is None]
        return _FakeResultado(list(filas))


class _FakeSchema:
    def __init__(self, cliente, schema):
        self._cliente = cliente
        self._schema = schema

    def table(self, tabla):
        clave = (self._schema, tabla)
        filas = self._cliente._db.setdefault(clave, [])
        return _FakeTabla(filas, self._cliente._contador_id)


class _FakeCliente:
    def __init__(self):
        self._db = {}
        self._contador_id = itertools.count(1)
        self.storage = _FakeStorage()

    def schema(self, schema):
        return _FakeSchema(self, schema)

    def filas(self, schema, tabla):
        return self._db.get((schema, tabla), [])


# ---------------------------------------------------------------------------
# DocumentStore.capturar() — modo Drive.
# ---------------------------------------------------------------------------


def test_capturar_modo_drive_sube_e_inserta():
    cliente = _FakeCliente()
    store = DocumentStore(client=cliente)

    resultado = store.capturar(
        DOCX_FIXTURE,
        capa="bronze",
        flujo="teoria",
        drive_file_id="1AbCxyz",
        modified_time="2026-07-28T10:15:00.000Z",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        extra={"tipo_fuente": "teoria"},
    )

    assert isinstance(resultado, ResultadoCaptura)
    assert resultado.destino == DESTINOS[("bronze", "teoria")]
    assert resultado.origen == "drive"
    assert resultado.ya_existia is False
    assert resultado.fila_id == "id-1"
    contenido = DOCX_FIXTURE.read_bytes()
    assert resultado.hash_md5 == hashlib.md5(contenido).hexdigest()
    assert resultado.object_path == (
        f"drive/1AbCxyz/20260728T101500.000Z/{DOCX_FIXTURE.name}"
    )

    # Subida real al bucket correcto, con la clave correcta.
    assert len(cliente.storage.subidas) == 1
    subida = cliente.storage.subidas[0]
    assert subida["bucket"] == "bronze-teoria"
    assert subida["path"] == resultado.object_path
    assert subida["file"] == contenido
    assert subida["file_options"] == {
        "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }

    # Fila insertada con las columnas propias + extra tal cual.
    filas = cliente.filas("bronze", "teoria_documentos")
    assert len(filas) == 1
    fila = filas[0]
    assert fila["bucket"] == "bronze-teoria"
    assert fila["object_path"] == resultado.object_path
    assert fila["drive_file_id"] == "1AbCxyz"
    assert fila["modified_time"] == "2026-07-28T10:15:00.000Z"
    assert fila["hash_md5"] == resultado.hash_md5
    assert fila["origen"] == "drive"
    assert fila["nombre"] == DOCX_FIXTURE.name
    assert fila["tamano_bytes"] == len(contenido)
    assert fila["tipo_fuente"] == "teoria"


def test_capturar_modo_drive_nombre_explicito_se_usa_en_la_clave():
    cliente = _FakeCliente()
    store = DocumentStore(client=cliente)

    resultado = store.capturar(
        DOCX_FIXTURE,
        capa="bronze",
        flujo="historico",
        drive_file_id="1AbCxyz",
        modified_time="2026-07-28T10:15:00.000Z",
        nombre="Curso de Demy.docx",
    )

    assert resultado.object_path == "drive/1AbCxyz/20260728T101500.000Z/Curso_de_Demy.docx"
    fila = cliente.filas("bronze", "historico_documentos")[0]
    assert fila["nombre"] == "Curso de Demy.docx"


# ---------------------------------------------------------------------------
# DocumentStore.capturar() — modo legacy.
# ---------------------------------------------------------------------------


def test_capturar_modo_legacy_sube_e_inserta():
    cliente = _FakeCliente()
    store = DocumentStore(client=cliente)

    resultado = store.capturar(
        TXT_FIXTURE,
        capa="bronze",
        flujo="teoria",
        extra={"tipo_fuente": "transcripcion_curso", "ruta_relativa": "Demy/sample_transcript.txt"},
    )

    assert resultado.origen == "local_legacy"
    assert resultado.ya_existia is False
    contenido = TXT_FIXTURE.read_bytes()
    hash_esperado = hashlib.md5(contenido).hexdigest()
    assert resultado.hash_md5 == hash_esperado
    assert resultado.object_path == f"local_legacy/{hash_esperado}/{TXT_FIXTURE.name}"

    fila = cliente.filas("bronze", "teoria_documentos")[0]
    assert fila["drive_file_id"] is None
    assert fila["modified_time"] is None
    assert fila["origen"] == "local_legacy"
    assert fila["ruta_relativa"] == "Demy/sample_transcript.txt"


# ---------------------------------------------------------------------------
# Idempotencia: segunda captura idéntica, y modified_time distinto.
# ---------------------------------------------------------------------------


def test_segunda_captura_identica_no_sube_ni_inserta():
    cliente = _FakeCliente()
    store = DocumentStore(client=cliente)

    primera = store.capturar(
        DOCX_FIXTURE,
        capa="bronze",
        flujo="teoria",
        drive_file_id="1AbCxyz",
        modified_time="2026-07-28T10:15:00.000Z",
    )
    assert primera.ya_existia is False
    assert len(cliente.storage.subidas) == 1

    segunda = store.capturar(
        DOCX_FIXTURE,
        capa="bronze",
        flujo="teoria",
        drive_file_id="1AbCxyz",
        modified_time="2026-07-28T10:15:00.000Z",
    )

    assert segunda.ya_existia is True
    assert segunda.fila_id == primera.fila_id
    assert segunda.object_path == primera.object_path
    # Sin subida nueva: el doble de storage sigue con una sola entrada.
    assert len(cliente.storage.subidas) == 1
    # Sin fila nueva.
    assert len(cliente.filas("bronze", "teoria_documentos")) == 1


def test_segunda_captura_legacy_identica_no_sube_ni_inserta():
    cliente = _FakeCliente()
    store = DocumentStore(client=cliente)

    primera = store.capturar(TXT_FIXTURE, capa="bronze", flujo="teoria")
    segunda = store.capturar(TXT_FIXTURE, capa="bronze", flujo="teoria")

    assert primera.ya_existia is False
    assert segunda.ya_existia is True
    assert segunda.fila_id == primera.fila_id
    assert len(cliente.storage.subidas) == 1
    assert len(cliente.filas("bronze", "teoria_documentos")) == 1


def test_modified_time_distinto_crea_fila_y_objeto_nuevo_sin_tocar_el_anterior():
    cliente = _FakeCliente()
    store = DocumentStore(client=cliente)

    primera = store.capturar(
        DOCX_FIXTURE,
        capa="bronze",
        flujo="teoria",
        drive_file_id="1AbCxyz",
        modified_time="2026-07-28T10:15:00.000Z",
    )
    segunda = store.capturar(
        DOCX_FIXTURE,
        capa="bronze",
        flujo="teoria",
        drive_file_id="1AbCxyz",
        modified_time="2026-07-29T09:00:00.000Z",
    )

    assert primera.ya_existia is False
    assert segunda.ya_existia is False
    assert segunda.fila_id != primera.fila_id
    assert segunda.object_path != primera.object_path

    # Las dos filas conviven y la primera sigue intacta.
    filas = cliente.filas("bronze", "teoria_documentos")
    assert len(filas) == 2
    assert {f["modified_time"] for f in filas} == {
        "2026-07-28T10:15:00.000Z",
        "2026-07-29T09:00:00.000Z",
    }
    assert filas[0]["hash_md5"] == filas[1]["hash_md5"] == primera.hash_md5

    # Los dos objetos conviven; el primero no se sobrescribió.
    assert len(cliente.storage.subidas) == 2
    claves = {s["path"] for s in cliente.storage.subidas}
    assert claves == {primera.object_path, segunda.object_path}


# ---------------------------------------------------------------------------
# Carrera: violación de unicidad en el INSERT tratada como ya_existia=True.
# ---------------------------------------------------------------------------


class _FakeTablaCarrera:
    """Simula la carrera de `SPEC.md` §Carrera: el SELECT del paso 3 (primera
    llamada) no ve la fila que "otro proceso" insertó justo antes -- pero el
    INSERT sí choca con el índice único real de Postgres. El SELECT de
    reintento (segunda llamada, tras capturar la excepción) sí la ve."""

    def __init__(self, fila_existente):
        self._fila_existente = fila_existente
        self._llamadas_select = 0
        self._insertando = False

    def select(self, columnas):
        self._insertando = False
        return self

    def eq(self, columna, valor):
        return self

    def is_(self, columna, valor):
        return self

    def insert(self, payload):
        self._insertando = True
        return self

    def execute(self):
        if self._insertando:
            raise APIError(
                {
                    "code": "23505",
                    "message": "duplicate key value violates unique constraint",
                }
            )
        self._llamadas_select += 1
        if self._llamadas_select == 1:
            return _FakeResultado([])
        return _FakeResultado([self._fila_existente])


class _FakeSchemaCarrera:
    def __init__(self, tabla):
        self._tabla = tabla

    def table(self, nombre_tabla):
        return self._tabla


class _FakeClienteCarrera:
    def __init__(self, fila_existente):
        self._tabla = _FakeTablaCarrera(fila_existente)
        self.storage = _FakeStorage()

    def schema(self, schema):
        return _FakeSchemaCarrera(self._tabla)


def test_violacion_de_unicidad_en_insert_se_trata_como_ya_existia():
    fila_existente = {"id": "id-ganador", "hash_md5": "no-importa"}
    cliente = _FakeClienteCarrera(fila_existente)
    store = DocumentStore(client=cliente)

    resultado = store.capturar(
        DOCX_FIXTURE,
        capa="bronze",
        flujo="teoria",
        drive_file_id="1AbCxyz",
        modified_time="2026-07-28T10:15:00.000Z",
    )

    assert resultado.ya_existia is True
    assert resultado.fila_id == "id-ganador"
    # El objeto sí se subió (huérfano tolerado, `SPEC.md` §Orden objeto-antes-que-fila):
    # el perdedor de la carrera ya había subido sus bytes antes de que el INSERT chocara.
    assert len(cliente.storage.subidas) == 1


def test_violacion_de_unicidad_legacy_se_trata_como_ya_existia():
    fila_existente = {"id": "id-ganador-legacy"}
    cliente = _FakeClienteCarrera(fila_existente)
    store = DocumentStore(client=cliente)

    resultado = store.capturar(TXT_FIXTURE, capa="bronze", flujo="teoria")

    assert resultado.ya_existia is True
    assert resultado.fila_id == "id-ganador-legacy"
