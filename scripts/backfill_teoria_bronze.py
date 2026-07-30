"""backfill_teoria_bronze — captura retroactiva de Bronze para el material de
teoría YA existente en local (task 66, P25).

Contrato (`src/theory/SPEC.md` §"Captura Bronze" — tabla "Quién captura cada
fichero" y §"Modo solo-local: la captura es opt-in"; `src/utils/SPEC.md`
§DocumentStore, ya aprobado en la task 58): la captura Bronze en modo legacy
para pendientes fuera del staging de Drive YA vive dentro de
`src/theory/pipeline.py` (etapa 0.5, task 59), pero SOLO se activa como
efecto colateral de una corrida completa del pipeline (`--capturar-bronze`) y
SOLO ve lo que `DriveMonitor` reporta como nuevo/modificado — nunca el
material ya procesado. Este script hace el mismo `document_store.capturar()`
en modo legacy, pero como barrido standalone de TODO `data/raw/**` (y
`data/staging/theory/` si existe), sin correr el resto de la cadena
(Parser/Cleaner/FormatNormalizer/...): existe precisamente para cubrir el
conjunto cerrado y conocido de material histórico que `DriveMonitor` nunca
volvería a ver (7 libros + 25 transcripciones, ver `SPEC.md` punto 2 de esa
sección), de una vez y en un solo sitio.

## Por qué NO se reimplementa la lógica de captura

`_PARSERS_POR_EXTENSION` (filtro de extensión elegible) y
`_tipo_fuente_por_extension` (derivación de `tipo_fuente`) se IMPORTAN desde
`src.theory.pipeline`, y `LICENCIA_POR_DEFECTO` desde
`src.theory.normalizers.format_normalizer`, en vez de reimplementarse aquí.
Los dos primeros son símbolos privados (prefijo `_`): el resto del repo evita
a propósito importar símbolos privados entre módulos hermanos (ver
`pipeline.py`, comentario junto a `_slugify_fuente`, que reimplementa en vez
de importar de `format_normalizer`) para no acoplar dos módulos que pueden
evolucionar por separado. Aquí se hace la excepción deliberada: este script
es, por diseño, el SEGUNDO consumidor del mismo contrato de captura legacy
que ya vive en `pipeline.py` (task 59) — el propio docstring de
`_tipo_fuente_por_extension` ya advierte que debe ser "la misma señal que
usará el Parser después, nunca se desincroniza". Reimplementar el filtro de
extensión o la derivación de `tipo_fuente` aquí crearía exactamente el riesgo
que esa nota previene: que este backfill suba (o etiquete) un fichero que el
pipeline real trataría distinto. Se prefiere el acoplamiento explícito de un
`import` a duplicar una tabla que debe permanecer sincronizada por
construcción.

## Qué NO hace este script

- No usa `drive_sync` ni `DRIVE_FOLDER_ID`: no hay modo Drive en un backfill
  de material ya local.
- No corre el resto de la cadena (Parser/Cleaner/FormatNormalizer/...): solo
  Bronze. Silver y Gold se generan, para este mismo material, con una corrida
  normal de `scripts/run_pipeline.py --capturar-bronze` (que sí recorre la
  cadena completa) — este backfill es puramente de recuperación de Bronze
  para lo que YA está en disco y todavía no tiene fila.
- No toca, renombra ni escribe nada en `data/raw/` ni en `data/staging/`:
  lectura pura (`Path.rglob` + `document_store.capturar()`, que lee los bytes
  del fichero pero nunca los modifica).

## Re-ejecutable sin duplicar — gratis del `DocumentStore` compartido

La clave de idempotencia en modo legacy es el `hash_md5` del contenido
(`drive_file_id IS NULL`), calculado por el propio `DocumentStore`. Una
segunda pasada de este script sobre el mismo fichero sin cambios devuelve
`ya_existia=True` sin volver a subir el objeto ni insertar la fila — no hace
falta llevar estado propio (ni fichero de progreso ni caché local): el
backlog es corto (~32 ficheros) y Supabase ya es la fuente de verdad de qué
está capturado. Este mismo hash también converge con lo que
`pipeline.py` etapa 0.5 pudiera haber capturado ya para el mismo fichero (ver
`src/theory/SPEC.md`, "Los dos caminos legacy convergen en la MISMA fila").

## `ruta_relativa` — relativa a la raíz de escaneo, no a `data/raw/`

Igual que en `pipeline.py` etapa 0.5 (`ruta_relativa = path.relative_to(carpeta_origen)`,
donde `carpeta_origen` es el elemento de `carpetas` del que salió el
pendiente, NO `data/raw/` en bloque): cada raíz barrida aquí
(`data/raw/books/`, `data/raw/notes/`, `data/raw/transcriptions/`,
`data/staging/theory/`) juega el mismo papel que `carpeta_origen` allí. Para
`data/raw/transcriptions/Demy/charla.txt`, `ruta_relativa` es
`"Demy/charla.txt"` (relativa a `transcriptions/`), preservando la
atribución por ponente que codifican las subcarpetas — exactamente el caso
que `src/theory/SPEC.md` señala como motivo de que esta columna exista
("el bucket es plano... sin esta columna la captura destruiría la
atribución por ponente").

## Un fallo no aborta el batch

Mismo criterio que la captura legacy de `pipeline.py` (etapa 0.5) y que
`scripts/reprocesar_bronze_pendiente.py`: cada fichero se captura en su
propio `try`, un fallo se recopila en `fallidos` y el barrido continúa con el
siguiente. El material sagrado no se pierde por un problema puntual (un
fichero corrupto, un hipo de red) — se reintenta en la siguiente pasada, que
es idempotente.

## Mecanismo del resumen: JSON por stdout, o `--summary-out`

Mismo mecanismo que el resto de scripts de este repo
(`run_pipeline.py`/`run_historico.py`/`reprocesar_bronze_pendiente.py`): por
defecto el resumen se imprime como JSON por **stdout** (único contenido de
stdout). Con `--summary-out <ruta>` se escribe ahí en su lugar y stdout queda
vacío. Los logs de progreso van siempre a **stderr**.

Esquema (`construir_resumen`):

    {
      "raices": ["data/raw/books", "data/raw/notes", "data/raw/transcriptions", "data/staging/theory"],
      "vistos": int,          # ficheros elegibles encontrados (todas las raíces)
      "nuevos": int,          # capturados por primera vez en esta pasada
      "existentes": int,      # ya_existia=True (ya capturados en una pasada anterior)
      "fallidos": [{"path": "...", "error": "..."}, ...],
      "error_fatal": "..." | null
    }

## Semántica de exit codes

Mismo esquema de tres niveles que `scripts/reprocesar_bronze_pendiente.py`
(0/1/2 — no 0/1/2/3 de `run_pipeline.py`, porque este script no tiene un paso
equivalente a `--ingest` que necesite un código propio):

- `0`: éxito total — todo fichero elegible encontrado quedó capturado (nuevo
  o `ya_existia`), sin ningún fallido. Incluye el caso "no había nada que
  capturar" (todas las raíces vacías o inexistentes): no es un fallo, es un
  resultado esperado.
- `1`: se recorrió el barrido completo pero algún fichero falló al
  capturarse (`fallidos` no vacío). El resto del batch sí se procesó — el
  resumen JSON lleva el detalle completo de qué falló y por qué.
- `2`: fallo fatal ANTES de poder iterar — típicamente `DocumentStoreError`
  al construir `DocumentStore()` por falta de `SUPABASE_URL`/
  `SUPABASE_SERVICE_KEY` en el entorno. Red de seguridad: nunca traceback
  crudo a un consumidor externo sin exit code fiable.

## Tests

`tests/unit/scripts/test_backfill_teoria_bronze.py` inyecta
`crear_document_store_fn` (y `raices`, para apuntar a una estructura de
directorios de prueba en `tmp_path` en vez de al `data/raw/` real del repo) —
nunca red real, mismo patrón que el resto de `tests/unit/scripts/`.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Bootstrap de sys.path: permite invocar `python scripts/backfill_teoria_bronze.py`
# (o vía subprocess con cualquier cwd) sin depender de PYTHONPATH ni de que el
# invocador esté en la raíz del repo — mismo requisito que el resto de
# `scripts/` (ver `run_pipeline.py`/`run_historico.py`).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.theory.normalizers.format_normalizer import LICENCIA_POR_DEFECTO  # noqa: E402
from src.theory.pipeline import (  # noqa: E402
    _PARSERS_POR_EXTENSION,
    _tipo_fuente_por_extension,
)
from src.utils.document_store import DocumentStore  # noqa: E402

CARPETA_BOOKS_POR_DEFECTO = Path("data/raw/books")
CARPETA_NOTES_POR_DEFECTO = Path("data/raw/notes")
CARPETA_TRANSCRIPTIONS_POR_DEFECTO = Path("data/raw/transcriptions")
CARPETA_STAGING_THEORY_POR_DEFECTO = Path("data/staging/theory")

RAICES_POR_DEFECTO: tuple[Path, ...] = (
    CARPETA_BOOKS_POR_DEFECTO,
    CARPETA_NOTES_POR_DEFECTO,
    CARPETA_TRANSCRIPTIONS_POR_DEFECTO,
    CARPETA_STAGING_THEORY_POR_DEFECTO,
)
"""Las cuatro raíces del material local de teoría (`src/theory/SPEC.md`
§"Modo solo-local"). `data/staging/theory/` puede no existir todavía en un
checkout dado (no es un error, ver `_listar_elegibles`)."""


def _crear_document_store_por_defecto() -> DocumentStore:
    """Crea un `DocumentStore` real contra Supabase. Import perezoso no hace
    falta aquí (a diferencia de `_crear_store_por_defecto` en otros scripts):
    `DocumentStore` ya hace su propio import perezoso de `supabase-py` dentro
    de `crear_cliente()`, así que construirlo sin credenciales configuradas
    ya lanza `DocumentStoreError` de forma temprana y explícita, sin tocar
    red (ver `src/utils/document_store.py`)."""
    return DocumentStore()


def _listar_elegibles(raiz: Path) -> list[Path]:
    """Recorre `raiz` recursivamente (subcarpetas incluidas — p.ej. las de
    ponente bajo `data/raw/transcriptions/`) y devuelve, en orden
    determinista, los ficheros cuya extensión está en
    `_PARSERS_POR_EXTENSION` (mismo filtro que usaría el pipeline real: nunca
    sube algo que el Parser no leería, p.ej. `.gitkeep`).

    `raiz` inexistente devuelve `[]` sin error — no es un fallo, es el caso
    normal de `data/staging/theory/` en un checkout que aún no lo tiene.
    """
    if not raiz.exists():
        return []
    return sorted(
        path
        for path in raiz.rglob("*")
        if path.is_file() and path.suffix.lower() in _PARSERS_POR_EXTENSION
    )


# ---------------------------------------------------------------------------
# Resumen JSON — función pura, sin I/O.
# ---------------------------------------------------------------------------


def construir_resumen(
    raices: list[str],
    vistos: int,
    nuevos: int,
    existentes: int,
    fallidos: list,
    error_fatal: Optional[str] = None,
) -> dict:
    """Construye el dict del resumen JSON (ver docstring del módulo,
    §Esquema). Función pura, separada de `main` para poder testear la forma
    del resumen sin pasar por argparse ni por stdout/fichero."""
    return {
        "raices": raices,
        "vistos": vistos,
        "nuevos": nuevos,
        "existentes": existentes,
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
        prog="backfill_teoria_bronze.py",
        description=(
            "Backfill de Bronze (bronze.teoria_documentos) para el material "
            "de teoria YA existente en local: recorre recursivamente "
            "data/raw/books/, data/raw/notes/, data/raw/transcriptions/ "
            "(subcarpetas por ponente incluidas) y data/staging/theory/ "
            "(si existe), y captura cada fichero elegible en modo legacy "
            "(drive_file_id NULL, hash_md5 como clave de idempotencia) via "
            "document_store.capturar(). No corre el resto de la cadena "
            "(Parser/Cleaner/...), no toca ni renombra nada en data/raw/, y "
            "es re-ejecutable sin duplicar (una segunda pasada sobre un "
            "fichero ya capturado devuelve ya_existia=True). El resumen se "
            "imprime como JSON por stdout (unico contenido de stdout) o se "
            "escribe en --summary-out si se indica; los logs de progreso "
            "van siempre a stderr. Exit codes: 0 = exito total (todo "
            "capturado o ya_existia), 1 = algun fichero fallo al "
            "capturarse (el resto del batch se completo), 2 = fallo fatal "
            "antes de poder iterar (p.ej. faltan SUPABASE_URL/"
            "SUPABASE_SERVICE_KEY)."
        ),
    )
    parser.add_argument(
        "--carpeta",
        dest="carpetas",
        action="append",
        type=Path,
        default=None,
        help=(
            "Raiz a barrer recursivamente (repetible: --carpeta a --carpeta "
            "b). Si se indica una o mas, SUSTITUYE por completo a las "
            "cuatro raices por defecto (data/raw/books, data/raw/notes, "
            "data/raw/transcriptions, data/staging/theory)."
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
    raices: tuple[Path, ...] = RAICES_POR_DEFECTO,
    crear_document_store_fn: Callable[[], Any] = _crear_document_store_por_defecto,
) -> int:
    """Entrada del CLI. `crear_document_store_fn` es inyectable (ver
    `tests/unit/scripts/test_backfill_teoria_bronze.py`); en producción es
    `DocumentStore` real. `raices` es inyectable para tests (estructura de
    directorios en `tmp_path`, nunca el `data/raw/` real del repo); en
    producción son las cuatro raíces por defecto salvo que `--carpeta` las
    sustituya."""
    parser = _construir_parser()
    args = parser.parse_args(argv)

    raices_efectivas = tuple(args.carpetas) if args.carpetas else tuple(raices)
    raices_str = [str(r) for r in raices_efectivas]

    try:
        document_store = crear_document_store_fn()
    except Exception as exc:  # noqa: BLE001 — red de seguridad, ver §Semántica de exit codes
        print(
            f"backfill_teoria_bronze: fallo fatal antes de poder iterar: {exc}",
            file=sys.stderr,
        )
        resumen = construir_resumen(raices_str, 0, 0, 0, [], error_fatal=str(exc))
        _emitir_resumen(resumen, args.summary_out)
        return 2

    vistos = 0
    nuevos = 0
    existentes = 0
    fallidos: list = []

    for raiz in raices_efectivas:
        raiz = Path(raiz)
        elegibles = _listar_elegibles(raiz)
        for path in elegibles:
            vistos += 1
            try:
                ruta_relativa = str(path.relative_to(raiz))
            except ValueError:
                ruta_relativa = path.name

            try:
                resultado = document_store.capturar(
                    ruta=path,
                    capa="bronze",
                    flujo="teoria",
                    drive_file_id=None,
                    modified_time=None,
                    nombre=path.name,
                    mime_type=mimetypes.guess_type(path.name)[0],
                    extra={
                        "tipo_fuente": _tipo_fuente_por_extension(path),
                        "licencia": LICENCIA_POR_DEFECTO,
                        "ruta_relativa": ruta_relativa,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — un fallo aislado no aborta el barrido
                fallidos.append({"path": str(path), "error": str(exc)})
                print(
                    f"backfill_teoria_bronze: {path} fallo: {exc}",
                    file=sys.stderr,
                )
                continue

            if resultado.ya_existia:
                existentes += 1
                print(f"backfill_teoria_bronze: {path} ya existia", file=sys.stderr)
            else:
                nuevos += 1
                print(f"backfill_teoria_bronze: {path} capturado", file=sys.stderr)

    print(
        f"backfill_teoria_bronze: {vistos} vistos, {nuevos} nuevos, "
        f"{existentes} ya existian, {len(fallidos)} fallidos",
        file=sys.stderr,
    )

    resumen = construir_resumen(raices_str, vistos, nuevos, existentes, fallidos)
    _emitir_resumen(resumen, args.summary_out)

    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
