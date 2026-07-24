"""pipeline — Flujo A (Teoría), módulo orquestador importable (task 22).

Contrato (`src/theory/SPEC.md` §Cadena de componentes, §Idempotencia y
versionado; `CHECKPOINTS.md`): encadena los componentes YA implementados y
testeados de Flujo A, cada uno con su propia responsabilidad y sus propios
tests unitarios — este módulo NO reimplementa ninguna etapa, solo las
conecta:

    DriveMonitor -> Parser -> SubtypeDetector -> Cleaner -> LanguageDetector
      -> LanguageNormalizer -> QualityScorer -> FormatNormalizer
      -> /data/processed/v{N}/

`QualityScorer` (`src/utils/quality_scorer.score_quality`) no se invoca aquí
como paso explícito: `format_normalizer.generar_version` YA lo invoca
internamente por fragmento para construir `stats.json` (ver su docstring,
§"esquema de manifest.json / stats.json", task 11/10). Repetirlo en este
módulo sería puntuar el mismo texto dos veces sin ganar nada — la cadena
real, tal y como ya la ejecuta `generar_version`, sigue siendo
`... -> LanguageNormalizer -> QualityScorer -> FormatNormalizer -> v{N}/`.

No es la única función pública, expone `run_pipeline` como entrada
principal (task 23, `scripts/run_pipeline.py`, la invocará). No toca
`drive_monitor.py`, ningún `parser`, `subtype_detector`, `transcript_cleaner`,
`language_normalizer` ni `format_normalizer` — se limitan a importarse y
llamarse tal cual ya están (scope de esta tarea).

## Fricción resuelta: DriveMonitor marca "visto" en `scan()`, no tras éxito

`DriveMonitor.scan()` (`src/theory/drive_monitor.py`, tasks 1-2) persiste el
hash MD5 de cada fichero nuevo/modificado en `processed_files.json` ANTES de
devolver la lista de pendientes — es idempotencia de DETECCIÓN ("¿ha
cambiado este fichero desde la última vez que lo vi?"), no de FINALIZACIÓN
("¿completó este fichero toda la cadena hasta `generar_version`?"). Son dos
contratos distintos que esta tarea exige reconciliar sin tocar
`drive_monitor.py` (fuera de scope, ver `CLAUDE.md`/instrucciones de la
tarea).

Solución (sin tocar el código de `DriveMonitor`, solo el fichero JSON que
gestiona — mismo formato `{"nombre.ext": {"md5": ...}}`, contrato externo
documentado y testeado en `test_drive_monitor.py`, no una API privada):

1. Antes de escanear, se carga el estado ya comprometido de
   `processed_files.json` (`estado_comprometido` — ficheros que YA
   completaron la cadena entera en un run anterior).
2. Se llama a `DriveMonitor.scan()` (una vez por carpeta vigilada). Esto
   persiste en disco hashes frescos también de los ficheros que resultan
   pendientes — escritura prematura del punto de vista de este módulo.
3. Inmediatamente se REVIERTE el fichero en disco a `estado_comprometido`
   (deshace la escritura prematura) — el fichero en disco solo refleja
   ficheros realmente terminados en todo momento salvo por la ventana
   brevísima del propio `scan()`.
4. Cada fichero pendiente se procesa de forma INDEPENDIENTE (try/except): si
   falla en cualquier etapa antes de `generar_version`, no se añade a
   `estado_comprometido` — sigue pendiente para el siguiente run. Si tiene
   éxito, su fragmento pasa a la lista que se vuelca a `generar_version`.
5. `generar_version` se llama UNA VEZ con todos los documentos que superaron
   la cadena en este run (un run = un intento de versión `v{N}`, coherente
   con "Flujo batch" de `src/theory/SPEC.md`). Si `generar_version` falla
   (p.ej. error de disco), NINGÚN fichero de este batch se marca como
   procesado — todos vuelven a estar pendientes en el siguiente run (la
   propia inmutabilidad de `generar_version` hace que una versión no
   finalizada se pueda reintentar sin corromper nada, ver su docstring).
   Si tiene éxito, solo ENTONCES se añaden los hashes (ya calculados en el
   paso 2, sin recalcular) de los ficheros de este batch a
   `estado_comprometido`, y se escribe a disco — el commit final.

Con esto, "reanudación" es literal: un fichero solo desaparece de
`DriveMonitor.scan()` en el siguiente run si completó la cadena entera con
éxito hasta `generar_version`; si no, se reintenta desde el principio de su
propia cadena (no hay reanudación fina intra-fichero, igual que
`format_normalizer` documenta que tampoco la hay intra-versión).

## Fricción resuelta: metadatos de documento que ningún Parser produce

`FormatNormalizer.DocumentoEntrada` exige `fuente`/`tipo_fuente` explícitos
(ver `format_normalizer.py`, ningún parser los deriva del fichero de
origen). Decisiones de esta tarea, ambas documentadas aquí por no haber
todavía una fuente de metadatos real (Drive real diferido, P18):

- `tipo_fuente`: se infiere de la EXTENSIÓN del fichero de origen —
  `.txt` (WhisperX) -> `"transcripcion_curso"` (cursos transcritos, ver
  `docs/specs/00-overview.md` §2); `.pdf`/`.docx`/`.epub` -> `"teoria"`
  (libros). Es la única señal disponible hoy sin metadatos reales de Drive.
- `fuente`: se deriva del nombre de fichero (`path.stem`, con `_`/`-` hacia
  espacios) — un placeholder legible, no una lectura "mágica": cuando P18 se
  reactive con Drive real y aporte metadatos propios (título, autor), este
  punto es el que hay que sustituir, sin tocar el resto de la cadena.
- `autor`: siempre `None` (desconocido) — ninguna fuente de metadatos actual
  lo provee.
- `licencia`: `LICENCIA_POR_DEFECTO` de `format_normalizer`
  (`"personal_only"`, correcto por defecto para `externo*`).

## Ficheros sin Parser conocido (p.ej. `.gitkeep`)

Un fichero cuya extensión no está en `_PARSERS_POR_EXTENSION` se reporta en
`ResultadoPipeline.ignorados`, no en `fallidos` (no es un error de la
cadena, es simplemente material fuera del alcance de cualquier Parser de
Flujo A — p.ej. el `.gitkeep` que mantiene `data/raw/books/` en git). Se
marca igualmente como "visto" en `processed_files.json` (con el hash que
`DriveMonitor.scan()` ya calculó) para no reportarlo en cada run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.drive_monitor import DriveMonitor
from src.theory.normalizers.format_normalizer import (
    LICENCIA_POR_DEFECTO,
    DocumentoEntrada,
    generar_version,
)
from src.theory.normalizers.language_normalizer import Traductor, normalize_language
from src.theory.parsers.docx_parser import parse_docx
from src.theory.parsers.epub_parser import parse_epub
from src.theory.parsers.pdf_parser import parse_pdf
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

CARPETA_BOOKS_POR_DEFECTO = Path("data/raw/books")
CARPETA_NOTES_POR_DEFECTO = Path("data/raw/notes")
DIRECTORIO_PROCESADO_POR_DEFECTO = Path("data/processed")
RUTA_ESTADO_POR_DEFECTO = Path("data/state/processed_files.json")
"""Rutas por defecto, relativas al directorio de trabajo (mismo criterio que
`scripts/validate_corpus.py`: se asume invocación desde la raíz del repo).
`data/state/` ya existe en el repo (`.gitkeep`) como carpeta de estado."""

_PARSERS_POR_EXTENSION: dict[str, tuple[Callable[[Path], object], str]] = {
    ".txt": (parse_whisperx_transcript, "transcripcion_curso"),
    ".pdf": (parse_pdf, "teoria"),
    ".docx": (parse_docx, "teoria"),
    ".epub": (parse_epub, "teoria"),
}
"""Extensión -> (función de parseo, `tipo_fuente` inferido). Ver docstring
del módulo, §Fricción resuelta: metadatos de documento."""


@dataclass
class ResultadoFichero:
    """Resultado de intentar procesar UN fichero pendiente."""

    path: Path
    error: str


@dataclass
class ResultadoPipeline:
    """Resultado agregado de una llamada a `run_pipeline`.

    - `version_dir`: `Path` de la `v{N}/` generada, o `None` si no había
      ningún documento listo para volcar (nada pendiente, o todo falló antes
      de `generar_version`) o si `generar_version` en sí falló.
    - `procesados`: ficheros que completaron la cadena entera con éxito y
      quedaron marcados en `processed_files.json`.
    - `fallidos`: ficheros que fallaron en alguna etapa (o el propio
      `generar_version`, representado con `path=Path("<generar_version>")`)
      — NO quedan marcados, se reintentan en el siguiente run.
    - `ignorados`: ficheros sin Parser conocido (ver docstring del módulo).
    """

    version_dir: Optional[Path] = None
    procesados: list[Path] = field(default_factory=list)
    fallidos: list[ResultadoFichero] = field(default_factory=list)
    ignorados: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True si no hubo ningún fallo (fichero vacío de pendientes cuenta como ok)."""
        return not self.fallidos


