"""supabase_store — cliente de acceso a Supabase para el contrato B/C.

Contrato (`src/jokes/SPEC.md` §Storage): los chistes viven nativos en
Supabase (Postgres + `pgvector`), a diferencia de teoría (ficheros `v{N}`).
Este módulo es el ÚNICO punto de acceso a las tablas `chistes`,
`chistes_revisiones`, `temas`, `tecnicas`, `fuentes` y
`candidatos_taxonomia` — usado igual desde Flujo B (Telegram) y Flujo C
(Histórico), igual que `silver.py`/`reconciliacion.py` (ver docstring de
`src/jokes/SPEC.md`). `teoria_chunks` NO se expone aquí (scope de la task 21,
ingesta de teoría) aunque su DDL vive en el mismo `schema.sql` por
comodidad — este módulo tampoco importa nada de `src/theory/` (regla de
`CLAUDE.md`: `theory/` y `jokes/` no se importan entre sí).

DDL vs. cliente (importante, no confundir): la API REST de Supabase
(PostgREST, autenticada con `SUPABASE_SERVICE_KEY` = clave `service_role`)
permite INSERT/SELECT/UPDATE sobre tablas YA EXISTENTES, pero NO puede
ejecutar DDL (`CREATE TABLE`, `CREATE EXTENSION`) — eso requeriría una
conexión Postgres directa que no está en `.env`. Por eso el DDL vive en
`src/jokes/schema.sql`, versionado y aplicado a MANO en el SQL Editor del
dashboard de Supabase; este módulo asume que esas tablas ya existen y solo
habla con ellas vía `supabase-py` (paquete `supabase`).

Diseño para TDD sin red frágil (mismo patrón que
`src/theory/normalizers/language_normalizer.py` / `_necesita_ocr_fallback`
de `pdf_parser.py`): la lógica de decisión — qué columnas son válidas, qué
valores de enum se aceptan, qué payload se construye — vive en funciones
puras de módulo (`_validar_*`, `_build_*_payload`) sin ninguna dependencia de
red, testeables con `tests/unit/jokes/test_supabase_store.py`. `SupabaseStore`
es una clase fina que llama a esas funciones puras y luego ejecuta la
llamada real contra `supabase-py` — se testea de verdad (sin mocks frágiles
de la librería) en `tests/integration/test_supabase_store_live.py`, con
`skip` explícito si `schema.sql` aún no se ha aplicado en Supabase.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

# Carga `.env` (si existe) para poblar `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`
# antes de que `crear_cliente` las lea. No falla si `.env` no existe (mismo
# patrón que `language_normalizer.py`).
load_dotenv()

# ---------------------------------------------------------------------------
# Enums del esquema (`src/jokes/SPEC.md` §Storage / §Silver) — fuente única
# de verdad de los valores permitidos, para no inventar columnas ni estados.
# ---------------------------------------------------------------------------

TIPOS_FUENTE_CHISTE = ("propio", "propio_historico")
ESTADOS_CHISTE = ("idea_suelta", "con_estructura", "rematado")
TIPOS_CANDIDATO_TAXONOMIA = ("tema", "tecnica")
ESTADOS_CANDIDATO_TAXONOMIA = ("pendiente", "aceptado", "rechazado")

# Columnas mutables de `chistes` vía `actualizar_chiste` (excluye `id` y
# `created_at`, que no se tocan tras la inserción).
_COLUMNAS_CHISTE_ACTUALIZABLES = (
    "texto_normalizado",
    "hash_normalizado",
    "embedding",
    "tipo_fuente",
    "tema_id",
    "tecnica_id",
    "fuente_id",
    "estado",
    "version_actual",
    "chiste_origen_id",
    "licencia",
)


class SupabaseStoreError(RuntimeError):
    """Error del contrato de acceso a Supabase (config ausente, payload inválido)."""


# ---------------------------------------------------------------------------
# Validación pura de enums (§Storage) — sin red, testeable directamente.
# ---------------------------------------------------------------------------

def _validar_enum(valor: Optional[str], permitidos: tuple, nombre_campo: str) -> Optional[str]:
    """Valida que `valor` (si no es None) esté en `permitidos`.

    Devuelve `valor` tal cual si es válido o None. Lanza `ValueError` con un
    mensaje explícito (campo + permitidos) si no lo es — nunca falla en
    silencio ni deja pasar un valor fuera de enum hacia Supabase.
    """
    if valor is None:
        return None
    if valor not in permitidos:
        raise ValueError(
            f"{nombre_campo} inválido: {valor!r} (permitidos: {', '.join(permitidos)})"
        )
    return valor


def _validar_tipo_fuente_chiste(tipo_fuente: Optional[str]) -> Optional[str]:
    return _validar_enum(tipo_fuente, TIPOS_FUENTE_CHISTE, "tipo_fuente")


def _validar_estado_chiste(estado: Optional[str]) -> Optional[str]:
    return _validar_enum(estado, ESTADOS_CHISTE, "estado")


def _validar_tipo_candidato(tipo: Optional[str]) -> Optional[str]:
    return _validar_enum(tipo, TIPOS_CANDIDATO_TAXONOMIA, "tipo")


def _validar_estado_candidato(estado: Optional[str]) -> Optional[str]:
    return _validar_enum(estado, ESTADOS_CANDIDATO_TAXONOMIA, "estado")


def _timestamp_actual() -> str:
    """Timestamp ISO 8601 UTC — usado para `updated_at` en updates desde cliente."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Construcción pura de payloads (§Storage) — decide qué se manda a Supabase,
