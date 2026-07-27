"""reprocesar_bronze_pendiente — script de recuperación de fallos, Flujo B
(Telegram, task 47).

Contrato (`src/jokes/telegram/SPEC.md` §"Recuperación de fallos"; task 46,
que añadió la columna `procesado_at`; task 35, `telegram/pipeline.py`, ya
aprobado): el webhook responde `200` a Telegram ANTES del tramo caro
(Silver → taxonomías → reconciliación → routing, agendado en
`BackgroundTasks`). Si ese tramo falla o el proceso muere a mitad, Telegram
no reintenta (ya recibió su 2xx) y el mensaje queda en
`chistes_telegram_bronze` con `procesado_at IS NULL`, sin llegar nunca a
`chistes`. Este script recupera esos eventos: selecciona las filas
pendientes y las reprocesa a través de la MISMA función que usa el webhook
(`telegram/pipeline.py::procesar_evento_background`) — no reimplementa nada
de la cadena Silver/taxonomías/reconciliación/routing, solo alimenta el
bucle. Mismo motivo que `scripts/run_pipeline.py`/`scripts/run_historico.py`
para que el contrato de proceso (exit codes + resumen JSON) importe más de
lo habitual: pensado para invocación externa por `subprocess`.

## Por qué cada fila tiene su propio `try`

Un fallo en una fila NO debe abortar el resto del lote (mismo criterio que
`historico/pipeline.py` usa por documento: un fallo aislado se reintenta en
el siguiente run, no bloquea a los demás). Esto cubre DOS fuentes de fallo
distintas:

1. `procesar_evento_background` (pasos 7-10) ya captura sus propias
   excepciones y las devuelve como `ResultadoBackground(ok=False, error=...)`
   — ese caso se detecta comprobando `resultado.ok`, no hace falta capturar
   nada.
2. El paso 11 (`store.marcar_telegram_bronze_procesado`, dentro de
   `procesar_evento_background`) vive FUERA de ese try interno (ver
   `telegram/pipeline.py`): si esa llamada lanza (p.ej. un hipo de red justo
   al marcar `procesado_at`), la excepción sale sin capturar de
   `procesar_evento_background`. Por eso este script envuelve la llamada
   entera en su propio `try` por fila, no solo el chequeo de `resultado.ok`.

## Texto limpio recalculado, nunca el `texto_raw` crudo

Bronze solo guarda `texto_raw` (literal, sagrado — `CLAUDE.md`); el
`texto_limpio` que consume `procesar_evento_background` se recalcula aquí
con `telegram_bot.limpiar_texto_telegram` (pura, sin red, barata y
determinista — mismo criterio que ya documenta
`telegram_bot.ResultadoProcesamiento`), nunca se persiste ni se reutiliza de
ningún sitio: cada reproceso lo recalcula desde `texto_raw`.

## Ningún flag de coste/allowlist

Esos controles ya se evaluaron cuando el mensaje entró en Bronze la primera
vez (`telegram/pipeline.py::procesar_update_sincrono`, task 35): este script
solo reintenta el tramo caro sobre eventos que YA pasaron esos controles.

## Ausencia de reintentos automáticos

Este script es manual o programado aparte (fuera de este alcance, ver
`telegram/SPEC.md` §"Recuperación de fallos" — "Ausencia de reintentos
automáticos"): no instala ningún cron ni retry-loop.

## Mecanismo del resumen: JSON por stdout, o `--summary-out`

Mismo mecanismo que `scripts/run_pipeline.py`/`scripts/run_historico.py`/
`scripts/set_telegram_webhook.py`: por defecto el resumen se imprime como
JSON por **stdout** (único contenido de stdout de este script). Con
`--summary-out <ruta>` se escribe ahí en su lugar y stdout queda vacío. Los
logs de progreso van siempre a **stderr**.

Esquema (`construir_resumen`):

    {
      "pendientes_encontrados": int,
      "reprocesados_ok": int,
      "fallidos": [{"fila_bronze_id": ..., "error": "..."}, ...],
      "error_fatal": "..." | null
    }

## Semántica de exit codes

- `0`: éxito — todas las filas pendientes se reprocesaron con éxito. Incluye
  el caso "no había ninguna pendiente" (`pendientes_encontrados == 0`): no es
  un fallo, es un run sin trabajo pendiente.
- `1`: alguna fila falló (`ResultadoBackground.ok=False` en al menos una, o
  una excepción inesperada aislada por fila — ver arriba). Las demás filas sí
  se reprocesaron y se reportan como tal en el resumen.
- `2`: fallo fatal inesperado ANTES de poder iterar (p.ej. construir el
  `store` o `listar_telegram_bronze_pendientes()` lanza una excepción —
  problema de conexión a Supabase). Red de seguridad: nunca traceback crudo
  a un consumidor externo.

## Tests

`tests/unit/scripts/test_reprocesar_bronze_pendiente.py` inyecta
`crear_store_fn`/`procesar_evento_background_fn`/`limpiar_texto_fn` de
mentira — nunca red real (mismo patrón que
`tests/unit/scripts/test_run_pipeline_cli.py`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Bootstrap de sys.path: permite invocar `python scripts/reprocesar_bronze_pendiente.py`
# (o vía subprocess con cualquier cwd) sin depender de PYTHONPATH ni de que el
# invocador esté en la raíz del repo — mismo requisito que
# `scripts/run_pipeline.py`/`scripts/run_historico.py`/`scripts/set_telegram_webhook.py`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.jokes.telegram.pipeline import (  # noqa: E402
    ResultadoBackground,
    procesar_evento_background,
)
from src.jokes.telegram.telegram_bot import limpiar_texto_telegram  # noqa: E402


def _crear_store_por_defecto():
    """Crea un `SupabaseStore` real. Import perezoso: solo se ejecuta en
    producción, no en tests que inyectan `crear_store_fn` (mismo patrón que
    `_crear_store_por_defecto` en `scripts/run_pipeline.py`)."""
    from src.jokes.supabase_store import SupabaseStore

    return SupabaseStore()


# ---------------------------------------------------------------------------
# Resumen JSON — función pura, sin I/O.
# ---------------------------------------------------------------------------


def construir_resumen(
    pendientes_encontrados: int,
    reprocesados_ok: int,
    fallidos: list,
    error_fatal: Optional[str] = None,
) -> dict:
    """Construye el dict del resumen JSON (ver docstring del módulo,
    §Esquema). Función pura, separada de `main` para poder testear la forma
    del resumen sin pasar por argparse ni por stdout/fichero."""
    return {
        "pendientes_encontrados": pendientes_encontrados,
        "reprocesados_ok": reprocesados_ok,
        "fallidos": fallidos,
        "error_fatal": error_fatal,
    }


def _emitir_resumen(resumen: dict, summary_out: Optional[Path]) -> None:
    """Escribe el resumen JSON por stdout, o en `summary_out` si se indica
    (ver docstring del módulo, §Mecanismo del resumen)."""
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
        prog="reprocesar_bronze_pendiente.py",
        description=(
            "Script de recuperacion de fallos del Flujo B (Telegram, "
            "src/jokes/telegram/SPEC.md #Recuperacion de fallos): reprocesa "
            "las filas de chistes_telegram_bronze con procesado_at IS NULL "
            "a traves de la misma cadena que usa el webhook "
            "(telegram/pipeline.py::procesar_evento_background). El resumen "
            "se imprime como JSON por stdout (unico contenido de stdout) o "
            "se escribe en --summary-out si se indica; los logs de progreso "
            "van siempre a stderr. Exit codes: 0 = exito (incluye 'no habia "
            "ninguna pendiente'), 1 = alguna fila fallo, 2 = fallo fatal "
            "antes de poder iterar (p.ej. conexion a Supabase)."
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


def main(
    argv: Optional[list] = None,
    *,
    crear_store_fn: Callable[[], Any] = _crear_store_por_defecto,
    procesar_evento_background_fn: Callable[..., ResultadoBackground] = procesar_evento_background,
    limpiar_texto_fn: Callable[[str], str] = limpiar_texto_telegram,
) -> int:
    """Entrada del CLI. `crear_store_fn`/`procesar_evento_background_fn`/
    `limpiar_texto_fn` son inyectables (ver
    `tests/unit/scripts/test_reprocesar_bronze_pendiente.py`); en producción
    son los componentes reales del Flujo B."""
    parser = _construir_parser()
    args = parser.parse_args(argv)

    try:
        store = crear_store_fn()
        pendientes = store.listar_telegram_bronze_pendientes()
    except Exception as exc:  # noqa: BLE001 — red de seguridad, ver §Semántica de exit codes
        print(
            f"reprocesar_bronze_pendiente: fallo fatal antes de poder iterar: {exc}",
            file=sys.stderr,
        )
        resumen = construir_resumen(0, 0, [], error_fatal=str(exc))
        _emitir_resumen(resumen, args.summary_out)
        return 2

    print(
        f"reprocesar_bronze_pendiente: {len(pendientes)} pendientes encontrados",
        file=sys.stderr,
    )

    reprocesados_ok = 0
    fallidos: list = []

    for fila in pendientes:
        fila_bronze_id = fila["id"]
        try:
            texto_limpio = limpiar_texto_fn(fila["texto_raw"])
            resultado = procesar_evento_background_fn(texto_limpio, fila_bronze_id, store)
        except Exception as exc:  # noqa: BLE001 — un fallo aislado no aborta el lote
            fallidos.append({"fila_bronze_id": fila_bronze_id, "error": str(exc)})
            print(
                f"reprocesar_bronze_pendiente: fila {fila_bronze_id} fallo inesperado: {exc}",
                file=sys.stderr,
            )
            continue

        if resultado.ok:
            reprocesados_ok += 1
            print(
                f"reprocesar_bronze_pendiente: fila {fila_bronze_id} reprocesada OK",
                file=sys.stderr,
            )
        else:
            fallidos.append({"fila_bronze_id": fila_bronze_id, "error": resultado.error})
            print(
                f"reprocesar_bronze_pendiente: fila {fila_bronze_id} fallo: {resultado.error}",
                file=sys.stderr,
            )

    resumen = construir_resumen(len(pendientes), reprocesados_ok, fallidos)
    _emitir_resumen(resumen, args.summary_out)

    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
