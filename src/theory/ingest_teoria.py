"""ingest_teoria — vuelca `/data/processed/v{N}/` a `teoria_chunks` en Supabase (task 21).

Contrato (`src/theory/SPEC.md` §Storage: "Un paso de ingesta vuelca la
teoría a Supabase (tabla `teoria_chunks`)... como copia indexable — no es la
fuente de verdad, esa sigue siendo el fichero `v{N}`"): lee un `v{N}/` ya
FINALIZADO (con `manifest.json`, ver `format_normalizer.generar_version`,
task 11) y crea un chunk por FRAGMENTO de cada documento (mismo fragmento
que ya produjo `LanguageNormalizer`/`SubtypeDetector` — no se reparte el
texto de otra forma: un fragmento YA es una unidad semántica con
`subtipo`/idioma resueltos, partirlo más finamente por tokens no está
pedido por el título de esta task ni por el SPEC, y añadiría una decisión de
tamaño de chunk arbitraria sin ninguna guía — BISTURÍ: mínimo viable).

**Excepción documentada al "Sin LLM" de Flujo A** (`src/theory/SPEC.md`
§Stack: "Sin LLM... Coste cero"; `docs/specs/llm-policy.md`): este módulo SÍ
llama a una API de embeddings (`generar_embedding`, `src/utils/llm/embeddings.py`,
ya implementado en la task 15 para Reconciliación). No es una excepción
nueva: la política ya la nombra explícitamente ("Los embeddings de
reconciliación/retrieval, ídem" — la excepción cubre RETRIEVAL en general,
no solo chistes) y el propio `SPEC.md` de teoría dice que `pgvector` es "el
índice único de consulta del RAG, COMPARTIDO con los chistes" — sin
embeddings de teoría, ese índice compartido no podría servir teoría en el
retrieval. No hay generación de contenido ni coste de LLM de texto: el
`.txt` de `v{N}/` no se toca ni se reescribe, solo se vectoriza para
indexar.

**Sin re-parseo de YAML**: `teoria_chunks` (`schema.sql`) NO tiene columnas
de `subtipo`/`idioma` por fragmento (a diferencia del `.txt`, que sí las
lleva en la cabecera) — solo `tipo_fuente`/`licencia` a nivel de documento,
que YA están en `manifest.json` sin necesidad de abrir el `.txt`. Por eso
este módulo no necesita un parser de YAML (que `format_normalizer.py`
decidió explícitamente no construir, ver su docstring §"emisor YAML
propio"): solo necesita separar la cabecera del cuerpo para extraer los
fragmentos, no sus metadatos individuales. `_separar_cuerpo` hace exactamente
eso, aprovechando que la cabecera SIEMPRE cierra con la línea delimitadora
`"---"` seguida de una línea en blanco (formato fijo, propio, controlado por
`render_document` — ver su docstring). **Limitación documentada**: esto
asume que el texto de un fragmento no contiene él mismo una línea en blanco
interna (dos saltos de línea seguidos) — cierto para los fragmentos que
produce la cadena actual (párrafo por fragmento); si algún día un fragmento
pudiera contener saltos de párrafo internos, esta función tendría que
cambiar de estrategia (o `manifest.json` tendría que empezar a llevar
`num_fragmentos` con separadores explícitos).

**Idempotencia**: `v{N}` es inmutable una vez finalizado (`manifest.json`
presente, `src/theory/SPEC.md` §Idempotencia y versionado) — reingestar la
misma versión no debe duplicar filas. `TeoriaStore.guardar_chunk` hace
`upsert` con `ON CONFLICT (doc_id, version_corpus, chunk_index) DO NOTHING`
(mismo patrón que `SupabaseStore.guardar_mensaje_telegram_bronze`, task 16).

Diseño para TDD sin red frágil: `_descubrir_ultima_version`, `_leer_manifest`
y `_separar_cuerpo` son puros/solo-IO-de-fichero, testeables con `tmp_path`
sin ninguna llamada de red. `ingestar_version` orquesta contra un `store`
inyectado (`TeoriaStore` real o doble de test) y un `generar_embedding_fn`
inyectado (real o fake), sin loop de reintento — una sola llamada de
embeddings por chunk, igual que `reconciliar_chiste` (task 15).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.utils.llm.embeddings import generar_embedding

NOMBRE_MANIFEST = "manifest.json"
_PATRON_VERSION = re.compile(r"^v(\d+)$")


class IngestaTeoriaError(ValueError):
    """Error de ingesta de teoría (versión inexistente, manifest/documento mal formado)."""


@dataclass(frozen=True)
class ChunkIngestado:
    """Resultado de ingestar un fragmento: el chunk guardado (o `None` si ya existía)."""

    doc_id: str
    chunk_index: int
    es_duplicado: bool
    fila: Optional[dict] = None


@dataclass(frozen=True)
class ResultadoIngesta:
    """Resultado agregado de `ingestar_version` para toda la `v{N}`."""

    version_corpus: str
    chunks: list[ChunkIngestado]

    @property
    def num_nuevos(self) -> int:
        return sum(1 for c in self.chunks if not c.es_duplicado)

    @property
    def num_duplicados(self) -> int:
        return sum(1 for c in self.chunks if c.es_duplicado)


# ---------------------------------------------------------------------------
# Descubrimiento de versión — solo I/O de fichero, sin red.
# ---------------------------------------------------------------------------

def _descubrir_ultima_version(directorio_base: Path) -> int:
    """Última versión FINALIZADA (con `manifest.json`) en `directorio_base`.

    Lanza `IngestaTeoriaError` si no hay ninguna — no hay nada que ingestar
    sin al menos una `v{N}` completa (mismo criterio de "finalizada" que
    `format_normalizer._version_finalizada`, reimplementado aquí en vez de
    importado: es una función privada de otro módulo, no parte de su
    interfaz pública).
    """
    directorio_base = Path(directorio_base)
    finalizadas = []
    if directorio_base.exists():
        for entrada in directorio_base.iterdir():
            match = entrada.is_dir() and _PATRON_VERSION.match(entrada.name)
            if match and (entrada / NOMBRE_MANIFEST).exists():
                finalizadas.append(int(match.group(1)))

    if not finalizadas:
        raise IngestaTeoriaError(
            f"No hay ninguna versión finalizada (con {NOMBRE_MANIFEST}) en {directorio_base}"
        )
    return max(finalizadas)


def _leer_manifest(directorio_version: Path) -> dict:
    """Lee y parsea `manifest.json` de `directorio_version`."""
    ruta = Path(directorio_version) / NOMBRE_MANIFEST
    if not ruta.exists():
        raise IngestaTeoriaError(f"No existe {ruta}: la versión no está finalizada")
    return json.loads(ruta.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Separación cabecera/cuerpo de un `.txt` de `v{N}/documents/` — pura.
# ---------------------------------------------------------------------------

def _separar_cuerpo(texto_documento: str) -> list[str]:
    """Extrae los textos de fragmento del cuerpo de un `.txt` de teoría.

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
# Orquestación — I/O de fichero + red (embeddings) + Supabase (`store`).
# ---------------------------------------------------------------------------

