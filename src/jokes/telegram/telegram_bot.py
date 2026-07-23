"""telegram_bot — Flujo B: ingesta Bronze de chistes propios desde Telegram (task 16).

Contrato (`src/jokes/telegram/SPEC.md` §Bronze, §Pre-limpieza mínima): cada
mensaje de Telegram se persiste LITERAL en `chistes_telegram_bronze`
(inmutable, sagrado — equivalente a `/data/raw/` para teoría), con
idempotencia por evento vía `telegram_update_id` (`ON CONFLICT DO NOTHING`,
ver `SupabaseStore.guardar_mensaje_telegram_bronze`). Por separado, se
calcula una versión pre-limpia del texto (`trim`, normalización unicode,
strip de comandos de bot y menciones — SIN el Cleaner agresivo de teoría,
que nunca se aplica a `propio*`) lista para el siguiente paso del contrato
compartido (Silver, `src/jokes/silver.py`).

**Scope de esta task, explícito por título** ("Bronze inmutable,
idempotencia por telegram_update_id, pre-limpieza mínima"): este módulo
persiste el evento crudo y produce el texto pre-limpio — NO llama a Silver
ni a Reconciliación (tasks 13/15, ya implementadas pero su wiring a
Telegram es responsabilidad de quien orqueste el flujo completo, fuera de
este título de tarea) ni implementa la conexión real con la API de Telegram
(polling/webhook): `procesar_mensaje_telegram` recibe el `update` ya como
`dict` con la forma exacta del JSON de la Bot API de Telegram
(https://core.telegram.org/bots/api#update) — es el mismo shape tanto si
llega por webhook como si lo entrega cualquier librería de polling
(`Update.to_dict()` en `python-telegram-bot`, por ejemplo), así que no hace
falta ninguna dependencia del SDK de Telegram para esta lógica ni para sus
tests. Mismo reparto que `historico/loader.py` (task 18): lee/decide, no
implementa el mecanismo de entrada (ahí, el watcher de carpeta; aquí, el
transporte del bot).

Diseño para TDD sin red frágil (mismo patrón que el resto del contrato B/C):
`_extraer_datos_mensaje`/`limpiar_texto_telegram` son puras y sin red,
testeables directamente en `tests/unit/jokes/telegram/test_telegram_bot.py`.
`procesar_mensaje_telegram` orquesta contra un `SupabaseStore` inyectado
(doble de test en unit, real en
`tests/integration/test_telegram_bot_live.py`).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Pre-limpieza mínima (§Pre-limpieza mínima) — pura, sin red.
# ---------------------------------------------------------------------------

# Comando de bot al inicio del mensaje: "/chiste", "/chiste@MiBotName" (formato
# exacto de la Bot API para comandos, siempre al principio del texto).
_PATRON_COMANDO_BOT = re.compile(r"^/[a-zA-Z0-9_]+(?:@[A-Za-z0-9_]+)?\s*")

# Mención de usuario de Telegram: "@" + 5-32 caracteres alfanuméricos/"_"
# (regla de longitud de username de Telegram) — evita comerse un "@" suelto
# que no sea una mención real.
_PATRON_MENCION = re.compile(r"@[A-Za-z][A-Za-z0-9_]{4,31}\b")


def limpiar_texto_telegram(texto: str) -> str:
    """Pre-limpieza NO destructiva (§Pre-limpieza mínima): trim, unicode NFC,
    strip de comando de bot inicial y de menciones.

    Explícitamente NO hace nada más — nunca quita muletillas, nunca cambia
    may/minúsculas, nunca reescribe: son artefactos de PLATAFORMA (Telegram),
    no del CONTENIDO del chiste, la única distinción que le importa a este
    paso (el Cleaner agresivo de teoría, que sí toca contenido, nunca se
    aplica aquí — regla dura de `src/jokes/SPEC.md`).
    """
    if not texto:
        return ""
    normalizado = unicodedata.normalize("NFC", texto)
    sin_comando = _PATRON_COMANDO_BOT.sub("", normalizado, count=1)
    sin_mencion = _PATRON_MENCION.sub("", sin_comando)
    return sin_mencion.strip()


# ---------------------------------------------------------------------------
# Extracción de datos del Update de Telegram — pura, sin red.
# ---------------------------------------------------------------------------

def _extraer_datos_mensaje(update: dict) -> Optional[dict]:
    """Extrae `telegram_update_id`/`chat_id`/`texto_raw`/`timestamp_telegram`
    de un `update` con la forma del JSON de la Bot API de Telegram.

    Devuelve `None` (no un error) si el update no es un mensaje de texto
    nuevo — ej. updates de `callback_query`, stickers, o `edited_message`
    (fuera de scope de esta task: un mensaje editado no es un evento Bronze
    nuevo). Un bot real recibe muchos updates que no son "un chiste nuevo
    por texto"; ignorarlos en silencio aquí es el comportamiento correcto,
    no una condición de error.
    """
    mensaje = update.get("message")
    if not isinstance(mensaje, dict):
        return None

    texto_raw = mensaje.get("text")
    if not texto_raw:
        return None

    timestamp_telegram = None
    fecha_unix = mensaje.get("date")
    if fecha_unix is not None:
        timestamp_telegram = datetime.fromtimestamp(fecha_unix, tz=timezone.utc).isoformat()

    chat_id = None
    chat = mensaje.get("chat")
    if isinstance(chat, dict):
        chat_id = chat.get("id")

    return {
        "telegram_update_id": update.get("update_id"),
        "chat_id": chat_id,
        "texto_raw": texto_raw,
        "timestamp_telegram": timestamp_telegram,
    }


# ---------------------------------------------------------------------------
# Resultado de la orquestación.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResultadoProcesamiento:
    """Resultado de `procesar_mensaje_telegram` para un update que SÍ era un mensaje de texto.

    `fila_bronze` es la fila insertada en `chistes_telegram_bronze`, o
    `None` si `es_duplicado` (idempotencia por `telegram_update_id`: el
    evento ya se había guardado, Bronze no se toca — §Bronze). `texto_limpio`
    viaja siempre (incluso en duplicado) porque es barato de recalcular y
    determinista; quien wire el siguiente paso (Silver) decide si lo usa.
    """

    es_duplicado: bool
    texto_limpio: str
    fila_bronze: Optional[dict] = None


# ---------------------------------------------------------------------------
# Orquestación — persiste en Bronze (idempotente) vía `store` inyectado.
# ---------------------------------------------------------------------------

def procesar_mensaje_telegram(update: dict, store) -> Optional[ResultadoProcesamiento]:
    """Procesa UN update de Telegram: Bronze idempotente + pre-limpieza (§Bronze).

    Devuelve `None` si el update no es un mensaje de texto nuevo (ver
    `_extraer_datos_mensaje`) — no hay nada que persistir. `store` es
    cualquier objeto con la interfaz de `SupabaseStore`
    (`guardar_mensaje_telegram_bronze`), inyectable para testear sin red
    (`tests/unit/jokes/telegram/test_telegram_bot.py`); en producción es un
    `SupabaseStore` real (`tests/integration/test_telegram_bot_live.py`).
    """
    datos = _extraer_datos_mensaje(update)
    if datos is None:
        return None

    fila_bronze = store.guardar_mensaje_telegram_bronze(
        telegram_update_id=datos["telegram_update_id"],
        texto_raw=datos["texto_raw"],
        chat_id=datos["chat_id"],
        timestamp_telegram=datos["timestamp_telegram"],
    )

    return ResultadoProcesamiento(
        es_duplicado=fila_bronze is None,
        texto_limpio=limpiar_texto_telegram(datos["texto_raw"]),
        fila_bronze=fila_bronze,
    )
