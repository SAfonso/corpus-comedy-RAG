"""silver — estructuración por LLM del contrato compartido B/C (task 13).

Contrato (`src/jokes/SPEC.md` §Silver): dado el texto de UN chiste ya
segmentado (una unidad completa — a Silver le da igual si viene de Telegram,
Flujo B, o de Histórico, Flujo C; la única diferencia entre flujos es de
dónde sale ese texto, no cómo se estructura), el LLM (modelo barato vía API,
`docs/specs/llm-policy.md`) produce 5 campos:

- `tema`: tema del chiste (string libre; el mapeo a `tema_id` de la
  taxonomía de Supabase es responsabilidad de la task 14, no de este módulo).
- `estructura_detectada`: técnica detectada (setup/punchline, callback,
  misdirection… — string libre; el mapeo a `tecnica_id` tampoco es de aquí).
- `estado`: enum ESTRICTO `idea_suelta | con_estructura | rematado` — se
  reusa `ESTADOS_CHISTE` de `supabase_store.py` como fuente única del enum
  (no se duplica la tupla), aunque Silver NO llama a Supabase directamente
  (eso es `reconciliacion.py`/`supabase_store.py` — Silver solo produce la
  estructura, no la persiste).
- `sugerencias_mejora`: generativo, no clasificatorio — no existe un
  criterio externo de "es un buen chiste" (P16, `docs/specs/llm-policy.md`).
- `chiste_normalizado`: reescritura que CONSERVA el timing y NO elimina
  muletillas (es una normalización de forma, no una limpieza agresiva —
  contraste deliberado con el Cleaner de teoría, que nunca se aplica aquí).

**Sin loop de reintento**: `estructurar_chiste` llama al LLM una única vez
(vía `generar_json` de `src/utils/llm/client.py`, con salida JSON
estructurada — `responseSchema` — para no parsear texto libre a mano) y
devuelve el resultado tal cual, incluso si "podría mejorar" — eso es
justamente lo que P16 prohíbe reintentar sin criterio externo verificable
(ver `docs/specs/llm-policy.md`). Lo que SÍ es determinista y está aislado en
funciones puras testeables sin red (mismo patrón que
`_necesita_ocr_fallback`/`supabase_store._validar_estado_chiste`): la
validación del enum `estado`, la construcción del prompt/schema, y el
parseo de la respuesta ya deserializada a `dict`.

Decisión sobre el texto de entrada (fixture `Freskito-Informático.md`, task
17): `estructurar_chiste` asume que le llega texto YA sin las etiquetas
`[REMATE]`/`[CHISTOIDE]`/`[/REMATE]`/`[/CHISTOIDE]` (texto plano de un
chiste ya segmentado) — esas etiquetas son metadato de marcado del Flujo C
para decidir DÓNDE cortar (`scripts/marcar_remates.py`, `historico/SPEC.md`),
no parte del contenido del chiste en sí; dejarlas en el prompt sería filtrar
al LLM una pista de dónde está el remate en vez de dejar que lo detecte él
mismo como parte de `estructura_detectada`/`estado`. Por eso
`_limpiar_marcado_historico` las quita antes de construir el prompt — así
Silver funciona igual reciba texto de Telegram (que nunca las tiene) o de
Histórico (que si segmenta bien, ya las debería haber consumido antes de
llamar a Silver; esta función es un cinturón de seguridad, no el punto
oficial donde se procesan esas etiquetas).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from src.jokes.supabase_store import ESTADOS_CHISTE
from src.utils.llm.client import generar_json

# ---------------------------------------------------------------------------
# Contrato de campos (§Silver) — nombres exactos que persistirá aguas abajo
# `chistes_revisiones`/`chistes` (task 15/12), aunque Silver no persiste.
# ---------------------------------------------------------------------------

CAMPOS_SILVER = (
    "tema",
    "estructura_detectada",
    "estado",
    "sugerencias_mejora",
    "chiste_normalizado",
)


class SilverError(ValueError):
    """Error de estructuración Silver (enum inválido, respuesta incompleta).

    Hereda de `ValueError` (no de `RuntimeError`) a propósito: el criterio de
    validación de `estado` (§Silver) es "valor fuera del enum permitido", el
    mismo tipo de error que `supabase_store._validar_enum` — mantenerlo como
    `ValueError` permite capturarlo con el mismo patrón (`except ValueError`)
    usado en el resto del contrato B/C sin perder el tipo específico del
    módulo (un `except SilverError` sigue siendo posible y más preciso).
    """


@dataclass(frozen=True)
class ChisteEstructurado:
    """Resultado de `estructurar_chiste` — los 5 campos del contrato §Silver."""

    tema: str
    estructura_detectada: str
    estado: str
    sugerencias_mejora: str
    chiste_normalizado: str


# ---------------------------------------------------------------------------
# Limpieza de marcado de Histórico — pura, sin red.
# ---------------------------------------------------------------------------

_PATRON_ETIQUETAS_HISTORICO = re.compile(r"\[/?(?:REMATE|CHISTOIDE)\]")


def _limpiar_marcado_historico(texto: str) -> str:
    """Quita `[REMATE]`/`[/REMATE]`/`[CHISTOIDE]`/`[/CHISTOIDE]` si están.

    No-op si el texto no las tiene (caso Telegram) — seguro de llamar
    siempre, sin necesidad de que el caller sepa de qué flujo viene el texto.
    """
    return _PATRON_ETIQUETAS_HISTORICO.sub("", texto).strip()


# ---------------------------------------------------------------------------
# Validación del enum `estado` — pura, reusa la fuente única de verdad de
# `supabase_store.py` (no se duplica la tupla de valores permitidos).
# ---------------------------------------------------------------------------

def _validar_estado(estado: object) -> str:
    """Valida que `estado` sea uno de `ESTADOS_CHISTE`, nada más.

    Lanza `SilverError` (con el valor recibido y los permitidos) para
    cualquier otra cosa — incluye valores no-string, porque una respuesta de
    LLM mal formada podría, en teoría, devolver cualquier tipo en JSON.
    """
    if not isinstance(estado, str) or estado not in ESTADOS_CHISTE:
        raise SilverError(
            f"'estado' inválido en la respuesta del LLM: {estado!r} "
            f"(permitidos: {', '.join(ESTADOS_CHISTE)})"
        )
    return estado


# ---------------------------------------------------------------------------
# Construcción del prompt y del schema — puras, testeables sin red.
# ---------------------------------------------------------------------------

def _build_prompt(texto_chiste: str) -> str:
    """Construye el prompt para el LLM a partir del texto YA limpio del chiste.

    Instrucciones explícitas de conservar timing/muletillas en
    `chiste_normalizado` (§Silver) y de restringir `estado` al enum exacto,
    para reducir (no eliminar — sigue siendo generativo) la probabilidad de
    que el LLM devuelva un valor fuera de enum.
    """
    return (
        "Eres un analista de comedia (stand-up, en español). Analiza el "
        "siguiente chiste, ya completo y segmentado, y devuelve un JSON con "
        "exactamente estos campos:\n"
        "- tema: el tema del chiste (string breve).\n"
        "- estructura_detectada: la técnica usada (por ejemplo "
        "setup/punchline, callback, misdirection, rule of three...).\n"
        "- estado: EXACTAMENTE uno de estos tres valores literales: "
        f"{', '.join(ESTADOS_CHISTE)}.\n"
        "- sugerencias_mejora: una propuesta breve de mejora.\n"
        "- chiste_normalizado: una reescritura del chiste que CONSERVE el "
        "timing y las muletillas originales (no es una limpieza agresiva, "
        "es solo una normalización de forma).\n\n"
        f"Chiste:\n{texto_chiste}"
    )


def _build_schema() -> dict:
    """`responseSchema` de Gemini (§Silver) — mayúsculas por convención de la API.

    `estado` lleva `enum` explícito en el propio schema (además de la
    validación posterior en `_validar_estado`) — pedir el enum en el schema
    reduce la probabilidad de que el LLM devuelva un valor fuera de rango,
    pero no la garantiza (sigue siendo generativo), por eso la validación
    posterior no es opcional ni redundante: es el criterio de parada externo
    real, el schema es solo una ayuda para el modelo.
    """
    return {
        "type": "OBJECT",
        "properties": {
            "tema": {"type": "STRING"},
            "estructura_detectada": {"type": "STRING"},
            "estado": {"type": "STRING", "enum": list(ESTADOS_CHISTE)},
            "sugerencias_mejora": {"type": "STRING"},
            "chiste_normalizado": {"type": "STRING"},
        },
        "required": list(CAMPOS_SILVER),
    }


# ---------------------------------------------------------------------------
# Parseo de la respuesta ya deserializada (dict) — pura, sin red.
# ---------------------------------------------------------------------------

def _parsear_respuesta(respuesta: dict) -> ChisteEstructurado:
    """Valida que `respuesta` tenga los 5 campos del contrato y los tipos.

    Lanza `SilverError` si falta cualquier campo o si alguno de los campos
    string no es `str` (defensivo ante una respuesta de LLM mal formada,
    aunque el `responseSchema` ya debería garantizarlo la mayoría de las
    veces). La validación de `estado` delega en `_validar_estado`.
    """
    faltantes = [campo for campo in CAMPOS_SILVER if campo not in respuesta]
    if faltantes:
        raise SilverError(
            f"Respuesta del LLM incompleta, faltan campos: {faltantes} "
            f"(recibido: {sorted(respuesta.keys())})"
        )

    for campo in ("tema", "estructura_detectada", "sugerencias_mejora", "chiste_normalizado"):
        if not isinstance(respuesta[campo], str):
            raise SilverError(
                f"'{campo}' debe ser string, recibido {type(respuesta[campo]).__name__}"
            )

    estado = _validar_estado(respuesta["estado"])

    return ChisteEstructurado(
        tema=respuesta["tema"],
        estructura_detectada=respuesta["estructura_detectada"],
        estado=estado,
        sugerencias_mejora=respuesta["sugerencias_mejora"],
        chiste_normalizado=respuesta["chiste_normalizado"],
    )


# ---------------------------------------------------------------------------
# Orquestación — única función pública que hace la llamada real (inyectable
# para test sin red). Una sola llamada, sin loop de reintento (P16).
# ---------------------------------------------------------------------------

def estructurar_chiste(
    texto_chiste: str,
    *,
    llamar_llm: Optional[Callable[[str, dict], dict]] = None,
) -> ChisteEstructurado:
    """Estructura UN chiste ya segmentado vía LLM (§Silver), una sola vez.

    `texto_chiste` puede venir con o sin etiquetas `[REMATE]`/`[CHISTOIDE]`
    de Histórico (se limpian antes de construir el prompt, ver docstring del
    módulo) — Telegram nunca las tiene, así que esto es un no-op en ese caso.

    `llamar_llm` es inyectable (`(prompt, schema) -> dict`) para testear la
    orquestación sin red (`tests/unit/jokes/test_silver.py`); por defecto usa
    `generar_json` de `src/utils/llm/client.py` contra la API real de Gemini
    (`tests/integration/test_silver_live.py`). No hay reintento: si
    `llamar_llm` falla o la respuesta no valida, se propaga la excepción tal
    cual — no se reintenta buscando que el LLM "mejore" su respuesta (P16).
    """
    if not texto_chiste or not texto_chiste.strip():
        raise SilverError("texto_chiste vacío: no hay nada que estructurar")

    texto_limpio = _limpiar_marcado_historico(texto_chiste)
    prompt = _build_prompt(texto_limpio)
    schema = _build_schema()

    llamar = llamar_llm if llamar_llm is not None else generar_json
    respuesta = llamar(prompt, schema)

    return _parsear_respuesta(respuesta)