def _cargar_estado(ruta_estado: Path) -> dict:
    """Lee `processed_files.json`; `{}` si todavía no existe.

    Mismo formato que `DriveMonitor._cargar_estado` — se reimplementa aquí
    (en vez de importarla) porque es un método privado de otra clase, no
    parte de su interfaz pública (ver `src/theory/drive_monitor.py`).
    """
    if not ruta_estado.exists():
        return {}
    return json.loads(ruta_estado.read_text(encoding="utf-8"))


def _guardar_estado(ruta_estado: Path, estado: dict) -> None:
    """Persiste `estado` en `processed_files.json` (mismo formato que DriveMonitor)."""
    ruta_estado.parent.mkdir(parents=True, exist_ok=True)
    ruta_estado.write_text(
        json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _fuente_desde_nombre(path: Path) -> str:
    """Deriva el metadato `fuente` (título legible) a partir del nombre de fichero.

    Ver docstring del módulo, §Fricción resuelta: metadatos de documento —
    placeholder documentado hasta que Drive real (P18) aporte metadatos
    propios de cada documento.
    """
    legible = path.stem.replace("_", " ").replace("-", " ").strip()
    return legible or path.stem


def _procesar_fichero(path: Path, traductor: Optional[Traductor]) -> DocumentoEntrada:
    """Ejecuta la cadena completa `Parser -> SubtypeDetector -> Cleaner ->
    LanguageNormalizer` sobre UN fichero y devuelve el `DocumentoEntrada`
    listo para `generar_version` (que internamente puntúa con QualityScorer,
    ver docstring del módulo).

    Deja propagar cualquier excepción de las etapas encadenadas: el llamador
    (`run_pipeline`) decide qué hacer con un fallo (no marcar el fichero
    como procesado, seguir con el resto del batch).
    """
    parser, tipo_fuente = _PARSERS_POR_EXTENSION[path.suffix.lower()]

    parseado = parser(path)
    texto = parseado.texto

    fragmentos_subtipo = detect_subtypes(texto)
    fragmentos_limpios = clean_fragments(fragmentos_subtipo)
    fragmentos_normalizados = normalize_language(fragmentos_limpios, traductor=traductor)

    return DocumentoEntrada(
        fragmentos=fragmentos_normalizados,
        fuente=_fuente_desde_nombre(path),
        tipo_fuente=tipo_fuente,
        autor=None,
        licencia=LICENCIA_POR_DEFECTO,
    )


def run_pipeline(
    carpetas: Optional[list[Path]] = None,
    *,
    directorio_procesado: Optional[Path] = None,
    ruta_estado: Optional[Path] = None,
    traductor: Optional[Traductor] = None,
    version: Optional[int] = None,
) -> ResultadoPipeline:
    """Ejecuta un run completo del Flujo A sobre los ficheros nuevos/modificados.

    - `carpetas`: carpetas vigiladas (por defecto `data/raw/books/` y
      `data/raw/notes/`, ver `src/theory/SPEC.md` §DriveMonitor). Carpetas
      que no existan se ignoran sin error (nada que vigilar en ellas).
    - `directorio_procesado`: destino de `v{N}/` (por defecto `data/processed/`).
    - `ruta_estado`: `processed_files.json` de idempotencia (por defecto
      `data/state/processed_files.json`).
    - `traductor`: inyectable, se pasa tal cual a `normalize_language` (por
      defecto DeepL real — ver `language_normalizer.py`); en tests se inyecta
      un traductor sin red.
    - `version`: se pasa tal cual a `generar_version` (por defecto, la
      siguiente versión libre).

    Marca cada fichero en `ruta_estado` SOLO tras completar con éxito toda
    la cadena hasta `generar_version` (ver docstring del módulo, §Fricción
    resuelta: DriveMonitor). Si no hay ningún fichero pendiente, no genera
    ninguna versión nueva (`version_dir=None`, listas vacías).
    """
    carpetas = (
        list(carpetas)
        if carpetas is not None
        else [CARPETA_BOOKS_POR_DEFECTO, CARPETA_NOTES_POR_DEFECTO]
    )
    directorio_procesado = Path(directorio_procesado) if directorio_procesado is not None else DIRECTORIO_PROCESADO_POR_DEFECTO
    ruta_estado = Path(ruta_estado) if ruta_estado is not None else RUTA_ESTADO_POR_DEFECTO

    estado_comprometido = _cargar_estado(ruta_estado)

    pendientes: list[Path] = []
    for carpeta in carpetas:
        carpeta = Path(carpeta)
        if not carpeta.exists():
            continue
        monitor = DriveMonitor(folder=carpeta, state_path=ruta_estado)
        pendientes.extend(monitor.scan())

    estado_fresco = _cargar_estado(ruta_estado)
    # Revierte la escritura prematura de DriveMonitor.scan() (ver docstring
    # del módulo): el fichero en disco vuelve a reflejar solo lo realmente
    # comprometido hasta este punto.
    _guardar_estado(ruta_estado, estado_comprometido)

    elegibles: list[Path] = []
    ignorados: list[Path] = []
    for path in pendientes:
        if path.suffix.lower() in _PARSERS_POR_EXTENSION:
            elegibles.append(path)
        else:
            ignorados.append(path)
            if path.name in estado_fresco:
                estado_comprometido[path.name] = estado_fresco[path.name]

    documentos_listos: list[tuple[Path, DocumentoEntrada]] = []
    fallidos: list[ResultadoFichero] = []

    for path in elegibles:
        try:
            documento = _procesar_fichero(path, traductor=traductor)
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de la cadena cuenta
            fallidos.append(ResultadoFichero(path=path, error=str(exc)))
            continue
        documentos_listos.append((path, documento))

    version_dir: Optional[Path] = None
    procesados: list[Path] = []

    if documentos_listos:
        documentos = [documento for _, documento in documentos_listos]
        try:
            version_dir = generar_version(documentos, directorio_procesado, version=version)
        except Exception as exc:  # noqa: BLE001
            # Fallo en el último paso: ningún fichero de este batch se marca
            # como procesado (ver docstring del módulo) — todos vuelven a
            # estar pendientes en el siguiente run.
            fallidos.append(ResultadoFichero(path=Path("<generar_version>"), error=str(exc)))
        else:
            for path, _ in documentos_listos:
                estado_comprometido[path.name] = estado_fresco[path.name]
                procesados.append(path)

    _guardar_estado(ruta_estado, estado_comprometido)

    return ResultadoPipeline(
        version_dir=version_dir,
        procesados=procesados,
        fallidos=fallidos,
        ignorados=ignorados,
    )
