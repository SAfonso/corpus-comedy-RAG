"""Tests unitarios de `src/theory/ingest_teoria.py` — sin red (`store`/embeddings dobles).

Contrato (task 61, `src/theory/SPEC.md` §"Puntos de enganche exactos en
código (tasks 60 y 61)" §Task 61): la unidad de trabajo es una fila de
`silver.teoria_documentos` (no ya un `v{N}/` con `manifest.json`), así que
`store` (doble en memoria, `_FakeTeoriaStore`) expone
`listar_documentos_pendientes`/`descargar_documento` además de
`buscar_o_crear_fuente`/`guardar_chunk`. `generar_embedding_fn` sigue siendo
una función fake — la llamada real de embeddings se cubre en
`tests/integration/test_ingest_teoria_live.py`.

`_separar_cuerpo` se testea aquí igual que antes de la task 61 (es pura, no
cambió ni una línea) contra un `.md` con el mismo formato que produce
`format_normalizer.render_document` (mismo delimitador `\\n---\\n` de cierre
de cabecera YAML que ya usaba el `.txt` de `v{N}/`, ver `src/theory/SPEC.md`
§Task 60).
"""
import pytest

from src.theory.ingest_teoria import (
    IngestaTeoriaError,
    ResultadoIngesta,
    _separar_cuerpo,
    ingestar_documentos_pendientes,
)

FUENTE_FIXTURE = "Curso de stand-up (fixture Silver)"
TIPO_FUENTE_FIXTURE = "transcripcion_curso"
LICENCIA_FIXTURE = "personal_only"

_MD_DOS_FRAGMENTOS = (
    "---\n"
    "fuente: Curso de stand-up (fixture Silver)\n"
    "tipo_fuente: transcripcion_curso\n"
    "---\n"
    "\n"
    "Primer fragmento de teoria.\n"
    "\n"
    "Segundo fragmento, distinto del primero.\n"
)


def _fila_silver(
    *,
    id_="doc-1",
    bucket="silver-teoria",
    object_path="local_legacy/hash1/doc.md",
    hash_md5="hash1",
    fuente=FUENTE_FIXTURE,
    tipo_fuente=TIPO_FUENTE_FIXTURE,
    licencia=LICENCIA_FIXTURE,
) -> dict:
    return {
        "id": id_,
        "bucket": bucket,
        "object_path": object_path,
        "hash_md5": hash_md5,
        "fuente": fuente,
        "tipo_fuente": tipo_fuente,
        "licencia": licencia,
    }


# ---------------------------------------------------------------------------
# _separar_cuerpo — pura, sin cambios respecto a antes de la task 61.
# ---------------------------------------------------------------------------


class TestSepararCuerpo:
    def test_recupera_los_textos_de_fragmento_en_orden(self):
        fragmentos_texto = _separar_cuerpo(_MD_DOS_FRAGMENTOS)

        assert fragmentos_texto == [
            "Primer fragmento de teoria.",
            "Segundo fragmento, distinto del primero.",
        ]

    def test_documento_sin_delimitador_de_cierre_lanza_error(self):
        with pytest.raises(IngestaTeoriaError):
            _separar_cuerpo("esto no tiene cabecera YAML en absoluto")


# ---------------------------------------------------------------------------
# ingestar_documentos_pendientes — orquestación con store/embeddings dobles.
# ---------------------------------------------------------------------------


class _FakeTeoriaStore:
    """Doble en memoria de `TeoriaStore` (task 61): además de
    `buscar_o_crear_fuente`/`guardar_chunk`, expone
    `listar_documentos_pendientes`/`descargar_documento` para no depender de
    disco ni de Supabase real."""

    def __init__(self, filas_silver: list[dict], contenidos: dict[str, bytes]):
        self._filas_silver = filas_silver
        self._contenidos = contenidos  # object_path -> bytes del .md
        self.chunks_guardados: dict[tuple, dict] = {}
        self.fuentes: dict[str, int] = {}
        self._siguiente_fuente_id = 1

    def listar_documentos_pendientes(self) -> list[dict]:
        doc_ids_con_chunks = {clave[0] for clave in self.chunks_guardados}
        return [
            fila for fila in self._filas_silver if str(fila["id"]) not in doc_ids_con_chunks
        ]

    def descargar_documento(self, bucket: str, object_path: str) -> bytes:
        return self._contenidos[object_path]

    def buscar_o_crear_fuente(self, nombre, *, tipo_fuente=None, licencia=None):
        if nombre not in self.fuentes:
            self.fuentes[nombre] = self._siguiente_fuente_id
            self._siguiente_fuente_id += 1
        return self.fuentes[nombre]

    def guardar_chunk(
        self, *, doc_id, version_corpus, chunk_index, contenido, embedding, tipo_fuente, fuente_id, licencia
    ):
        clave = (doc_id, version_corpus, chunk_index)
        if clave in self.chunks_guardados:
            return None
        fila = {
            "doc_id": doc_id,
            "version_corpus": version_corpus,
            "chunk_index": chunk_index,
            "contenido": contenido,
            "embedding": embedding,
            "tipo_fuente": tipo_fuente,
            "fuente_id": fuente_id,
            "licencia": licencia,
        }
        self.chunks_guardados[clave] = fila
        return fila


def _embedding_fake(texto: str) -> list:
    return [float(len(texto)), 0.0, 0.0]


