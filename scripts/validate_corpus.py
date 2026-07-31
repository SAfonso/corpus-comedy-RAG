"""validate_corpus — gate de validación de CONTENIDO de Flujo A (Teoría) +
validación de TOPOLOGÍA del almacenamiento del sistema entero (task 68).

Contrato (`src/theory/SPEC.md` §"`validate_corpus.py` sin `manifest.json`
(task 62)", `CHECKPOINTS.md`): último paso antes de dar por bueno un
commit/PR que toque la salida de Flujo A. Hasta la task 62 validaba
`v{N}/documents/*.txt` + `manifest.json` en disco; desde P25 la fuente de
verdad es `silver.teoria_documentos` (Supabase) + el objeto correspondiente
en el bucket privado `silver-teoria` — este script deja de leer `v{N}/` como
fuente POR DEFECTO. `generar_version`/`v{N}` no se retiran con esta task
(scope exclusivo de la task 63): este script simplemente deja de consumirlos
como gate por defecto.

## Frontera task 62 / task 68 (`src/theory/SPEC.md` §"task 62 valida
## contenido, task 68 valida topología")

Los dos modos de abajo (Supabase, ruta local) son de CONTENIDO — de la task
62, sin cambios de esta task. El modo nuevo, `--topologia` (task 68, ver
`check_silver_tiene_bronze_teoria` y compañía más abajo en el módulo), es
distinto en todo: valida la relación Bronze↔Silver↔Storage de TODAS las
filas de los cuatro destinos fijos (`bronze`/`silver` x `teoria`/`historico`,
`src/utils/document_store.DESTINOS`), no solo las vigentes de teoría; no lee
ni parsea contenido de documento en ningún caso (ni `.md`, ni cabecera YAML);
y no es un gate por commit sino una auditoría del sistema entero. `silver.chistes`
no tiene destino propio en `DESTINOS` — no es una tabla de documentos, es la
unidad indexable de B/C (`src/jokes/SPEC.md` §Storage) — y este script no le
añade, reabre ni duplica ningún check de contenido: sigue fuera de su
alcance, igual que antes de esta task.

## Dos modos (contenido, task 62 — sin cambios)

1. **Supabase (por defecto, sin argumento)**: construye un `TeoriaStore` real
   (credenciales de `.env` — si faltan, `TeoriaStoreError` con mensaje claro,
   nunca un traceback críptico), lista las filas VIGENTES de
   `silver.teoria_documentos` (`TeoriaStore.listar_documentos_vigentes()`:
   una por clave de linaje `origen_hash_md5`, la de `created_at` más
   reciente por grupo — los renderizados antiguos se conservan por diseño y
   NO se validan, ver SPEC), descarga el objeto de cada una desde
   `silver-teoria` y corre los 7 checks de contenido + `check_fila_objeto_coherente`.
2. **Ruta local (`python scripts/validate_corpus.py <ruta>`)**: para testear
   los 7 checks con fixtures reales sin red. `<ruta>` apunta a un directorio
   que contiene ficheros `.md` DIRECTAMENTE (glob `*.md` — ya NO
   `v{N}/documents/*.txt` + `manifest.json`, ese formato se retira con esta
   task). Corre SOLO los 7 checks: no hay fila Supabase con la que comparar,
   así que `check_fila_objeto_coherente` NO APLICA — se reporta
   explícitamente como tal en el resumen impreso (nunca en silencio, nunca
   como fallo) y el total pasa a ser sobre 7, no sobre 8.

## Los 8 checks (7 conservados + `check_fila_objeto_coherente`)

1. ningún fichero tiene timestamps `[XX.XXs]`
2. ningún fichero tiene `SPEAKER_XX:`
3. todos los ficheros tienen cabecera de metadatos completa (los 7 campos de
   `CHECKPOINTS.md`: `fuente`, `autor`, `idioma_original`, `idioma_fragmento`,
   `subtipo`, `tipo_fuente`, `licencia`)
4. ningún fichero tiene menos de 100 palabras
5. ningún fichero tiene más de 50.000 palabras
6. no hay ficheros duplicados (hash MD5 del contenido completo)
7. todos los idiomas detectados (`idioma_original`/`idioma_fragmento` de cada
   fragmento) están en la lista permitida (`es`, `en` — corpus bilingüe
   explícito de teoría, ver `src/theory/SPEC.md` §Idioma)
8. `check_fila_objeto_coherente` (sustituye a `check_manifest_sincronizado`,
   retirado junto con `manifest.json`): por cada fila vigente, (a) el objeto
   se descarga sin error desde `silver-teoria` bajo su `object_path` — si
   `TeoriaStore.descargar_documento` lanza, es el fallo de este check (objeto
   ausente/404, el huérfano "fila sin objeto correcto" que `document_store`
   decide tolerar al escribir el objeto antes que la fila); (b)
   `md5(bytes descargados) == fila["hash_md5"]`. Más fuerte que el check 8
   original (que solo comparaba nombres, nunca contenido). NO comprueba la
   completitud Bronze<->Silver — eso es la task 68 (topología cross-capa),
   fuera de alcance de este script (§SPEC "task 62 valida contenido, task 68
   valida topología").

## Por qué un parser YAML propio (no `pyyaml`)

Mismo criterio que `format_normalizer.py` (ver su docstring, §Decisión:
emisor YAML propio): `pyyaml` no está en `requirements.txt`/`src/theory/SPEC.md`
§Stack, y el YAML que hay que leer aquí es exactamente el que emite
`_yaml_frontmatter` (forma fija, controlada por nosotros) — un parser mínimo
a medida (`_parsear_frontmatter`) basta, sin arrastrar una dependencia nueva
para un formato tan acotado. Si `format_normalizer.py` cambiara su forma de
emisión, este parser tendría que actualizarse en paralelo (mismo motivo por
el que ambos ficheros documentan el formato exacto en sus docstrings).

## Diseño: funciones puras por check

Cada uno de los 7 checks de contenido es una función pura
`check_xxx(documentos: list[DocumentoLeido]) -> ResultadoCheck` que NO hace
I/O — recibe los documentos ya leídos y parseados, sin que le importe si
vinieron de disco (`cargar_documentos`, modo ruta local) o de Storage
(`_leer_filas_vigentes`, modo Supabase): mismo algoritmo de parseo en los dos
casos (`_parsear_documento`), agnóstico del origen. `check_fila_objeto_coherente`
es la excepción deliberada: necesita, además del `DocumentoLeido`, la fila
Supabase y los bytes crudos descargados (para el hash), así que opera sobre
`list[FilaSilverLeida]`, no sobre `list[DocumentoLeido]` — es justo el check
que no tiene equivalente en modo ruta local. Determinista, sin LLM.

## CLI

    python scripts/validate_corpus.py [ruta_local | --topologia]

Sin argumento: modo Supabase de contenido (filas vigentes de
`silver.teoria_documentos`). Con `ruta_local`: modo ruta local de contenido,
valida los `.md` de esa ruta directamente (sin red, sin `TeoriaStore`, útil
para testear contra un `tmp_path` con fixtures). Con `--topologia`: modo
topología (task 68), construye un `DocumentStore` real y audita los cuatro
destinos fijos. `ruta_local` y `--topologia` son mutuamente excluyentes
(`parser.error`, exit code 2, si se dan los dos). Exit code 0 si todos los
checks aplicables pasan, 1 si alguno falla o si el modo elegido no puede ni
siquiera arrancar (credenciales ausentes) — nunca una excepción críptica
para esos casos.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Bootstrap de sys.path: permite invocar `python scripts/validate_corpus.py`
# sin depender de PYTHONPATH ni de que el invocador esté en la raíz del repo
# (mismo patrón que `scripts/run_pipeline.py`/`scripts/reprocesar_bronze_pendiente.py`
# — necesario desde esta task porque, a diferencia de antes de la task 62,
# el script ahora importa `src.theory.teoria_store` para el modo Supabase).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.theory.teoria_store import TeoriaStore, TeoriaStoreError  # noqa: E402
from src.utils.document_store import DESTINOS, DocumentStore, DocumentStoreError  # noqa: E402

MIN_PALABRAS = 100
MAX_PALABRAS = 50_000
IDIOMAS_PERMITIDOS = frozenset({"es", "en"})

CAMPOS_DOCUMENTO_REQUERIDOS = ("fuente", "autor", "tipo_fuente", "licencia")
CAMPOS_FRAGMENTO_REQUERIDOS = ("subtipo", "idioma_original", "idioma_fragmento")
"""Junto con los 4 de documento, suman los 7 campos de `CHECKPOINTS.md`."""

BUCKET_SILVER_TEORIA = "silver-teoria"

_PATRON_TIMESTAMP = re.compile(r"\[\d+(?:\.\d+)?s\]")
_PATRON_SPEAKER = re.compile(r"SPEAKER_\d+:")


# --- Lectura y parseo (I/O mínimo, aislado del resto) -----------------------


@dataclass
class DocumentoLeido:
    """Un documento (`.md` de Silver, o `.md`/`.txt` de un fixture local) ya
    leído y parseado.

    `path_relativo`: identifica el documento en los mensajes de error de los
    7 checks. En modo Supabase es el `object_path` de la fila; en modo ruta
    local, el nombre del fichero.
    """

    path_relativo: str
    contenido: str
    cabecera_raw: str
    cuerpo: str
    metadatos: dict
    fragmentos_meta: list


def _parsear_valor_yaml(valor: str):
    """Inversa de `_yaml_valor` de `format_normalizer.py` (`"str"` -> `str`, `null` -> `None`)."""
    valor = valor.strip()
    if valor == "null":
        return None
    if len(valor) >= 2 and valor[0] == '"' and valor[-1] == '"':
        interior = valor[1:-1]
        interior = interior.replace('\\"', '"').replace("\\\\", "\\")
        return interior
    return valor


def _parsear_frontmatter(cabecera: str) -> tuple[dict, list]:
    """Parsea la cabecera YAML (sin delimitadores `---`) emitida por
    `_yaml_frontmatter` de `format_normalizer.py`: claves top-level
    (`fuente`/`autor`/`tipo_fuente`/`licencia`) + `fragmentos:` con una
    secuencia de mappings (`subtipo`/`idioma_original`/`idioma_fragmento`).

    Devuelve `(metadatos_documento, fragmentos_meta)`. Tolerante a campos
    ausentes (un fichero con la cabecera mutilada debe poder leerse para que
    `check_cabecera_completa` pueda reportar QUÉ falta, no reventar al leerlo).
    """
    metadatos: dict = {}
    fragmentos: list = []
    fragmento_actual: Optional[dict] = None
    en_fragmentos = False

    for linea in cabecera.splitlines():
        if not linea.strip():
            continue
        if not en_fragmentos:
            if linea.strip() == "fragmentos:":
                en_fragmentos = True
                continue
            clave, separador, valor = linea.partition(":")
            if not separador:
                continue
            metadatos[clave.strip()] = _parsear_valor_yaml(valor)
        else:
            if linea.startswith("  - "):
                if fragmento_actual is not None:
                    fragmentos.append(fragmento_actual)
                fragmento_actual = {}
                resto = linea[4:]
                clave, separador, valor = resto.partition(":")
                if separador:
                    fragmento_actual[clave.strip()] = _parsear_valor_yaml(valor)
            elif linea.startswith("    ") and fragmento_actual is not None:
                clave, separador, valor = linea.strip().partition(":")
                if separador:
                    fragmento_actual[clave.strip()] = _parsear_valor_yaml(valor)
            # cualquier otra línea dentro de "fragmentos:" que no encaje en el
            # patrón esperado se ignora: no es responsabilidad de este parser
            # validar la forma del YAML, solo extraer lo que reconoce (el
            # check de cabecera completa es quien decide si falta algo).

    if fragmento_actual is not None:
        fragmentos.append(fragmento_actual)

    return metadatos, fragmentos


def _parsear_documento(contenido: str, path_relativo: str) -> DocumentoLeido:
    """Algoritmo de parseo puro (cabecera YAML `---\\n...\\n---\\n` + cuerpo),
    SIN cambios respecto a antes de la task 62 — solo se extrajo de
    `leer_documento` para poder aplicarlo también a texto ya en memoria
    (bytes descargados de Storage y decodificados), no solo a un `Path` en
    disco. Agnóstico del origen del texto (mismo formato que emite
    `render_document` de `format_normalizer.py`, tanto para el `.txt` de
    `v{N}/` legacy como para el `.md` que sube la task 60 a Silver)."""
    partes = contenido.split("---\n", 2)

    if len(partes) < 3 or partes[0] != "":
        # No hay cabecera YAML reconocible (delimitador de apertura ausente o
        # desplazado): se trata como cabecera vacía + todo el contenido como
        # cuerpo, para que check_cabecera_completa lo marque como incompleto
        # en vez de reventar aquí con una excepción críptica.
        return DocumentoLeido(
            path_relativo=path_relativo,
            contenido=contenido,
            cabecera_raw="",
            cuerpo=contenido,
            metadatos={},
            fragmentos_meta=[],
        )

    cabecera_raw, cuerpo = partes[1], partes[2]
    if cuerpo.startswith("\n"):
        cuerpo = cuerpo[1:]
    metadatos, fragmentos_meta = _parsear_frontmatter(cabecera_raw)

    return DocumentoLeido(
        path_relativo=path_relativo,
        contenido=contenido,
        cabecera_raw=cabecera_raw,
        cuerpo=cuerpo,
        metadatos=metadatos,
        fragmentos_meta=fragmentos_meta,
    )


def leer_documento(ruta: Path, path_relativo: str) -> DocumentoLeido:
    """Lee y parsea UN `.md`/`.txt` de disco (modo ruta local). Envoltorio de
    I/O sobre `_parsear_documento` — el algoritmo de parseo en sí no cambia."""
    contenido = ruta.read_text(encoding="utf-8")
    return _parsear_documento(contenido, path_relativo)


def cargar_documentos(directorio: Path) -> list[DocumentoLeido]:
    """Modo ruta local: lee todos los `.md` de `directorio` DIRECTAMENTE
    (glob `*.md`, ordenados por nombre para determinismo del reporte).

    Ya NO `directorio/documents/*.txt` + `manifest.json` — ese formato se
    retira con la task 62 (`src/theory/SPEC.md`); `directorio` apunta ahora
    al directorio que contiene los `.md` sin más, típicamente un `tmp_path`
    de test con fixtures."""
    if not directorio.is_dir():
        return []
    rutas = sorted(directorio.glob("*.md"))
    return [leer_documento(ruta, ruta.name) for ruta in rutas]


# --- Resultado de un check ---------------------------------------------------


@dataclass
class ResultadoCheck:
    """Resultado de un check individual. `detalles`: lista de líneas humanas
    describiendo cada incidencia encontrada (vacía si `ok=True`)."""

    nombre: str
    ok: bool
    detalles: list = field(default_factory=list)


# --- Los 7 checks de contenido (funciones puras, testeables por separado) ---


def check_sin_timestamps(documentos: list[DocumentoLeido]) -> ResultadoCheck:
    """Check 1: ningún fichero conserva timestamps `[XX.XXs]` de WhisperX sin limpiar."""
    detalles = [
        f"{doc.path_relativo}: contiene timestamp(s) sin limpiar (p.ej. {_PATRON_TIMESTAMP.search(doc.contenido).group()!r})"
        for doc in documentos
        if _PATRON_TIMESTAMP.search(doc.contenido)
    ]
    return ResultadoCheck("sin_timestamps", not detalles, detalles)


def check_sin_speaker_tags(documentos: list[DocumentoLeido]) -> ResultadoCheck:
    """Check 2: ningún fichero conserva `SPEAKER_XX:` de WhisperX sin limpiar."""
    detalles = [
        f"{doc.path_relativo}: contiene speaker tag sin limpiar (p.ej. {_PATRON_SPEAKER.search(doc.contenido).group()!r})"
        for doc in documentos
        if _PATRON_SPEAKER.search(doc.contenido)
    ]
    return ResultadoCheck("sin_speaker_tags", not detalles, detalles)


def check_cabecera_completa(documentos: list[DocumentoLeido]) -> ResultadoCheck:
    """Check 3: los 7 campos de `CHECKPOINTS.md` presentes — los 4 de
    documento a nivel superior, y los 3 por fragmento en CADA fragmento (un
    documento sin ningún fragmento tampoco tiene cabecera completa: no hay
    `subtipo`/`idioma_original`/`idioma_fragmento` que mostrar)."""
    detalles = []
    for doc in documentos:
        faltantes_doc = [c for c in CAMPOS_DOCUMENTO_REQUERIDOS if c not in doc.metadatos]
        if faltantes_doc:
            detalles.append(
                f"{doc.path_relativo}: faltan campos de documento en la cabecera: {faltantes_doc}"
            )
        if not doc.fragmentos_meta:
            detalles.append(
                f"{doc.path_relativo}: la cabecera no tiene ningún fragmento en 'fragmentos:'"
            )
        for i, frag in enumerate(doc.fragmentos_meta):
            faltantes_frag = [c for c in CAMPOS_FRAGMENTO_REQUERIDOS if c not in frag]
            if faltantes_frag:
                detalles.append(
                    f"{doc.path_relativo}: fragmento {i} sin campos {faltantes_frag}"
                )
    return ResultadoCheck("cabecera_completa", not detalles, detalles)


def check_min_palabras(documentos: list[DocumentoLeido]) -> ResultadoCheck:
    """Check 4: ningún fichero tiene menos de `MIN_PALABRAS` en el cuerpo
    (cabecera YAML excluida — cuenta contenido, no metadatos)."""
    detalles = []
    for doc in documentos:
        n = len(doc.cuerpo.split())
        if n < MIN_PALABRAS:
            detalles.append(
                f"{doc.path_relativo}: {n} palabras (< {MIN_PALABRAS}, probable error de parsing)"
            )
    return ResultadoCheck("min_palabras", not detalles, detalles)


def check_max_palabras(documentos: list[DocumentoLeido]) -> ResultadoCheck:
    """Check 5: ningún fichero tiene más de `MAX_PALABRAS` en el cuerpo
    (probable error de concatenación de varios documentos en uno)."""
    detalles = []
    for doc in documentos:
        n = len(doc.cuerpo.split())
        if n > MAX_PALABRAS:
            detalles.append(
                f"{doc.path_relativo}: {n} palabras (> {MAX_PALABRAS}, probable error de concatenación)"
            )
    return ResultadoCheck("max_palabras", not detalles, detalles)


def check_sin_duplicados(documentos: list[DocumentoLeido]) -> ResultadoCheck:
    """Check 6: no hay dos ficheros con el mismo hash MD5 de contenido completo."""
    por_hash: dict[str, list[str]] = {}
    for doc in documentos:
        h = hashlib.md5(doc.contenido.encode("utf-8")).hexdigest()
        por_hash.setdefault(h, []).append(doc.path_relativo)

    detalles = [
        f"contenido duplicado (md5={h}): {sorted(rutas)}"
        for h, rutas in por_hash.items()
        if len(rutas) > 1
    ]
    return ResultadoCheck("sin_duplicados", not detalles, detalles)


def check_idiomas_permitidos(documentos: list[DocumentoLeido]) -> ResultadoCheck:
    """Check 7: `idioma_original`/`idioma_fragmento` de cada fragmento están
    en `IDIOMAS_PERMITIDOS` (`es`, `en` — ver `src/theory/SPEC.md` §Idioma)."""
    detalles = []
    for doc in documentos:
        for i, frag in enumerate(doc.fragmentos_meta):
            for campo in ("idioma_original", "idioma_fragmento"):
                valor = frag.get(campo)
                if valor not in IDIOMAS_PERMITIDOS:
                    detalles.append(
                        f"{doc.path_relativo}: fragmento {i} tiene {campo}={valor!r}, "
                        f"fuera de la lista permitida {sorted(IDIOMAS_PERMITIDOS)}"
                    )
    return ResultadoCheck("idiomas_permitidos", not detalles, detalles)


CHECKS = (
    check_sin_timestamps,
    check_sin_speaker_tags,
    check_cabecera_completa,
    check_min_palabras,
    check_max_palabras,
    check_sin_duplicados,
    check_idiomas_permitidos,
)
"""Los 7 checks de contenido, comunes a los dos modos — cada uno solo
necesita `list[DocumentoLeido]`, nunca la fila Supabase (ver
`check_fila_objeto_coherente` para el check 8, que sí la necesita)."""


# --- Check 8: fila Silver <-> objeto de Storage (modo Supabase, task 62) ----


@dataclass
class FilaSilverLeida:
    """Una fila VIGENTE de `silver.teoria_documentos` con el resultado de
    intentar descargar y parsear su objeto de `silver-teoria`.

    `contenido_bytes`/`documento` son `None` si la descarga falló (objeto
    ausente/404 — ver `check_fila_objeto_coherente`, punto (a)): esa fila
    sigue apareciendo aquí para que el check 8 la reporte, pero no aporta un
    `DocumentoLeido` a los 7 checks de contenido (no hay contenido que
    validar sin objeto)."""

    fila: dict
    contenido_bytes: Optional[bytes] = None
    documento: Optional[DocumentoLeido] = None
    error_descarga: Optional[str] = None


def _leer_filas_vigentes(store: TeoriaStore, filas_vigentes: list[dict]) -> list[FilaSilverLeida]:
    """Descarga y parsea el objeto de cada fila vigente. Un fallo de
    descarga de UNA fila no aborta la validación de las demás — se registra
    en `FilaSilverLeida.error_descarga` y lo reporta `check_fila_objeto_coherente`."""
    resultado = []
    for fila in filas_vigentes:
        try:
            contenido_bytes = store.descargar_documento(fila["bucket"], fila["object_path"])
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de storage es "objeto ausente" para el check 8
            resultado.append(FilaSilverLeida(fila=fila, error_descarga=str(exc)))
            continue
        documento = _parsear_documento(contenido_bytes.decode("utf-8"), fila["object_path"])
        resultado.append(
            FilaSilverLeida(fila=fila, contenido_bytes=contenido_bytes, documento=documento)
        )
    return resultado


def check_fila_objeto_coherente(filas_leidas: list[FilaSilverLeida]) -> ResultadoCheck:
    """Check 8 (sustituye a `check_manifest_sincronizado`, retirado junto con
    `manifest.json`): por cada fila vigente, (a) el objeto se descargó sin
    error de `silver-teoria` (si no, objeto ausente/404 — el huérfano "fila
    sin objeto correcto" que `document_store` decide tolerar al escribir el
    objeto antes que la fila, §DocumentStore); (b)
    `md5(bytes descargados) == fila["hash_md5"]`. Más fuerte que el check 8
    original (que solo comparaba nombres, nunca contenido)."""
    detalles = []
    for fl in filas_leidas:
        object_path = fl.fila.get("object_path", "<object_path desconocido>")
        if fl.contenido_bytes is None:
            detalles.append(
                f"{object_path}: objeto no encontrado en {BUCKET_SILVER_TEORIA} "
                f"({fl.error_descarga})"
            )
            continue
        md5_real = hashlib.md5(fl.contenido_bytes).hexdigest()
        md5_esperado = fl.fila.get("hash_md5")
        if md5_real != md5_esperado:
            detalles.append(
                f"{object_path}: contenido descargado no coincide con la fila "
                f"(fila.hash_md5={md5_esperado!r}, md5(objeto)={md5_real!r})"
            )
    return ResultadoCheck("fila_objeto_coherente", not detalles, detalles)


# --- Orquestación de los dos modos -------------------------------------------


def validar_supabase(store: TeoriaStore) -> list[ResultadoCheck]:
    """Modo Supabase: filas vigentes de `silver.teoria_documentos` + objeto
    de `silver-teoria` -> 7 checks de contenido + `check_fila_objeto_coherente`."""
    filas_vigentes = store.listar_documentos_vigentes()
    filas_leidas = _leer_filas_vigentes(store, filas_vigentes)
    documentos = [fl.documento for fl in filas_leidas if fl.documento is not None]

    resultados = [check(documentos) for check in CHECKS]
    resultados.append(check_fila_objeto_coherente(filas_leidas))
    return resultados


def validar_ruta_local(directorio: Path) -> list[ResultadoCheck]:
    """Modo ruta local: solo los 7 checks de contenido — no hay fila Supabase
    con la que comparar, así que `check_fila_objeto_coherente` no aplica (ver
    `main`, que lo comunica explícitamente en el resumen impreso)."""
    documentos = cargar_documentos(directorio)
    return [check(documentos) for check in CHECKS]


# --- Checks de topología del almacenamiento (task 68) ------------------------
#
# NO son checks de contenido (eso es la task 62, arriba, intacto): validan la
# relación entre capas y entre fila y objeto, para los CUATRO destinos fijos
# de `document_store.DESTINOS` (bronze/silver x teoria/historico), sin
# distinguir "vigente"/"histórico" como sí hace `check_fila_objeto_coherente`
# — la task 68 "corre sobre el sistema entero" (`src/theory/SPEC.md` §"task
# 62 valida contenido, task 68 valida topología"), incluidos los renderizados
# antiguos que el modo Supabase de contenido ya no mira. Puramente
# relacionales sobre las filas ya traídas (mismo criterio de "funciones puras
# por check" del resto del módulo): ni parsean ni descargan contenido salvo
# `TopologiaStore.objeto_existe`, que solo comprueba existencia (nunca hash —
# esa comparación más fuerte, solo para Silver teoría vigente, ya la hace
# `check_fila_objeto_coherente`, task 62; no se duplica aquí).
#
# El huérfano "objeto sin fila" (`src/utils/SPEC.md` §DocumentStore, orden
# objeto→fila) es TOLERADO por diseño y no se comprueba aquí — solo el
# inverso, "fila sin objeto", que sí es una garantía real (CLAUDE.md, capa
# Bronze).


def check_silver_tiene_bronze_teoria(
    filas_bronze: list[dict], filas_silver: list[dict]
) -> ResultadoCheck:
    """Cada fila de `silver.teoria_documentos` referencia, por
    `origen_hash_md5`, una fila existente en `bronze.teoria_documentos`
    (`hash_md5`) — `src/theory/SPEC.md` §`silver.teoria_documentos`:
    `origen_hash_md5` es "la columna de linaje que manda", la única clave que
    existe en los dos modos (Drive y legacy) porque Silver de teoría se
    captura SIEMPRE en modo legacy (`drive_file_id=None`), incluso cuando su
    Bronze vino de Drive (`src/theory/pipeline.py:_persistir_silver`)."""
    hashes_bronze = {f.get("hash_md5") for f in filas_bronze}
    detalles = [
        f"{f.get('object_path', '<object_path desconocido>')}: "
        f"origen_hash_md5={f.get('origen_hash_md5')!r} no tiene fila correspondiente "
        f"en bronze.teoria_documentos"
        for f in filas_silver
        if f.get("origen_hash_md5") not in hashes_bronze
    ]
    return ResultadoCheck("silver_tiene_bronze_teoria", not detalles, detalles)


def check_silver_tiene_bronze_historico(
    filas_bronze: list[dict], filas_silver: list[dict]
) -> ResultadoCheck:
    """Cada fila de `silver.historico_documentos` referencia, por el par
    `(drive_file_id, modified_time)`, una fila existente en
    `bronze.historico_documentos` — `src/jokes/historico/SPEC.md` §"Cómo
    referencia Silver a su Bronze": el mismo par, no una FK, porque el
    histórico no tiene modo legacy (siempre Drive, tasks 64/65)."""
    claves_bronze = {(f.get("drive_file_id"), f.get("modified_time")) for f in filas_bronze}
    detalles = [
        f"{f.get('object_path', '<object_path desconocido>')}: "
        f"(drive_file_id={f.get('drive_file_id')!r}, modified_time={f.get('modified_time')!r}) "
        f"no tiene fila correspondiente en bronze.historico_documentos"
        for f in filas_silver
        if (f.get("drive_file_id"), f.get("modified_time")) not in claves_bronze
    ]
    return ResultadoCheck("silver_tiene_bronze_historico", not detalles, detalles)


def check_filas_tienen_objeto(
    filas: list[dict], existe_objeto, nombre_check: str
) -> ResultadoCheck:
    """Cada fila tiene su objeto correspondiente en su bucket (`fila.bucket`,
    `fila.object_path`) — genérico, se reutiliza para los cuatro destinos
    (bronze/silver x teoria/historico). `existe_objeto(bucket, object_path)
    -> bool` es inyectable (`TopologiaStore.objeto_existe` en producción, un
    `dict`/lambda en tests) para no acoplar este check a `supabase-py`."""
    detalles = [
        f"{f.get('object_path', '<object_path desconocido>')}: objeto no encontrado "
        f"en bucket {f.get('bucket')!r}"
        for f in filas
        if not existe_objeto(f.get("bucket"), f.get("object_path"))
    ]
    return ResultadoCheck(nombre_check, not detalles, detalles)


def check_claves_idempotencia_unicas(filas: list[dict], nombre_check: str) -> ResultadoCheck:
    """Dentro de UNA tabla, no hay dos filas con la misma clave de
    idempotencia — los mismos dos índices únicos PARCIALES de Postgres (task
    53) que ya consulta `DocumentStore._buscar_fila_existente` antes de
    insertar: `(drive_file_id, modified_time)` cuando `drive_file_id` no es
    `None` (modo Drive), `hash_md5` cuando sí lo es (modo legacy). Los dos
    espacios de claves NUNCA se mezclan entre sí (son índices distintos), así
    que una fila Drive y una legacy jamás colisionan aunque compartan algún
    valor por casualidad."""
    por_par: dict[tuple, list] = {}
    por_hash_legacy: dict[Any, list] = {}
    for f in filas:
        identificador = f.get("object_path", f.get("id"))
        if f.get("drive_file_id") is not None:
            clave = (f["drive_file_id"], f.get("modified_time"))
            por_par.setdefault(clave, []).append(identificador)
        else:
            por_hash_legacy.setdefault(f.get("hash_md5"), []).append(identificador)

    detalles = [
        f"(drive_file_id, modified_time)={clave!r} duplicado en {len(ids)} filas: "
        f"{sorted(str(i) for i in ids)}"
        for clave, ids in por_par.items()
        if len(ids) > 1
    ]
    detalles += [
        f"hash_md5(legacy)={clave!r} duplicado en {len(ids)} filas: "
        f"{sorted(str(i) for i in ids)}"
        for clave, ids in por_hash_legacy.items()
        if len(ids) > 1
    ]
    return ResultadoCheck(nombre_check, not detalles, detalles)


class TopologiaStore:
    """Capa fina de solo lectura sobre `DocumentStore` para el modo
    `--topologia` (task 68): reutiliza su cliente/credenciales y el mapeo
    `DESTINOS` (mismos cuatro destinos fijos que la captura), pero SOLO hace
    `SELECT` y comprobaciones de existencia de objeto — nunca llama a
    `.capturar()` ni a ningún método de escritura. Bronze es append-only
    (`CLAUDE.md`); este componente ni siquiera tiene la posibilidad de
    violarlo, igual que `DocumentStore` no expone `UPDATE`/`DELETE`.
    """

    def __init__(self, document_store: DocumentStore):
        self._store = document_store

    def filas(self, capa: str, flujo: str) -> list[dict]:
        """Todas las filas de la tabla de `(capa, flujo)` — sin filtrar por
        vigencia, a diferencia de `TeoriaStore.listar_documentos_vigentes`
        (task 68 corre sobre el sistema entero, no solo lo consumible hoy)."""
        destino = DESTINOS[(capa, flujo)]
        resultado = (
            self._store.client.schema(destino.schema).table(destino.tabla).select("*").execute()
        )
        return resultado.data or []

    def objeto_existe(self, bucket: Optional[str], object_path: Optional[str]) -> bool:
        """Existencia (no contenido): intenta descargar y descarta los bytes.
        Mismo mecanismo que `check_fila_objeto_coherente` (task 62) para
        detectar el 404, sin comparar hash — esta comprobación es más barata
        y deliberadamente más débil (ver docstring de la sección)."""
        if not bucket or not object_path:
            return False
        try:
            self._store.client.storage.from_(bucket).download(object_path)
            return True
        except Exception:  # noqa: BLE001 — cualquier fallo de storage es "objeto ausente" aquí
            return False


def validar_topologia(store: "TopologiaStore") -> list[ResultadoCheck]:
    """Modo topología (task 68): 2 checks de linaje Silver→Bronze (uno por
    flujo) + 4 checks de fila→objeto + 4 checks de claves duplicadas (uno por
    tabla) = 10 checks en total sobre los cuatro destinos fijos."""
    bronze_teoria = store.filas("bronze", "teoria")
    silver_teoria = store.filas("silver", "teoria")
    bronze_historico = store.filas("bronze", "historico")
    silver_historico = store.filas("silver", "historico")

    return [
        check_silver_tiene_bronze_teoria(bronze_teoria, silver_teoria),
        check_silver_tiene_bronze_historico(bronze_historico, silver_historico),
        check_filas_tienen_objeto(bronze_teoria, store.objeto_existe, "bronze_teoria_tiene_objeto"),
        check_filas_tienen_objeto(silver_teoria, store.objeto_existe, "silver_teoria_tiene_objeto"),
        check_filas_tienen_objeto(
            bronze_historico, store.objeto_existe, "bronze_historico_tiene_objeto"
        ),
        check_filas_tienen_objeto(
            silver_historico, store.objeto_existe, "silver_historico_tiene_objeto"
        ),
        check_claves_idempotencia_unicas(bronze_teoria, "claves_unicas_bronze_teoria"),
        check_claves_idempotencia_unicas(silver_teoria, "claves_unicas_silver_teoria"),
        check_claves_idempotencia_unicas(bronze_historico, "claves_unicas_bronze_historico"),
        check_claves_idempotencia_unicas(silver_historico, "claves_unicas_silver_historico"),
    ]


# --- CLI ---------------------------------------------------------------------


def _imprimir_resultados(resultados: list[ResultadoCheck]) -> None:
    for resultado in resultados:
        marca = "✓" if resultado.ok else "✗"
        print(f"{marca} {resultado.nombre}")
        for detalle in resultado.detalles:
            print(f"    - {detalle}")


def main(
    argv: Optional[list] = None,
    *,
    store: Optional[TeoriaStore] = None,
    topologia_store: Optional["TopologiaStore"] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_corpus.py",
        description=(
            "Valida el CONTENIDO de Flujo A: por defecto, las filas vigentes de "
            "silver.teoria_documentos + su objeto en silver-teoria (Supabase). "
            "Gate previo a cada commit que toque el corpus (CLAUDE.md raíz). "
            "Con --topologia, valida en su lugar la topología del almacenamiento "
            "(task 68): Silver<->Bronze, fila<->objeto y claves de idempotencia "
            "duplicadas, para teoría e histórico — corre sobre el sistema entero, "
            "no es un gate por commit."
        ),
    )
    parser.add_argument(
        "ruta",
        nargs="?",
        default=None,
        help=(
            "Ruta explícita a un directorio con ficheros .md a validar (modo "
            "ruta local, sin red — p.ej. para testear contra un tmp_path con "
            "fixtures). Por defecto: modo Supabase, filas vigentes de "
            "silver.teoria_documentos. Mutuamente excluyente con --topologia."
        ),
    )
    parser.add_argument(
        "--topologia",
        action="store_true",
        help=(
            "Modo topología del almacenamiento (task 68), en vez del modo de "
            "contenido: cada fila Silver tiene su Bronze de origen, cada fila "
            "tiene su objeto en Storage, y no hay claves de idempotencia "
            "duplicadas — para bronze/silver x teoria/historico. No repite los "
            "checks de contenido de la task 62. Mutuamente excluyente con `ruta`."
        ),
    )
    args = parser.parse_args(argv)

    if args.ruta is not None and args.topologia:
        parser.error("ruta (modo ruta local) y --topologia son mutuamente excluyentes")

    if args.topologia:
        if topologia_store is None:
            try:
                topologia_store = TopologiaStore(DocumentStore())
            except DocumentStoreError as exc:
                print(f"✗ {exc}")
                return 1

        resultados = validar_topologia(topologia_store)
        _imprimir_resultados(resultados)

        todos_ok = all(r.ok for r in resultados)
        n_ok = sum(1 for r in resultados if r.ok)
        print(f"\n{n_ok}/{len(resultados)} checks OK (modo topología)")
        return 0 if todos_ok else 1

    if args.ruta is not None:
        directorio = Path(args.ruta)
        resultados = validar_ruta_local(directorio)
        _imprimir_resultados(resultados)
        print(
            "ℹ fila_objeto_coherente: no aplica en modo ruta local "
            "(sin fila Supabase con la que comparar el objeto)"
        )

        todos_ok = all(r.ok for r in resultados)
        n_ok = sum(1 for r in resultados if r.ok)
        print(f"\n{n_ok}/{len(resultados)} checks OK (modo ruta local) — {directorio}")
        return 0 if todos_ok else 1

    if store is None:
        try:
            store = TeoriaStore()
        except TeoriaStoreError as exc:
            print(f"✗ {exc}")
            return 1

    resultados = validar_supabase(store)
    _imprimir_resultados(resultados)

    todos_ok = all(r.ok for r in resultados)
    n_ok = sum(1 for r in resultados if r.ok)
    print(f"\n{n_ok}/{len(resultados)} checks OK (modo Supabase)")
    return 0 if todos_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
