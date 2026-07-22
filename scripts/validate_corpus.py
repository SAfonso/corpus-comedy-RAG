"""validate_corpus — gate de validación de `/data/processed/v{N}/` (Flujo A).

Contrato (`ROADMAP_DATA_PIPELINE.md` §"Checks de Validación del Corpus
Final", `CHECKPOINTS.md`, `src/theory/SPEC.md` §Storage/§Metadatos): último
paso antes de dar por bueno un commit/PR que toque la salida de Flujo A.
Valida el `.txt` + `manifest.json` + `stats.json` que genera
`generar_version` (`src/theory/normalizers/format_normalizer.py`, task 11) —
NO reprocesa nada, NO llama a ningún LLM, NO toca Flujos B/C (Supabase,
Telegram): esos flujos no tienen credenciales configuradas todavía (ver
`progress/errors.md`) y su validación queda fuera de alcance de esta tarea.

## Los 8 checks (contrato literal de `ROADMAP_DATA_PIPELINE.md`)

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
8. `manifest.json` referencia exactamente los mismos ficheros que existen en
   `documents/`

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

Cada check es una función pura `check_xxx(documentos: list[DocumentoLeido], ...)
-> ResultadoCheck` que NO hace I/O — recibe los documentos ya leídos y
parseados por `leer_documento`/`cargar_documentos`. Esto permite testear cada
check por separado con datos ya en memoria, y a la vez componerlos todos en
`validar_version` (que sí hace I/O: lee `manifest.json` + `documents/*.txt`
de un `v{N}/` real). Determinista, sin LLM, sin red.

## CLI

    python scripts/validate_corpus.py [ruta_a_v{N}]

Sin argumento: valida la última versión FINALIZADA (con `manifest.json`) de
`data/processed/`. Con argumento: valida esa ruta explícita (debe apuntar
directamente al directorio `v{N}/`, útil para testear contra un `tmp_path`
en vez del repo real). Exit code 0 si todos los checks pasan, 1 si alguno
falla o si no hay ninguna versión que validar (nunca una excepción críptica
para ese caso: es un resultado esperado, no un bug).
"""
import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MIN_PALABRAS = 100
MAX_PALABRAS = 50_000
IDIOMAS_PERMITIDOS = frozenset({"es", "en"})

CAMPOS_DOCUMENTO_REQUERIDOS = ("fuente", "autor", "tipo_fuente", "licencia")
CAMPOS_FRAGMENTO_REQUERIDOS = ("subtipo", "idioma_original", "idioma_fragmento")
"""Junto con los 4 de documento, suman los 7 campos de `CHECKPOINTS.md`."""

NOMBRE_MANIFEST = "manifest.json"
DIRECTORIO_DOCUMENTOS = "documents"

_PATRON_VERSION = re.compile(r"^v(\d+)$")
_PATRON_TIMESTAMP = re.compile(r"\[\d+(?:\.\d+)?s\]")
_PATRON_SPEAKER = re.compile(r"SPEAKER_\d+:")


# --- Lectura y parseo (I/O mínimo, aislado del resto) -----------------------


