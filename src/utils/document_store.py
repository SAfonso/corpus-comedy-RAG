"""document_store — captura durable de documentos Bronze/Silver (P25, task 58).

Contrato completo: `src/utils/SPEC.md` §"DocumentStore — captura durable de
documentos (P25, 2026-07-28)". Este módulo materializa la regla de
`CLAUDE.md` "el material original es sagrado y quien lo garantiza es la capa
Bronze en Supabase" para los documentos de los Flujos A (teoría) y C
(histórico): sube el fichero a su bucket privado e inserta la fila
Bronze/Silver append-only que lo indexa.

Vive en `utils/` porque la operación es idéntica en teoría e histórico (misma
razón que `drive_sync.py`) y la regla de dependencias de `CLAUDE.md` prohíbe
que `theory/` y `jokes/` se importen entre sí. Ningún flujo lo consume
todavía — lo consumen las tasks 59/61 (teoría), 64/65 (histórico) y 66
(backfill legacy).

Diseño para TDD sin red (mismo patrón que `src/jokes/supabase_store.py`): la
resolución del `Destino`, la construcción del `object_path`, el saneado del
nombre, la derivación de `origen`, el cálculo del `hash_md5` y la validación
de argumentos son funciones puras de módulo, sin dependencia de red — ahí
vive la cobertura unitaria (`tests/unit/utils/test_document_store.py`).
`DocumentStore` es una clase fina que llama a esas funciones puras y luego
ejecuta la llamada real contra `supabase-py` (cliente inyectable, mismo
patrón que `SupabaseStore`/`DriveSync`).

`crear_cliente()` se duplica a propósito respecto a
`src/jokes/supabase_store.py:crear_cliente` — importarla desde `utils/`
invertiría la regla de dependencias (`utils/` no puede depender de `jokes/`).
Ver justificación completa en `SPEC.md`.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from postgrest.exceptions import APIError

# Carga `.env` (si existe) para poblar `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`
# antes de que `crear_cliente` las lea. No falla si `.env` no existe (mismo
# patrón que `supabase_store.py`/`language_normalizer.py`).
load_dotenv()

CAPAS = ("bronze", "silver")
FLUJOS = ("teoria", "historico")
ORIGENES = ("drive", "local_legacy")

# Código Postgres de violación de unicidad, tal cual lo traduce PostgREST en
# `postgrest.exceptions.APIError.code` (ver `src/jokes/KNOWN_ERRORS.md` sobre
# cómo PostgREST traduce los códigos nativos de Postgres a los suyos propios;
# `23505` sí viaja literal, a diferencia de `42P01`/`PGRST205`).
_CODIGO_VIOLACION_UNICIDAD = "23505"

# Columnas que gestiona el propio componente — `extra` no puede pisarlas
# (`SPEC.md` §Validación de argumentos).
_COLUMNAS_RESERVADAS = frozenset(
    {
        "bucket",
        "object_path",
        "drive_file_id",
        "modified_time",
        "hash_md5",
        "origen",
        "nombre",
        "mime_type",
        "tamano_bytes",
    }
)

# Todo lo que no sea [A-Za-z0-9._-] se sustituye por "_" en el nombre lógico
# antes de usarlo como segmento de la clave del objeto.
_NOMBRE_SANEADO_RE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class Destino:
    bucket: str
    schema: str
    tabla: str


# Los cuatro destinos fijos de P25 (task 53/56, ya creados en producción
# real). Tabla explícita, NO derivada por concatenación de strings — ver
# justificación en `SPEC.md` §"Destinos: cuatro, fijos, en una tabla explícita".
DESTINOS: dict[tuple[str, str], Destino] = {
    ("bronze", "teoria"): Destino(
        bucket="bronze-teoria", schema="bronze", tabla="teoria_documentos"
    ),
    ("silver", "teoria"): Destino(
        bucket="silver-teoria", schema="silver", tabla="teoria_documentos"
    ),
    ("bronze", "historico"): Destino(
        bucket="bronze-historico", schema="bronze", tabla="historico_documentos"
    ),
    ("silver", "historico"): Destino(
        bucket="silver-historico", schema="silver", tabla="historico_documentos"
    ),
}


@dataclass(frozen=True)
class ResultadoCaptura:
    destino: Destino
    object_path: str
    hash_md5: str
    origen: str
    ya_existia: bool
    fila_id: Optional[str]


class DocumentStoreError(RuntimeError):
    """Config ausente, destino desconocido, argumentos incoherentes o fallo de captura."""


def crear_cliente():
    """Crea un cliente `supabase-py` real desde `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`.

    Lanza `DocumentStoreError` con un mensaje explícito si las variables de
    entorno no están configuradas — nunca intenta conectar con valores
    vacíos. Duplicada a propósito respecto a
    `src/jokes/supabase_store.py:crear_cliente` (ver docstring del módulo).
    """
    from supabase import create_client  # import perezoso: no requerido para lógica pura/tests unit

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise DocumentStoreError(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas en el entorno (.env). "
            "Revisa .env.example — son necesarias para instanciar DocumentStore."
        )
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Funciones puras — sin red, cobertura unitaria directa.
# ---------------------------------------------------------------------------


def _sanear_nombre(nombre: str) -> str:
    """Sustituye todo lo que no sea `[A-Za-z0-9._-]` por `_`."""
    return _NOMBRE_SANEADO_RE.sub("_", nombre)


def _compactar_modified_time(modified_time: str) -> str:
    """RFC 3339 de Drive a forma básica sin separadores.

    `2026-07-28T10:15:00.000Z` -> `20260728T101500Z`. Solo elimina `-` y `:`
    (los únicos separadores que introduce RFC 3339 en fecha/hora); el resto
    del string (incluida la fracción de segundo y la `Z`) viaja tal cual.
    """
    return modified_time.replace("-", "").replace(":", "")


def _derivar_origen(drive_file_id: Optional[str]) -> str:
    """`'drive'` si viene `drive_file_id`, `'local_legacy'` si es `None`.

    `origen` no es un parámetro de `capturar()` — se deriva siempre de este
    único hecho para evitar dos fuentes de verdad (`SPEC.md`).
    """
    return "drive" if drive_file_id is not None else "local_legacy"


def _hash_md5(contenido: bytes) -> str:
    return hashlib.md5(contenido).hexdigest()


def _resolver_destino(capa: str, flujo: str) -> Destino:
    destino = DESTINOS.get((capa, flujo))
    if destino is None:
        raise DocumentStoreError(
            f"Destino desconocido para (capa={capa!r}, flujo={flujo!r}). "
            f"capa válida en {CAPAS}, flujo válido en {FLUJOS}."
        )
    return destino


def _validar_argumentos(
    ruta: Path,
    capa: str,
    flujo: str,
    drive_file_id: Optional[str],
    modified_time: Optional[str],
    extra: Optional[dict],
) -> Destino:
    """Validación fail-fast completa, antes de tocar red (`SPEC.md` §Validación)."""
    destino = _resolver_destino(capa, flujo)

    if not ruta.exists() or not ruta.is_file():
        raise DocumentStoreError(f"ruta no existe o no es un fichero: {ruta}")

    if (drive_file_id is None) != (modified_time is None):
        raise DocumentStoreError(
            "drive_file_id y modified_time deben venir ambos o ninguno: la clave "
            "de idempotencia es el PAR, medio par no identifica una versión "
            f"(drive_file_id={drive_file_id!r}, modified_time={modified_time!r})."
        )

    if extra:
        conflictivas = _COLUMNAS_RESERVADAS.intersection(extra)
        if conflictivas:
            raise DocumentStoreError(
                "extra intenta escribir columnas que gestiona el propio "
                f"DocumentStore: {sorted(conflictivas)}"
            )

    return destino


def _construir_object_path(
    origen: str,
    drive_file_id: Optional[str],
    modified_time: Optional[str],
    hash_md5: str,
    nombre_saneado: str,
) -> str:
    """Clave del objeto dentro del bucket (`SPEC.md` §Naming del objeto).

    La clave ES la clave de idempotencia de la fila: `drive/{drive_file_id}/
    {modified_time_compacto}/{nombre_saneado}` en modo Drive,
    `local_legacy/{hash_md5}/{nombre_saneado}` en legacy.
    """
    if origen == "drive":
        modified_time_compacto = _compactar_modified_time(modified_time)
        return f"drive/{drive_file_id}/{modified_time_compacto}/{nombre_saneado}"
    return f"local_legacy/{hash_md5}/{nombre_saneado}"


class DocumentStore:
    """Captura durable de documentos Bronze/Silver (`SPEC.md`).

    Inyecta `client` (cualquier objeto con la interfaz de `supabase-py`:
    `.schema().table().insert/select/eq/is_/execute` y
    `.storage.from_().upload()`), típicamente un doble de test; si se omite,
    crea un cliente real vía `crear_cliente()`.
    """

    def __init__(self, client: Optional[Any] = None):
        self.client = client if client is not None else crear_cliente()

    def capturar(
        self,
        ruta: Path,
        capa: str,
        flujo: str,
        drive_file_id: Optional[str] = None,
        modified_time: Optional[str] = None,
        nombre: Optional[str] = None,
        mime_type: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> ResultadoCaptura:
        """Captura `ruta` en el bucket/tabla de `(capa, flujo)`.

        Orden fijo (`SPEC.md` §Comportamiento de `capturar()`, NO reordenar):
        1. Valida argumentos y resuelve `Destino`.
        2. Calcula `hash_md5` del contenido (siempre, no solo en legacy).
        3. Comprueba idempotencia con un SELECT; si ya hay fila, retorno
           inmediato con `ya_existia=True`, sin subir el objeto ni insertar.
        4. Sube el objeto al bucket.
        5. Inserta la fila (columnas propias + `extra`).
        6. Devuelve `ResultadoCaptura(ya_existia=False, ...)`.

        Objeto-antes-que-fila es deliberado: el huérfano tolerado es "objeto
        sin fila" (invisible, cuota de Storage), nunca "fila sin objeto"
        (mentira indexada) — ver justificación completa en `SPEC.md`.

        Carrera: si el INSERT del paso 5 viola el índice único parcial
        (`postgrest.exceptions.APIError` código `23505`), se trata como
        `ya_existia=True` (vuelve a hacer el SELECT para el `fila_id` real),
        nunca como error — la garantía real es el índice de Postgres, el
        SELECT del paso 3 es solo una optimización para no subir el objeto.
        """
        ruta = Path(ruta)
        destino = _validar_argumentos(ruta, capa, flujo, drive_file_id, modified_time, extra)

        origen = _derivar_origen(drive_file_id)
        nombre_logico = nombre if nombre is not None else ruta.name
        nombre_saneado = _sanear_nombre(nombre_logico)

        contenido = ruta.read_bytes()
        hash_md5 = _hash_md5(contenido)

        object_path = _construir_object_path(
            origen, drive_file_id, modified_time, hash_md5, nombre_saneado
        )

        fila_id = self._buscar_fila_existente(destino, drive_file_id, modified_time, hash_md5)
        if fila_id is not None:
            return ResultadoCaptura(
                destino=destino,
                object_path=object_path,
                hash_md5=hash_md5,
                origen=origen,
                ya_existia=True,
                fila_id=fila_id,
            )

        file_options = {"content-type": mime_type} if mime_type else None
        self.client.storage.from_(destino.bucket).upload(object_path, contenido, file_options)

        payload = {
            "bucket": destino.bucket,
            "object_path": object_path,
            "drive_file_id": drive_file_id,
            "modified_time": modified_time,
            "hash_md5": hash_md5,
            "origen": origen,
            "nombre": nombre_logico,
            "mime_type": mime_type,
            "tamano_bytes": len(contenido),
        }
        if extra:
            payload.update(extra)

        try:
            resultado = (
                self.client.schema(destino.schema).table(destino.tabla).insert(payload).execute()
            )
        except APIError as exc:
            if exc.code == _CODIGO_VIOLACION_UNICIDAD:
                fila_id = self._buscar_fila_existente(
                    destino, drive_file_id, modified_time, hash_md5
                )
                return ResultadoCaptura(
                    destino=destino,
                    object_path=object_path,
                    hash_md5=hash_md5,
                    origen=origen,
                    ya_existia=True,
                    fila_id=fila_id,
                )
            raise DocumentStoreError(
                f"fallo insertando fila en {destino.schema}.{destino.tabla}: {exc}"
            ) from exc

        nueva_fila_id = None
        if resultado.data:
            nueva_fila_id = resultado.data[0].get("id")

        return ResultadoCaptura(
            destino=destino,
            object_path=object_path,
            hash_md5=hash_md5,
            origen=origen,
            ya_existia=False,
            fila_id=nueva_fila_id,
        )

    def _buscar_fila_existente(
        self,
        destino: Destino,
        drive_file_id: Optional[str],
        modified_time: Optional[str],
        hash_md5: str,
    ) -> Optional[str]:
        """SELECT de idempotencia (`SPEC.md` §Comportamiento, paso 3).

        Modo Drive: por `(drive_file_id, modified_time)`. Legacy: por
        `hash_md5` con `drive_file_id IS NULL` — el mismo par de índices
        únicos parciales que garantiza Postgres (task 53).
        """
        query = self.client.schema(destino.schema).table(destino.tabla).select("id")
        if drive_file_id is not None:
            query = query.eq("drive_file_id", drive_file_id).eq("modified_time", modified_time)
        else:
            query = query.is_("drive_file_id", "null").eq("hash_md5", hash_md5)
        resultado = query.execute()
        if resultado.data:
            return resultado.data[0]["id"]
        return None
