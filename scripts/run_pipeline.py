"""run_pipeline — CLI estable de Flujo A (Teoría) para invocación externa (task 23).

Contrato (título de la task 23, `src/theory/SPEC.md` §Storage/§Idempotencia y
versionado, `CHECKPOINTS.md`): wiring puro sobre dos componentes YA
implementados y aprobados — `src/theory/pipeline.run_pipeline` (task 22) y
`src/theory/ingest_teoria.ingestar_documentos_pendientes` (task 21, leyendo
de `silver.teoria_documentos` desde la task 61, P25). Este módulo NO
reimplementa ninguna lógica de la cadena, solo la invoca y traduce su
resultado a un contrato de proceso estable (argv -> exit code + JSON).

**Task 61 — `--ingest` deja de derivar una versión de `version_dir`**: antes
de P25, `--ingest` leía `manifest.json` de la `v{N}/` que acababa de generar
`run_pipeline_fn`, así que necesitaba su número de versión
(`_version_desde_dir`). Desde que `ingest_teoria` lee `silver.teoria_documentos`
en vez de disco, la función de ingesta ya no recibe ni necesita ningún
directorio — solo el `store` — y su selección por defecto ("todas las filas
Silver sin chunks en Gold", ver `src/theory/ingest_teoria.py`) es
"resumible": no depende de qué versión generó este run en concreto (opción
(a) de `src/theory/SPEC.md` §Task 61, elegida por menor blast radius — no
exige tocar `pipeline.py`/`ResultadoPipeline`, que hoy no exponen los
`fila_id` de Silver escritos en un run). `_version_desde_dir` desaparece con
ese uso.

**Task 63 — `--ingest` deja de ser un no-op cuando `version_dir is None`**:
la task 61 dejó `ingestar_documentos_pendientes` completamente resumible
(no depende de qué generó este run), pero `main()` todavía tenía una rama
`if resultado.version_dir is None: ... no-op ...` heredada de antes de P25.
Con la task 63, `run_pipeline` NUNCA vuelve a generar una versión —
`resultado.version_dir` es SIEMPRE `None` — así que esa condición convertía
`--ingest` en un no-op PERMANENTE, sin importar cuánto se acumulara en
`silver.teoria_documentos`: un bug que habría roto la cadena
Drive→Bronze→Silver→Gold en producción. Corregido: con `--ingest` presente,
`ingestar_fn` se invoca SIEMPRE (ver §"`--ingest` siempre se ejecuta si se
pide" abajo) — sigue siendo barato si no hay nada pendiente en Silver, porque
`ingestar_documentos_pendientes` ya gestiona ese caso devolviendo
`num_nuevos=0`, no es responsabilidad de este CLI adivinarlo mirando
`version_dir`.

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
      "version_dir": null,  # SIEMPRE null desde la task 63, P25 (ver abajo)
      "ingesta": null | {
          "intentada": true,
          "ejecutada": true | false,
          # si ejecutada=true:
          "num_documentos": N, "num_nuevos": N, "num_duplicados": M,
          # si ejecutada=false:
          "motivo": "..." | "error": "..."
      },
      "bronze": {
          "activada": true | false,
          # si activada=false por --sin-captura-bronze (solo con --sync-drive):
          "omitida": true,
          "nuevos": N, "existentes": M  # ResultadoPipeline.bronze_* (task 63)
      },
      "silver": {
          "activa": true | false,       # ResultadoPipeline.silver_activo
          "nuevos": N, "existentes": M  # ResultadoPipeline.silver_*
      }
    }

**`"version_dir"` (task 63, P25)**: se mantiene SIEMPRE `null` — es un
contrato externo (ver §"Por qué el contrato de CLI importa más de lo
habitual" abajo), y `null` es exactamente lo que ya significaba "este run no
produjo versión" antes de esta tarea. `run_pipeline` ya no genera ninguna
versión nunca (ver `src/theory/pipeline.py`), así que el valor no cambia,
solo deja de variar. Lo realmente entregado en este run se reporta en las
claves NUEVAS `"bronze"`/`"silver"` (con contadores reales), que un
consumidor anterior a esta tarea simplemente ignora sin enterarse — no se
quita ninguna clave documentada, solo se añaden.

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
  `ingestar_documentos_pendientes` lanzó una excepción (red/Supabase/formato).
- `3`: el propio `run_pipeline` lanzó una excepción inesperada (fuera de su
  contrato normal, que ya captura los fallos por fichero en `fallidos` — ver
  `pipeline.py`). Red de seguridad para no propagar un traceback críptico a
  un consumidor externo sin exit code fiable. **Incluye el fallo de
  configuración de `--sync-drive`** (p.ej. `RuntimeError` de
  `desde_entorno()` por falta de `DRIVE_FOLDER_ID`, ver §`--sync-drive`
  abajo) **y el fallo de configuración de la captura Bronze** (p.ej.
  `DocumentStoreError` de `DocumentStore()` por falta de `SUPABASE_URL`/
  `SUPABASE_SERVICE_KEY`, ver §`--capturar-bronze` / `--sin-captura-bronze`
  abajo) — ninguno de los dos es un caso nuevo, son exactamente "fallo fatal
  inesperado antes de completar el run", así que reutilizan el mismo exit
  code, no uno nuevo.

## `--sync-drive` (task 45, P23) — etapa 0 opcional de Drive real

Por defecto (`--sync-drive` ausente) este script es wiring puro sobre el
modo solo-local de `run_pipeline` — no importa credenciales de Drive ni toca
red, comportamiento idéntico al de antes de esta tarea. Con `--sync-drive`,
construye un `DriveSyncTeoria` vía `crear_drive_sync_fn` (inyectable, por
defecto `src.theory.drive_sync.desde_entorno`) usando `--drive-staging-dir`/
`--drive-state-path` (o `None` -> defaults de `src.theory.drive_sync`), y lo
pasa a `run_pipeline_fn(..., drive_sync=<instancia>)` para que la etapa 0
corra antes de `DriveMonitor` (ver `src/theory/pipeline.py`). La construcción
ocurre DENTRO del mismo `try` que ya envuelve la llamada a `run_pipeline_fn`
— un fallo ahí (típicamente `DRIVE_FOLDER_ID` sin definir) se trata igual que
cualquier fallo fatal de `run_pipeline`: exit code `3`, mismo resumen JSON de
error (ver arriba).

## `--capturar-bronze` / `--sin-captura-bronze` (task 59, P25) — captura Bronze

Construye un `DocumentStore` (vía `crear_document_store_fn`, inyectable, por
defecto `src.utils.document_store.DocumentStore`) y lo pasa a
`run_pipeline_fn(..., document_store=<instancia>)` para la etapa 0.5 (ver
`src/theory/pipeline.py`). El criterio de activación es asimétrico a
propósito (ver `src/theory/SPEC.md` §"Captura Bronze", "Modo Drive-real: la
captura va por defecto" / "Modo solo-local: la captura es opt-in"):

- Con `--sync-drive`: la captura está activa **por defecto**; `--sin-captura-bronze`
  la desactiva (para depurar el sync sin escribir en Supabase). Un modo de
  producción donde la durabilidad dependa de acordarse de un flag no es una
  garantía.
- Sin `--sync-drive` (modo solo-local): la captura está **desactivada por
  defecto** — capturar en cada corrida convertiría el comando sin flags (el
  de desarrollo/tests, que no exige credenciales) en uno que falla sin
  `SUPABASE_URL`. `--capturar-bronze` la activa explícitamente para quien
  cura `data/raw/` a mano y quiere durabilidad en el mismo comando.

La construcción de `DocumentStore` ocurre DENTRO del mismo `try` que ya
envuelve `run_pipeline_fn`/`drive_sync_instance`: un fallo de configuración de
Supabase (típicamente `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` ausentes) reutiliza
el mismo exit code `3` de "fallo fatal antes de completar el run" — mismo
criterio que `DRIVE_FOLDER_ID` con `--sync-drive`. El resumen JSON lleva
`bronze.activada` siempre, `bronze.omitida=true` en el caso explícito
`--sync-drive --sin-captura-bronze` (contractual, ver `src/theory/SPEC.md`),
y desde la task 63 también `bronze.nuevos`/`bronze.existentes` (contadores
reales de `ResultadoPipeline.bronze_nuevos`/`bronze_existentes` — 0/0 si la
captura no llegó a activarse, p.ej. por un fallo fatal antes de completar el
run). La clave `"silver"` (nueva en la task 63) refleja
`ResultadoPipeline.silver_activo`/`silver_nuevos`/`silver_existentes` —
mismo interruptor que Bronze (`document_store is not None`), sin una clave
de configuración propia porque Silver no tiene flags CLI independientes de
Bronze.

## `--ingest` siempre se ejecuta si se pide (task 63, P25)

Antes de la task 63, `--ingest` comprobaba `resultado.version_dir is None`
para decidir si había "algo pendiente" que ingestar, y si lo era, hacía
no-op sin llamar a `ingestar_fn`. Esa condición dejó de tener sentido en
cuanto `run_pipeline` dejó de generar versiones: `version_dir` pasa a ser
SIEMPRE `None` (ver `src/theory/pipeline.py`), así que la condición original
habría convertido `--ingest` en un no-op PERMANENTE — nunca volvería a
ingestar nada, ni con `silver.teoria_documentos` lleno de filas sin chunks en
Gold. Corregido: con `--ingest` presente, `ingestar_fn` se invoca SIEMPRE,
sin mirar `resultado.version_dir` ni `resultado.procesados` — la propia
`ingestar_documentos_pendientes` (task 61) ya es resumible por sí misma (lee
"todas las filas Silver sin chunks en Gold", no depende de qué generó este
run en concreto) y ya devuelve `num_nuevos=0` sin error cuando no hay nada
pendiente, así que no hace falta que este CLI adivine el caso "nada que
ingestar" mirando un campo que ya no significa eso.

## Credenciales de Supabase — solo si `--ingest`

`TeoriaStore()` (vía `_crear_store_por_defecto`, importado perezosamente)
solo se instancia si `--ingest` está presente, así que un run sin `--ingest`
no requiere `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` configuradas.

## Tests

`tests/unit/scripts/test_run_pipeline_cli.py` inyecta `run_pipeline_fn`/
`ingestar_fn`/`crear_store_fn` de mentira — nunca llama a Supabase ni
a red real (mismo patrón que `test_ingest_teoria.py`, cuyas llamadas reales
están en `tests/integration/`, fuera de scope de esta task).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Bootstrap de sys.path: permite invocar `python scripts/run_pipeline.py` (o
# vía subprocess con cualquier cwd) sin depender de PYTHONPATH ni de que el
# invocador esté en la raíz del repo — requisito de fiabilidad del "contrato
# externo" (ver docstring del módulo). `python -m scripts.run_pipeline` desde
# la raíz ya añade la raíz solo, esto cubre además la invocación directa por
# ruta de fichero, que es la forma más simple de invocar desde `subprocess`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.theory.drive_sync import desde_entorno  # noqa: E402
from src.theory.ingest_teoria import ResultadoIngesta, ingestar_documentos_pendientes  # noqa: E402
from src.theory.pipeline import ResultadoPipeline, run_pipeline  # noqa: E402
from src.utils.document_store import DocumentStore  # noqa: E402


def _crear_store_por_defecto():
    """Crea un `TeoriaStore` real contra Supabase. Import perezoso: solo se
    ejecuta si `--ingest` está presente, para no exigir credenciales de
    Supabase en runs que no ingestan (ver docstring del módulo)."""
    from src.theory.teoria_store import TeoriaStore

    return TeoriaStore()


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


def _resumen_bronze(resultado: ResultadoPipeline, activada: bool, omitida: bool) -> dict:
    """Construye la clave `bronze` del resumen (task 59, ver docstring del
    módulo §--capturar-bronze / --sin-captura-bronze). `omitida=True` es
    contractual para el caso `--sync-drive --sin-captura-bronze`; el resto de
    la forma (`activada` siempre presente) es criterio de esta implementación,
    consistente con cómo `ingesta`/`error_fatal` documentan su propio estado.
    `activada`/`omitida` reflejan la CONFIGURACIÓN pedida por CLI (siguen
    presentes aunque `run_pipeline_fn` falle antes de completar el run);
    `nuevos`/`existentes` (task 63) son los contadores REALES de
    `resultado.bronze_nuevos`/`bronze_existentes` — 0/0 si la captura no
    llegó a ejecutarse."""
    bronze: dict = {"activada": activada}
    if omitida:
        bronze["omitida"] = True
    bronze["nuevos"] = resultado.bronze_nuevos
    bronze["existentes"] = resultado.bronze_existentes
    return bronze


def _resumen_silver(resultado: ResultadoPipeline) -> dict:
    """Construye la clave `silver` del resumen (task 63, P25) — sin flags CLI
    propios (mismo interruptor que Bronze, `document_store is not None`), así
    que, a diferencia de `_resumen_bronze`, `activa` refleja directamente
    `resultado.silver_activo` (el estado real tras ejecutar `run_pipeline_fn`,
    no una intención de configuración previa)."""
    return {
        "activa": resultado.silver_activo,
        "nuevos": resultado.silver_nuevos,
        "existentes": resultado.silver_existentes,
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
            "(DriveMonitor -> Parser -> ... -> Bronze/Silver en Supabase, "
            "opcional) y, con --ingest, vuelca a Supabase las filas de "
            "silver.teoria_documentos aun pendientes "
            "(ingestar_documentos_pendientes). Contrato pensado para invocacion externa por "
            "subprocess (p.ej. desde un proyecto RAG sin acceso al codigo "
            "Python de este repo): el resumen se imprime como JSON por stdout "
            "(unico contenido de stdout) o se escribe en --summary-out si se "
            "indica; los logs de progreso van siempre a stderr. Exit codes: 0 "
            "= exito (incluye 'nada pendiente', que no es un fallo), 1 = algun "
            "fichero en 'fallidos', 2 = --ingest fallo (pipeline sin fallidos), "
            "3 = fallo fatal inesperado de run_pipeline (incluye fallo de "
            "configuracion de --sync-drive, p.ej. falta DRIVE_FOLDER_ID)."
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
    # `--directorio-procesado`/`--version` se RETIRAN en la task 63 (P25),
    # junto con sus parámetros homónimos de `run_pipeline()`: existían solo
    # para pasarse a `generar_version`, que ya no se invoca (ver
    # `src/theory/pipeline.py`). Sin evidencia de ningún caller externo que
    # los use (el contrato externo real es el resumen JSON, no estos flags),
    # se retiran en vez de dejarse como no-ops silenciosos.
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
        "--ingest",
        action="store_true",
        help=(
            "Tras un run con exito, ingesta las filas de silver.teoria_documentos "
            "aun sin chunks en Supabase (ingestar_documentos_pendientes, "
            "gold.teoria_chunks). Se ejecuta SIEMPRE que se pase esta flag "
            "(task 63: ya no depende de si este run en concreto proceso algo "
            "nuevo) — ingestar_documentos_pendientes ya es resumible por si "
            "sola y no falla si no hay nada pendiente (ver docstring del "
            "modulo)."
        ),
    )
    parser.add_argument(
        "--sync-drive",
        action="store_true",
        help=(
            "Activa el modo Drive-real: ejecuta DriveSyncTeoria.sync() como "
            "etapa 0 antes de DriveMonitor (ver src/theory/SPEC.md #Fuente de "
            "entrada, P23). Por defecto NO se activa (modo solo-local "
            "intacto: no se importa credenciales de Drive ni se toca red)."
        ),
    )
    parser.add_argument(
        "--capturar-bronze",
        dest="capturar_bronze",
        action="store_true",
        help=(
            "Activa la captura Bronze (etapa 0.5, ver src/theory/SPEC.md "
            "#Captura Bronze) en modo solo-local (sin --sync-drive), donde "
            "por defecto NO corre (para no exigir SUPABASE_URL/"
            "SUPABASE_SERVICE_KEY en el modo de desarrollo/tests). Sin "
            "efecto si --sync-drive esta presente (ahi la captura ya va por "
            "defecto, ver --sin-captura-bronze)."
        ),
    )
    parser.add_argument(
        "--sin-captura-bronze",
        dest="sin_captura_bronze",
        action="store_true",
        help=(
            "Desactiva la captura Bronze (etapa 0.5) en modo --sync-drive, "
            "donde por defecto SI corre (P25: la durabilidad del original no "
            "puede depender de acordarse de un flag). Util para depurar el "
            "sync sin escribir en Supabase. Sin efecto sin --sync-drive (ahi "
            "la captura ya esta desactivada por defecto, ver "
            "--capturar-bronze)."
        ),
    )
    parser.add_argument(
        "--drive-staging-dir",
        dest="drive_staging_dir",
        type=Path,
        default=None,
        help=(
            "Staging local del sync de Drive (solo con --sync-drive; por "
            "defecto STAGING_DIR_POR_DEFECTO de src.theory.drive_sync)."
        ),
    )
    parser.add_argument(
        "--drive-state-path",
        dest="drive_state_path",
        type=Path,
        default=None,
        help=(
            "Fichero de estado de idempotencia del sync de Drive (solo con "
            "--sync-drive; por defecto RUTA_ESTADO_DRIVE_POR_DEFECTO de "
            "src.theory.drive_sync)."
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
    ingestar_fn: Callable[..., ResultadoIngesta] = ingestar_documentos_pendientes,
    crear_store_fn: Callable[[], object] = _crear_store_por_defecto,
    crear_drive_sync_fn: Callable[..., Any] = desde_entorno,
    crear_document_store_fn: Callable[[], Any] = DocumentStore,
) -> int:
    """Entrada del CLI. `run_pipeline_fn`/`ingestar_fn`/`crear_store_fn`/
    `crear_drive_sync_fn`/`crear_document_store_fn` son inyectables (ver
    `tests/unit/scripts/test_run_pipeline_cli.py`); en producción son los
    componentes reales de Flujo A. `crear_drive_sync_fn` solo se invoca si
    `--sync-drive` está presente (ver docstring del módulo, §--sync-drive).
    `crear_document_store_fn` solo se invoca si la captura Bronze queda
    activada según `--sync-drive`/`--capturar-bronze`/`--sin-captura-bronze`
    (ver docstring del módulo, §--capturar-bronze / --sin-captura-bronze)."""
    parser = _construir_parser()
    args = parser.parse_args(argv)

    # Activación asimétrica (ver docstring del módulo): con --sync-drive la
    # captura va por defecto (--sin-captura-bronze la apaga); sin
    # --sync-drive, va apagada por defecto (--capturar-bronze la enciende).
    captura_bronze_activada = (
        args.sync_drive and not args.sin_captura_bronze
    ) or (not args.sync_drive and args.capturar_bronze)
    bronze_omitida = args.sync_drive and args.sin_captura_bronze

    try:
        drive_sync_instance: Optional[Any] = None
        if args.sync_drive:
            drive_sync_instance = crear_drive_sync_fn(
                staging_dir=args.drive_staging_dir,
                state_path=args.drive_state_path,
            )

        document_store_instance: Optional[Any] = None
        if captura_bronze_activada:
            document_store_instance = crear_document_store_fn()

        # `directorio_procesado`/`version` ya NO se pasan (task 63): se
        # retiraron de `run_pipeline()` junto con la llamada a
        # `generar_version` que los consumía (ver `src/theory/pipeline.py`).
        kwargs_run_pipeline: dict = {
            "ruta_estado": args.ruta_estado,
        }
        if drive_sync_instance is not None:
            kwargs_run_pipeline["drive_sync"] = drive_sync_instance
        if document_store_instance is not None:
            kwargs_run_pipeline["document_store"] = document_store_instance

        resultado = run_pipeline_fn(args.carpetas, **kwargs_run_pipeline)
    except Exception as exc:  # noqa: BLE001 — red de seguridad, ver §Semántica de exit codes
        print(f"run_pipeline: fallo fatal antes de completar el run: {exc}", file=sys.stderr)
        resultado_vacio = ResultadoPipeline()
        resumen = construir_resumen(resultado_vacio, None)
        resumen["error_fatal"] = str(exc)
        resumen["bronze"] = _resumen_bronze(resultado_vacio, captura_bronze_activada, bronze_omitida)
        resumen["silver"] = _resumen_silver(resultado_vacio)
        _emitir_resumen(resumen, args.summary_out)
        return 3

    print(
        f"run_pipeline: {len(resultado.procesados)} procesados, "
        f"{len(resultado.fallidos)} fallidos, {len(resultado.ignorados)} ignorados",
        file=sys.stderr,
    )

    ingesta_info: Optional[dict] = None
    ingest_fallo = False

    if args.ingest:
        # Task 63: se invoca SIEMPRE que se pida la flag, sin mirar
        # `resultado.version_dir`/`resultado.procesados` — ver docstring del
        # módulo §"--ingest siempre se ejecuta si se pide". El no-op
        # condicionado a `version_dir is None` desapareció: `version_dir` es
        # SIEMPRE `None` desde esta tarea, así que esa condición habría
        # convertido `--ingest` en un no-op permanente.
        try:
            store = crear_store_fn()
            resultado_ingesta = ingestar_fn(store)
        except Exception as exc:  # noqa: BLE001
            ingest_fallo = True
            ingesta_info = {"intentada": True, "ejecutada": False, "error": str(exc)}
            print(f"--ingest: fallo: {exc}", file=sys.stderr)
        else:
            ingesta_info = {
                "intentada": True,
                "ejecutada": True,
                "num_documentos": resultado_ingesta.num_documentos,
                "num_nuevos": resultado_ingesta.num_nuevos,
                "num_duplicados": resultado_ingesta.num_duplicados,
            }
            print(
                f"--ingest: {resultado_ingesta.num_nuevos} nuevos, "
                f"{resultado_ingesta.num_duplicados} duplicados "
                f"({resultado_ingesta.num_documentos} documentos Silver)",
                file=sys.stderr,
            )

    resumen = construir_resumen(resultado, ingesta_info)
    resumen["bronze"] = _resumen_bronze(resultado, captura_bronze_activada, bronze_omitida)
    resumen["silver"] = _resumen_silver(resultado)
    _emitir_resumen(resumen, args.summary_out)

    return _exit_code(resultado, ingest_fallo)


if __name__ == "__main__":
    raise SystemExit(main())
