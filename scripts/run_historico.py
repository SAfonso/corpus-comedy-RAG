"""run_historico — CLI estable de Flujo C (Histórico) para invocación externa (task 28).

Contrato (título de la task 28, `src/jokes/historico/SPEC.md` §"Coste",
`src/jokes/historico/coste.py` (task 26), `src/jokes/historico/pipeline.py`
(task 27), `CHECKPOINTS.md`): wiring puro sobre dos componentes YA
implementados y aprobados — `historico.coste.estimar_y_evaluar` (task 26) y
`historico.pipeline.run_historico_pipeline` (task 27). Este módulo NO
reimplementa ninguna lógica de esas etapas: evalúa el gate de coste ANTES de
gastar dinero de verdad y, si lo permite (y no se pidió `--dry-run`), invoca
el orquestador completo. Mismo motivo que `scripts/run_pipeline.py` (Flujo A,
task 23) para que el contrato de proceso importe más de lo habitual: lo
invoca por `subprocess` un proyecto RAG externo sin acceso al código Python
de este repo.

## El reto de diseño: cómo conseguir `documentos` para el gate sin duplicar
## `run_historico_pipeline`

`estimar_y_evaluar` (task 26) necesita `documentos: list[{"name", "content"}]`
— la MISMA forma que devuelve `Loader.load()` — pero esa lista solo existe
DESPUÉS de las etapas 0->0b->1->2 (`DriveSource.sync_con_metadata()` ->
captura Bronze -> `marcar_remates.procesar_docx` -> `Loader.load()`), que hoy
viven DENTRO de `run_historico_pipeline` (task 27). Este script ejecuta él
mismo esas etapas previas (`_documentos_pendientes_para_gate`, delegando 0/0b/1
en `sincronizar_y_marcar`, `src/jokes/historico/pipeline.py`, task 64 — la
MISMA función que usa `run_historico_pipeline` por dentro, ver su docstring
§"Punto de enganche exacto") para poder evaluar el gate ANTES de decidir si
merece la pena correr el resto de la cadena (Segmentador/Silver/taxonomía/
reconciliación/Supabase, que sí son caras). Si el gate permite y no hay
`--dry-run`, se invoca `run_historico_pipeline`, que VUELVE a ejecutar esas
mismas etapas por dentro (vía la misma `sincronizar_y_marcar`).

Dos precauciones para que esa doble pasada sea realmente inofensiva (no solo
"trabajo repetido en local", sino sin efectos secundarios que rompan el run
real):

1. **Se reutilizan las MISMAS instancias de `drive_source` y `document_store`**
   (nunca se construye una segunda de cada) entre el paso de gate y la llamada
   a `run_historico_pipeline`. Así el segundo `sync_con_metadata()` (interno a
   `run_historico_pipeline`, vía `sincronizar_y_marcar`) ve el estado de Drive
   ya comprometido por el primero y no repite descargas, capturas Bronze ni
   pierde de vista qué `.docx` hay que reintentar si la captura o
   `marcar_remates` fallaron en el paso de gate (ver punto 2) — evita además
   una segunda ronda de llamadas a la API de Drive/Supabase con instancias
   distintas que podrían (en teoría) divergir en `staging_dir`/credenciales si
   algún día un caller pasara valores distintos por error. En la práctica,
   `sincronizar_y_marcar` ni siquiera vuelve a tocar `document_store` en esa
   segunda pasada: `sync_con_metadata()` ya devuelve vacío.
2. **El estado del `Loader` (`loader_state_path`) se revierte tras leerlo**
   en el paso de gate (`_documentos_pendientes_para_gate`), exactamente el
   mismo truco de "escritura prematura + revert" que ya usa
   `run_historico_pipeline` internamente para su propio problema equivalente
   (ver `src/jokes/historico/pipeline.py`, comentario "Reanudación por
   documento"). Es IMPRESCINDIBLE: `Loader.load()` (a diferencia de
   `DriveSource.sync()`, que ya es idempotente sin ayuda) escribe su hash MD5
   en `state_path` de forma incondicional en cuanto lee un documento nuevo, no
   solo cuando ese documento termina de procesarse. Si este script llamara a
   `Loader.load()` para construir `documentos` sin revertir esa escritura, el
   `Loader.load()` INTERNO de `run_historico_pipeline` (el que de verdad
   decide qué se procesa) encontraría el MD5 ya registrado y devolvería una
   lista vacía — el gate habría dicho "adelante" pero el run real no
   procesaría NADA, un fallo silencioso. Revertir el fichero de estado a como
   estaba antes de este `Loader.load()` de más hace que la llamada interna de
   `run_historico_pipeline` vea exactamente los mismos documentos pendientes
   que evaluó el gate.

Con estas dos precauciones, la doble pasada es segura: `DriveSource` no
re-descarga nada (metadata sin cambios), `marcar_remates` no se re-ejecuta
sobre los mismos `.docx` (el segundo `.sync()` no los vuelve a "stagear",
así que el bucle de `marcar_remates` de `run_historico_pipeline` ni siquiera
itera sobre ellos — el `.md` ya escrito en el paso de gate se queda tal cual
en `carpeta_md`, que es caché reconstruible, no material sagrado) y `Loader`
ve el mismo hueco pendiente que ya vio el gate.

**Fallos de captura Bronze (0b) o de marcado (`marcar_remates`, 1) detectados
durante el paso de gate** (`marcado_fallidos_gate`) por tanto NO reaparecen en
`resultado.marcado_fallidos` de la llamada real a `run_historico_pipeline`
(ese `.docx` ya no vuelve a "stagearse" en el segundo `sync_con_metadata()`,
así que `sincronizar_y_marcar` ni siquiera itera sobre él) — este script los combina en el
resumen final (`marcado_fallidos_gate` + `pipeline.marcado_fallidos`) para no
perder visibilidad, y los cuenta como fallo (exit 1) cuando se ejecuta el run
real (ver §Semántica de exit codes). En un `--dry-run` NO cuentan para el
exit code (ver más abajo, es una decisión deliberada).

## Mecanismo del resumen: JSON por stdout, o `--summary-out`

Mismo mecanismo que `scripts/run_pipeline.py`: por defecto el resumen se
imprime como JSON por **stdout** (único contenido de stdout de este script).
Con `--summary-out <ruta>` se escribe ahí en su lugar y stdout queda vacío.
Los logs de progreso van siempre a **stderr**.

Esquema del resumen (`construir_resumen`):

    {
      "gate": null | {
          "permitir": bool, "razon": str,
          "umbral_tokens": int, "umbral_eur": float | null,
          "estimacion": {"documentos": int, "llamadas_segmentador": int,
                         "llamadas_silver": int, "tokens_segmentador": int,
                         "tokens_silver": int, "tokens_totales": int,
                         "coste_eur_estimado": float}
      },
      "dry_run": bool,
      "marcado_fallidos_gate": [{"docx": "...", "error": "..."}, ...],
      "pipeline": null | {
          "descargados": [...], "procesados": [...],
          "fallidos": [{"name": "...", "error": "..."}, ...],
          "marcado_fallidos": [{"docx": "...", "error": "..."}, ...],
          "chistes_ruteados": [{"chiste_id", "decision", "tema_id",
                                 "tecnica_id", "inicio_localizado"}, ...]
      },
      "error_fatal": "..." (solo si hubo excepción no capturada por el contrato normal)
    }

`gate` es `null` solo si una excepción impidió siquiera evaluarlo (ver
`error_fatal`). `pipeline` es `null` si no se llegó a invocar
`run_historico_pipeline` (gate bloqueó, o `--dry-run`).

## Semántica de exit codes (título de la task 28: 0/1/2)

- `0`: éxito. Incluye `--dry-run` con el gate en verde (el propósito de
  `--dry-run` es justo reportar la decisión del gate sin correr nada más; ver
  más abajo por qué NO se pliegan ahí los `marcado_fallidos_gate`) y un run
  real sin ningún documento/`.docx` fallido.
- `1`: fallo. Un run real (no `--dry-run`) con algún documento fallido en
  `run_historico_pipeline` (`resultado.ok` es `False`, que ya incluye tanto
  `resultado.fallidos` como `resultado.marcado_fallidos`), o combinado con
  `marcado_fallidos_gate` no vacío (ver §El reto de diseño), o cualquier
  excepción inesperada en cualquier etapa (construcción de `drive_source`,
  `.sync()`, `marcar_remates`, `Loader.load()`, el gate, o el propio
  `run_historico_pipeline`).
- `2`: abort por coste. El gate de la task 26 no permitió el run
  (`decision.permitir is False`) — se aplica IGUAL con o sin `--dry-run`
  (nunca se ejecuta `run_historico_pipeline` en este caso). Tiene prioridad
  sobre cualquier otra señal: si el gate bloquea, ni siquiera se mira
  `marcado_fallidos_gate`.

### Por qué `--dry-run` no pliega `marcado_fallidos_gate` en el exit code

`--dry-run` existe para responder una única pregunta ("¿el gate deja pasar
este volumen?") sin tocar Segmentador/Silver/Supabase. Un fallo de marcado
detectado de camino (efecto colateral de tener que materializar `documentos`
para poder estimar coste, ver §El reto de diseño) es una señal real, pero
mezclarla en el exit code de `--dry-run` overloadearía una bandera pensada
para UNA sola pregunta binaria (coste sí/no). Se reporta en el JSON siempre
(visibilidad completa para el consumidor externo); solo afecta al exit code
cuando de verdad se ejecuta la cadena completa (no `--dry-run`), que es
cuando "fallo" tiene el mismo significado que en el resto del contrato de
`ResultadoHistorico.ok`.

## Credenciales

Este script SIEMPRE necesita acceso a Drive (vía `crear_drive_source_fn`,
`GOOGLE_APPLICATION_CREDENTIALS` por defecto) porque el gate necesita
contenido real de documentos — a diferencia de `--ingest` en
`scripts/run_pipeline.py`, aquí no hay forma de evaluar el gate sin
sincronizar Drive primero.

Desde la task 64, también necesita **siempre** credenciales de Supabase
(`SUPABASE_URL`/`SUPABASE_SERVICE_KEY`, vía `crear_document_store_fn`) para la
captura Bronze (etapa 0b) — a diferencia del `store` de routing (chistes),
que sigue construyéndose perezosamente dentro de `run_historico_pipeline` y
solo hace falta si el gate permite el run real. `document_store` se construye
ANTES de saber si hay algo que capturar (mismo criterio eager que
`drive_source`), así que es necesario incluso en `--dry-run`: la captura
Bronze ocurre en el paso de gate, no después (`SPEC.md`, "La captura ocurre
también en `--dry-run`").

## Tests

`tests/unit/scripts/test_run_historico_cli.py` inyecta `run_historico_pipeline_fn`,
`estimar_y_evaluar_fn`, `crear_drive_source_fn`, `crear_document_store_fn` y
`procesar_docx_fn` de mentira (mismo patrón que `test_run_pipeline_cli.py`) —
nunca red real. Un caso usa el fixture real
`tests/fixtures/Freskito-Informático.docx` con `marcar_remates.procesar_docx`
REAL (puro, sin red) para verificar que
`documentos` llega al gate con el marcado `[REMATE]` real.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Bootstrap de sys.path: permite invocar `python scripts/run_historico.py` (o
# vía subprocess con cualquier cwd) sin depender de PYTHONPATH ni de que el
# invocador esté en la raíz del repo (ver docstring del módulo, mismo
# requisito que `scripts/run_pipeline.py`).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.marcar_remates import procesar_docx  # noqa: E402
from src.jokes.historico.coste import DecisionGate, estimar_y_evaluar  # noqa: E402
from src.jokes.historico.loader import Loader  # noqa: E402
from src.jokes.historico.pipeline import (  # noqa: E402
    CARPETA_MD_POR_DEFECTO,
    DRIVE_STATE_POR_DEFECTO,
    ENV_FOLDER_ID,
    LOADER_STATE_POR_DEFECTO,
    STAGING_DIR_POR_DEFECTO,
    FalloMarcado,
    ResultadoHistorico,
    run_historico_pipeline,
    sincronizar_y_marcar,
)


# ---------------------------------------------------------------------------
# Construcción por defecto de `drive_source` — import perezoso (no exige
# `google-api-python-client` en tests que inyectan `crear_drive_source_fn`,
# mismo patrón que `_crear_store_por_defecto` en `scripts/run_pipeline.py`).
# ---------------------------------------------------------------------------


def _crear_drive_source_por_defecto(
    *,
    folder_id: Optional[str],
    staging_dir: Path,
    state_path: Path,
    credentials_path: Optional[Path],
):
    if not folder_id:
        raise RuntimeError(
            "run_historico sin folder_id: pásalo con --folder-id o define "
            f"{ENV_FOLDER_ID} (carpeta de Drive del histórico)."
        )
    from src.jokes.historico.drive_source import DriveSource

    return DriveSource(
        folder_id=folder_id,
        staging_dir=staging_dir,
        state_path=state_path,
        credentials_path=credentials_path,
    )


def _crear_document_store_por_defecto() -> Any:
    """Construye un `DocumentStore` real (captura Bronze, etapa 0b, task 64).

    Import perezoso, mismo patrón que `_crear_drive_source_por_defecto`: no
    exige `supabase-py`/credenciales en tests que inyectan
    `crear_document_store_fn`. Se invoca de forma EAGER en `main()` (igual que
    `crear_drive_source_fn`), antes de saber si `sync_con_metadata()` va a
    devolver algo — por eso requiere `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`
    para CUALQUIER invocación del CLI (incluido `--dry-run`), no solo cuando
    el gate permite el run real: la captura ocurre en el paso de gate, no
    después (`SPEC.md`, "La captura ocurre también en --dry-run"). Es una
    ampliación deliberada de las credenciales que este script siempre
    necesita — ver docstring del módulo, §Credenciales."""
    from src.utils.document_store import DocumentStore

    return DocumentStore()


# ---------------------------------------------------------------------------
# Estado del Loader — JSON `{name: {"md5": ...}}`, mismo contrato que
# `Loader`/`historico/pipeline.py`. Reimplementado aquí (no importado, son
# métodos privados de otra clase) — mismo criterio documentado en
# `src/jokes/historico/pipeline.py` ("Estado de idempotencia del Loader").
# ---------------------------------------------------------------------------


def _cargar_estado_loader(ruta_estado: Path) -> dict:
    if not ruta_estado.exists():
        return {}
    return json.loads(ruta_estado.read_text(encoding="utf-8"))


def _guardar_estado_loader(ruta_estado: Path, estado: dict) -> None:
    ruta_estado.parent.mkdir(parents=True, exist_ok=True)
    ruta_estado.write_text(
        json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Etapas 0->0b->1->2 (DriveSource -> captura Bronze -> marcar_remates ->
# Loader), ejecutadas por este script para poder materializar `documentos`
# ANTES del gate (ver docstring del módulo, §El reto de diseño). Etapas
# 0/0b/1 delegan en `sincronizar_y_marcar` (única implementación, task 64).
# Revierte la escritura prematura del `Loader` para no interferir con la
# llamada real a `run_historico_pipeline`.
# ---------------------------------------------------------------------------


def _documentos_pendientes_para_gate(
    drive_source: Any,
    carpeta_md: Path,
    loader_state_path: Path,
    *,
    document_store: Any,
    procesar_docx_fn: Callable[..., tuple] = procesar_docx,
) -> tuple[list[dict], list[FalloMarcado]]:
    """Ejecuta las etapas 0->0b->1 y 2 del Flujo C y devuelve
    `(documentos, marcado_fallidos)`.

    Etapas 0 (DriveSource) -> 0b (captura Bronze) -> 1 (marcar_remates) viven
    en `sincronizar_y_marcar` (`src/jokes/historico/pipeline.py`, task 64) —
    la MISMA función que usa `run_historico_pipeline` por dentro (ver
    docstring del módulo, §El reto de diseño y §"Punto de enganche exacto" de
    `SPEC.md`): esta es la pasada que sincroniza y captura de verdad en el uso
    real por CLI.

    `documentos` tiene la forma que consume `estimar_y_evaluar`
    (`list[{"name", "content"}]`). El estado del `Loader` se revierte tras
    leerlo (ver docstring del módulo, §El reto de diseño, punto 2) — el
    fichero en `loader_state_path` queda exactamente como estaba ANTES de
    esta llamada.
    """
    carpeta_md.mkdir(parents=True, exist_ok=True)

    _, marcado_fallidos = sincronizar_y_marcar(
        drive_source,
        carpeta_md,
        document_store=document_store,
        procesar_docx_fn=procesar_docx_fn,
    )

    estado_previo = _cargar_estado_loader(loader_state_path)
    loader = Loader(folder=carpeta_md, state_path=loader_state_path)
    documentos = loader.load()  # escribe MD5 en loader_state_path (prematuro)
    _guardar_estado_loader(loader_state_path, estado_previo)  # revierte

    return documentos, marcado_fallidos


# ---------------------------------------------------------------------------
# Resumen JSON — función pura, sin I/O.
# ---------------------------------------------------------------------------


def _decision_a_dict(decision: DecisionGate) -> dict:
    return {
        "permitir": decision.permitir,
        "razon": decision.razon,
        "umbral_tokens": decision.umbral_tokens,
        "umbral_eur": decision.umbral_eur,
        "estimacion": {
            "documentos": decision.estimacion.documentos,
            "llamadas_segmentador": decision.estimacion.llamadas_segmentador,
            "llamadas_silver": decision.estimacion.llamadas_silver,
            "tokens_segmentador": decision.estimacion.tokens_segmentador,
            "tokens_silver": decision.estimacion.tokens_silver,
            "tokens_totales": decision.estimacion.tokens_totales,
            "coste_eur_estimado": decision.estimacion.coste_eur_estimado,
        },
    }


def _resultado_a_dict(resultado: ResultadoHistorico) -> dict:
    return {
        "descargados": list(resultado.descargados),
        "procesados": resultado.procesados,
        "fallidos": [{"name": d.name, "error": d.error} for d in resultado.fallidos],
        "marcado_fallidos": [
            {"docx": f.docx, "error": f.error} for f in resultado.marcado_fallidos
        ],
        "chistes_ruteados": [
            {
                "chiste_id": c.chiste_id,
                "decision": c.decision,
                "tema_id": c.tema_id,
                "tecnica_id": c.tecnica_id,
                "inicio_localizado": c.inicio_localizado,
            }
            for c in resultado.chistes_ruteados
        ],
    }


def construir_resumen(
    decision: Optional[DecisionGate],
    resultado: Optional[ResultadoHistorico],
    marcado_fallidos_gate: list[FalloMarcado],
    *,
    dry_run: bool,
) -> dict:
    """Construye el dict del resumen JSON (ver docstring del módulo, §Esquema).

    Función pura, sin I/O — separada de `main` para poder testear la forma
    del resumen sin pasar por argparse ni por stdout/fichero.
    """
    return {
        "gate": _decision_a_dict(decision) if decision is not None else None,
        "dry_run": dry_run,
        "marcado_fallidos_gate": [
            {"docx": f.docx, "error": f.error} for f in marcado_fallidos_gate
        ],
        "pipeline": _resultado_a_dict(resultado) if resultado is not None else None,
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
        prog="run_historico.py",
        description=(
            "CLI estable de Flujo C (Historico): evalua el gate de coste "
            "(historico.coste.estimar_y_evaluar, task 26) sobre los "
            "documentos pendientes y, si lo permite y no se paso --dry-run, "
            "ejecuta el pipeline completo (historico.pipeline."
            "run_historico_pipeline, task 27). Contrato pensado para "
            "invocacion externa por subprocess (p.ej. desde un proyecto RAG "
            "sin acceso al codigo Python de este repo): el resumen se "
            "imprime como JSON por stdout (unico contenido de stdout) o se "
            "escribe en --summary-out si se indica; los logs de progreso "
            "van siempre a stderr. Exit codes: 0 = exito (incluye --dry-run "
            "con el gate en verde), 1 = fallo (documento/.docx fallido o "
            "excepcion inesperada), 2 = abort por coste (el gate no "
            "permitio el run, con o sin --dry-run)."
        ),
    )
    parser.add_argument(
        "--folder-id",
        dest="folder_id",
        default=None,
        help=(
            f"ID de la carpeta de Drive del historico (por defecto, "
            f"variable de entorno {ENV_FOLDER_ID})."
        ),
    )
    parser.add_argument(
        "--staging-dir",
        dest="staging_dir",
        type=Path,
        default=None,
        help=f"Dir local de staging de .docx (por defecto {STAGING_DIR_POR_DEFECTO}).",
    )
    parser.add_argument(
        "--carpeta-md",
        dest="carpeta_md",
        type=Path,
        default=None,
        help=f"Carpeta de .md marcados (por defecto {CARPETA_MD_POR_DEFECTO}).",
    )
    parser.add_argument(
        "--drive-state",
        dest="drive_state",
        type=Path,
        default=None,
        help=f"JSON de idempotencia de DriveSource (por defecto {DRIVE_STATE_POR_DEFECTO}).",
    )
    parser.add_argument(
        "--loader-state",
        dest="loader_state",
        type=Path,
        default=None,
        help=f"JSON de idempotencia del Loader (por defecto {LOADER_STATE_POR_DEFECTO}).",
    )
    parser.add_argument(
        "--credentials-path",
        dest="credentials_path",
        type=Path,
        default=None,
        help="JSON de cuenta de servicio para Drive (por defecto GOOGLE_APPLICATION_CREDENTIALS).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Solo evalua el gate de coste y reporta la decision; nunca "
            "ejecuta run_historico_pipeline (ver docstring del modulo)."
        ),
    )
    parser.add_argument(
        "--umbral-tokens",
        dest="umbral_tokens",
        type=int,
        default=None,
        help="Umbral de tokens del gate (por defecto HISTORICO_COSTE_MAX_TOKENS o el default de coste.py).",
    )
    parser.add_argument(
        "--umbral-eur",
        dest="umbral_eur",
        type=float,
        default=None,
        help="Umbral de coste en EUR del gate, opcional (por defecto HISTORICO_COSTE_MAX_EUR, si existe).",
    )
    parser.add_argument(
        "--precio-eur-por-millon-tokens",
        dest="precio_eur_por_millon_tokens",
        type=float,
        default=None,
        help="Precio EUR/millon de tokens para el desglose en EUR (por defecto HISTORICO_COSTE_EUR_POR_MILLON_TOKENS o el default de coste.py).",
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
    run_historico_pipeline_fn: Callable[..., ResultadoHistorico] = run_historico_pipeline,
    estimar_y_evaluar_fn: Callable[..., DecisionGate] = estimar_y_evaluar,
    crear_drive_source_fn: Callable[..., Any] = _crear_drive_source_por_defecto,
    crear_document_store_fn: Callable[[], Any] = _crear_document_store_por_defecto,
    procesar_docx_fn: Callable[..., tuple] = procesar_docx,
) -> int:
    """Entrada del CLI. `run_historico_pipeline_fn`/`estimar_y_evaluar_fn`/
    `crear_drive_source_fn`/`crear_document_store_fn`/`procesar_docx_fn` son
    inyectables (ver `tests/unit/scripts/test_run_historico_cli.py`); en
    producción son los componentes reales del Flujo C."""
    parser = _construir_parser()
    args = parser.parse_args(argv)

    folder_id = args.folder_id or os.environ.get(ENV_FOLDER_ID)
    staging_dir = args.staging_dir if args.staging_dir is not None else STAGING_DIR_POR_DEFECTO
    carpeta_md = args.carpeta_md if args.carpeta_md is not None else CARPETA_MD_POR_DEFECTO
    drive_state_path = args.drive_state if args.drive_state is not None else DRIVE_STATE_POR_DEFECTO
    loader_state_path = args.loader_state if args.loader_state is not None else LOADER_STATE_POR_DEFECTO

    # --- Construcción de drive_source/document_store + etapas 0->0b->1->2 (gate) ---
    try:
        drive_source = crear_drive_source_fn(
            folder_id=folder_id,
            staging_dir=staging_dir,
            state_path=drive_state_path,
            credentials_path=args.credentials_path,
        )
        document_store = crear_document_store_fn()
        documentos, marcado_fallidos_gate = _documentos_pendientes_para_gate(
            drive_source,
            carpeta_md,
            loader_state_path,
            document_store=document_store,
            procesar_docx_fn=procesar_docx_fn,
        )
        decision = estimar_y_evaluar_fn(
            documentos,
            precio_eur_por_millon_tokens=args.precio_eur_por_millon_tokens,
            umbral_tokens=args.umbral_tokens,
            umbral_eur=args.umbral_eur,
        )
    except Exception as exc:  # noqa: BLE001 — red de seguridad, ver §Semántica de exit codes
        print(f"run_historico: fallo fatal antes de completar el gate de coste: {exc}", file=sys.stderr)
        resumen = construir_resumen(None, None, [], dry_run=args.dry_run)
        resumen["error_fatal"] = str(exc)
        _emitir_resumen(resumen, args.summary_out)
        return 1

    print(f"gate de coste: {decision.razon}", file=sys.stderr)

    if not decision.permitir:
        print("abort por coste: no se ejecuta run_historico_pipeline", file=sys.stderr)
        resumen = construir_resumen(decision, None, marcado_fallidos_gate, dry_run=args.dry_run)
        _emitir_resumen(resumen, args.summary_out)
        return 2

    if args.dry_run:
        print("--dry-run: gate permitido, no se ejecuta el pipeline completo", file=sys.stderr)
        resumen = construir_resumen(decision, None, marcado_fallidos_gate, dry_run=True)
        _emitir_resumen(resumen, args.summary_out)
        return 0

    # --- Gate permitido y sin --dry-run: run real (reutiliza drive_source y
    # document_store ya construidos, ver docstring del módulo, §El reto de
    # diseño, punto 1 — sincronizar_y_marcar() ve sync_con_metadata() vacío
    # en esta segunda pasada, así que ni siquiera necesita document_store) ---
    try:
        resultado = run_historico_pipeline_fn(
            carpeta_md=carpeta_md,
            loader_state_path=loader_state_path,
            drive_source=drive_source,
            document_store=document_store,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"run_historico_pipeline: fallo fatal: {exc}", file=sys.stderr)
        resumen = construir_resumen(decision, None, marcado_fallidos_gate, dry_run=False)
        resumen["error_fatal"] = str(exc)
        _emitir_resumen(resumen, args.summary_out)
        return 1

    print(
        f"run_historico_pipeline: {len(resultado.procesados)} procesados, "
        f"{len(resultado.fallidos)} fallidos, {len(resultado.marcado_fallidos)} marcado_fallidos",
        file=sys.stderr,
    )

    resumen = construir_resumen(decision, resultado, marcado_fallidos_gate, dry_run=False)
    _emitir_resumen(resumen, args.summary_out)

    ok = resultado.ok and not marcado_fallidos_gate
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
