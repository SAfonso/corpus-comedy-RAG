"""run_pipeline — CLI estable de Flujo A (Teoría) para invocación externa (task 23).

Contrato (título de la task 23, `src/theory/SPEC.md` §Storage/§Idempotencia y
versionado, `CHECKPOINTS.md`): wiring puro sobre dos componentes YA
implementados y aprobados — `src/theory/pipeline.run_pipeline` (task 22) y
`src/theory/ingest_teoria.ingestar_version` (task 21). Este módulo NO
reimplementa ninguna lógica de la cadena, solo la invoca y traduce su
resultado a un contrato de proceso estable (argv -> exit code + JSON).

**Por qué el contrato de CLI importa más de lo habitual**: este script no es
solo conveniencia interna — lo invoca por `subprocess` un proyecto RAG
externo sin acceso al código Python de este repo. Necesita exit codes
fiables y un resumen legible por máquina, no logs de texto libre.

## Mecanismo elegido para el resumen: JSON por stdout, o `--summary-out`

Por defecto el resumen (`construir_resumen`) se imprime como JSON por
**stdout** — es el único contenido que este script escribe por stdout, así
que un consumidor externo puede hacer `json.loads(stdout)` sin filtrar nada.
Si se pasa `--summary-out <ruta>`, el JSON se escribe en ese fichero en su
lugar (y NO se imprime por stdout). Todos los logs de progreso "humanos"
(cuántos ficheros se procesaron, si `--ingest` corrió, etc.) van siempre a
**stderr**, nunca a stdout, precisamente para no mezclarse con el JSON
elegido como canal del resumen (sea cual sea el mecanismo).

Esquema del resumen (siempre las mismas claves, ver `construir_resumen`):

    {
      "procesados": ["ruta/fichero.txt", ...],
      "fallidos": [{"path": "...", "error": "..."}, ...],
      "ignorados": ["ruta/fichero.sin_parser", ...],
      "version_dir": "data/processed/v3" | null,
      "ingesta": null | {
          "intentada": true,
          "ejecutada": true | false,
          # si ejecutada=true:
          "version_corpus": "v3", "num_nuevos": N, "num_duplicados": M,
          # si ejecutada=false:
          "motivo": "..." | "error": "..."
      }
    }

## Semántica de exit codes (`_exit_code`)

- `0`: éxito completo. Incluye el caso "no había nada pendiente que procesar"
  (`resultado.procesados`/`fallidos` vacíos, `version_dir=None`) — eso NO es
  un fallo, es un resultado esperado de un run sin trabajo pendiente
  (`ResultadoPipeline.ok` ya lo modela así, ver `pipeline.py`).
- `1`: el propio `run_pipeline` terminó con algún fichero en `fallidos`
  (`resultado.ok` es `False`). Tiene prioridad sobre el resultado de
  `--ingest`: si ambos fallan, el exit code es `1` (el JSON sigue llevando
  el detalle completo de ambos, el exit code es solo la señal binaria
  gruesa para quien no quiera parsear JSON).
- `2`: `--ingest` se pidió, el pipeline en sí no tuvo fallidos, pero
  `ingestar_version` lanzó una excepción (red/Supabase/formato).
- `3`: el propio `run_pipeline` lanzó una excepción inesperada (fuera de su
  contrato normal, que ya captura los fallos por fichero en `fallidos` — ver
  `pipeline.py`). Red de seguridad para no propagar un traceback críptico a
  un consumidor externo sin exit code fiable.

## `--ingest` cuando no había nada pendiente

Si `run_pipeline` no generó ninguna versión nueva (`version_dir=None`),
`--ingest` es **no-op sin error** (no se llama a `ingestar_version` sobre
una versión antigua) — este run no produjo nada nuevo que ingestar; el
resumen lo refleja con `ingesta.ejecutada=false` y un `motivo` explícito.

## Credenciales de Supabase — solo si `--ingest`

`TeoriaStore()` (vía `_crear_store_por_defecto`, importado perezosamente)
solo se instancia si `--ingest` está presente, así que un run sin `--ingest`
no requiere `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` configuradas.

## Tests

`tests/unit/scripts/test_run_pipeline_cli.py` inyecta `run_pipeline_fn`/
`ingestar_version_fn`/`crear_store_fn` de mentira — nunca llama a Supabase ni
a red real (mismo patrón que `test_ingest_teoria.py`, cuyas llamadas reales
están en `tests/integration/`, fuera de scope de esta task).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Optional

# Bootstrap de sys.path: permite invocar `python scripts/run_pipeline.py` (o
# vía subprocess con cualquier cwd) sin depender de PYTHONPATH ni de que el
# invocador esté en la raíz del repo — requisito de fiabilidad del "contrato
# externo" (ver docstring del módulo). `python -m scripts.run_pipeline` desde
# la raíz ya añade la raíz solo, esto cubre además la invocación directa por
# ruta de fichero, que es la forma más simple de invocar desde `subprocess`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.theory.ingest_teoria import ResultadoIngesta, ingestar_version  # noqa: E402
from src.theory.pipeline import ResultadoPipeline, run_pipeline  # noqa: E402

_PATRON_VERSION_DIR = re.compile(r"^v(\d+)$")


def _crear_store_por_defecto():
    """Crea un `TeoriaStore` real contra Supabase. Import perezoso: solo se
    ejecuta si `--ingest` está presente, para no exigir credenciales de
    Supabase en runs que no ingestan (ver docstring del módulo)."""
    from src.theory.teoria_store import TeoriaStore

    return TeoriaStore()


def _version_desde_dir(version_dir: Path) -> int:
    """Extrae `N` de un directorio `v{N}/` (mismo formato que emite
    `generar_version`, ver `format_normalizer.py`)."""
    match = _PATRON_VERSION_DIR.match(version_dir.name)
    if not match:
        raise ValueError(f"version_dir con nombre inesperado (no 'v{{N}}'): {version_dir.name!r}")
    return int(match.group(1))


def construir_resumen(resultado: ResultadoPipeline, ingesta: Optional[dict]) -> dict:
    """Construye el dict del resumen JSON (ver docstring del módulo, §Esquema).

    Función pura, sin I/O — separada de `main` para poder testear la forma
    del resumen sin pasar por argparse ni por stdout/fichero.
    """
    return {
        "procesados": [str(p) for p in resultado.procesados],
        "fallidos": [{"path": str(f.path), "error": f.error} for f in resultado.fallidos],
        "ignorados": [str(p) for p in resultado.ignorados],
        "version_dir": str(resultado.version_dir) if resultado.version_dir is not None else None,
        "ingesta": ingesta,
    }


def _exit_code(resultado: ResultadoPipeline, ingest_fallo: bool) -> int:
    """Mapea resultado -> exit code (ver docstring del módulo, §Semántica de exit codes)."""
    if not resultado.ok:
        return 1
    if ingest_fallo:
        return 2
    return 0


def _emitir_resumen(resumen: dict, summary_out: Optional[Path]) -> None:
    """Escribe el resumen JSON por stdout, o en `summary_out` si se indica
    (ver docstring del módulo, §Mecanismo elegido)."""
    texto = json.dumps(resumen, indent=2, ensure_ascii=False)
    if summary_out is not None:
        summary_out.parent.mkdir(parents=True, exist_ok=True)
        summary_out.write_text(texto, encoding="utf-8")
    else:
        print(texto)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description=(
            "CLI estable de Flujo A (Teoria): ejecuta run_pipeline "
            "(DriveMonitor -> Parser -> ... -> /data/processed/v{N}/) y, con "
            "--ingest, vuelca la version resultante a Supabase "
            "(ingestar_version). Contrato pensado para invocacion externa por "
            "subprocess (p.ej. desde un proyecto RAG sin acceso al codigo "
            "Python de este repo): el resumen se imprime como JSON por stdout "
            "(unico contenido de stdout) o se escribe en --summary-out si se "
            "indica; los logs de progreso van siempre a stderr. Exit codes: 0 "
            "= exito (incluye 'nada pendiente', que no es un fallo), 1 = algun "
            "fichero en 'fallidos', 2 = --ingest fallo (pipeline sin fallidos), "
            "3 = fallo fatal inesperado de run_pipeline."
        ),
    )
    parser.add_argument(
        "--carpeta",
        dest="carpetas",
        action="append",
        type=Path,
        default=None,
        help=(
            "Carpeta a vigilar (repetible: --carpeta a --carpeta b). Por "
            "defecto data/raw/books/ y data/raw/notes/ (ver "
            "src/theory/pipeline.py)."
        ),
    )
    parser.add_argument(
        "--directorio-procesado",
        dest="directorio_procesado",
        type=Path,
        default=None,
        help="Destino de v{N}/ (por defecto data/processed/).",
    )
    parser.add_argument(
        "--ruta-estado",
        dest="ruta_estado",
        type=Path,
        default=None,
        help=(
            "processed_files.json de idempotencia (por defecto "
            "data/state/processed_files.json)."
        ),
    )
    parser.add_argument(
        "--version",
        dest="version",
        type=int,
        default=None,
        help="Numero de version explicito para generar_version (por defecto, la siguiente libre).",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help=(
            "Tras un run con exito, ingesta la v{N}/ resultante en Supabase "
            "(ingestar_version, teoria_chunks). Si no habia nada pendiente "
            "(version_dir=None), no-op sin error (ver docstring del modulo)."
        ),
    )
    parser.add_argument(
        "--summary-out",
        dest="summary_out",
        type=Path,
        default=None,
        help=(
            "Ruta donde escribir el resumen JSON. Por defecto se imprime por "
            "stdout (unico contenido de stdout de este script; los logs de "
            "progreso van a stderr)."
        ),
    )
    return parser


def main(
    argv: Optional[list] = None,
    *,
    run_pipeline_fn: Callable[..., ResultadoPipeline] = run_pipeline,
    ingestar_version_fn: Callable[..., ResultadoIngesta] = ingestar_version,
    crear_store_fn: Callable[[], object] = _crear_store_por_defecto,
) -> int:
    """Entrada del CLI. `run_pipeline_fn`/`ingestar_version_fn`/`crear_store_fn`
    son inyectables (ver `tests/unit/scripts/test_run_pipeline_cli.py`); en
    producción son los componentes reales de Flujo A."""
    parser = _construir_parser()
    args = parser.parse_args(argv)

    try:
        resultado = run_pipeline_fn(
            args.carpetas,
            directorio_procesado=args.directorio_procesado,
            ruta_estado=args.ruta_estado,
            version=args.version,
        )
    except Exception as exc:  # noqa: BLE001 — red de seguridad, ver §Semántica de exit codes
        print(f"run_pipeline: fallo fatal antes de completar el run: {exc}", file=sys.stderr)
        resumen = construir_resumen(ResultadoPipeline(), None)
        resumen["error_fatal"] = str(exc)
        _emitir_resumen(resumen, args.summary_out)
        return 3

    print(
        f"run_pipeline: {len(resultado.procesados)} procesados, "
        f"{len(resultado.fallidos)} fallidos, {len(resultado.ignorados)} ignorados"
        + (f", version_dir={resultado.version_dir}" if resultado.version_dir else ""),
        file=sys.stderr,
    )

    ingesta_info: Optional[dict] = None
    ingest_fallo = False

    if args.ingest:
        if resultado.version_dir is None:
            ingesta_info = {
                "intentada": True,
                "ejecutada": False,
                "motivo": "no se genero ninguna version nueva en este run (nada pendiente)",
            }
            print("--ingest: no-op (nada pendiente en este run)", file=sys.stderr)
        else:
            try:
                store = crear_store_fn()
                version_num = _version_desde_dir(resultado.version_dir)
                resultado_ingesta = ingestar_version_fn(
                    resultado.version_dir.parent, store, version=version_num
                )
            except Exception as exc:  # noqa: BLE001
                ingest_fallo = True
                ingesta_info = {"intentada": True, "ejecutada": False, "error": str(exc)}
                print(f"--ingest: fallo: {exc}", file=sys.stderr)
            else:
                ingesta_info = {
                    "intentada": True,
                    "ejecutada": True,
                    "version_corpus": resultado_ingesta.version_corpus,
                    "num_nuevos": resultado_ingesta.num_nuevos,
                    "num_duplicados": resultado_ingesta.num_duplicados,
                }
                print(
                    f"--ingest: {resultado_ingesta.num_nuevos} nuevos, "
                    f"{resultado_ingesta.num_duplicados} duplicados "
                    f"({resultado_ingesta.version_corpus})",
                    file=sys.stderr,
                )

    resumen = construir_resumen(resultado, ingesta_info)
    _emitir_resumen(resumen, args.summary_out)

    return _exit_code(resultado, ingest_fallo)


if __name__ == "__main__":
    raise SystemExit(main())