# aplica defaults de Python que la DDL no cubre (`estado`, `version_actual`)
# y omite campos opcionales no proporcionados (deja que la DDL aplique sus
# propios defaults, ej. `licencia`, cuando el caller no fuerza un valor).
# ---------------------------------------------------------------------------

def _build_chiste_payload(
    *,
    texto_normalizado: str,
    hash_normalizado: str,
    tipo_fuente: str,
    embedding: Optional[list] = None,
    tema_id: Optional[int] = None,
    tecnica_id: Optional[int] = None,
    fuente_id: Optional[int] = None,
    estado: str = "idea_suelta",
    version_actual: int = 1,
    chiste_origen_id: Optional[str] = None,
    licencia: Optional[str] = None,
) -> dict:
    """Construye el payload de INSERT para `chistes` (§Storage).

    Campos obligatorios del boceto (`texto_normalizado`, `hash_normalizado`,
    `tipo_fuente`) son argumentos requeridos aquí también — un `TypeError` de
    Python al faltar uno es preferible a un INSERT silencioso con columnas a
    NULL. `estado` y `version_actual` llevan default de Python porque la DDL
    no les da default de tabla; `licencia` si se omite (`None`) se excluye
    del payload para que aplique el default de la DDL (`'comercializable'`).
    """
    _validar_tipo_fuente_chiste(tipo_fuente)
    _validar_estado_chiste(estado)

    payload: dict[str, Any] = {
        "texto_normalizado": texto_normalizado,
        "hash_normalizado": hash_normalizado,
        "tipo_fuente": tipo_fuente,
        "estado": estado,
        "version_actual": version_actual,
    }
    opcionales = {
        "embedding": embedding,
        "tema_id": tema_id,
        "tecnica_id": tecnica_id,
        "fuente_id": fuente_id,
        "chiste_origen_id": chiste_origen_id,
        "licencia": licencia,
    }
    for clave, valor in opcionales.items():
        if valor is not None:
            payload[clave] = valor
    return payload


def _build_chiste_update_payload(campos: dict) -> dict:
    """Construye el payload de UPDATE para `chistes` (`actualizar_chiste`).

    Rechaza (ValueError) cualquier clave fuera de
    `_COLUMNAS_CHISTE_ACTUALIZABLES` — protección directa contra inventar
    columnas o tocar `id`/`created_at` desde el cliente. Valida `tipo_fuente`
    y `estado` si están presentes, y añade siempre `updated_at`.
    """
    desconocidas = set(campos) - set(_COLUMNAS_CHISTE_ACTUALIZABLES)
    if desconocidas:
        raise ValueError(
            f"columnas no actualizables en 'chistes': {sorted(desconocidas)} "
            f"(permitidas: {', '.join(_COLUMNAS_CHISTE_ACTUALIZABLES)})"
        )
    if "tipo_fuente" in campos:
        _validar_tipo_fuente_chiste(campos["tipo_fuente"])
    if "estado" in campos:
        _validar_estado_chiste(campos["estado"])

    payload = dict(campos)
    payload["updated_at"] = _timestamp_actual()
    return payload


def _build_revision_payload(
    *,
    chiste_id: str,
    version: int,
    contenido: str,
    estructura_detectada: Optional[dict] = None,
    estado: Optional[str] = None,
    sugerencias_mejora: Optional[str] = None,
) -> dict:
    """Construye el payload de INSERT (append-only) para `chistes_revisiones`."""
    _validar_estado_chiste(estado)

    payload: dict[str, Any] = {
        "chiste_id": chiste_id,
        "version": version,
        "contenido": contenido,
    }
    if estructura_detectada is not None:
        payload["estructura_detectada"] = estructura_detectada
    if estado is not None:
        payload["estado"] = estado
    if sugerencias_mejora is not None:
        payload["sugerencias_mejora"] = sugerencias_mejora
    return payload


def _build_candidato_payload(
    *,
    tipo: str,
    texto: str,
    propuesto_por: Optional[str] = None,
    estado: str = "pendiente",
) -> dict:
    """Construye el payload de INSERT para `candidatos_taxonomia` (§Taxonomías).

    Se usa cuando el loop acotado de resolución de taxonomía (P16, ≤3
    intentos) agota los intentos sin encontrar un `tema_id`/`tecnica_id`
    existente — encola para revisión humana, nunca crea la fila de taxonomía
    directamente (regla de `SPEC.md` §Taxonomías).
    """
    _validar_tipo_candidato(tipo)
    _validar_estado_candidato(estado)

    payload: dict[str, Any] = {"tipo": tipo, "texto": texto, "estado": estado}
    if propuesto_por is not None:
        payload["propuesto_por"] = propuesto_por
    return payload