def ingestar_version(
    directorio_base: Path,
    store,
    *,
    version: Optional[int] = None,
    generar_embedding_fn: Optional[Callable[[str], list]] = None,
) -> ResultadoIngesta:
    """Ingesta `v{N}/` (la última finalizada si `version` es `None`) en `teoria_chunks`.

    Por cada documento de `manifest.json`, lee su `.txt`, separa sus
    fragmentos (`_separar_cuerpo`) y, para cada uno: resuelve/crea la fila de
    `fuentes` (una sola vez por `fuente` distinta encontrada, cacheada en
    memoria durante esta llamada para no repetir el roundtrip), calcula su
    embedding (única llamada de red por fragmento, sin reintento) y hace
    upsert idempotente vía `store.guardar_chunk`.

    `store` es cualquier objeto con la interfaz de `TeoriaStore`
    (`guardar_chunk`, `buscar_o_crear_fuente`), inyectable para testear sin
    red (`tests/unit/theory/test_ingest_teoria.py`); en producción es un
    `TeoriaStore` real (`tests/integration/test_ingest_teoria_live.py`).
    `generar_embedding_fn` es igual de inyectable, por defecto
    `generar_embedding` real de `src/utils/llm/embeddings.py`.
    """
    directorio_base = Path(directorio_base)
    version = version if version is not None else _descubrir_ultima_version(directorio_base)
    directorio_version = directorio_base / f"v{version}"
    version_corpus = f"v{version}"

    manifest = _leer_manifest(directorio_version)
    generar = generar_embedding_fn if generar_embedding_fn is not None else generar_embedding

    cache_fuentes: dict[str, int] = {}
    chunks: list[ChunkIngestado] = []

    for doc in manifest.get("documents", []):
        ruta_documento = directorio_version / doc["path"]
        texto_documento = ruta_documento.read_text(encoding="utf-8")
        fragmentos = _separar_cuerpo(texto_documento)

        nombre_fuente = doc["fuente"]
        if nombre_fuente not in cache_fuentes:
            cache_fuentes[nombre_fuente] = store.buscar_o_crear_fuente(
                nombre_fuente, tipo_fuente=doc["tipo_fuente"], licencia=doc.get("licencia")
            )
        fuente_id = cache_fuentes[nombre_fuente]

        for indice, texto_fragmento in enumerate(fragmentos):
            embedding = generar(texto_fragmento)
            fila = store.guardar_chunk(
                doc_id=doc["path"],
                version_corpus=version_corpus,
                chunk_index=indice,
                contenido=texto_fragmento,
                embedding=embedding,
                tipo_fuente=doc["tipo_fuente"],
                fuente_id=fuente_id,
                licencia=doc.get("licencia"),
            )
            chunks.append(
                ChunkIngestado(
                    doc_id=doc["path"],
                    chunk_index=indice,
                    es_duplicado=fila is None,
                    fila=fila,
                )
            )

    return ResultadoIngesta(version_corpus=version_corpus, chunks=chunks)
