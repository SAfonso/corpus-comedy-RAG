"""format_normalizer — Flujo A (Teoría), FormatNormalizer + versionado `v{N}`.

Contrato (`src/theory/SPEC.md` §Storage, §Metadatos, §Idempotencia y
versionado; `docs/specs/00-overview.md` §2; `CHECKPOINTS.md`): último paso de
la cadena `... -> QualityScorer -> FormatNormalizer -> /data/processed/v{N}/`.
Consume la salida de `language_normalizer.py` (`FragmentoNormalizado`, lista
de fragmentos de UN documento con su `texto`/`subtipo`/`idioma_original`/
`idioma_fragmento` ya resueltos) y metadatos DEL DOCUMENTO que ningún módulo
previo produce (`fuente`, `autor`, `tipo_fuente`, `licencia`): se reciben
explícitos como parámetros, nunca derivados del nombre de fichero de origen
(ese nombre pertenece al Parser/DriveMonitor, no a este módulo).

## Qué hace este módulo (dos niveles, mismo patrón que `language_normalizer.py`)

1. `render_document(...)`: función PURA, sin I/O — construye el `.txt` de UN
   documento (cabecera YAML + cuerpo) a partir de sus fragmentos + metadatos.
2. `generar_version(...)`: orquestación con I/O — vuelca una lista de
   documentos a `/data/processed/v{N}/` (`documents/*.txt` + `manifest.json`
   + `stats.json`), respetando la inmutabilidad de versiones ya finalizadas.

## Decisión: cabecera YAML — dónde vive cada campo (los 7 de CHECKPOINTS.md)

`fuente`/`autor`/`tipo_fuente`/`licencia` son metadatos DE DOCUMENTO (un solo
valor por fichero); `subtipo`/`idioma_original`/`idioma_fragmento` son
metadatos POR FRAGMENTO (`FragmentoNormalizado` ya los lleva así, y un
documento típico mezcla `explicacion`/`ejemplo` e idiomas distintos — colapsar
esto a un valor por documento perdería información real, p.ej. un fragmento
`ejemplo` en inglés dentro de una explicación ya traducida a español).

Se opta por UNA sola cabecera YAML por documento (no un YAML por fragmento:
repetir `fuente`/`autor`/`tipo_fuente`/`licencia` en cada fragmento sería
puro ruido redundante para un campo que no cambia en todo el fichero) que
lleva los 4 campos de documento a nivel superior, más una clave `fragmentos`
con una entrada por fragmento (`subtipo`, `idioma_original`,
`idioma_fragmento`) EN EL MISMO ORDEN que los párrafos del cuerpo — la
correspondencia posicional (fragmento N de la lista <-> párrafo N del cuerpo,
separados por línea en blanco) es el mecanismo legible elegido para no
duplicar el texto completo dentro del YAML. Los 7 campos de
`CHECKPOINTS.md` quedan así todos presentes como claves YAML de la cabecera:

```
---
fuente: "..."
autor: null
tipo_fuente: transcripcion_curso
licencia: personal_only
fragmentos:
  - subtipo: explicacion
    idioma_original: es
    idioma_fragmento: es
  - subtipo: ejemplo
    idioma_original: es
    idioma_fragmento: es
---
<párrafo 1 (fragmento 1)>

<párrafo 2 (fragmento 2)>
```

## Decisión: emisor YAML propio, sin depender de `pyyaml`

`requirements.txt`/`src/theory/SPEC.md` §Stack no incluyen `pyyaml` (no está
instalado en el entorno de este repo) y añadirlo sería un cambio de
dependencia no pedido por la tarea. El YAML que este módulo necesita emitir
es de forma fija y totalmente controlada por nosotros (escalares
string/None a nivel superior + una secuencia de mappings pequeños con solo
escalares string): un emisor mínimo y determinista (`_yaml_frontmatter`,
`_yaml_valor`) basta para producir YAML válido (secuencia de bloque estándar,
strings entrecomillados siempre para evitar cualquier ambigüedad de escaping)
sin arrastrar una librería general para un caso de uso tan acotado. Si en el
futuro hiciera falta PARSEAR YAML arbitrario de vuelta, esa sí sería una
decisión de stack a tomar explícitamente (mismo criterio que la nota de
`DeeplTranslator` en `src/theory/KNOWN_ERRORS.md`).

## Decisión: NO usar `src/theory/enrichers/metadata_tagger.py`

La tarea permite separar ahí la construcción del YAML frontmatter. Se decide
NO hacerlo: la construcción de la cabecera está fuertemente acoplada al
contrato de `render_document` (mismos campos, mismo orden posicional con el
cuerpo) y partirla en dos ficheros solo añadiría una dependencia interna
cruzada sin beneficio real de reutilización — ningún otro módulo de la cadena
necesita generar este YAML. `metadata_tagger.py` queda vacío (placeholder de
Fase 0), sin tocar.

## Decisión: `tipo_fuente` — enum cerrado, SOLO Flujo A

`docs/specs/00-overview.md` §2 define el enum completo de `tipo_fuente`
(`teoria`, `transcripcion_curso`, `propio`, `propio_historico`), pero
`FormatNormalizer` es exclusivo de Flujo A (`externo*`): solo acepta
`"teoria"`/`"transcripcion_curso"`. `"propio"`/`"propio_historico"` son de
Flujos B/C (Supabase, no ficheros `v{N}`) y se RECHAZAN aquí explícitamente
(`ValueError`) para no colar por error un documento de otro flujo en el
storage de teoría.

## Decisión: esquema de `manifest.json` / `stats.json`

- `manifest.json`: índice inmutable — `{"version": N, "documents": [...]}`,
  cada entrada con `fuente`/`autor`/`tipo_fuente`/`licencia` (los mismos 4
  campos de documento del YAML, para poder listar el corpus sin abrir cada
  `.txt`) + `path` (ruta relativa a `v{N}/`) + `num_fragmentos`.
- `stats.json`: agregados de la versión completa — nº documentos, nº
  fragmentos totales, y `quality_score` (media/min/max sobre
  `score_quality(fragmento.texto)` de CADA fragmento de CADA documento, task
  10 — se puntúa `texto` final, es decir tras traducción si la hubo, porque es
  el contenido que de verdad queda persistido y se sirve al RAG) más un
  desglose `por_documento` (útil para detectar un documento concreto de baja
  calidad sin recorrer todos los fragmentos a mano).

## Decisión: mecanismo de inmutabilidad `v{N}` -> `v{N+1}`

Una versión se considera **finalizada** si y solo si existe su
`manifest.json` (se escribe el ÚLTIMO de todos los ficheros de la versión,
después de `documents/*.txt` y `stats.json` — es el marcador de "esta versión
ya está completa e inmutable").

- `generar_version(..., version=None)` (comportamiento por defecto): calcula
  automáticamente la siguiente versión LIBRE = `max(versiones finalizadas) + 1`
  (o `1` si no hay ninguna finalizada todavía). Nunca reutiliza ni pisa una
  versión finalizada.
- `generar_version(..., version=N)` (versión explícita): si `v{N}/manifest.json`
  ya existe, se RECHAZA con `VersionInmutableError` (no se sobrescribe bajo
  ningún concepto). Si `v{N}` no existe o existe pero no está finalizada
  (p.ej. un intento previo interrumpido a mitad, sin `manifest.json`), se
  permite escribir/rehacer esa `v{N}` — no es una versión "real" todavía.

**Gap de scope documentado:** la reanudación fina dentro de una versión
interrumpida (retomar documento a documento sin reprocesar los ya escritos,
como sí hace `processed_files.json` a nivel de fichero de origen en
`DriveMonitor`) NO se implementa aquí — si una versión no finalizada se
regenera, se reprocesan todos sus documentos desde cero. Lo que SÍ es un
requisito duro de `CHECKPOINTS.md`/`src/theory/SPEC.md` (y sí se implementa y
testea) es que una versión FINALIZADA (con `manifest.json`) nunca se
sobrescribe. Añadir idempotencia fina intra-versión queda como posible tarea
futura si se observa un caso real de corte a mitad de una versión grande.

Sin LLM, determinista (regla de Flujo A, ver `docs/specs/llm-policy.md`).
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.theory.normalizers.language_normalizer import FragmentoNormalizado
from src.utils.quality_scorer import score_quality

LICENCIA_POR_DEFECTO = "personal_only"
"""Licencia por defecto para `externo*` (`docs/specs/00-overview.md` §2)."""

TIPOS_FUENTE_VALIDOS = ("teoria", "transcripcion_curso")
"""Enum cerrado de `tipo_fuente` que acepta Flujo A (`externo*`). Valores de
Flujo B/C (`propio`, `propio_historico`) se rechazan explícitamente: no son
responsabilidad de este módulo (ver docstring)."""

NOMBRE_MANIFEST = "manifest.json"
NOMBRE_STATS = "stats.json"
DIRECTORIO_DOCUMENTOS = "documents"

_PATRON_VERSION = re.compile(r"^v(\d+)$")
_PATRON_NO_SLUG = re.compile(r"[^a-z0-9]+")


class VersionInmutableError(Exception):
    """Se pidió generar/sobrescribir una versión `v{N}` ya finalizada.

    Una versión está finalizada si su `manifest.json` existe (ver docstring
    del módulo, §Decisión: mecanismo de inmutabilidad). Nunca se sobrescribe.
    """


@dataclass
class DocumentoEntrada:
    """Un documento a volcar a `/data/processed/v{N}/` vía `generar_version`.

    - `fragmentos`: salida real de `normalize_language` (task 9) para ESTE
      documento, en el orden en que deben aparecer en el cuerpo del `.txt`.
    - `fuente`/`tipo_fuente`: obligatorios. `tipo_fuente` debe ser uno de
      `TIPOS_FUENTE_VALIDOS`.
    - `autor`: `None` si se desconoce (caso frecuente en transcripciones).
    - `licencia`: por defecto `LICENCIA_POR_DEFECTO` (`externo*`), pero
      parametrizable (p.ej. un libro de dominio público sería
      `tipo_fuente="teoria"` con `licencia="comercializable"`, ver overview §2).
    - `nombre_fichero`: nombre base del `.txt` de salida (sin extensión), sin
      derivarlo del nombre de fichero ORIGEN (ese es del Parser, no de aquí).
      Si no se indica, se deriva de `fuente` (`_slugify`) — este slug es
      metadato de SALIDA, no una lectura mágica de un fichero de entrada.
    """

    fragmentos: list[FragmentoNormalizado]
    fuente: str
    tipo_fuente: str
    autor: Optional[str] = None
    licencia: str = LICENCIA_POR_DEFECTO
    nombre_fichero: Optional[str] = None


def _validar_tipo_fuente(tipo_fuente: str) -> None:
    if tipo_fuente not in TIPOS_FUENTE_VALIDOS:
        raise ValueError(
            f"tipo_fuente desconocido {tipo_fuente!r}: Flujo A (FormatNormalizer) "
            f"solo acepta {TIPOS_FUENTE_VALIDOS} (ver docs/specs/00-overview.md §2); "
            f"'propio'/'propio_historico' son de Flujo B/C, no de teoría"
        )


def _slugify(texto: str) -> str:
    """Deriva un nombre de fichero determinista y legible a partir de `fuente`.

    Minúsculas, todo lo que no sea `[a-z0-9]` colapsado a un único `-`, sin
    guiones sobrantes en los bordes. `""`/solo símbolos -> `"documento"`
    (fallback defensivo, no debería ocurrir con una `fuente` real no vacía).
    """
    slug = _PATRON_NO_SLUG.sub("-", texto.lower()).strip("-")
    return slug or "documento"


def _yaml_valor(valor) -> str:
    """Serializa un valor escalar a YAML (string entrecomillado, o `null`).

    Ver docstring del módulo, §Decisión: emisor YAML propio: entrecomillar
    SIEMPRE los strings evita cualquier ambigüedad de caracteres especiales
    de YAML (`:`, `#`, comillas, etc.) sin necesitar la lógica de decisión de
    cuándo hace falta quoting que sí tiene un emisor YAML general.
    """
    if valor is None:
        return "null"
    escapado = str(valor).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escapado}"'


def _yaml_frontmatter(metadatos_documento: dict, fragmentos_meta: list[dict]) -> str:
    """Construye la cabecera YAML completa (delimitadores `---` incluidos).

    `metadatos_documento`: dict ordenado de campos de documento (`fuente`,
    `autor`, `tipo_fuente`, `licencia`). `fragmentos_meta`: lista de dicts,
    un item por fragmento (`subtipo`, `idioma_original`, `idioma_fragmento`),
    en el mismo orden que los párrafos del cuerpo (ver docstring del módulo).
    """
    lineas = ["---"]
    for clave, valor in metadatos_documento.items():
        lineas.append(f"{clave}: {_yaml_valor(valor)}")

    lineas.append("fragmentos:")
    for fragmento_meta in fragmentos_meta:
        prefijo = "  - "
        for clave, valor in fragmento_meta.items():
            lineas.append(f"{prefijo}{clave}: {_yaml_valor(valor)}")
            prefijo = "    "

    lineas.append("---")
    return "\n".join(lineas) + "\n"


def render_document(
    fragmentos: list[FragmentoNormalizado],
    *,
    fuente: str,
    tipo_fuente: str,
    autor: Optional[str] = None,
    licencia: str = LICENCIA_POR_DEFECTO,
) -> str:
    """Construye el contenido completo (cabecera YAML + cuerpo) de UN documento.

    Función PURA: sin I/O, mismo input siempre produce el mismo output. No
    reordena ni modifica el texto de los fragmentos, solo los envuelve en el
    formato de salida (ver docstring del módulo para el porqué de la
    estructura de cabecera elegida).

    `fragmentos` no puede estar vacío: un documento sin fragmentos no tiene
    contenido que versionar (señal de un fallo aguas arriba, no un caso válido
    de salida silenciosa).
    """
    _validar_tipo_fuente(tipo_fuente)
    if not fragmentos:
        raise ValueError(
            f"documento {fuente!r} sin fragmentos: nada que escribir "
            f"(revisa la cadena aguas arriba: Parser/SubtypeDetector/Cleaner/LanguageNormalizer)"
        )

    metadatos_documento = {
        "fuente": fuente,
        "autor": autor,
        "tipo_fuente": tipo_fuente,
        "licencia": licencia,
    }
    fragmentos_meta = [
        {
            "subtipo": f.subtipo,
            "idioma_original": f.idioma_original,
            "idioma_fragmento": f.idioma_fragmento,
        }
        for f in fragmentos
    ]

    frontmatter = _yaml_frontmatter(metadatos_documento, fragmentos_meta)
    cuerpo = "\n\n".join(f.texto for f in fragmentos)

    return f"{frontmatter}\n{cuerpo}\n"


def _versiones_existentes(directorio_base: Path) -> list[int]:
    """Lista los números de versión (`v{N}`) presentes en `directorio_base`."""
    if not directorio_base.exists():
        return []
    versiones = []
    for entrada in directorio_base.iterdir():
        if entrada.is_dir():
            match = _PATRON_VERSION.match(entrada.name)
            if match:
                versiones.append(int(match.group(1)))
    return sorted(versiones)


def _version_finalizada(directorio_version: Path) -> bool:
    """True si `directorio_version` tiene `manifest.json` (ver §Inmutabilidad)."""
    return (directorio_version / NOMBRE_MANIFEST).exists()


def _siguiente_version_libre(directorio_base: Path) -> int:
    """Siguiente número de versión que no pisa ninguna versión finalizada."""
    finalizadas = [
        v
        for v in _versiones_existentes(directorio_base)
        if _version_finalizada(directorio_base / f"v{v}")
    ]
    return max(finalizadas, default=0) + 1


def _media(valores: list[float]) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def generar_version(
    documentos: list[DocumentoEntrada],
    directorio_base: Path,
    version: Optional[int] = None,
) -> Path:
    """Vuelca `documentos` a `directorio_base/v{N}/` (`documents/`, `manifest.json`, `stats.json`).

    - `version=None` (por defecto): elige automáticamente la siguiente
      versión libre (`max(versiones finalizadas) + 1`, o `1` si no hay
      ninguna). Nunca sobrescribe una versión finalizada.
    - `version=N` explícito: si `v{N}` YA está finalizada (tiene
      `manifest.json`), lanza `VersionInmutableError` — rechazo explícito, no
      sobrescribe bajo ningún concepto (ver docstring del módulo).

    Escribe, en este orden: `documents/<nombre>.txt` (uno por documento, vía
    `render_document`) -> `stats.json` -> `manifest.json` (el último: marca la
    versión como finalizada/inmutable). Devuelve el `Path` de `v{N}/` creado.

    Nunca escribe fuera de `directorio_base` (en los tests, un `tmp_path` de
    pytest — nunca el `/data/processed/` real del repo, ver CLAUDE.md).
    """
    directorio_base = Path(directorio_base)

    if version is not None:
        directorio_version = directorio_base / f"v{version}"
        if _version_finalizada(directorio_version):
            raise VersionInmutableError(
                f"v{version} ya existe y está finalizada ({NOMBRE_MANIFEST} presente) "
                f"en {directorio_version}: una versión inmutable nunca se sobrescribe. "
                f"Usa version=None para generar la siguiente versión libre."
            )
    else:
        version = _siguiente_version_libre(directorio_base)
        directorio_version = directorio_base / f"v{version}"

    directorio_documentos = directorio_version / DIRECTORIO_DOCUMENTOS
    directorio_documentos.mkdir(parents=True, exist_ok=True)

    manifest_documentos: list[dict] = []
    stats_por_documento: list[dict] = []
    scores_totales: list[float] = []
    nombres_usados: set[str] = set()

    for documento in documentos:
        _validar_tipo_fuente(documento.tipo_fuente)

        nombre_base = documento.nombre_fichero or _slugify(documento.fuente)
        nombre = nombre_base
        sufijo = 2
        while nombre in nombres_usados:
            nombre = f"{nombre_base}-{sufijo}"
            sufijo += 1
        nombres_usados.add(nombre)

        contenido = render_document(
            documento.fragmentos,
            fuente=documento.fuente,
            tipo_fuente=documento.tipo_fuente,
            autor=documento.autor,
            licencia=documento.licencia,
        )
        ruta_relativa = f"{DIRECTORIO_DOCUMENTOS}/{nombre}.txt"
        (directorio_version / ruta_relativa).write_text(contenido, encoding="utf-8")

        scores_documento = [score_quality(f.texto) for f in documento.fragmentos]
        scores_totales.extend(scores_documento)

        manifest_documentos.append(
            {
                "fuente": documento.fuente,
                "autor": documento.autor,
                "tipo_fuente": documento.tipo_fuente,
                "licencia": documento.licencia,
                "path": ruta_relativa,
                "num_fragmentos": len(documento.fragmentos),
            }
        )
        stats_por_documento.append(
            {
                "fuente": documento.fuente,
                "num_fragmentos": len(documento.fragmentos),
                "quality_score_medio": _media(scores_documento),
            }
        )

    stats = {
        "version": version,
        "num_documentos": len(documentos),
        "num_fragmentos": len(scores_totales),
        "quality_score": {
            "media": _media(scores_totales),
            "min": min(scores_totales) if scores_totales else 0.0,
            "max": max(scores_totales) if scores_totales else 0.0,
        },
        "por_documento": stats_por_documento,
    }
    (directorio_version / NOMBRE_STATS).write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "version": version,
        "documents": manifest_documentos,
    }
    (directorio_version / NOMBRE_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return directorio_version