@dataclass
class DocumentoLeido:
    """Un documento `.txt` de `v{N}/documents/` ya leído y parseado.

    `path_relativo`: ruta relativa a `v{N}/` (p.ej. `"documents/foo.txt"`,
    tal como aparece en `manifest.json`), NO un `Path` absoluto — es lo que
    se compara contra `manifest.json` en el check 8.
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


def leer_documento(ruta: Path, path_relativo: str) -> DocumentoLeido:
    """Lee y parsea UN `.txt` de `documents/`. Sin validar nada — solo extrae."""
    contenido = ruta.read_text(encoding="utf-8")
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


def cargar_documentos(directorio_version: Path) -> list[DocumentoLeido]:
    """Lee todos los `.txt` de `directorio_version/documents/`, ordenados por
    nombre (determinismo del orden de reporte)."""
    directorio_documentos = directorio_version / DIRECTORIO_DOCUMENTOS
    if not directorio_documentos.is_dir():
        return []
    rutas = sorted(directorio_documentos.glob("*.txt"))
    return [
        leer_documento(ruta, f"{DIRECTORIO_DOCUMENTOS}/{ruta.name}") for ruta in rutas
    ]


def cargar_manifest(directorio_version: Path) -> dict:
    """Lee `manifest.json` de la versión. Lanza `FileNotFoundError` (con
    mensaje claro) si no existe — lo captura `main`, nunca una excepción
    críptica llega al usuario final del CLI."""
    ruta_manifest = directorio_version / NOMBRE_MANIFEST
    if not ruta_manifest.exists():
        raise FileNotFoundError(
            f"{ruta_manifest} no existe: {directorio_version} no es una versión "
            f"válida de Flujo A (falta el índice inmutable del corpus)"
        )
    return json.loads(ruta_manifest.read_text(encoding="utf-8"))


# --- Resultado de un check ---------------------------------------------------


@dataclass
class ResultadoCheck:
    """Resultado de un check individual. `detalles`: lista de líneas humanas
    describiendo cada incidencia encontrada (vacía si `ok=True`)."""

    nombre: str
    ok: bool
    detalles: list = field(default_factory=list)


# --- Los 8 checks (funciones puras, testeables por separado) ----------------


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


def check_manifest_sincronizado(
    documentos: list[DocumentoLeido], manifest: dict
) -> ResultadoCheck:
    """Check 8: `manifest.json["documents"][*]["path"]` == exactamente los
    ficheros que existen en `documents/` (ni de más ni de menos)."""
    rutas_manifest = {entrada["path"] for entrada in manifest.get("documents", [])}
    rutas_reales = {doc.path_relativo for doc in documentos}

    detalles = []
    solo_en_manifest = sorted(rutas_manifest - rutas_reales)
    solo_en_disco = sorted(rutas_reales - rutas_manifest)
    if solo_en_manifest:
        detalles.append(
            f"manifest.json referencia fichero(s) inexistente(s): {solo_en_manifest}"
        )
    if solo_en_disco:
        detalles.append(
            f"documents/ tiene fichero(s) no referenciado(s) en manifest.json: {solo_en_disco}"
        )
    return ResultadoCheck("manifest_sincronizado", not detalles, detalles)


CHECKS = (
    check_sin_timestamps,
    check_sin_speaker_tags,
    check_cabecera_completa,
    check_min_palabras,
    check_max_palabras,
    check_sin_duplicados,
    check_idiomas_permitidos,
)
"""Checks que solo necesitan `documentos` (check 8 necesita también `manifest`
y se ejecuta aparte en `validar_version`)."""


def validar_version(directorio_version: Path) -> list[ResultadoCheck]:
    """Ejecuta los 8 checks contra `directorio_version` (un `v{N}/` real, con
    `documents/*.txt` + `manifest.json`). Lanza `FileNotFoundError` si
    `directorio_version` no tiene `manifest.json` — responsabilidad de quien
    llama (p.ej. `main`) decidir cómo reportarlo sin traceback críptico."""
    manifest = cargar_manifest(directorio_version)
    documentos = cargar_documentos(directorio_version)

    resultados = [check(documentos) for check in CHECKS]
    resultados.append(check_manifest_sincronizado(documentos, manifest))
    return resultados


# --- Localización de la última versión (CLI) --------------------------------


def _version_finalizada(directorio_version: Path) -> bool:
    return (directorio_version / NOMBRE_MANIFEST).exists()


def encontrar_ultima_version(directorio_base: Path) -> Optional[Path]:
    """Devuelve el `Path` de la versión `v{N}/` finalizada (con
    `manifest.json`) de mayor `N` dentro de `directorio_base`, o `None` si no
    hay ninguna (ni `directorio_base` existe, ni ninguna `v{N}/` está
    finalizada) — mismo criterio de "finalizada" que
    `format_normalizer._version_finalizada` (ver su docstring)."""
    if not directorio_base.is_dir():
        return None

    finalizadas = []
    for entrada in directorio_base.iterdir():
        if entrada.is_dir() and _PATRON_VERSION.match(entrada.name) and _version_finalizada(entrada):
            finalizadas.append((int(_PATRON_VERSION.match(entrada.name).group(1)), entrada))

    if not finalizadas:
        return None
    return max(finalizadas, key=lambda par: par[0])[1]


# --- CLI ---------------------------------------------------------------------


def _imprimir_resultados(resultados: list[ResultadoCheck]) -> None:
    for resultado in resultados:
        marca = "✓" if resultado.ok else "✗"
        print(f"{marca} {resultado.nombre}")
        for detalle in resultado.detalles:
            print(f"    - {detalle}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_corpus.py",
        description=(
            "Valida la salida de Flujo A en /data/processed/v{N}/ contra los "
            "8 checks de ROADMAP_DATA_PIPELINE.md. Gate previo a cada commit "
            "que toque el corpus (CLAUDE.md raíz)."
        ),
    )
    parser.add_argument(
        "ruta",
        nargs="?",
        default=None,
        help=(
            "Ruta explícita a un directorio v{N}/ a validar (p.ej. para "
            "testear contra un tmp_path). Por defecto: la última versión "
            "finalizada de data/processed/."
        ),
    )
    args = parser.parse_args(argv)

    if args.ruta is not None:
        directorio_version = Path(args.ruta)
    else:
        directorio_base = Path("data/processed")
        directorio_version = encontrar_ultima_version(directorio_base)
        if directorio_version is None:
            print(
                f"✗ No hay ninguna versión finalizada que validar en "
                f"{directorio_base}/ (ninguna v{{N}}/manifest.json encontrada). "
                f"Ejecuta el pipeline de Flujo A (scripts/run_pipeline.py) "
                f"antes de validar."
            )
            return 1

    try:
        resultados = validar_version(directorio_version)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1

    _imprimir_resultados(resultados)

    todos_ok = all(r.ok for r in resultados)
    n_ok = sum(1 for r in resultados if r.ok)
    print(f"\n{n_ok}/{len(resultados)} checks OK — {directorio_version}")
    return 0 if todos_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
