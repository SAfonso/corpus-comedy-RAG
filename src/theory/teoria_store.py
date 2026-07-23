"""teoria_store — cliente de acceso a Supabase para la ingesta de teoría (task 21).

Contrato (`src/theory/SPEC.md` §Storage, `src/jokes/SPEC.md` §Storage —
`teoria_chunks` se documenta ahí junto al resto del esquema, pero su cliente
de acceso es explícitamente scope de esta task, no de `src/jokes/`): único
punto de acceso a las tablas `teoria_chunks` y (de lectura/creación
mínima) `fuentes` PARA EL FLUJO DE TEORÍA.

**Por qué un cliente propio y no reusar `src/jokes/supabase_store.py`**:
regla dura de `CLAUDE.md` — `theory/` y `jokes/` NUNCA se importan entre sí,
código común va a `src/utils/`. `supabase_store.py` ya implementa
`crear_cliente`/`listar_fuentes`/`crear_fuente` para el contrato B/C, pero
importarlo desde `src/theory/` rompería esa regla aunque el acceso a
`fuentes` sea conceptualmente el mismo. La duplicación mínima de este
fichero (credenciales + `fuentes`) es el coste aceptado de mantener los dos
flujos desacoplados — mismo criterio ya aplicado en otros sitios del repo
(cada flujo con su propia idempotencia, sin capa de abstracción compartida
prematura).

Diseño para TDD sin red frágil (mismo patrón que `src/jokes/supabase_store.py`):
la construcción de payloads vive en funciones puras
(`_build_chunk_payload`), testeables sin red en
`tests/unit/theory/test_teoria_store.py`. La clase `TeoriaStore` es una capa
fina sobre `supabase-py`, testeada de verdad (sin mocks frágiles de la
librería) en `tests/integration/test_ingest_teoria_live.py`.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv

# Carga `.env` (si existe) — mismo patrón que `src/jokes/supabase_store.py`.
load_dotenv()


class TeoriaStoreError(RuntimeError):
    """Error del cliente de acceso a Supabase para teoría (config ausente, payload inválido)."""


# ---------------------------------------------------------------------------
# Construcción pura de payloads — sin red, testeable directamente.
# ---------------------------------------------------------------------------

def _build_chunk_payload(
    *,
    doc_id: str,
    version_corpus: str,
    chunk_index: int,
    contenido: str,
    embedding: list,
    tipo_fuente: str,
    fuente_id: Optional[int] = None,
    licencia: Optional[str] = None,
) -> dict:
    """Construye el payload de upsert para `teoria_chunks` (§Storage, task 21).

    `doc_id`/`version_corpus`/`chunk_index` son la clave de idempotencia
    (`unique(doc_id, version_corpus, chunk_index)` en `schema.sql`) — los
    tres son obligatorios, igual que `contenido`/`embedding`/`tipo_fuente`
    (un chunk sin alguno de estos no es un chunk válido). `fuente_id` es
    opcional (puede no haberse podido resolver/crear la fila de `fuentes`);
    `licencia` se omite si no se da para que aplique el default de la DDL
    (`'personal_only'`, coherente con que `teoria_chunks` es
    predominantemente `externo*`).
    """
    payload: dict[str, Any] = {
        "doc_id": doc_id,
        "version_corpus": version_corpus,
        "chunk_index": chunk_index,
        "contenido": contenido,
        "embedding": embedding,
        "tipo_fuente": tipo_fuente,
    }
    if fuente_id is not None:
        payload["fuente_id"] = fuente_id
    if licencia is not None:
        payload["licencia"] = licencia
    return payload


# ---------------------------------------------------------------------------
# Cliente Supabase — capa fina sobre supabase-py.
# ---------------------------------------------------------------------------

def crear_cliente():
    """Crea un cliente `supabase-py` real desde `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`.

    Mismas variables de entorno que `src/jokes/supabase_store.py` (un único
    proyecto Supabase compartido por todo el pipeline, ver
    `src/jokes/SPEC.md` §Storage: "pgvector es el índice único... compartido
    con teoría") — leerlas de nuevo aquí es la duplicación aceptada descrita
    en el docstring del módulo, no una segunda fuente de configuración.
    """
    from supabase import create_client  # import perezoso: no requerido para lógica pura/tests unit

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise TeoriaStoreError(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas en el entorno (.env). "
            "Revisa .env.example — son necesarias para instanciar TeoriaStore."
        )
    return create_client(url, key)


class TeoriaStore:
    """Cliente de acceso a `teoria_chunks`/`fuentes` para la ingesta de teoría (task 21).

    Inyecta `client` (cualquier objeto con la interfaz de `supabase-py`,
    típicamente un doble de test) para testear sin red; si se omite, crea un
    cliente real vía `crear_cliente()`.
    """

    def __init__(self, client: Optional[Any] = None):
        self.client = client if client is not None else crear_cliente()

    # -- teoria_chunks ----------------------------------------------------

    def guardar_chunk(self, **kwargs) -> Optional[dict]:
        """Upsert idempotente por `(doc_id, version_corpus, chunk_index)`.

        `ignore_duplicates=True`: reingestar la misma `v{N}` (inmutable por
        diseño, `src/theory/SPEC.md` §Idempotencia) nunca duplica filas ni
        vuelve a gastar una llamada de embeddings en vano desde el punto de
        vista de los datos ya guardados — mismo patrón que
        `SupabaseStore.guardar_mensaje_telegram_bronze` (task 16).
        `None` significa "el chunk ya existía, no se tocó nada".
        """
        payload = _build_chunk_payload(**kwargs)
        resultado = (
            self.client.table("teoria_chunks")
            .upsert(payload, on_conflict="doc_id,version_corpus,chunk_index", ignore_duplicates=True)
            .execute()
        )
        return resultado.data[0] if resultado.data else None

    # -- fuentes (lectura/creación mínima, tabla compartida) ---------------

    def buscar_o_crear_fuente(
        self, nombre: str, *, tipo_fuente: Optional[str] = None, licencia: Optional[str] = None
    ) -> int:
        """Devuelve el `id` de `fuentes` con este `nombre`, creándolo si no existe.

        Búsqueda exacta por `nombre` (columna sin `UNIQUE` en `schema.sql`,
        así que en teoría podría haber duplicados si dos callers crean la
        misma fuente en paralelo — riesgo aceptado, volumen bajo y batch, no
        concurrente, ver `src/theory/SPEC.md` §Riesgos). Si hay más de una
        fila con el mismo nombre, se queda con la primera (determinista por
        orden de Supabase, no relevante en la práctica para este volumen).
        """
        resultado = self.client.table("fuentes").select("id").eq("nombre", nombre).execute()
        if resultado.data:
            return resultado.data[0]["id"]

        payload: dict[str, Any] = {"nombre": nombre}
        if tipo_fuente is not None:
            payload["tipo_fuente"] = tipo_fuente
        if licencia is not None:
            payload["licencia"] = licencia
        creado = self.client.table("fuentes").insert(payload).execute()
        return creado.data[0]["id"]
