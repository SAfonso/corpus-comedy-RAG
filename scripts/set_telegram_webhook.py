"""set_telegram_webhook — registro one-shot re-ejecutable del webhook de
Telegram, Flujo B (task 37).

Contrato (`src/jokes/telegram/SPEC.md` §"Registro del webhook (setWebhook)",
`CHECKPOINTS.md`; mismo patrón de exit codes/resumen JSON que
`scripts/run_pipeline.py` (task 23) y `scripts/run_historico.py` (task 28)):
one-shot y re-ejecutable — se corre cada vez que cambia `TELEGRAM_WEBHOOK_URL`
o se rota `TELEGRAM_WEBHOOK_SECRET_TOKEN`, o para borrar el webhook
(`--delete`). No inventa idempotencia propia: `setWebhook` de la Bot API ya
sobrescribe el webhook anterior por diseño, así que correr este script dos
veces seguidas con la misma config no falla ni duplica nada.

## Modo por defecto — registro

Llama a `setWebhook` con `url`, `secret_token` y `allowed_updates=["message"]`
(fijo, no configurable — spec §Registro del webhook). Tras un `ok: true`,
llama a `getWebhookInfo` para **verificar** que el registro cuajó: la Bot API
puede aceptar un `setWebhook` (`ok: true`) sin que el estado resultante sea el
esperado, así que "aceptado por la API" y "verificado" son dos señales
distintas — este script solo declara éxito (exit 0) si ambas coinciden. Si
`setWebhook` ya devuelve `ok: false`, ni se intenta verificar: es un fallo
inmediato.

## `--delete`

Llama a `deleteWebhook` en vez de `setWebhook` (nunca ambos en la misma
ejecución). Se verifica igual con `getWebhookInfo`: la `url` devuelta debe
quedar vacía.

## Cliente HTTP inyectable — se reutiliza `httpx`, sin dependencia nueva

`httpx` ya está en `requirements.txt` desde la task 36 (lo usa el
`TestClient` de FastAPI/Starlette) — es la opción más simple ya disponible
para el único uso que hace falta aquí (`POST` síncrono simple contra la Bot
API), así que no se añade `requests`. El cliente real vive detrás de
`http_post_fn: Callable[[str, dict], dict]`, un **parámetro de función**, no
solo un import a nivel de módulo — los tests inyectan un doble que nunca
toca la red (mismo patrón de inyección que `run_pipeline_fn`/
`ingestar_version_fn` en `scripts/run_pipeline.py`).

## Exit codes

- `0`: éxito — el registro/borrado se verificó con `getWebhookInfo`.
- `1`: fallo — `setWebhook`/`deleteWebhook` devolvió `ok: false`, la
  verificación posterior con `getWebhookInfo` no coincide con lo esperado
  (registro "aceptado" pero no verificado), o cualquier excepción inesperada
  durante las llamadas a la API (red caída, timeout...).
- `2`: fallo de configuración — falta `TELEGRAM_BOT_TOKEN` en cualquier modo;
  en modo registro (sin `--delete`) además faltan `TELEGRAM_WEBHOOK_URL` o
  `TELEGRAM_WEBHOOK_SECRET_TOKEN`. Se comprueba ANTES de llamar a la API: un
  fallo de configuración no gasta ni una sola petición de red.

## Resumen JSON

Mismo mecanismo que `scripts/run_pipeline.py`/`scripts/run_historico.py`:
por defecto se imprime como JSON por **stdout** (único contenido de stdout de
este script); con `--summary-out <ruta>` se escribe ahí en su lugar y stdout
queda vacío. Los logs de progreso van siempre a **stderr**.

El `secret_token` NUNCA viaja en claro en el resumen (se enmascara con
`_enmascarar_secreto`) — el resumen puede acabar en logs de un consumidor
externo por `subprocess`, y no hay motivo para que el secreto compartido con
Telegram pase por ahí una segunda vez.

Esquema (siempre las mismas claves, ver `construir_resumen`):

    {
      "accion": "set" | "delete",
      "url_solicitada": "https://..." | null,
      "allowed_updates": ["message"] | null,
      "secret_token_enmascarado": "***abcd" | null,
      "resultado_api": {"ok": bool, ...} | null,
      "verificacion": null | {
          "ok": bool, "url_esperada": str, "url_obtenida": str
      },
      "error_configuracion": "..." | null,
      "error_fatal": "..." (solo si hubo excepción no capturada por el contrato normal)
    }

## Tests

`tests/unit/scripts/test_set_telegram_webhook.py` inyecta `http_post_fn` de
mentira que simula las respuestas de la Bot API (`{"ok": true, "result":
...}` / `{"ok": false, "description": ...}`) — nunca red real (misma
convención que el resto de `tests/unit/scripts/`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

# Bootstrap de sys.path: permite invocar `python scripts/set_telegram_webhook.py`
# (o vía subprocess con cualquier cwd) sin depender de PYTHONPATH ni de que el
# invocador esté en la raíz del repo — mismo requisito que
# `scripts/run_pipeline.py`/`scripts/run_historico.py`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

BOT_API_URL_TEMPLATE = "https://api.telegram.org/bot{token}/{metodo}"
ALLOWED_UPDATES = ["message"]

ENV_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_WEBHOOK_URL = "TELEGRAM_WEBHOOK_URL"
ENV_SECRET_TOKEN = "TELEGRAM_WEBHOOK_SECRET_TOKEN"


# ---------------------------------------------------------------------------
# Cliente HTTP por defecto — import perezoso de httpx (no se ejecuta en tests
# que inyectan `http_post_fn`, mismo patrón que `_crear_store_por_defecto` en
# `scripts/run_pipeline.py`).
# ---------------------------------------------------------------------------


def _crear_http_post_por_defecto() -> Callable[[str, dict], dict]:
    import httpx

    def _post(url: str, payload: dict) -> dict:
        respuesta = httpx.post(url, json=payload, timeout=10.0)
        respuesta.raise_for_status()
        return respuesta.json()

    return _post


def _llamar_bot_api(
    http_post_fn: Callable[[str, dict], dict], bot_token: str, metodo: str, payload: dict
) -> dict:
    url = BOT_API_URL_TEMPLATE.format(token=bot_token, metodo=metodo)
    return http_post_fn(url, payload)


def _enmascarar_secreto(secreto: Optional[str]) -> Optional[str]:
    """`***` + los últimos 4 caracteres (o `***` a secas si es más corto) —
    suficiente para que un humano reconozca "es el mismo secreto de siempre"
    en un log sin que el valor completo viaje en claro."""
    if not secreto:
        return None
    if len(secreto) <= 4:
        return "***"
    return f"***{secreto[-4:]}"


# ---------------------------------------------------------------------------
# Resumen JSON — función pura, sin I/O.
# ---------------------------------------------------------------------------


def construir_resumen(
    accion: str,
    *,
    url_solicitada: Optional[str] = None,
    allowed_updates: Optional[list] = None,
    secret_token: Optional[str] = None,
    resultado_api: Optional[dict] = None,
    verificacion: Optional[dict] = None,
    error_configuracion: Optional[str] = None,
) -> dict:
    """Construye el dict del resumen (ver docstring del módulo, §Resumen JSON).

    Función pura, separada de `main` para poder testear la forma del resumen
    sin pasar por argparse ni por stdout/fichero (mismo patrón que
    `construir_resumen` en `scripts/run_pipeline.py`)."""
    return {
        "accion": accion,
        "url_solicitada": url_solicitada,
        "allowed_updates": allowed_updates,
        "secret_token_enmascarado": _enmascarar_secreto(secret_token),
        "resultado_api": resultado_api,
        "verificacion": verificacion,
        "error_configuracion": error_configuracion,
    }


def _emitir_resumen(resumen: dict, summary_out: Optional[Path]) -> None:
    """Escribe el resumen JSON por stdout, o en `summary_out` si se indica
    (ver docstring del módulo, §Resumen JSON)."""
    texto = json.dumps(resumen, indent=2, ensure_ascii=False)
    if summary_out is not None:
        summary_out.parent.mkdir(parents=True, exist_ok=True)
        summary_out.write_text(texto, encoding="utf-8")
    else:
        print(texto)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="set_telegram_webhook.py",
        description=(
            "Registro one-shot re-ejecutable del webhook de Telegram (Flujo "
            "B, src/jokes/telegram/SPEC.md §Registro del webhook). Por "
            "defecto llama a setWebhook (url + secret_token + "
            "allowed_updates=['message']) y verifica con getWebhookInfo; "
            "con --delete llama a deleteWebhook en su lugar. El resumen se "
            "imprime como JSON por stdout (unico contenido de stdout) o se "
            "escribe en --summary-out si se indica; los logs de progreso "
            "van siempre a stderr. Exit codes: 0 = exito verificado, 1 = "
            "fallo (ok:false de la API, o verificacion no coincide, o "
            "excepcion inesperada), 2 = fallo de configuracion (variables "
            "de entorno ausentes), sin llamar a la API."
        ),
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Borra el webhook (deleteWebhook) en vez de registrarlo (setWebhook).",
    )
    parser.add_argument(
        "--bot-token",
        dest="bot_token",
        default=None,
        help=f"Token del bot (por defecto variable de entorno {ENV_BOT_TOKEN}).",
    )
    parser.add_argument(
        "--url",
        dest="url",
        default=None,
        help=(
            "URL publica completa del endpoint del webhook, con puerto y "
            f"ruta (por defecto variable de entorno {ENV_WEBHOOK_URL}). "
            "Solo hace falta en modo registro (sin --delete)."
        ),
    )
    parser.add_argument(
        "--secret-token",
        dest="secret_token",
        default=None,
        help=(
            "Secreto compartido con Telegram, 1-256 caracteres de "
            f"A-Za-z0-9_- (por defecto variable de entorno {ENV_SECRET_TOKEN}). "
            "Solo hace falta en modo registro (sin --delete)."
        ),
    )
    parser.add_argument(
        "--summary-out",
        dest="summary_out",
        type=Path,
        default=None,
        help=(
            "Ruta donde escribir el resumen JSON. Por defecto se imprime "
            "por stdout (unico contenido de stdout de este script; los "
            "logs de progreso van a stderr)."
        ),
    )
    return parser


def _resolver_config(args: argparse.Namespace) -> tuple:
    """Resuelve `(bot_token, url, secret_token, error_configuracion)`.

    `error_configuracion` es `None` si la config basta para la acción
    pedida; si no, describe qué falta y ningún llamador debe seguir adelante
    (exit 2, ver docstring del módulo)."""
    bot_token = args.bot_token or os.environ.get(ENV_BOT_TOKEN)
    if not bot_token:
        return None, None, None, f"{ENV_BOT_TOKEN} no configurado o vacío (ni --bot-token ni el entorno)."

    if args.delete:
        # En modo --delete, url/secret_token no hacen falta (ver docstring del módulo).
        return bot_token, None, None, None

    url = args.url or os.environ.get(ENV_WEBHOOK_URL)
    secret_token = args.secret_token or os.environ.get(ENV_SECRET_TOKEN)
    faltantes = []
    if not url:
        faltantes.append(f"{ENV_WEBHOOK_URL} (o --url)")
    if not secret_token:
        faltantes.append(f"{ENV_SECRET_TOKEN} (o --secret-token)")
    if faltantes:
        return None, None, None, "Falta configuración de registro: " + ", ".join(faltantes)

    return bot_token, url, secret_token, None


def main(
    argv: Optional[list] = None,
    *,
    http_post_fn: Optional[Callable[[str, dict], dict]] = None,
) -> int:
    """Entrada del CLI. `http_post_fn` es inyectable (ver
    `tests/unit/scripts/test_set_telegram_webhook.py`); en producción es un
    cliente `httpx` real (`_crear_http_post_por_defecto`)."""
    parser = _construir_parser()
    args = parser.parse_args(argv)
    accion = "delete" if args.delete else "set"

    bot_token, url, secret_token, error_configuracion = _resolver_config(args)
    if error_configuracion is not None:
        print(f"set_telegram_webhook: {error_configuracion}", file=sys.stderr)
        resumen = construir_resumen(accion, error_configuracion=error_configuracion)
        _emitir_resumen(resumen, args.summary_out)
        return 2

    http_post = http_post_fn if http_post_fn is not None else _crear_http_post_por_defecto()
    allowed_updates = list(ALLOWED_UPDATES) if accion == "set" else None

    try:
        if accion == "set":
            payload = {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": list(ALLOWED_UPDATES),
            }
            resultado_api = _llamar_bot_api(http_post, bot_token, "setWebhook", payload)
        else:
            resultado_api = _llamar_bot_api(http_post, bot_token, "deleteWebhook", {})
    except Exception as exc:  # noqa: BLE001 — red de seguridad, ver §Exit codes
        print(f"set_telegram_webhook: fallo de red en {accion}Webhook: {exc}", file=sys.stderr)
        resumen = construir_resumen(
            accion,
            url_solicitada=url,
            allowed_updates=allowed_updates,
            secret_token=secret_token,
        )
        resumen["error_fatal"] = str(exc)
        _emitir_resumen(resumen, args.summary_out)
        return 1

    if not resultado_api.get("ok"):
        print(
            f"set_telegram_webhook: {accion}Webhook devolvio ok=false: "
            f"{resultado_api.get('description')}",
            file=sys.stderr,
        )
        resumen = construir_resumen(
            accion,
            url_solicitada=url,
            allowed_updates=allowed_updates,
            secret_token=secret_token,
            resultado_api=resultado_api,
        )
        _emitir_resumen(resumen, args.summary_out)
        return 1

    print(f"set_telegram_webhook: {accion}Webhook ok=true, verificando con getWebhookInfo", file=sys.stderr)

    try:
        info = _llamar_bot_api(http_post, bot_token, "getWebhookInfo", {})
    except Exception as exc:  # noqa: BLE001
        print(f"set_telegram_webhook: fallo de red en getWebhookInfo: {exc}", file=sys.stderr)
        resumen = construir_resumen(
            accion,
            url_solicitada=url,
            allowed_updates=allowed_updates,
            secret_token=secret_token,
            resultado_api=resultado_api,
        )
        resumen["error_fatal"] = str(exc)
        _emitir_resumen(resumen, args.summary_out)
        return 1

    url_obtenida = info.get("result", {}).get("url", "") if info.get("ok") else ""
    url_esperada = url if accion == "set" else ""
    verificado = url_obtenida == url_esperada

    verificacion = {"ok": verificado, "url_esperada": url_esperada, "url_obtenida": url_obtenida}
    print(
        f"set_telegram_webhook: verificacion {'OK' if verificado else 'FALLIDA'} "
        f"(esperada={url_esperada!r}, obtenida={url_obtenida!r})",
        file=sys.stderr,
    )

    resumen = construir_resumen(
        accion,
        url_solicitada=url,
        allowed_updates=allowed_updates,
        secret_token=secret_token,
        resultado_api=resultado_api,
        verificacion=verificacion,
    )
    _emitir_resumen(resumen, args.summary_out)

    return 0 if verificado else 1


if __name__ == "__main__":
    raise SystemExit(main())