def _store_con_un_documento() -> _FakeTeoriaStore:
    fila = _fila_silver()
    return _FakeTeoriaStore(
        filas_silver=[fila],
        contenidos={fila["object_path"]: _MD_DOS_FRAGMENTOS.encode("utf-8")},
    )


class TestIngestarDocumentosPendientes:
    def test_ingesta_todos_los_fragmentos_del_documento(self):
        store = _store_con_un_documento()

        resultado = ingestar_documentos_pendientes(store, generar_embedding_fn=_embedding_fake)

        assert isinstance(resultado, ResultadoIngesta)
        assert resultado.num_documentos == 1
        assert resultado.num_duplicados == 0
        assert resultado.num_nuevos == len(store.chunks_guardados)
        assert resultado.num_nuevos == 2

    def test_doc_id_y_version_corpus_reexpresan_id_y_hash_md5_de_la_fila_silver(self):
        store = _store_con_un_documento()

        ingestar_documentos_pendientes(store, generar_embedding_fn=_embedding_fake)

        claves = set(store.chunks_guardados)
        assert claves == {("doc-1", "hash1", 0), ("doc-1", "hash1", 1)}
        for chunk in store.chunks_guardados.values():
            assert chunk["doc_id"] == "doc-1"
            assert chunk["version_corpus"] == "hash1"

    def test_reingestar_una_fila_ya_ingestada_es_idempotente_y_no_gasta_embedding(self):
        store = _store_con_un_documento()
        llamadas = []

        def espia(texto):
            llamadas.append(texto)
            return _embedding_fake(texto)

        ingestar_documentos_pendientes(store, generar_embedding_fn=espia)
        assert len(llamadas) == 2  # una llamada por fragmento, la primera vez

        # La selección por defecto ("pendientes") ya no encuentra esta fila:
        # tiene chunks en Gold, así que la segunda llamada no reingesta nada
        # ni gasta un solo embedding más.
        resultado_repetido = ingestar_documentos_pendientes(store, generar_embedding_fn=espia)

        assert resultado_repetido.chunks == []
        assert resultado_repetido.num_nuevos == 0
        assert resultado_repetido.num_duplicados == 0
        assert len(llamadas) == 2  # sin llamadas nuevas

    def test_seleccion_por_defecto_excluye_filas_ya_ingestadas_pero_incluye_las_nuevas(self):
        fila_vieja = _fila_silver(id_="doc-1", object_path="local_legacy/hash1/a.md", hash_md5="hash1")
        fila_nueva = _fila_silver(id_="doc-2", object_path="local_legacy/hash2/b.md", hash_md5="hash2")
        store = _FakeTeoriaStore(
            filas_silver=[fila_vieja, fila_nueva],
            contenidos={
                fila_vieja["object_path"]: _MD_DOS_FRAGMENTOS.encode("utf-8"),
                fila_nueva["object_path"]: _MD_DOS_FRAGMENTOS.encode("utf-8"),
            },
        )
        # doc-1 ya tiene chunks en Gold (simulado a mano, sin pasar por la
        # función): listar_documentos_pendientes debe seguir excluyéndolo.
        store.chunks_guardados[("doc-1", "hash1", 0)] = {"doc_id": "doc-1"}

        resultado = ingestar_documentos_pendientes(store, generar_embedding_fn=_embedding_fake)

        assert {c.doc_id for c in resultado.chunks} == {"doc-2"}

    def test_resuelve_fuente_una_sola_vez_por_documento(self):
        store = _store_con_un_documento()

        ingestar_documentos_pendientes(store, generar_embedding_fn=_embedding_fake)

        assert store.fuentes == {FUENTE_FIXTURE: 1}
        assert all(fila["fuente_id"] == 1 for fila in store.chunks_guardados.values())

    def test_chunks_llevan_tipo_fuente_y_licencia_de_la_fila_silver(self):
        store = _store_con_un_documento()

        ingestar_documentos_pendientes(store, generar_embedding_fn=_embedding_fake)

        for fila in store.chunks_guardados.values():
            assert fila["tipo_fuente"] == TIPO_FUENTE_FIXTURE
            assert fila["licencia"] == LICENCIA_FIXTURE

    def test_documentos_explicitos_ignora_listar_documentos_pendientes(self):
        fila_a = _fila_silver(id_="doc-a", object_path="local_legacy/hasha/a.md", hash_md5="hasha")
        fila_b = _fila_silver(id_="doc-b", object_path="local_legacy/hashb/b.md", hash_md5="hashb")
        store = _FakeTeoriaStore(
            filas_silver=[fila_a, fila_b],
            contenidos={
                fila_a["object_path"]: _MD_DOS_FRAGMENTOS.encode("utf-8"),
                fila_b["object_path"]: _MD_DOS_FRAGMENTOS.encode("utf-8"),
            },
        )

        resultado = ingestar_documentos_pendientes(
            store, documentos=[fila_a], generar_embedding_fn=_embedding_fake
        )

        assert {c.doc_id for c in resultado.chunks} == {"doc-a"}

    def test_no_llama_dos_veces_al_generador_de_embeddings_por_el_mismo_fragmento(self):
        store = _store_con_un_documento()
        llamadas = []

        def espia(texto):
            llamadas.append(texto)
            return _embedding_fake(texto)

        resultado = ingestar_documentos_pendientes(store, generar_embedding_fn=espia)

        assert len(llamadas) == len(resultado.chunks)
