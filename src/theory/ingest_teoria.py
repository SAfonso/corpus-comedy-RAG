"""ingest_teoria — vuelca `silver.teoria_documentos` a `gold.teoria_chunks` en Supabase.

Contrato (`src/theory/SPEC.md` §Storage, §"Puntos de enganche exactos en
código (tasks 60 y 61)" §Task 61): hasta la task 60, la unidad de trabajo era
un `v{N}/` completo con su `manifest.json`; desde P25, la fuente de verdad
Silver es la propia tabla `silver.teoria_documentos` (task 51/60), así que
este módulo deja de leer disco (`manifest.json`/`v{N}/documents/*.txt`) y
pasa a leer FILAS Silver vía `store.listar_documentos_pendientes()` /
`store.descargar_documento(...)`. `_descubrir_ultima_version`/`_leer_manifest`
desaparecen con `manifest.json` — no tienen reemplazo, no hay "última
versión" que descubrir: la selección por defecto es "lo que aún no tiene
chunks en Gold" (ver `ingestar_documentos_pendientes`).

`_separar_cuerpo` **no cambia ni una línea**: el `.md` que sube la task 60 a
Silver es bit a bit el mismo formato que el `.txt` de `v{N}/` (mismo YAML
frontmatter, mismos fragmentos separados por línea en blanco — solo cambia
la extensión y el destino, ver `src/theory/SPEC.md` §Task 60), así que la
misma estrategia de split por el delimitador de cierre de cabecera sigue
siendo correcta. Su limitación documentada (un fragmento no puede contener
una línea en blanco interna) sigue vigente tal cual.

**Excepción documentada al "Sin LLM" de Flujo A** (`src/theory/SPEC.md`
§Stack: "Sin LLM... Coste cero"; `docs/specs/llm-policy.md`): este módulo SÍ
llama a una API de embeddings (`generar_embedding`, `src/utils/llm/embeddings.py`,
ya implementado en la task 15 para Reconciliación). No es una excepción
nueva: la política ya la nombra explícitamente ("Los embeddings de
reconciliación/retrieval, ídem" — la excepción cubre RETRIEVAL en general,
no solo chistes) y el propio `SPEC.md` de teoría dice que `pgvector` es "el
índice único de consulta del RAG, COMPARTIDO con los chistes" — sin
embeddings de teoría, ese índice compartido no podría servir teoría en el
retrieval. No hay generación de contenido ni coste de LLM de texto: el `.md`
de Silver no se toca ni se reescribe, solo se vectoriza para indexar.

**Sin re-parseo de YAML**: `gold.teoria_chunks` (`schema.sql`) NO tiene
columnas de `subtipo`/`idioma` por fragmento — solo `tipo_fuente`/`licencia`
a nivel de documento, que ahora vienen de las columnas homónimas de la fila
Silver (antes, de `manifest.json`), sin necesidad de abrir el `.md`.
`_separar_cuerpo` solo separa la cabecera del cuerpo para extraer los
fragmentos, no sus metadatos individuales.

**Qué sustituye a `version_corpus` (decidido en la spec, no reabierto aquí)**:
la clave de idempotencia de reingesta sigue siendo `unique(doc_id,
version_corpus, chunk_index)` en `gold.teoria_chunks` — SIN cambio de DDL ni
del `on_conflict` de `TeoriaStore.guardar_chunk` — reexpresada así:

- `doc_id` = `str(silver.teoria_documentos.id)` (antes: `doc["path"]` del
  manifest). Se prefiere al `object_path` porque ese formato lo decide
  `document_store` (otro módulo): amarrar la identidad de Gold a esa
  convención convertiría un renombrado en `utils/` en una migración de datos
  en Gold.
- `version_corpus` = `silver.teoria_documentos.hash_md5` (antes: `f"v{N}"`).
  Dos ingestas del mismo documento con distinto código de limpieza dan
  `hash_md5` distinto, exactamente como daban `v3`/`v4` — y, a diferencia de
  `v{N}`, no depende de que nadie lo incremente a mano.
- `chunk_index` no cambia.

El triple sigue siendo único por construcción, así que una reingesta de la
misma fila Silver no duplica ni una fila ni paga un embedding.

**`ResultadoIngesta`/`ChunkIngestado` cambian de forma** (decisión de diseño
de esta task, documentada aquí y en el ledger): el modelo antiguo procesaba
una sola `v{N}` con un `version_corpus` GLOBAL compartido por todos los
chunks de la llamada. En el modelo nuevo cada fila Silver tiene su PROPIO
`version_corpus` (`hash_md5` distinto por documento), así que un
`ResultadoIngesta.version_corpus: str` único ya no puede representar
correctamente el resultado de ingestar N filas Silver con N hashes
distintos. Se mueve `version_corpus` a `ChunkIngestado` (ahí sí es correcto:
cada chunk pertenece a una fila Silver concreta) y `ResultadoIngesta` cambia
su campo agregado por `num_documentos` — cuántas filas Silver DISTINTAS se
procesaron en la llamada (`len({c.doc_id for c in chunks})`), que sí
generaliza sin perder información: es el equivalente agregable de "cuántos
`v{N}` procesé" cuando ya no hay un único run por versión. `num_nuevos`/
`num_duplicados` no cambian de significado, siguen siendo agregados sobre
`chunks`.

**Selección "todas las pendientes" (opción (a) de la spec, elegida aquí)**:
`store.listar_documentos_pendientes()` no acota a "las filas escritas en
ESTE run" — usa el criterio general "Silver sin chunks en Gold". Es la
opción de menor blast radius: no exige tocar `pipeline.py`/`ResultadoPipeline`
(que hoy no exponen los `fila_id` de Silver escritos en un run — el
`ResultadoCaptura` de cada `document_store.capturar(capa="silver", ...)` se
usa solo para leer `hash_md5` en memoria durante la task 60 y se descarta),
es "resumible y se autocura" tal cual pide la spec, y en el caso común
(nadie corre `--ingest` en paralelo con otra fuente de Silver) el conjunto
"pendiente" coincide con "lo escrito en este run" de todas formas. Queda un
parámetro `documentos` opcional para que un caller que sí tenga una lista
explícita de filas la pase sin pasar por `listar_documentos_pendientes()` —
cubre la opción (b) sin exigirla; ver `scripts/run_pipeline.py`, que no la
usa (opta por (a) sin más).

Diseño para TDD sin red frágil: `_separar_cuerpo` es pura, testeable con
strings. `ingestar_documentos_pendientes` orquesta contra un `store`
inyectado (`TeoriaStore` real o doble de test, con
`listar_documentos_pendientes`/`descargar_documento`/`buscar_o_crear_fuente`/
`guardar_chunk`) y un `generar_embedding_fn` inyectado (real o fake), sin
loop de reintento — una sola llamada de embeddings por chunk, igual que
`reconciliar_chiste` (task 15).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from src.utils.llm.embeddings import generar_embedding


class IngestaTeoriaError(ValueError):
    """Error de ingesta de teoría (fila Silver/documento mal formado)."""


@dataclass(frozen=True)
class ChunkIngestado:
    """Resultado de ingestar un fragmento: el chunk guardado (o `None` si ya existía).

    `version_corpus` viaja por chunk (no en `ResultadoIngesta`, ver docstring
    del módulo): es el `hash_md5` de la fila Silver dueña de este fragmento.
    """

    doc_id: str
    version_corpus: str
    chunk_index: int
    es_duplicado: bool
    fila: Optional[dict] = None


@dataclass(frozen=True)
class ResultadoIngesta:
    """Resultado agregado de `ingestar_documentos_pendientes` para toda la llamada.

    `num_documentos` sustituye al antiguo `version_corpus: str` (ver
    docstring del módulo, §"`ResultadoIngesta`/`ChunkIngestado` cambian de
    forma"): cuántas filas Silver DISTINTAS se procesaron, no importa cuántos
    chunks tuviera cada una.
    """

    chunks: list[ChunkIngestado]

    @property
    def num_documentos(self) -> int:
        return len({c.doc_id for c in self.chunks})

    @property
    def num_nuevos(self) -> int:
        return sum(1 for c in self.chunks if not c.es_duplicado)

    @property
    def num_duplicados(self) -> int:
        return sum(1 for c in self.chunks if c.es_duplicado)


# ---------------------------------------------------------------------------
# Separación cabecera/cuerpo de un `.md` de Silver — pura, sin cambios (ver
# docstring del módulo).
# ---------------------------------------------------------------------------

def _separar_cuerpo(texto_documento: str) -> list[str]:
    """Extrae los textos de fragmento del cuerpo de un `.md` de teoría.

    Ver docstring del módulo, §"Sin re-parseo de YAML", para el porqué de
    esta estrategia (split por el delimitador de cierre de cabecera, sin
    parsear el YAML en sí) y su limitación documentada.
    """
    partes = texto_documento.split("\n---\n", 1)
    if len(partes) != 2:
        raise IngestaTeoriaError(
            "El documento no tiene el formato esperado (cabecera YAML delimitada "
            "por '---'): no se encontró el delimitador de cierre"
        )
    cuerpo = partes[1].strip("\n")
    if not cuerpo:
        return []
    return cuerpo.split("\n\n")


# ---------------------------------------------------------------------------
# Orquestación — red (Storage + embeddings) + Supabase (`store`).
# ---------------------------------------------------------------------------

def ingestar_documentos_pendientes(
    store,
    *,
    documentos: Optional[list[dict]] = None,
    generar_embedding_fn: Optional[Callable[[str], list]] = None,
) -> ResultadoIngesta:
    """Ingesta filas de `silver.teoria_documentos` en `gold.teoria_chunks`.

    Por defecto (`documentos=None`) usa `store.listar_documentos_pendientes()`
    — todas las filas Silver que aún no tienen chunks en Gold (opción (a) de
    la spec, ver docstring del módulo). Se puede pasar `documentos` de forma
    explícita (una lista de filas ya conocidas, mismo shape que devuelve
    `listar_documentos_pendientes`) para acotar la llamada a un subconjunto
    concreto, sin exigir tocar esta función si algún día un caller quiere la
    opción (b) ("las filas escritas en este run").

    Por cada fila: descarga su `.md` (`store.descargar_documento(fila["bucket"],
    fila["object_path"])`, decodificado UTF-8), separa sus fragmentos
    (`_separar_cuerpo`) y, para cada uno: resuelve/crea la fila de `fuentes`
    (una sola vez por `fuente` distinta encontrada, cacheada en memoria
    durante esta llamada para no repetir el roundtrip), calcula su embedding
    (única llamada de red por fragmento, sin reintento) y hace upsert
    idempotente vía `store.guardar_chunk` con `doc_id=str(fila["id"])`,
    `version_corpus=fila["hash_md5"]`.

    `store` es cualquier objeto con la interfaz de `TeoriaStore`
    (`listar_documentos_pendientes`, `descargar_documento`,
    `buscar_o_crear_fuente`, `guardar_chunk`), inyectable para testear sin
    red (`tests/unit/theory/test_ingest_teoria.py`); en producción es un
    `TeoriaStore` real (`tests/integration/test_ingest_teoria_live.py`).
    `generar_embedding_fn` es igual de inyectable, por defecto
    `generar_embedding` real de `src/utils/llm/embeddings.py`.
    """
    filas = documentos if documentos is not None else store.listar_documentos_pendientes()
    generar = generar_embedding_fn if generar_embedding_fn is not None else generar_embedding

    cache_fuentes: dict[str, int] = {}
    chunks: list[ChunkIngestado] = []

    for fila in filas:
        contenido_bytes = store.descargar_documento(fila["bucket"], fila["object_path"])
        texto_documento = contenido_bytes.decode("utf-8")
        fragmentos = _separar_cuerpo(texto_documento)

        doc_id = str(fila["id"])
        version_corpus = fila["hash_md5"]

        nombre_fuente = fila["fuente"]
        if nombre_fuente not in cache_fuentes:
            cache_fuentes[nombre_fuente] = store.buscar_o_crear_fuente(
                nombre_fuente, tipo_fuente=fila["tipo_fuente"], licencia=fila.get("licencia")
            )
        fuente_id = cache_fuentes[nombre_fuente]

        for indice, texto_fragmento in enumerate(fragmentos):
            embedding = generar(texto_fragmento)
            fila_guardada = store.guardar_chunk(
                doc_id=doc_id,
                version_corpus=version_corpus,
                chunk_index=indice,
                contenido=texto_fragmento,
                embedding=embedding,
                tipo_fuente=fila["tipo_fuente"],
                fuente_id=fuente_id,
                licencia=fila.get("licencia"),
            )
            chunks.append(
                ChunkIngestado(
                    doc_id=doc_id,
                    version_corpus=version_corpus,
                    chunk_index=indice,
                    es_duplicado=fila_guardada is None,
                    fila=fila_guardada,
                )
            )

    return ResultadoIngesta(chunks=chunks)