def _build_candidato_update_payload(estado: str) -> dict:
    """Construye el payload de UPDATE de `candidatos_taxonomia` (resolución humana)."""
    _validar_estado_candidato(estado)
    return {"estado": estado}


# ---------------------------------------------------------------------------
# Cliente Supabase — capa fina sobre supabase-py. Solo habla con tablas ya
# existentes (ver docstring del módulo); nunca ejecuta DDL.
# ---------------------------------------------------------------------------

def crear_cliente():
    """Crea un cliente `supabase-py` real desde `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`.

    Lanza `SupabaseStoreError` con un mensaje explícito si las variables de
    entorno no están configuradas — nunca intenta conectar con valores vacíos.
    """
    from supabase import create_client  # import perezoso: no requerido para lógica pura/tests unit

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SupabaseStoreError(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas en el entorno (.env). "
            "Revisa .env.example — son necesarias para instanciar SupabaseStore."
        )
    return create_client(url, key)


class SupabaseStore:
    """Cliente de acceso a las tablas del contrato B/C (`src/jokes/SPEC.md` §Storage).

    Inyecta `client` (cualquier objeto con la interfaz de `supabase-py`,
    típicamente un doble de test) para testear sin red; si se omite, crea un
    cliente real vía `crear_cliente()`.
    """

    def __init__(self, client: Optional[Any] = None):
        self.client = client if client is not None else crear_cliente()

    # -- chistes --------------------------------------------------------

    def crear_chiste(self, **kwargs) -> dict:
        payload = _build_chiste_payload(**kwargs)
        resultado = self.client.table("chistes").insert(payload).execute()
        return resultado.data[0]

    def obtener_chiste(self, chiste_id: str) -> Optional[dict]:
        resultado = (
            self.client.table("chistes").select("*").eq("id", chiste_id).execute()
        )
        filas = resultado.data
        return filas[0] if filas else None

    def actualizar_chiste(self, chiste_id: str, **campos) -> dict:
        payload = _build_chiste_update_payload(campos)
        resultado = (
            self.client.table("chistes").update(payload).eq("id", chiste_id).execute()
        )
        return resultado.data[0]

    # -- chistes_revisiones (append-only, §Versionado) -------------------

    def crear_revision(self, **kwargs) -> dict:
        payload = _build_revision_payload(**kwargs)
        resultado = self.client.table("chistes_revisiones").insert(payload).execute()
        return resultado.data[0]

    def listar_revisiones(self, chiste_id: str) -> list[dict]:
        resultado = (
            self.client.table("chistes_revisiones")
            .select("*")
            .eq("chiste_id", chiste_id)
            .order("version")
            .execute()
        )
        return resultado.data

    # -- taxonomías editables (temas, tecnicas, fuentes) -----------------

    def listar_temas(self) -> list[dict]:
        return self.client.table("temas").select("*").execute().data

    def crear_tema(self, nombre: str) -> dict:
        resultado = self.client.table("temas").insert({"nombre": nombre}).execute()
        return resultado.data[0]

    def listar_tecnicas(self) -> list[dict]:
        return self.client.table("tecnicas").select("*").execute().data

    def crear_tecnica(self, nombre: str) -> dict:
        resultado = self.client.table("tecnicas").insert({"nombre": nombre}).execute()
        return resultado.data[0]

    def listar_fuentes(self) -> list[dict]:
        return self.client.table("fuentes").select("*").execute().data

    def crear_fuente(
        self, nombre: str, tipo_fuente: Optional[str] = None, licencia: Optional[str] = None
    ) -> dict:
        payload: dict[str, Any] = {"nombre": nombre}
        if tipo_fuente is not None:
            payload["tipo_fuente"] = tipo_fuente
        if licencia is not None:
            payload["licencia"] = licencia
        resultado = self.client.table("fuentes").insert(payload).execute()
        return resultado.data[0]

    # -- candidatos_taxonomia (cola de revisión humana, §Taxonomías) -----

    def crear_candidato_taxonomia(self, **kwargs) -> dict:
        payload = _build_candidato_payload(**kwargs)
        resultado = self.client.table("candidatos_taxonomia").insert(payload).execute()
        return resultado.data[0]

    def listar_candidatos_taxonomia(self, estado: str = "pendiente") -> list[dict]:
        _validar_estado_candidato(estado)
        resultado = (
            self.client.table("candidatos_taxonomia")
            .select("*")
            .eq("estado", estado)
            .execute()
        )
        return resultado.data

    def actualizar_candidato_taxonomia(self, candidato_id: int, estado: str) -> dict:
        payload = _build_candidato_update_payload(estado)
        resultado = (
            self.client.table("candidatos_taxonomia")
            .update(payload)
            .eq("id", candidato_id)
            .execute()
        )
        return resultado.data[0]
