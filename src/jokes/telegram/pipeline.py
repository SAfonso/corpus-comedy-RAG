"""pipeline — Flujo B (Telegram), módulo orquestador importable (task 35).

Contrato (`src/jokes/telegram/SPEC.md` §"Orquestación end-to-end",
§"Recuperación de fallos"; `src/jokes/SPEC.md` §Silver/§Taxonomías/
§Reconciliación/§Routing): encadena los componentes YA implementados y
testeados del contrato B/C, cada uno con su propia responsabilidad y sus
propios tests unitarios — este módulo NO reimplementa ninguna etapa, solo las
conecta:

    _extraer_datos_mensaje (telegram_bot, puro)
      -> allowlist de chat_id                                    (tuyo)
      -> procesar_mensaje_telegram (telegram_bot, Bronze + pre-limpieza)
    ── 200 OK ──────────────────────────────────────────────────────────────
      -> estructurar_chiste (Silver)
      -> resolver_taxonomia (tema + técnica, loop P16)
      -> reconciliar_chiste  (IGUAL/CAMBIADO/NUEVO, candidatos 'propio_historico')
      -> rutear_chiste (routing.py, tipo_fuente='propio')
      -> marcar_telegram_bronze_procesado                        (tuyo)

## Dos entradas públicas, no una función monolítica

`telegram/SPEC.md` §Orquestación fija dos tramos con timing y consumidores
distintos, y este módulo expone una función pública por tramo en vez de una
sola:

- `procesar_update_sincrono` — pasos 3-5 (extracción -> allowlist -> Bronze).
  Corre ANTES del `200`. Su único consumidor hoy es `telegram/webhook_app.py`
  (task 36), que agenda el tramo background en `BackgroundTasks` tras
  responder — pero esta función no sabe nada de HTTP ni de FastAPI, así que
  es igual de invocable desde un test sin red.
- `procesar_evento_background` — pasos 7-11 (Silver -> taxonomías ->
  reconciliación -> routing -> marcado de `procesado_at`). Corre DESPUÉS del
  `200`. Tiene DOS consumidores con puntos de entrada distintos que no deben
  rediseñar nada para reutilizarla:
  1. El webhook (task 36), sobre un evento recién aceptado en el mismo run.
  2. `scripts/reprocesar_bronze_pendiente.py` (task 47), sobre una fila de
     Bronze YA existente con `procesado_at IS NULL` — no vuelve a pasar por
     allowlist/Bronze (esos pasos ya ocurrieron cuando se insertó la fila),
     así que recibe directamente `texto_limpio` + el `id` de esa fila.

Separar las dos funciones es lo que hace posible que la task 47 reutilice el
tramo caro sin reimplementarlo y sin arrastrar la lógica de allowlist/Bronze,
que no le pertenece (mismo criterio de reuso que
`historico/pipeline.py::run_historico_pipeline` frente a sus etapas
inyectables).

## Orden fijo del tramo síncrono — extracción -> allowlist -> Bronze

La extracción (`telegram_bot._extraer_datos_mensaje`, privado de MÓDULO, no
de paquete — se reutiliza tal cual, nunca se reparsea el `Update` a mano)
precede a la allowlist solo porque hace falta el `chat_id` para evaluarla; es
pura, sin red ni escritura, así que en el momento del chequeo de allowlist
NADA ha tocado Supabase todavía — que es justo lo que ese control garantiza
(`telegram/SPEC.md` §"Allowlist de chat_id"). Solo si la allowlist autoriza
se llama a `telegram_bot.procesar_mensaje_telegram` (Bronze + pre-limpieza,
task 16, congelado). Un duplicado (`es_duplicado=True`, idempotencia por
`telegram_update_id`) NO agenda el tramo background — es el segundo control
de coste de la spec: un reenvío de Telegram nunca paga LLM dos veces.

## Alcance de los candidatos de reconciliación — 'propio_historico', no 'propio'

`src/jokes/SPEC.md` §"Obtención de candidatos" fija que un `propio` entrante
(Telegram) compara contra `propio_historico`: `TIPO_FUENTE_CANDIDATOS` es una
constante DISTINTA de `TIPO_FUENTE` (el valor de escritura) precisamente para
que este módulo nunca las confunda.

## `licencia` — no se pasa explícita

`rutear_chiste`/`crear_chiste` no reciben `licencia` como parámetro (ver sus
firmas en `routing.py`/`supabase_store.py`): la columna `chistes.licencia`
tiene `default 'comercializable'` en `schema.sql`, así que omitirla del
payload de INSERT ya produce el valor correcto (`_build_chiste_payload`
excluye del payload cualquier opcional no dado, dejando que la DDL aplique su
default) — no hace falta cablearla aquí.

## Errores del tramo background — nunca rompen al caller, nunca marcan a medias

`procesar_evento_background` captura cualquier excepción de los pasos 7-10 y
la devuelve como `ResultadoBackground(ok=False, error=...)` en vez de
propagarla: el caller (webhook tras el 200, o el script de reproceso) decide
qué hacer con el fallo (loguear, reintentar en el siguiente batch), pero
nunca ve una excepción sin capturar que pudiera derribar el proceso. El
propio `logging` del módulo deja constancia de la excepción completa
(`logger.exception`) ANTES de devolver el resultado. `procesado_at` solo se
marca si los pasos 7-10 completan enteros sin excepción — un fallo a mitad
deja la fila `NULL`, tal como exige `telegram/SPEC.md` §"Recuperación de
fallos": "no se intenta un UPDATE parcial".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.jokes.reconciliacion import ResultadoReconciliacion, reconciliar_chiste
from src.jokes.routing import ResultadoRuteo, rutear_chiste
from src.jokes.silver import ChisteEstructurado, estructurar_chiste
from src.jokes.taxonomias import ResultadoTaxonomia, resolver_taxonomia
from src.jokes.telegram.telegram_bot import (
    ResultadoProcesamiento,
    _extraer_datos_mensaje,
    procesar_mensaje_telegram,
)

logger = logging.getLogger(__name__)

# `tipo_fuente` de ESCRITURA de este flujo (enum cerrado en
# `supabase_store.TIPOS_FUENTE_CHISTE`, ver `src/jokes/SPEC.md` §Storage).
TIPO_FUENTE = "propio"

# `tipo_fuente` contra el que se comparan los candidatos de reconciliación
# (§"Obtención de candidatos" — DISTINTO del valor de escritura, ver docstring
# del módulo). NO usar TIPO_FUENTE aquí por error.
TIPO_FUENTE_CANDIDATOS = "propio_historico"

# Estados del tramo síncrono — mismo enum que `telegram/SPEC.md` §Contrato
# HTTP (task 32), consumido tal cual por `webhook_app.py` (task 36) para
# construir el body de la respuesta `200`.
ESTADOS_SINCRONOS = (
    "aceptado",
    "duplicado",
    "ignorado_no_texto",
    "ignorado_no_autorizado",
)


@dataclass(frozen=True)
class ResultadoSincrono:
    """Resultado de `procesar_update_sincrono` (pasos 3-5, tramo síncrono).

    - `estado`: uno de `ESTADOS_SINCRONOS` — observabilidad y base del body
      de respuesta HTTP que construye `webhook_app.py` (task 36).
    - `texto_limpio`: pre-limpio (§Pre-limpieza mínima), presente en
      `aceptado` y `duplicado` (barato de recalcular, ver
      `telegram_bot.ResultadoProcesamiento`); `None` en los dos estados
      `ignorado_*`, donde nunca se llegó a extraer/limpiar nada útil.
    - `fila_bronze_id`: `id` de la fila en `chistes_telegram_bronze`, SOLO en
      `aceptado` — es lo que el caller (webhook o script de reproceso) pasa a
      `procesar_evento_background` para agendar el tramo caro. `None` en
      cualquier otro estado (un duplicado no tiene fila NUEVA que agendar: su
      fila ya se procesó, o está pendiente, en un run anterior).
    """

    estado: str
    texto_limpio: Optional[str] = None
    fila_bronze_id: Optional[Any] = None


@dataclass(frozen=True)
class ResultadoBackground:
    """Resultado de `procesar_evento_background` (pasos 7-11, tramo caro).

    - `ok`: `True` si los pasos 7-10 completaron sin excepción Y `procesado_at`
      quedó marcado (paso 11). `False` si cualquier paso falló — la fila de
      Bronze queda `procesado_at IS NULL` para que el script de reproceso
      (task 47) la recoja.
    - `ruteo`: el `ResultadoRuteo` de `routing.py` (decisión + IDs), presente
      solo si `ok`.
    - `error`: `str(excepción)` del paso que falló, presente solo si `not ok`.
    """

    ok: bool
    ruteo: Optional[ResultadoRuteo] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Tramo síncrono (pasos 3-5) — corre ANTES del 200.
# ---------------------------------------------------------------------------

def procesar_update_sincrono(
    update: dict,
    store: Any,
    allowlist: frozenset,
) -> ResultadoSincrono:
    """Extracción -> allowlist de `chat_id` -> Bronze + pre-limpieza.

    Orden fijo (ver docstring del módulo, no reordenar): primero se extrae
    con `telegram_bot._extraer_datos_mensaje` (pura, sin red — reutilizada
    tal cual, nunca reparseada a mano); si el `update` no es un mensaje de
    texto nuevo, `ignorado_no_texto` sin tocar nada. Con el `chat_id` ya
    extraído se evalúa la allowlist ANTES de tocar Supabase: si no está en
    `allowlist`, `ignorado_no_autorizado` — Bronze nunca se llama. Solo si
    pasa ambos controles se invoca `telegram_bot.procesar_mensaje_telegram`
    (Bronze idempotente + pre-limpieza, task 16).

    `allowlist` es un `frozenset[int]` YA parseado — el parseo de
    `TELEGRAM_ALLOWED_CHAT_IDS` desde el entorno es responsabilidad del
    caller (`webhook_app.py`, task 36), no de este módulo.
    """
    datos = _extraer_datos_mensaje(update)
    if datos is None:
        return ResultadoSincrono(estado="ignorado_no_texto")

    if datos["chat_id"] not in allowlist:
        logger.warning(
            "Telegram: chat_id no autorizado (allowlist), update descartado sin tocar Supabase: %s",
            datos["chat_id"],
        )
        return ResultadoSincrono(estado="ignorado_no_autorizado")

    resultado: Optional[ResultadoProcesamiento] = procesar_mensaje_telegram(update, store)
    if resultado is None:
        # No debería ocurrir (ya sabemos que `datos` no es None con el mismo
        # `update`), pero se cubre por robustez sin volver a tocar Supabase.
        return ResultadoSincrono(estado="ignorado_no_texto")

    if resultado.es_duplicado:
        return ResultadoSincrono(estado="duplicado", texto_limpio=resultado.texto_limpio)

    fila_bronze_id = resultado.fila_bronze["id"] if resultado.fila_bronze else None
    return ResultadoSincrono(
        estado="aceptado",
        texto_limpio=resultado.texto_limpio,
        fila_bronze_id=fila_bronze_id,
    )


# ---------------------------------------------------------------------------
# Tramo background (pasos 7-11) — corre DESPUÉS del 200.
# ---------------------------------------------------------------------------

def procesar_evento_background(
    texto_limpio: str,
    fila_bronze_id: Any,
    store: Any,
    *,
    llamar_llm: Optional[Callable[[str, dict], dict]] = None,
    generar_embedding_fn: Optional[Callable[[str], list]] = None,
    estructurar_fn: Callable[..., ChisteEstructurado] = estructurar_chiste,
    resolver_taxonomia_fn: Callable[..., ResultadoTaxonomia] = resolver_taxonomia,
    reconciliar_fn: Callable[..., ResultadoReconciliacion] = reconciliar_chiste,
    rutear_fn: Callable[..., ResultadoRuteo] = rutear_chiste,
) -> ResultadoBackground:
    """Silver -> taxonomías -> reconciliación -> routing -> `procesado_at`.

    Reutilizable tal cual por el webhook (tras el `200`, agendado en
    `BackgroundTasks`, task 36) y por `scripts/reprocesar_bronze_pendiente.py`
    (task 47, sobre una fila de Bronze antigua con `procesado_at IS NULL`):
    ambos le pasan `texto_limpio` + el `id` de la fila Bronze y no necesitan
    saber nada de allowlist ni de Bronze, que ya ocurrieron aguas arriba.

    Cualquier excepción de los pasos 7-10 se captura y se devuelve como
    `ResultadoBackground(ok=False, error=...)` — NUNCA se propaga (rompería
    al caller del webhook, que ya respondió `200`) y NUNCA se llama a
    `marcar_telegram_bronze_procesado` en ese caso: la fila queda `NULL`,
    lista para el reproceso (§"Recuperación de fallos").

    Inyección (mismo patrón que `historico/pipeline.py`): `llamar_llm`/
    `generar_embedding_fn` viajan tal cual a las etapas LLM/embeddings;
    `estructurar_fn`/`resolver_taxonomia_fn`/`reconciliar_fn`/`rutear_fn` son
    las etapas mismas, inyectables para testear sin red (por defecto las
    reales, congeladas — este módulo no las reimplementa).
    """
    try:
        # Paso 7 — Silver: estructura el chiste (5 campos, §Silver).
        estructurado = estructurar_fn(texto_limpio, llamar_llm=llamar_llm)

        # Paso 8 — Taxonomías: mapea tema/técnica a IDs reales (loop P16, ≤3).
        res_tema = resolver_taxonomia_fn(
            estructurado.tema, "tema", store, llamar_llm=llamar_llm
        )
        res_tecnica = resolver_taxonomia_fn(
            estructurado.estructura_detectada, "tecnica", store, llamar_llm=llamar_llm
        )
        tema_id = res_tema.fila["id"] if res_tema.match and res_tema.fila else None
        tecnica_id = res_tecnica.fila["id"] if res_tecnica.match and res_tecnica.fila else None

        # Paso 9 — Reconciliación: candidatos SIEMPRE 'propio_historico'
        # (nunca TIPO_FUENTE='propio' — ver docstring del módulo).
        candidatos = store.listar_candidatos_reconciliacion(TIPO_FUENTE_CANDIDATOS)
        recon = reconciliar_fn(
            estructurado.chiste_normalizado,
            candidatos,
            generar_embedding_fn=generar_embedding_fn,
        )

        # Paso 10 — Routing a Supabase, tipo_fuente='propio' (Flujo B).
        ruteo = rutear_fn(
            store, estructurado, recon,
            tipo_fuente=TIPO_FUENTE, tema_id=tema_id, tecnica_id=tecnica_id,
        )
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de la cadena 7-10
        logger.exception(
            "Tramo background de Telegram falló (fila_bronze_id=%s); "
            "procesado_at queda NULL para reproceso.",
            fila_bronze_id,
        )
        return ResultadoBackground(ok=False, error=str(exc))

    # Paso 11 — SOLO si 7-10 completaron enteros sin excepción.
    store.marcar_telegram_bronze_procesado(fila_bronze_id)

    return ResultadoBackground(ok=True, ruteo=ruteo)
