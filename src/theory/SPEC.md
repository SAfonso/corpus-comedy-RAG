# Flujo A — Teoría (Drive → ficheros)

> Spec de `src/theory/`. Ver también [`docs/specs/00-overview.md`](../../docs/specs/00-overview.md)
> (contexto general, `tipo_fuente`, layout) y
> [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md) (regla "no LLM" para este flujo).

Flujo batch, 100% determinista, sin LLM. Es el pipeline original; se conserva
intacto salvo por el nuevo paso de ingesta al índice (ver §Storage).

## Cadena de componentes (el orden importa)

```
[DriveSync (etapa 0, solo modo Drive-real)] → DriveMonitor → Parser
  → SubtypeDetector → Cleaner → LanguageDetector
  → LanguageNormalizer → QualityScorer → FormatNormalizer → /data/processed/v{N}/
```

- **DriveSync (etapa 0, `src/theory/drive_sync.py`):** sincroniza la carpeta de
  Google Drive real a `data/staging/theory/` y deja ahí los ficheros
  nuevos/modificados. **Opcional:** solo corre en modo Drive-real; en modo
  solo-local la cadena empieza en `DriveMonitor` como siempre — ver §Fuente de
  entrada abajo (P23).
- **DriveMonitor:** vigila una carpeta de origen y procesa solo ficheros
  nuevos (idempotencia por hash MD5, ver §Idempotencia). Su interfaz **no
  cambia** con P23; lo que cambia según el modo es **qué carpeta** vigila
  (`data/staging/theory/` en modo Drive-real, `data/raw/books/` +
  `data/raw/notes/` en modo solo-local).
- **Parser:** una función por tipo de fuente. `whisperx_parser` (.txt con
  `[timestamp] SPEAKER_XX: texto`), `pdf_parser`/`docx_parser` (markitdown,
  con Tesseract OCR de fallback si está escaneado — ver §Parser abajo),
  `epub_parser`. Código común → `src/utils/` (ver `src/utils/SPEC.md`).
- **SubtypeDetector:** clasifica cada fragmento en `explicacion | ejemplo`.
  Ejecuta ANTES del Cleaner porque los ejemplos tienen reglas distintas.
- **Cleaner:** limpieza AGRESIVA (ver §Limpieza) para `subtipo=explicacion`;
  conserva el estilo oral en `subtipo=ejemplo`.
- **LanguageDetector / LanguageNormalizer:** corpus bilingüe (ver §Idioma):
  teoría se traduce a español, los ejemplos se conservan en idioma original.
- **QualityScorer:** puntuación 0–1 de densidad de contenido útil. Obligatorio.
- **FormatNormalizer:** salida uniforme `.txt` + cabecera YAML (ver §Metadatos).

**WhisperX — contrato del parser:** elimina timestamps y speaker tags del texto;
conserva el speaker dominante como metadato del documento; une líneas
consecutivas del mismo speaker; preserva el contenido, nunca lo interpreta.

## Parser — decisión markitdown (P17, 2026-07-21)

`pdf_parser` y `docx_parser` usan [`markitdown`](https://github.com/microsoft/markitdown)
(Microsoft) para convertir el original a Markdown, en vez de extracción manual
con `pymupdf`/`python-docx`. `epub_parser` migra igual si markitdown cubre
EPUB con calidad suficiente; si no, se mantiene `ebooklib`.

- **Dónde ocurre:** DESPUÉS de Bronze — el original en `/data/raw/` es
  intocable (sagrado). markitdown sustituye la implementación **interna** del
  Parser, no el material de entrada: la cadena sigue siendo
  `DriveMonitor → Parser (fichero original sagrado → texto) → SubtypeDetector → ...`.
- **Por qué:** la salida Markdown (headings, listas, tablas) es más legible
  que un `.txt` plano para quien mantiene y depura el pipeline — incluido el
  propio agente de código — y evita reimplementar a mano la extracción de
  estructura del documento.
- **Lo que NO cambia:** markitdown solo re-extrae texto ya presente en el
  fichero (PDFs con texto nativo, DOCX). Para los PDFs realmente escaneados
  (riesgo pendiente de verificar, ver `docs/CORPUS_INVENTORY.md`) sigue
  haciendo falta OCR: Tesseract se mantiene como *fallback*, invocado cuando
  markitdown no extrae texto útil de una página.
- **`whisperx_parser` no cambia:** su entrada ya es `.txt` plano generado en
  Colab, no un formato que markitdown convierta.
- **Sin LLM:** se usa solo el modo de conversión determinista de markitdown;
  su plugin opcional de *captioning* de imágenes vía LLM no se activa —
  mantiene la regla "sin LLM" de teoría (ver `docs/specs/llm-policy.md`).

## Fuente de entrada — Drive real y modo dual (P23, 2026-07-27)

> **Cierra P18** (2026-07-22, "integración con la API de Drive diferida"). Lo
> que P18 prometía —"cuando se decida activar Drive de verdad, se añaden
> `GOOGLE_APPLICATION_CREDENTIALS`/`DRIVE_FOLDER_ID` y se cambia la fuente de
> `DriveMonitor`, el resto de la cadena queda intacto"— es exactamente lo que
> hace esta sección. La promesa se cumple literalmente: **ninguna etapa aguas
> abajo cambia**, y `DriveMonitor` tampoco cambia de interfaz.

El Flujo A pasa a poder leer de una **carpeta de Google Drive real**, con el
mismo mecanismo que ya usa el Flujo C (P19), reutilizando el núcleo compartido
`src/utils/drive_sync.py` (§DriveSync en [`src/utils/SPEC.md`](../utils/SPEC.md))
en vez de reimplementarlo. La especialización de teoría vive en
`src/theory/drive_sync.py` (task 44) y el *wiring* en `pipeline.py` /
`scripts/run_pipeline.py` es la task 45.

### Los dos modos (y por qué siguen siendo dos)

| | **Modo solo-local** (por defecto, comportamiento de HOY) | **Modo Drive-real** (nuevo, opt-in) |
|---|---|---|
| Etapa 0 (`DriveSync`) | no corre | `DriveSyncTeoria.sync()` → `data/staging/theory/` |
| Carpetas que vigila `DriveMonitor` | `data/raw/books/` + `data/raw/notes/` | `data/staging/theory/` |
| Quién deja los ficheros ahí | una persona, a mano | el `sync()` de Drive |
| Credenciales de Drive | no hacen falta | obligatorias |

El modo solo-local **no se retira ni se deprecia**: es el modo de desarrollo y
de tests (no exige credenciales ni red), y es el que sirve al corpus ya
descargado a mano que documenta [`docs/CORPUS_INVENTORY.md`](../../docs/CORPUS_INVENTORY.md)
(27 transcripciones + 5 libros ya en `data/raw/`). Un run en modo Drive-real y
otro en modo solo-local producen la misma cadena a partir de `DriveMonitor`;
lo único que difiere es de dónde salieron los ficheros.

### `data/staging/theory/`, no `data/raw/` — qué significa "sagrado" ahora

**Restricción dura: el sync automático de Drive escribe en
`data/staging/theory/` y NUNCA en `data/raw/`.**

`CLAUDE.md` (§"Regla más importante") declara `/data/raw/` material sagrado:
nunca se modifica, elimina ni sobrescribe. Un proceso automático que
re-descarga ficheros de Drive **sobrescribe por definición** cada vez que algo
cambia en el origen: apuntarlo a `data/raw/` sería contradecir la regla en el
primer run útil. De ahí la distinción, que P23 fija explícitamente:

- **En modo solo-local, lo sagrado es el fichero local.** `data/raw/books/` y
  `data/raw/notes/` son el original curado a mano: no hay ninguna otra copia de
  la que reconstruirlos, así que perderlos es perder corpus. Siguen siendo
  intocables, y P23 no los mueve, no los borra y no escribe en ellos.
- **En modo Drive-real, lo sagrado es la carpeta de Drive.** El fichero local
  es una **caché reconstruible**: si se borra `data/staging/theory/` (o su
  fichero de estado), el siguiente `sync()` lo vuelve a poblar desde el origen.
  Perderlo no pierde corpus. Es la misma distinción que ya hizo el Flujo C con
  `data/staging/historico/` (P19).

Lo sagrado, en los dos casos, es **el único ejemplar que existe del original**;
lo que cambia con el modo es dónde vive ese ejemplar. Lo que la regla prohíbe
—que un proceso automático pise material del que no hay copia— se respeta
igual en ambos.

- `data/staging/theory/` es **caché, no entregable**: no se versiona en git
  (ya cubierto por `data/**` en `.gitignore`), no entra en `v{N}` y se puede
  borrar entero sin pérdida de información.
- `data/raw/books/` y `data/raw/notes/` **siguen existiendo y siguen siendo
  válidos**. Lo que P23 prohíbe es que el sync escriba ahí, no que el modo
  manual siga usándolos.

### `src/theory/drive_sync.py` — especialización de teoría (task 44)

Subclase/adaptador fino sobre `utils.drive_sync.DriveSync` que solo aporta la
configuración de teoría (todo el mecanismo —auth, listado paginado,
idempotencia, staging— es del núcleo compartido, ver §DriveSync en
`src/utils/SPEC.md`):

```python
MIMES_TEORIA: dict[str, Optional[str]] = {
    "application/pdf": None,                 # libro PDF          -> get_media  -> .pdf
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": None,
                                             # DOCX subido        -> get_media  -> .docx
    "application/epub+zip": None,            # libro EPUB         -> get_media  -> .epub
    "text/plain": None,                      # transcripción WhisperX -> get_media -> .txt
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),                                       # Google Doc nativo  -> export     -> .docx
}

STAGING_DIR_POR_DEFECTO = Path("data/staging/theory")
RUTA_ESTADO_DRIVE_POR_DEFECTO = Path("data/state/theory_drive.json")

class DriveSyncTeoria(DriveSync): ...

def desde_entorno(
    folder_id: Optional[str] = None,        # por defecto, DRIVE_FOLDER_ID
    staging_dir: Optional[Path] = None,
    state_path: Optional[Path] = None,
    credentials_path: Optional[Path] = None,  # por defecto, GOOGLE_APPLICATION_CREDENTIALS
    service=None,
) -> DriveSyncTeoria:
    """Construye el sync de teoría desde el entorno. `RuntimeError` explícito
    si falta `DRIVE_FOLDER_ID` — nunca arranca a medias."""
```

**Los cinco MIMEs y sus destinos:**

- `application/pdf`, `…wordprocessingml.document` (DOCX), `application/epub+zip`
  y `text/plain` se **descargan directos** (`get_media`): ya vienen en un
  formato que el Parser de teoría sabe leer (`pdf_parser`, `docx_parser`,
  `epub_parser`, `whisperx_parser`). No hay nada que convertir.
- **Google Docs nativos (`application/vnd.google-apps.document`) se exportan a
  `.docx`**, no a texto plano. Coincide con lo que hace el histórico, pero
  **por motivos propios de teoría, no por copia** — el motivo del histórico
  (conservar `w:color` a nivel de run para `marcar_remates`) aquí no aplica:
  teoría no lee color.
  1. **El `.docx` cae en `docx_parser` → markitdown**, que ya está aprobado
     (P17) y devuelve Markdown con estructura (headings, listas, tablas). Un
     export a texto plano tiraría esa estructura *antes* de que el pipeline la
     vea, y `SubtypeDetector` (`explicacion` vs `ejemplo`) y el Cleaner
     trabajan mejor con ella que con un muro de texto.
  2. **`.txt` está reservado en teoría a las transcripciones WhisperX.**
     `src/theory/pipeline.py` enruta Parser y deriva `tipo_fuente` **por
     extensión**: `.txt` → `whisperx_parser` + `transcripcion_curso`. Un Google
     Doc exportado a `.txt` acabaría en el parser de WhisperX (que espera
     `[timestamp] SPEAKER_XX: texto`) y etiquetado como transcripción de curso
     — dos errores, no uno. Exportar a `.docx` lo enruta a `docx_parser` y a
     `tipo_fuente='teoria'`, que es lo correcto para un apunte escrito.
  3. Coherencia de stack: `.docx` ya es un formato de primera clase del Flujo A;
     no añade dependencias.
- **Cualquier otro MIME se ignora** (hojas de cálculo, Slides, imágenes,
  carpetas): no hay Parser de teoría para ellos. No es un error, sencillamente
  no se listan (la query de Drive ya filtra por MIME).
- **Sin recursión en subcarpetas** (heredado del núcleo: `'folder' in parents`).
  La carpeta de Drive de teoría debe ser plana. Si más adelante hace falta
  recorrer subcarpetas, es un cambio del núcleo compartido, no de teoría.

**Una sola carpeta de Drive, un solo staging.** El modo solo-local vigila dos
carpetas (`books/`, `notes/`) y el modo Drive-real una sola
(`data/staging/theory/`): no se pierde nada, porque **la carpeta de origen no
lleva semántica**. `tipo_fuente` y la elección de Parser se derivan de la
extensión del fichero (`src/theory/pipeline.py`), nunca de en qué carpeta
estaba. Por eso `DRIVE_FOLDER_ID` es una variable, no dos.

### Variables de entorno

- **`DRIVE_FOLDER_ID` se mantiene** como ID de la carpeta de Drive de teoría.
  Ya existe en `.env.example` desde Fase 0, reservada precisamente para esto, y
  `src/jokes/historico/SPEC.md` (P19) ya la describe como "la carpeta de
  libros/teoría del Flujo A" al justificar por qué el histórico necesitaba una
  variable **distinta** (`DRIVE_FOLDER_ID_HISTORICO`). Renombrarla a
  `DRIVE_FOLDER_ID_TEORIA` solo compraría simetría cosmética y a cambio
  invalidaría esa spec, el comentario de `.env.example` y cualquier `.env` ya
  rellenado. **La asimetría de nombres es deliberada**: `DRIVE_FOLDER_ID` es la
  carpeta por defecto/original del proyecto, `DRIVE_FOLDER_ID_HISTORICO` la que
  se añadió después.
- **`GOOGLE_APPLICATION_CREDENTIALS`** se comparte con el Flujo C: misma cuenta
  de servicio, con acceso de lectura a las dos carpetas. Scope
  `https://www.googleapis.com/auth/drive.readonly`. **Nunca OAuth interactivo**
  — el Flujo A tiene que poder correr desatendido (`APScheduler`, o un runner
  como el del Flujo C).
- Con `--sync-drive` y sin `DRIVE_FOLDER_ID`, el arranque **falla explícito**
  (`RuntimeError`), no en silencio ni con un sync vacío.

### Activación (task 45) — qué no queda por decidir

- **`run_pipeline(...)` recibe un parámetro nuevo `drive_sync=None`** (objeto
  inyectado, no un booleano). `None` → modo solo-local, comportamiento actual
  bit a bit. Un `DriveSyncTeoria` → se ejecuta `drive_sync.sync()` como etapa 0
  antes de instanciar ningún `DriveMonitor`.
- **Qué carpetas se vigilan, sin ambigüedad:**
  - `drive_sync=None` y `carpetas=None` → `[data/raw/books/, data/raw/notes/]`
    (default actual, intacto).
  - `drive_sync` presente y `carpetas=None` → `[drive_sync.staging_dir]`. El
    default local **no** se añade: en modo Drive-real la fuente es Drive.
  - `carpetas` explícito → se respeta tal cual; si además hay `drive_sync`, su
    `staging_dir` se añade si no estaba (permite un run híbrido deliberado).
- **CLI (`scripts/run_pipeline.py`):** flag `--sync-drive` (`store_true`, por
  defecto **off** → modo solo-local intacto) que construye el sync con
  `theory.drive_sync.desde_entorno(...)`, más `--drive-staging-dir` y
  `--drive-state-path` para sobrescribir rutas. Sin el flag, el CLI no importa
  credenciales ni toca la red. Configuración incompleta con `--sync-drive`
  (falta `DRIVE_FOLDER_ID`) → mensaje a stderr y **exit code 3** (el ya
  existente "fallo fatal"; no se inventa un código nuevo, se amplía su
  descripción en el `--help` y en el docstring del script).
- **El resumen JSON del CLI** gana un bloque opcional con el resultado de la
  etapa 0 (nº de ficheros sincronizados); sin `--sync-drive` no aparece, para
  no romper a los consumidores externos del contrato de stdout (task 23).
- **`drive_monitor.py` no se toca** en ninguna de las tres tasks (43/44/45): su
  interfaz (`DriveMonitor(folder, state_path).scan()`) es la misma y sigue
  siendo idempotencia por MD5 sobre un `Path`.
- **Los metadatos de documento siguen igual — P23 NO los enriquece.**
  `pipeline.py` deriva hoy `fuente` del nombre de fichero y deja `autor=None`,
  anotando que "cuando P18 se reactive con Drive real y aporte metadatos
  propios (título, autor), este punto es el que hay que sustituir". P23 cierra
  P18 **sin** hacer eso, deliberadamente: `sync()` devuelve `list[Path]` y el
  nombre del fichero staged es el mismo nombre que tiene en Drive, así que
  `path.stem` sigue dando exactamente lo mismo y el placeholder sigue siendo
  válido. Traer título/autor reales exigiría que `sync()` devolviera metadata
  además de paths — cambio del núcleo compartido y de su contrato, es decir,
  una tarea aparte y no un efecto colateral del wiring. La task 45 **no** debe
  tocar la derivación de `fuente`/`autor`.

### Idempotencia en capas (independientes)

Mismo patrón que el Flujo C (`src/jokes/historico/SPEC.md` §"Idempotencia en
capas"): **no se fusionan**, cada capa vigila su propio estado en su propio
fichero.

| Capa | Pregunta | Clave | Estado |
|------|----------|-------|--------|
| `DriveSync` (etapa 0, solo modo Drive-real) | ¿qué fichero **descargar** de Drive? | `fileId` + `modifiedTime` (metadata) | `data/state/theory_drive.json` |
| `DriveMonitor` | ¿qué fichero local **procesar**? | MD5 del contenido | `data/state/processed_files.json` |

`modifiedTime` se lee de la metadata **sin descargar** el fichero: la capa 0
evita justo el trabajo caro (la descarga). El MD5 del `DriveMonitor` necesita
el fichero ya en local, por eso no puede sustituirla.

Consecuencia práctica que la task 45 **no** tiene que resolver, pero sí conocer:
si un fichero se descarga y luego la cadena falla aguas abajo, `DriveSync` ya
lo dio por descargado (no lo re-descargará), pero el fichero sigue en el
staging y `processed_files.json` nunca lo comprometió (ver `pipeline.py`,
§"Fricción resuelta"), así que el siguiente run lo reintenta desde el staging.
Correcto. Solo si se borra el staging **sin** borrar
`data/state/theory_drive.json` habría que borrar también ese estado para
forzar la re-descarga — los dos ficheros de estado se borran juntos o no se
borra ninguno.

## Storage

- **Entrada:** `data/raw/books/` + `data/raw/notes/` (modo solo-local, material
  curado a mano) o `data/staging/theory/` (modo Drive-real, **caché
  reconstruible** poblada por `DriveSync`) — ver §Fuente de entrada (P23). El
  staging no es entregable ni entra en `v{N}`.
- `/data/processed/v{N}/` = fuente de verdad y entregable inmutable de teoría.
  Un `.txt` por documento en `/data/processed/v{N}/documents/`, más
  `manifest.json` (índice inmutable) y `stats.json`.
- Un paso de **ingesta** vuelca la teoría a Supabase (tabla `teoria_chunks`,
  ver `src/jokes/SPEC.md` §Esquema de tablas) como copia indexable — **no** es
  la fuente de verdad, esa sigue siendo el fichero `v{N}`.
- `pgvector` es el índice único de consulta del RAG (compartido con los
  chistes); toda consulta filtra por `tipo_fuente`.

## Limpieza

`externo*` (`teoria`, `transcripcion_curso`): AGRESIVA por defecto (elimina
muletillas, repeticiones, corrige errores obvios, separa en párrafos).
Excepción: `subtipo=ejemplo` conserva el estilo oral.

## Idioma

Corpus bilingüe explícito — teoría se traduce a español, los ejemplos se
conservan en idioma original. RAG configurado para multiidioma.

## Metadatos

YAML frontmatter en el `.txt`: `fuente`, `autor`, `idioma_original`,
`idioma_fragmento`, `subtipo`, + `tipo_fuente`, `licencia`.

## Idempotencia y versionado

`processed_files.json` (hash MD5) para idempotencia de fichero de origen. En
modo Drive-real hay **una capa más, independiente y por encima**: la de
`DriveSync` (`fileId` + `modifiedTime` en `data/state/theory_drive.json`) — no
sustituye al MD5, ver §Fuente de entrada, «Idempotencia en capas».
Versionado `v{N}` **inmutable**: su `manifest.json` es inmutable una vez
generado, nunca se sobrescribe una versión. Es el único flujo con `v{N}` de
corpus (los chistes son un store vivo, sin snapshots — ver `src/jokes/SPEC.md`).

**Reanudación:** si el flujo falla a mitad, retoma desde el último fichero no
completado, sin reprocesar lo ya hecho.

## Stack

`markitdown` (conversión PDF/DOCX → Markdown, ver §Parser), `pytesseract` +
`pdf2image` (OCR *fallback* para páginas escaneadas), `ebooklib` (EPUB, si no
migra a markitdown), `langdetect`, `deep-translator` (DeepL free /
LibreTranslate), `APScheduler`, `google-api-python-client` (Drive real, en uso
desde P23 vía `src/utils/drive_sync.py` — ya no es una dependencia reservada).
Coste cero.
**Sin LLM** — ver [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md).

## Riesgos propios de este flujo

| Riesgo | Mitigación |
|--------|-----------|
| PDFs escaneados con OCR de baja calidad | Tesseract + revisión de muestra; API externa solo si es inaceptable |
| Traducción automática de teoría de baja calidad | Solo traducir teoría, conservar ejemplos en original; revisar muestra |
| Corpus real más pequeño de lo esperado post-limpieza | Medir con `scripts/stats_report.py` antes de comprometerse |
| El sync automático de Drive sobrescribe material sagrado de `data/raw/` | El sync escribe SOLO en `data/staging/theory/` (caché reconstruible); `data/raw/` no es destino de escritura de ninguna etapa (P23) |
| Un fichero de Drive con MIME inesperado rompe el pipeline | La query filtra por los 5 MIMEs de `MIMES_TEORIA`; el resto ni se lista. Un fichero staged sin Parser conocido cae en `ResultadoPipeline.ignorados`, no en `fallidos` (ver `pipeline.py`) |
