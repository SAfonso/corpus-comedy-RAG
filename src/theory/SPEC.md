# Flujo A — Teoría (Drive → ficheros)

> Spec de `src/theory/`. Ver también [`docs/specs/00-overview.md`](../../docs/specs/00-overview.md)
> (contexto general, `tipo_fuente`, layout) y
> [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md) (regla "no LLM" para este flujo).

Flujo batch, 100% determinista, sin LLM. Es el pipeline original y su cadena de
transformación **no ha cambiado**; lo que cambió con P25 es **dónde acaba**: el
entregable pasa de `/data/processed/v{N}/` a las capas `bronze`/`silver`/`gold`
de Supabase (ver §Storage y §Idempotencia y versionado).

## Cadena de componentes (el orden importa)

```
[DriveSync (etapa 0, solo modo Drive-real)] → [captura Bronze] → DriveMonitor → Parser
  → SubtypeDetector → Cleaner → LanguageDetector
  → LanguageNormalizer → QualityScorer → FormatNormalizer → [persistencia Silver]
  → [ingesta Gold]
```

- **DriveSync (etapa 0, `src/theory/drive_sync.py`):** sincroniza la carpeta de
  Google Drive real a `data/staging/theory/` y deja ahí los ficheros
  nuevos/modificados. **Opcional:** solo corre en modo Drive-real; en modo
  solo-local la cadena empieza en `DriveMonitor` como siempre — ver §Fuente de
  entrada abajo (P23).
- **Captura Bronze (etapa 0.5, `src/utils/document_store.py`):** sube el fichero
  ORIGINAL, sin tocarlo, a `bronze.teoria_documentos` + bucket `bronze-teoria`
  antes de que ningún Parser lo abra (P25, task 59). Ver §Fuente de entrada,
  «Captura Bronze».
- **Persistencia Silver / ingesta Gold:** el `.md` limpio/traducido/normalizado
  va a `silver.teoria_documentos` + bucket `silver-teoria` (task 60) y de ahí se
  chunkea/embebe a `gold.teoria_chunks` (task 61). **`/data/processed/v{N}/` se
  retira** (task 63): ver §Storage y §Idempotencia y versionado.
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
- **FormatNormalizer:** salida uniforme con cabecera YAML (ver §Metadatos),
  producida por `render_document`. Su destino es el `.md` de
  `silver.teoria_documentos` (P25); el `.txt` de `v{N}` se retira (task 63).

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

## Fuente de entrada — Drive real, modo dual y captura Bronze (P23 + P25)

> **P23 (2026-07-27) cierra P18** (2026-07-22, "integración con la API de Drive
> diferida") y fija los dos modos de entrada. **P25 (2026-07-28) añade la captura
> Bronze**: todo fichero que entra al Flujo A se guarda en Supabase, tal cual,
> ANTES de que ningún Parser lo abra. Lo de P23 sigue vigente salvo en un punto,
> señalado abajo: **qué es "lo sagrado"** — ya no es el fichero (ni el local ni el
> de Drive), es la fila Bronze.
>
> Lo que P18 prometía —"cuando se decida activar Drive de verdad, se añaden
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

| | **Modo solo-local** (por defecto) | **Modo Drive-real** (opt-in, `--sync-drive`) |
|---|---|---|
| Etapa 0 (`DriveSync`) | no corre | `DriveSyncTeoria.sync_con_metadata()` → `data/staging/theory/` |
| Carpetas que vigila `DriveMonitor` | `data/raw/books/` + `data/raw/notes/` | `data/staging/theory/` |
| Quién deja los ficheros ahí | una persona, a mano | el sync de Drive |
| Credenciales de Drive | no hacen falta | obligatorias |
| Captura Bronze (P25) | **opt-in** (`--capturar-bronze`); por defecto la cubre `scripts/backfill_teoria_bronze.py` | **por defecto sí**, en modo Drive (`--sin-captura-bronze` para desactivarla) |
| Credenciales de Supabase | solo si se pide captura o `--ingest` | obligatorias (salvo `--sin-captura-bronze`) |

El modo solo-local **no se retira ni se deprecia**: es el modo de desarrollo y
de tests (no exige credenciales ni red), y es el que sirve al corpus ya
descargado a mano que documenta [`docs/CORPUS_INVENTORY.md`](../../docs/CORPUS_INVENTORY.md)
(hoy en disco: **25 transcripciones** en `data/raw/transcriptions/` repartidas en
tres subcarpetas por ponente y **7 libros** PDF/EPUB en `data/raw/books/`;
`data/raw/notes/` está vacío). Un run en modo Drive-real y
otro en modo solo-local producen la misma cadena a partir de `DriveMonitor`;
lo único que difiere es de dónde salieron los ficheros.

### Qué significa "sagrado" ahora — P25 corrige a P23

**Restricción dura que P23 fijó y P25 no toca: el sync automático de Drive
escribe en `data/staging/theory/` y NUNCA en `data/raw/`.** Un proceso que
re-descarga de Drive **sobrescribe por definición** cada vez que algo cambia en
el origen (`drive_sync.py` resuelve el destino por nombre de fichero y hace
`write_bytes`); apuntarlo a `data/raw/` habría pisado material curado a mano en
el primer run útil.

Lo que P25 **sí** cambia es la conclusión. P23 razonaba que lo sagrado es "el
único ejemplar que existe del original" y que ese ejemplar vive en `data/raw/`
(modo local) o en Drive (modo Drive-real). Saber **dónde vive** un ejemplar
único no lo protege: Drive es un espacio de trabajo editable por una persona
—borrar ahí borra el único ejemplar— y el staging es caché que el propio sync
sobrescribe. La conclusión correcta, y la que `CLAUDE.md` §"Regla más
importante" ya recoge, es que hace falta **una copia inmutable propia**:

- **Lo sagrado es la fila Bronze** (`bronze.teoria_documentos` + su objeto en el
  bucket `bronze-teoria`), append-only, fuera del alcance de cualquier edición
  manual. Ver §Storage.
- **`data/raw/books/`, `data/raw/notes/` y `data/raw/transcriptions/` siguen en
  uso operativo** —son la entrada del modo solo-local— y se siguen tratando como
  **solo lectura**: ninguna etapa escribe ahí, ni el sync, ni la captura, ni el
  backfill (task 66). Lo que ya no son es la pieza que garantiza que el material
  sobreviva.
- **`data/staging/theory/` es caché, no entregable**: no se versiona en git (ya
  cubierto por `data/**` en `.gitignore`) y se puede borrar entero sin pérdida de
  información — ahora con más motivo que en P23, porque lo que había ahí ya está
  en Bronze.
- **Perder `data/raw/` deja de ser perder corpus** en cuanto el backfill de la
  task 66 haya corrido. Hasta ese momento sigue siéndolo: por eso P25 insiste en
  que el backfill se hace **ahora** y no "de aquí en adelante".

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

### Captura Bronze — todo lo que entra se guarda antes de tocarlo (P25, task 59)

La captura es una etapa **0.5**: después del sync, antes del parsing. No
transforma nada — sube los bytes del original tal cual y escribe la fila que los
indexa, vía `document_store.capturar(capa="bronze", flujo="teoria", ...)`
(contrato completo en [`src/utils/SPEC.md`](../utils/SPEC.md) §DocumentStore; las
columnas propias de teoría, en §Storage de este documento).

**Punto de enganche exacto (task 59), en `src/theory/pipeline.py`:**

- El bloque `if drive_sync is not None:` que hoy hace `drive_sync.sync()` por su
  efecto y **descarta el valor de retorno** pasa a hacer
  `archivos = drive_sync.sync_con_metadata()` y a iterar sobre él capturando cada
  `ArchivoSincronizado`. Sigue estando donde está: **antes** de resolver
  `carpetas` y de instanciar ningún `DriveMonitor`.
- **`sync()` y `sync_con_metadata()` no se llaman las dos en la misma corrida**
  (comparten el estado de idempotencia y la segunda devolvería `[]`, ver
  `src/utils/SPEC.md` §DriveSync): `run_pipeline` llama **solo** a
  `sync_con_metadata()`, con o sin captura activada.
- El contrato duck-typed del parámetro `drive_sync` se **amplía** de
  `.sync() -> list[Path]` + `.staging_dir` a `.sync_con_metadata() -> list[ArchivoSincronizado]`
  + `.staging_dir`. Los dobles de prueba de `tests/unit/theory/test_pipeline.py`
  se actualizan en la task 59 (son dobles internos, no el contrato de regresión
  de las tasks 43/44, que la task 57 deja intacto y sin modificar).
- La captura legacy de los pendientes fuera del staging va **después** de resolver
  `pendientes`/`elegibles` y **antes** del bucle de `_procesar_fichero` — es el
  primer punto donde existe la lista de ficheros que la cadena va a abrir.
- `document_store` entra como **parámetro inyectable** de `run_pipeline`
  (`document_store=None` → sin captura, comportamiento actual bit a bit), mismo
  patrón que `drive_sync` y `traductor`: en tests se inyecta un doble y **ningún
  test unitario toca la red**.
- En `scripts/run_pipeline.py`, la construcción del `DocumentStore` va **dentro
  del mismo `try`** que ya envuelve `run_pipeline_fn` y la construcción del sync:
  un fallo de configuración de Supabase es "fallo fatal antes de completar el
  run", es decir **exit code 3**, sin inventar un código nuevo (mismo criterio
  que la task 45 con `DRIVE_FOLDER_ID`).

**Quién captura cada fichero, con qué metadata y con qué clave** — esta tabla es
el contrato, no hay más casos:

| Fichero | Quién lo captura | Modo | Clave de idempotencia |
|---|---|---|---|
| Devuelto por `sync_con_metadata()` **en este run** | `run_pipeline`, etapa 0.5 (task 59) | Drive | `(drive_file_id, modified_time)` |
| Pendiente **bajo `drive_sync.staging_dir`** pero no sincronizado en este run | nadie: ya se capturó en el run que lo sincronizó | — | — |
| Pendiente **fuera del staging** (`data/raw/**`, `carpetas` explícitas) y captura pedida | `run_pipeline`, misma etapa 0.5, modo legacy | legacy | `hash_md5` del contenido |
| Todo `data/raw/**` de una vez, sin correr la cadena | `scripts/backfill_teoria_bronze.py` (tasks 66/67) | legacy | `hash_md5` del contenido |

Cuatro consecuencias que las tasks 59/66 no tienen que redescubrir:

- **Los dos caminos legacy convergen en la MISMA fila.** El backfill y el
  pipeline usan la misma clave (`hash_md5`, `drive_file_id IS NULL`), así que
  correr uno después del otro —en cualquier orden, cuantas veces sea— no duplica
  nada: la segunda captura devuelve `ya_existia=True` sin subir el objeto.
- **Un fichero del staging nunca se captura en modo legacy.** Si un pendiente
  está bajo `drive_sync.staging_dir`, ya tiene (o tendrá) su fila en modo Drive;
  capturarlo además como legacy crearía una **segunda fila para el mismo
  documento**, porque las dos claves viven en índices únicos parciales distintos
  y no se ven entre sí (P25). La regla es local y verificable: *¿está bajo
  `staging_dir`? entonces no lo toca la rama legacy.*
- **Un fallo aguas abajo no pierde la captura.** Bronze se escribe antes del
  Parser, así que un PDF que revienta el OCR ya está guardado y se puede depurar
  contra el original real. Es el mismo orden que el Flujo B (P22: "guarda
  primero, procesa después").
- **La captura no cambia la idempotencia de `DriveMonitor`.** Un fichero
  capturado que después falla en la cadena **no** queda marcado en
  `processed_files.json` (ver `pipeline.py` §"Fricción resuelta"): el siguiente
  run lo reintenta y su captura devuelve `ya_existia=True`. Las dos idempotencias
  responden a preguntas distintas, ver la tabla de capas abajo.

**Modo Drive-real: la captura va por defecto.** Con `--sync-drive` la captura
está **activa salvo que se pida `--sin-captura-bronze`**, y ese flag deja rastro
en el resumen JSON (`bronze.omitida=true`). Un modo de producción en el que la
garantía de durabilidad dependa de que alguien recuerde un flag no es una
garantía; y el opt-out sigue existiendo para depurar el sync sin escribir en
Supabase.

**Modo solo-local: la captura es opt-in (`--capturar-bronze`), y no corre por
defecto.** No es una excepción a P25, es dónde vive el trabajo:

1. El modo solo-local es, por P23, **el modo de desarrollo y de tests**: su
   propiedad definitoria es que no exige credenciales ni red. Capturar en cada
   corrida convertiría `python scripts/run_pipeline.py` sin flags en un comando
   que falla sin `SUPABASE_URL` — rompiendo el "comportamiento por defecto
   intacto" que exigen tanto P23 como la propia task 59.
2. El material local es un **conjunto cerrado y conocido** (7 libros en
   `data/raw/books/` y 25 transcripciones en `data/raw/transcriptions/`, repartidas
   en tres subcarpetas por ponente; `data/raw/notes/` está vacío), no un flujo
   continuo. Un backfill re-ejecutable lo cubre entero de una vez y en un solo
   sitio (task 66), incluida la **recursión en subcarpetas** que `DriveMonitor` no
   hace.
3. `DriveMonitor` solo devuelve ficheros **nuevos o modificados**, así que una
   captura enganchada al bucle del pipeline nunca vería el material ya procesado:
   apoyarse en ella para la durabilidad del corpus histórico dejaría fuera
   justamente lo más antiguo, que es lo que más tiempo lleva sin copia.
4. El flag existe igualmente para quien cure `data/raw/` a mano y quiera
   durabilidad en el mismo comando, sin acordarse de lanzar el backfill después.

**Riesgo aceptado y su mitigación:** material dejado en `data/raw/` y procesado
en solo-local sin captura queda sin fila Bronze —y, si además se pidió Silver,
produciría una fila Silver huérfana—. Lo detecta la validación de topología de la
task 68 ("cada fila Silver tiene su fila Bronze de origen"), y lo cura una pasada
del backfill. Por eso la persistencia Silver **comparte interruptor** con la
captura Bronze (§Storage): no hay ninguna combinación de flags que escriba Silver
sin haber intentado escribir Bronze.

### Idempotencia en capas (independientes)

Mismo patrón que el Flujo C (`src/jokes/historico/SPEC.md` §"Idempotencia en
capas"): **no se fusionan**, cada capa vigila su propio estado en su propio
sitio. Las dos primeras son estado local reconstruible; las tres últimas viven en
Supabase y son las que mandan (P25) — la tabla completa está en §Idempotencia y
versionado.

| Capa | Pregunta | Clave | Estado |
|------|----------|-------|--------|
| `DriveSync` (etapa 0, solo modo Drive-real) | ¿qué fichero **descargar** de Drive? | `fileId` + `modifiedTime` (metadata) | `data/state/theory_drive.json` |
| `DocumentStore` Bronze (etapa 0.5) | ¿esta **versión del original** ya está guardada? | `(drive_file_id, modified_time)` o `hash_md5` | `bronze.teoria_documentos` |
| `DriveMonitor` | ¿qué fichero local **procesar**? | MD5 del contenido | `data/state/processed_files.json` |

`modifiedTime` se lee de la metadata **sin descargar** el fichero: la capa 0
evita justo el trabajo caro (la descarga). El MD5 del `DriveMonitor` necesita
el fichero ya en local, por eso no puede sustituirla. Y la capa Bronze no
sustituye a ninguna de las dos: responde a "¿está en Supabase?", no a "¿cambió en
Drive?" ni a "¿lo procesé ya?" — borrar los JSON locales fuerza re-descarga y
reproceso, pero **no puede duplicar nada**, porque la capa Bronze lo detiene.

Consecuencia práctica que la task 45 **no** tiene que resolver, pero sí conocer:
si un fichero se descarga y luego la cadena falla aguas abajo, `DriveSync` ya
lo dio por descargado (no lo re-descargará), pero el fichero sigue en el
staging y `processed_files.json` nunca lo comprometió (ver `pipeline.py`,
§"Fricción resuelta"), así que el siguiente run lo reintenta desde el staging.
Correcto. Solo si se borra el staging **sin** borrar
`data/state/theory_drive.json` habría que borrar también ese estado para
forzar la re-descarga — los dos ficheros de estado se borran juntos o no se
borra ninguno.

## Storage — Bronze / Silver / Gold en Supabase (P25, 2026-07-28)

> Sección reescrita por la **task 51**. Aquí se fija **qué se guarda de teoría,
> dónde y con qué columnas propias**. El DDL exacto es de la **task 53**; el
> componente que escribe (buckets, naming del objeto, orden objeto→fila,
> idempotencia) es [`src/utils/SPEC.md`](../utils/SPEC.md) §DocumentStore
> (task 58); el patrón de acceso por schema (`client.schema("bronze").table(...)`),
> los *exposed schemas* y los GRANTs son [`src/jokes/SPEC.md`](../jokes/SPEC.md)
> §Storage y **aplican igual aquí sin redefinirse**. Nada de eso se repite.

**El entregable de teoría deja de ser un fichero.** Hasta P25 la fuente de verdad
era `/data/processed/v{N}/` y Supabase una "copia indexable". Se invierte: la
fuente de verdad es Supabase y el disco local pasa a ser entrada (`data/raw/`) o
caché (`data/staging/theory/`).

| Capa | Tabla | Bucket | Qué contiene | Quién escribe |
|---|---|---|---|---|
| Bronze | `bronze.teoria_documentos` | `bronze-teoria` | el **original intacto**: PDF/EPUB/DOCX/TXT de libros, apuntes y transcripciones WhisperX | `pipeline.py` etapa 0.5 (task 59) y `scripts/backfill_teoria_bronze.py` (task 66) |
| Silver | `silver.teoria_documentos` | `silver-teoria` | el **`.md` limpio/traducido/normalizado**: la salida de `render_document` | `pipeline.py` tras `FormatNormalizer` (task 60) |
| Gold | `gold.teoria_chunks` | — (solo filas) | chunk + embedding: la **capa de consumo** del RAG | `ingest_teoria.py` vía `teoria_store.py` (task 61) |

- **Las tres capas son append-only.** Nunca `UPDATE` ni `DELETE` de una fila, ni
  sobrescritura de un objeto: `document_store` ni siquiera expone un método para
  hacerlo. Una versión nueva es una fila nueva.
- **Los cuatro buckets son privados y se crean a mano** (P25): el corpus incluye
  material `personal_only` (`docs/specs/llm-policy.md`).
- **`pgvector` sigue siendo el índice único de consulta del RAG** (compartido con
  los chistes) y toda consulta filtra por `tipo_fuente`. Eso no cambia: lo que
  cambia es de dónde sale lo que se indexa.
- **`gold.teoria_chunks` mantiene su DDL en el `schema.sql` de `src/jokes/`** y su
  cliente fuera de ese módulo (`src/theory/teoria_store.py`), tal y como fija
  `src/jokes/SPEC.md` §Storage. La regla de dependencias sigue intacta:
  `theory/` no importa nada de `jokes/`.

### `bronze.teoria_documentos` — qué añade teoría vía `extra`

`document_store` ya escribe por su cuenta `bucket`, `object_path`,
`drive_file_id`, `modified_time`, `hash_md5`, `origen`, `nombre`, `mime_type` y
`tamano_bytes`, y **rechaza un `extra` que intente pisarlas**. Teoría añade tres
columnas y ninguna más:

| Columna (`extra`) | Valor | Por qué en Bronze |
|---|---|---|
| `tipo_fuente` | `teoria` \| `transcripcion_curso` | Discriminador cerrado de `docs/specs/00-overview.md` §2, presente en toda unidad del corpus. Se deriva **de la extensión**, exactamente igual que hoy en `pipeline._PARSERS_POR_EXTENSION` (`.txt` → `transcripcion_curso`; `.pdf`/`.docx`/`.epub` → `teoria`), que es la única señal disponible en la etapa 0.5 — y la misma que usará el Parser después, así que no puede desincronizarse. Es una **clasificación, no una transformación**: no toca los bytes, así que no contradice que Bronze sea crudo. |
| `licencia` | `personal_only` por defecto (`externo*`, §2) | Es un atributo legal **del original**, no del derivado: si vive solo en Silver, se pierde en cuanto se regenere o se consulte Bronze directamente. |
| `ruta_relativa` | ruta del original relativa a la raíz de escaneo (`transcriptions/Demy/DENNY_1…txt`), `NULL` en modo Drive | El bucket es **plano** (`local_legacy/{hash}/{nombre}`), así que sin esta columna la captura **destruiría** la atribución por ponente que hoy codifican las subcarpetas de `data/raw/transcriptions/` (Demy, Pinol, Tomas). Capturar no puede perder información que el original traía. `NULL` en Drive porque la carpeta de Drive es plana por diseño (§DriveSync, "sin recursión en subcarpetas"). |

Lo que **no** lleva Bronze: `fuente`, `autor` ni ningún metadato derivado. P23
congeló esa derivación (`fuente` = `path.stem` embellecido, `autor=None`) y sigue
siendo cosa del procesado, no del original. Tampoco `fuente_id` — ver abajo.

**Un Google Doc nativo exportado a `.docx` se captura como `teoria`**, coherente
con la justificación ya cerrada en §Fuente de entrada (se exporta a `.docx`
precisamente para que no aterrice en el parser de WhisperX ni se etiquete como
transcripción de curso).

### `silver.teoria_documentos` — qué añade teoría vía `extra`

El contenido del `.md` **no va en la fila**: va al bucket `silver-teoria` como
objeto, igual que Bronze. La fila lleva metadata y linaje:

| Columna (`extra`) | Valor | Para qué |
|---|---|---|
| `origen_hash_md5` | `hash_md5` de la fila Bronze de la que deriva (= MD5 del **original**) | **La columna de linaje que manda.** Es la única clave que existe en los dos modos (Drive y legacy) y el pipeline la obtiene sin consultar nada: es el MD5 del fichero que está procesando — el mismo que `DriveMonitor` ya calcula, y el mismo que `document_store` escribe **siempre** en Bronze ("no solo en legacy", §DocumentStore paso 2). Sobre ella se resuelve "cada fila Silver tiene su Bronze de origen" (task 68). **NOT NULL.** |
| `origen_drive_file_id`, `origen_modified_time` | copia del par de Drive de la fila Bronze; `NULL` si el Bronze es legacy | Redundantes con `origen_hash_md5` pero baratas: hacen que el linaje se pueda leer sin `JOIN` y que "todas las versiones de este documento de Drive" sea una consulta directa sobre Silver. |
| `fuente` | título legible (`path.stem` embellecido, ver `pipeline._fuente_desde_nombre`) | Lo consumía `manifest.json`; al retirarse, la fila Silver es su único hogar. `ingest_teoria` lo necesita para `buscar_o_crear_fuente`. |
| `autor` | `None` (placeholder, P23) | Idem. Se rellenará cuando haya una fuente real de metadatos. |
| `tipo_fuente` | propagado desde Bronze | Desnormalizado a propósito: es el discriminador de §2 y ningún consumidor de Silver debería tener que ir a Bronze para saberlo. |
| `licencia` | propagada desde Bronze (`personal_only`) | Idem — `ingest_teoria` la propaga a `gold.teoria_chunks`. |
| `idioma_original` | idioma detectado del documento (`LanguageDetector`) | Hoy solo vive en el YAML del `.txt`; hace falta consultable para el corpus bilingüe (§Idioma) y para el check 7 de `validate_corpus`. |
| `traducido` | `boolean`: si `LanguageNormalizer` tradujo teoría al español | Distingue un documento español de origen de uno traducido, sin abrir el objeto. |
| `num_fragmentos` | `int` | Lo daba `manifest.json`; lo consumen la ingesta y la validación como *sanity check* barato. |
| `quality_score` | media de `score_quality` por fragmento del documento | Lo daba `stats.json`, que desaparece con `v{N}`. `QualityScorer` es **obligatorio** en la cadena (§Cadena de componentes) y su resultado se quedaría sin sitio donde vivir. Misma agregación que `format_normalizer` usa hoy para `stats.json` (`quality_score_medio` por documento). |

### La fila Silver **no** reutiliza el par de Drive de su Bronze — y por qué

Esta es la decisión de diseño de la task 51, y resuelve el límite que
[`src/utils/SPEC.md`](../utils/SPEC.md) §"Silver también entra por aquí" delegó
aquí explícitamente: **con el par del original como clave, regenerar Silver es un
no-op** — si se cambia el Cleaner o el traductor y se vuelve a capturar el mismo
`(drive_file_id, modified_time)`, `capturar()` encuentra fila y el `.md` nuevo no
llega nunca al bucket.

**Decisión: la fila Silver de teoría se escribe en modo legacy** —
`drive_file_id=None`, `modified_time=None`—, con lo que su clave de idempotencia
pasa a ser **el `hash_md5` del propio `.md`**, y la procedencia viaja en las
columnas `origen_*` de arriba.

```python
document_store.capturar(
    ruta=<el .md renderizado>, capa="silver", flujo="teoria",
    drive_file_id=None, modified_time=None,          # <- clave = hash del .md
    nombre=f"{slug}.md", mime_type="text/markdown",
    extra={"origen_hash_md5": <md5 del original>, ...},
)
```

Lo que esto compra, y por qué se eligió frente a las alternativas:

- **El "¿ha cambiado el proceso?" se responde con el resultado, no con una
  declaración.** Cambia el Cleaner → cambian los bytes del `.md` → cambia el hash
  → **fila nueva y objeto nuevo**, sin pisar el anterior. No cambia el resultado
  (un refactor, un renombrado) → mismo hash → `ya_existia=True` → no-op real: ni
  un objeto duplicado, ni **una sola llamada de embeddings pagada de más** en la
  ingesta Gold. Ninguna otra opción tiene esta segunda propiedad.
- **No exige tocar `document_store`.** Es la única alternativa que resuelve el
  problema **dentro** del contrato ya aprobado de la task 50: `drive_file_id`
  y `modified_time` ambos `None` es el modo legacy soportado y validado, y la
  clave `hash_md5` es la que el componente ya usa ahí. Cero enmiendas a un
  componente compartido que también consume el Flujo C.
- **Funciona igual en los dos modos.** Con el par del original, un `.md` derivado
  de material legacy (sin `fileId`) heredaría como clave el `hash_md5` **del
  original**, no el del `.md` — es decir, la clave de Silver y la de Bronze serían
  la misma cadena en tablas distintas, y el linaje quedaría amarrado a una
  coincidencia. Con esta decisión, Silver tiene clave propia siempre.
- **No necesita saber nada de Bronze en tiempo de escritura.** El pipeline no
  tiene que arrastrar el `id` de la fila Bronze en memoria ni consultarla: el
  `origen_hash_md5` se calcula del fichero que ya tiene abierto. Eso importa
  porque un pendiente puede venir de un run anterior (sincronizado entonces,
  procesado ahora) y su metadata de Drive **no está disponible** en el run que lo
  procesa.

Alternativas evaluadas y descartadas:

- **(a) Columna `version_proceso` (hash del código/config de limpieza) comparada
  antes de capturar.** Descartada: un hash del código cambia con refactores que no
  cambian ni un byte de salida (re-embebe el corpus entero para nada) y **no
  cambia** cuando cambia algo externo que sí altera la salida — DeepL puede
  devolver otra traducción del mismo texto sin que este repo se entere. Además
  obliga a un `SELECT` propio fuera de `document_store` y a un camino de escritura
  que esquive su idempotencia, es decir, a enmendar el componente compartido.
- **(b) Clave sintética por snapshot (cada regeneración es "otro documento").**
  Descartada: es `v{N}` con otro nombre — un número global que hay que acordarse
  de subir, que reescribe **todos** los documentos aunque solo haya cambiado uno,
  y cuyo defecto ya diagnosticó P25 (un versionado que depende de un gesto humano
  es un versionado que no ocurre).
- **(c) Aceptar el no-op y regenerar borrando la fila a mano.** Descartada: exige
  un `DELETE` sobre una capa declarada append-only, y `document_store` —
  correctamente— no ofrece la herramienta. Convertiría "mejorar el Cleaner" en una
  operación manual sobre producción.

**Divergencia declarada con `src/utils/SPEC.md`.** Esa spec describe el caso por
defecto ("la fila Silver lleva el mismo `(drive_file_id, modified_time)` que la
versión Bronze de la que deriva") y en la misma subsección delega en la task 51 la
elección del mecanismo. Teoría elige el mecanismo de arriba, así que **para
`silver.teoria_documentos` esa frase no aplica**; el contrato del componente no
cambia (el modo legacy es suyo y está soportado tal cual). La frase de `utils/`
merece una nota de remisión cuando se vuelva a tocar ese fichero — fuera del scope
de esta task, que no edita `src/utils/SPEC.md`.

**Consecuencias que las tasks 53 y 68 deben conocer:**

- En `silver.teoria_documentos`, `drive_file_id` y `modified_time` son **siempre
  `NULL`** (las escribe `document_store`, no se retiran de la tabla: las cuatro
  tablas de documentos mantienen la misma forma). De los dos índices únicos
  parciales de P25, en esta tabla solo muerde el segundo: `(hash_md5) WHERE
  drive_file_id IS NULL` — que es, de hecho, `unique(hash_md5)`.
- `origen` vale **siempre `'local_legacy'`** en esta tabla, y hay que leerlo como
  *"generado localmente por el pipeline"*, que es literalmente cierto: el `.md` no
  viene de Drive, lo produce esta cadena. Es un artefacto del nombre de la
  constante en `document_store` (`ORIGENES`), no una afirmación falsa sobre la
  procedencia — que vive en las columnas `origen_*`. Renombrar la constante
  tocaría un componente compartido y una spec ya mergeada a cambio de estética:
  no se hace. Cualquier recuento por `origen` debe agrupar **primero por capa**.
- Dos originales distintos que limpien a un `.md` byte a byte idéntico comparten
  fila Silver. Es **deduplicación deseada**, no una colisión: `data/raw/books/`
  contiene hoy dos EPUB del mismo libro (`…step-by-step-to-stand-up-comedy-revised-edition.epub`
  y `… (1).epub`), que Bronze ya colapsa en una sola fila legacy por el mismo
  motivo (P25: "si el contenido es el mismo, es el mismo documento"). El check 6
  de `validate_corpus` (`sin_duplicados`) existe precisamente para hacerlo
  visible.

### `fuente_id` no viaja en las filas de documento

Ni Bronze ni Silver llevan `fuente_id` (FK a `silver.fuentes`). La resolución
sigue donde está hoy: en la **ingesta Gold**, vía
`TeoriaStore.buscar_o_crear_fuente`, que ya escribe `gold.teoria_chunks.fuente_id`.

- `silver.fuentes` es **taxonomía editable a mano** del contrato B/C
  (`src/jokes/SPEC.md` §Taxonomías). Añadir un segundo escritor —el pipeline de
  teoría, en cada captura— duplica los sitios desde los que puede nacer una fuente
  con el nombre ligeramente distinto.
- Bronze **no puede depender de nada aguas abajo**: una captura que falle porque
  la taxonomía no responde es exactamente el fallo que P25 existe para evitar. La
  escritura de Bronze y de Silver es un `INSERT` sin lecturas previas a otras
  tablas, y así se queda.
- Su único consumidor real (`gold.teoria_chunks`) se puebla **después** de Silver,
  cuando la resolución ya ocurre igualmente.
- Si algún día se añade, la FK cross-schema funciona como documenta
  `src/jokes/SPEC.md` §"FK cross-schema": misma semántica que una FK normal,
  referencia **siempre cualificada** con el schema, y `silver.fuentes` creada antes
  que la tabla que la referencia.

### Puntos de enganche exactos en código (tasks 60 y 61)

**Task 60 — persistencia Silver.** El `.md` está disponible en `run_pipeline`,
en el bucle que hoy acumula `documentos_listos`, **después** de que
`_procesar_fichero` haya devuelto su `DocumentoEntrada`
(`Parser → SubtypeDetector → Cleaner → LanguageNormalizer` completos):

- El productor del `.md` es **`format_normalizer.render_document(fragmentos, fuente=…,
  tipo_fuente=…, autor=…, licencia=…)`**, que ya es público y que
  `generar_version` invoca internamente. La task 60 lo llama directamente, una vez
  por documento, y sube su salida. **El contenido no cambia** respecto al `.txt`
  de `v{N}` —mismo YAML frontmatter, mismos fragmentos separados por línea en
  blanco—; lo único que cambia es la extensión (`.md`, coherente con P25 y con que
  los Parsers ya emiten Markdown desde P17) y el destino. Que el formato sea
  idéntico es lo que permite que `validate_corpus` (task 62) y
  `ingest_teoria._separar_cuerpo` (task 61) sigan funcionando **sin cambiar su
  parser**.
- El nombre del objeto usa el mismo `nombre_fichero or _slugify(fuente)` que
  `generar_version`, con `.md`. La desambiguación por sufijo `-2` de
  `generar_version` **no hace falta**: el `object_path` ya no colisiona nunca
  (lleva el `hash_md5` en el prefijo, §DocumentStore §Naming).
- `quality_score`, `num_fragmentos`, `idioma_original` y `traducido` se calculan
  aquí, del `DocumentoEntrada` que ya está en memoria (no se releen del `.md`).
- **Comparte interruptor con la captura Bronze**: el mismo `document_store`
  inyectado. No existe configuración que escriba Silver sin Bronze.
- La task 60 **no** retira `generar_version`: durante esa task se siguen
  generando los `v{N}` tal cual. Retirarlos es scope exclusivo de la task 63.

**Task 61 — `ingest_teoria.py` / `teoria_store.py` leen de Silver.** Deja de
existir `manifest.json`, así que desaparecen `_descubrir_ultima_version` y
`_leer_manifest` como origen de trabajo; en su lugar:

- La unidad de trabajo es una **fila de `silver.teoria_documentos`**; su `.md` se
  descarga del bucket `silver-teoria` por su `object_path` y se trocea con el
  `_separar_cuerpo` de hoy, **sin cambios** (mismo formato, ver arriba). Su
  limitación documentada (un fragmento no puede contener una línea en blanco
  interna) sigue vigente tal cual.
- `fuente`, `tipo_fuente` y `licencia` —que se leían de `manifest.json`— se leen
  ahora de las columnas homónimas de la fila Silver. `buscar_o_crear_fuente` no
  cambia.
- **Qué sustituye a `version_corpus`**, decidido aquí para que la task 61 no lo
  reabra: la clave de idempotencia de reingesta sigue siendo
  `unique (doc_id, version_corpus, chunk_index)` —**sin cambio de DDL ni del
  `on_conflict` de `TeoriaStore.guardar_chunk`**— reexpresada así:
  - `doc_id` = **`silver.teoria_documentos.id`** (el id de la fila, como texto).
    Es la identidad estable de "esta versión de este documento en Silver". Se
    prefiere al `object_path` porque el formato del path lo decide
    `document_store` (otro módulo): amarrar la identidad de Gold a esa convención
    convertiría un renombrado en `utils/` en una migración de datos en Gold.
  - `version_corpus` = **`silver.teoria_documentos.hash_md5`** (el hash del `.md`).
    Conserva literalmente la semántica de la columna —"¿a qué versión del corpus
    pertenece este chunk?"— con un valor **comparable**: dos ingestas del mismo
    documento con distinto código de limpieza dan `version_corpus` distinto,
    exactamente como daban `v3` y `v4`. Y, a diferencia de `v{N}`, no depende de
    que nadie lo incremente.
  - `chunk_index` no cambia: sigue siendo el índice del fragmento.
  - El triple es único por construcción (ya lo es `doc_id` solo), así que la
    constraint existente sigue siendo correcta y **una reingesta de la misma fila
    Silver no duplica ni una fila ni paga un embedding**.
- Selección por defecto de qué ingestar: las filas Silver **que aún no tienen
  chunks en Gold**, no "la última versión". Es resumible y se autocura tras un
  fallo parcial, que es lo que antes daba la inmutabilidad de `v{N}`.
  `scripts/run_pipeline.py --ingest` pasa a ingestar las filas Silver escritas en
  ese run (y deja de derivar el número de versión de `version_dir`, ver
  §Idempotencia y versionado).

## Limpieza

`externo*` (`teoria`, `transcripcion_curso`): AGRESIVA por defecto (elimina
muletillas, repeticiones, corrige errores obvios, separa en párrafos).
Excepción: `subtipo=ejemplo` conserva el estilo oral.

## Idioma

Corpus bilingüe explícito — teoría se traduce a español, los ejemplos se
conservan en idioma original. RAG configurado para multiidioma.

## Metadatos

YAML frontmatter en el documento renderizado (el `.md` de Silver; hasta la
task 63, también el `.txt` de `v{N}`): `fuente`, `autor`, `idioma_original`,
`idioma_fragmento`, `subtipo`, + `tipo_fuente`, `licencia`. **El formato no
cambia con P25** — es lo que permite que el parser de `validate_corpus` y
`ingest_teoria._separar_cuerpo` sigan sirviendo sin tocarse (§Storage). Los
metadatos a nivel de documento se **duplican además como columnas** de
`silver.teoria_documentos`, para poder consultarlos sin descargar el objeto.

## Idempotencia y versionado (P25, 2026-07-28)

### Qué sustituye al "versionado de corpus"

`v{N}` era un **snapshot global**: una carpeta inmutable con todo el corpus
procesado en un run, indexada por `manifest.json`. Lo sustituye un **linaje
append-only por documento**, con tres eslabones y ninguna numeración global:

```
original (versión N)          →  renderizado (versión M)         →  chunks
bronze.teoria_documentos         silver.teoria_documentos           gold.teoria_chunks
clave: (drive_file_id,           clave: hash_md5 del .md            clave: (doc_id,
        modified_time)           linaje: origen_hash_md5 →                  version_corpus,
        o hash_md5 (legacy)              bronze.hash_md5                    chunk_index)
```

- **Una versión del corpus es ahora un par por documento**: (versión del
  original) × (versión del renderizado). Editar un libro en Drive añade una fila
  Bronze; mejorar el Cleaner añade una fila Silver; ninguna de las dos pisa nada.
- **La inmutabilidad la garantiza la base de datos, no una convención.** `v{N}`
  se defendía con una comprobación en Python (`VersionInmutableError` si el
  `manifest.json` ya existía); ahora son dos índices únicos parciales (P25,
  task 53) más un componente que no expone `UPDATE` ni `DELETE`.
- **Es más fino, y por eso funciona.** `v{N}` obligaba a reescribir el corpus
  entero para reflejar el cambio de un documento, y dependía de que alguien
  ejecutara el run que generaba la versión: por eso, en la práctica, **no se
  generó nunca ninguna** (`data/processed/` está vacío). El linaje por documento
  no necesita ningún gesto: sale de haber procesado un fichero.
- **Qué se pierde y cómo se recupera:** con `v{N}` bastaba un token (`v3`) para
  nombrar "el corpus entero en ese momento". El equivalente es una **ventana
  temporal**: como nada se borra y toda fila lleva `created_at`, "el corpus a
  fecha D" es *la última fila Silver por clave de linaje con `created_at <= D`*.
  Misma pregunta, una condición en vez de un directorio.
- **"Cuál es el renderizado vigente" es una pregunta de lectura, no una columna.**
  Es la fila Silver más reciente por clave de linaje (`origen_drive_file_id` +
  `origen_modified_time`, o `origen_hash_md5`). No hay flag `vigente`: mantenerlo
  exigiría un `UPDATE` sobre una capa append-only para no ganar nada que un
  `order by created_at desc limit 1` no dé.
- **Podar Gold no entra en P25.** Tras una regeneración deliberada, los chunks
  viejos siguen en `gold.teoria_chunks` con su `version_corpus` anterior. Quien
  consulte debe filtrar por la versión vigente; una política de poda es una
  decisión aparte, no un efecto colateral de esta reorganización.

### Las cinco capas de idempotencia

Ninguna sustituye a otra: cada una responde a una pregunta distinta y guarda su
estado en su propio sitio. Las dos primeras son **estado local reconstruible**;
las tres últimas viven en Supabase y son las que mandan.

| # | Capa | Pregunta | Clave | Dónde vive |
|---|------|----------|-------|------------|
| 1 | `DriveSync` (solo modo Drive-real) | ¿qué fichero **descargar**? | `fileId` + `modifiedTime` | `data/state/theory_drive.json` |
| 2 | `DriveMonitor` | ¿qué fichero local **procesar**? | MD5 del contenido | `data/state/processed_files.json` |
| 3 | `DocumentStore` Bronze | ¿esta **versión del original** ya está guardada? | `(drive_file_id, modified_time)`, o `hash_md5` en legacy | `bronze.teoria_documentos` |
| 4 | `DocumentStore` Silver | ¿este **renderizado exacto** ya está guardado? | `hash_md5` del `.md` | `silver.teoria_documentos` |
| 5 | `TeoriaStore` Gold | ¿este chunk ya está indexado? | `(doc_id, version_corpus, chunk_index)` | `gold.teoria_chunks` |

**Propiedad que hace utilizable todo lo demás: borrar los estados locales (1 y 2)
es seguro.** Fuerza re-descarga y reproceso, pero no puede duplicar nada, porque
3, 4 y 5 lo detienen. Eso convierte "regenerar el corpus con el Cleaner nuevo" en
una operación legítima y barata —borrar `processed_files.json` y volver a
correr— en vez de una maniobra peligrosa: lo que haya cambiado de verdad entra
como fila nueva, y lo que no, ni se sube ni se re-embebe.

**Corolario: la regeneración nunca es accidental.** Un cambio de código no
reprocesa nada por sí solo (la capa 2 solo devuelve ficheros nuevos o
modificados); hace falta el gesto explícito de invalidar el estado local. Y aun
entonces, si el `.md` sale idéntico, la capa 4 lo convierte en un no-op completo.

**Reanudación:** si el flujo falla a mitad, retoma desde el último fichero no
completado, sin reprocesar lo ya hecho (ver `pipeline.py`, §"Fricción resuelta").
Un fichero capturado en Bronze que después falle en el Parser **conserva su
captura** y se reintenta en el siguiente run contra la misma fila.

### Retirada de `/data/processed/v{N}/` (task 63)

- **Deja de generarse.** `pipeline.py` y `scripts/run_pipeline.py` dejan de
  invocar `generar_version`. El Flujo A no vuelve a escribir corpus en disco.
- **El código no se borra.** `generar_version` se queda en
  `src/theory/normalizers/format_normalizer.py`, sin llamador en el flujo, junto
  con `manifest.json`/`stats.json` y sus tests. `render_document` **sí** conserva
  llamador: es quien produce el `.md` de Silver (task 60).
- **Los `v{N}` que existieran no se tocan** (hoy no existe ninguno:
  `data/processed/` solo contiene su `.gitkeep`).
- **`ResultadoPipeline.version_dir` deja de ser un entregable**: se mantiene el
  atributo con valor `None` permanente y una nota de deprecación en su docstring,
  y `ResultadoPipeline` gana el recuento de lo realmente entregado
  (filas Bronze y Silver escritas / ya existentes / omitidas).
- **La clave `"version_dir"` del resumen JSON del CLI se mantiene, siempre
  `null`.** Ese resumen es un **contrato externo**: lo consume por `subprocess`
  un proyecto RAG que no tiene acceso a este código (ver `scripts/run_pipeline.py`,
  §"Por qué el contrato de CLI importa más de lo habitual"). Quitar una clave
  documentada rompería a un consumidor que no controlamos; dejarla en `null` es
  exactamente lo que ese contrato ya define para "este run no produjo versión".
  Las capas nuevas se reportan en **claves nuevas** (`bronze`, `silver`), que un
  consumidor viejo ignora sin enterarse.
- `_version_desde_dir` y el paso de `version=N` a la ingesta desaparecen con el
  cambio de la task 61 (la ingesta ya no se dirige por número de versión).

### `validate_corpus.py` sin `manifest.json` (task 62)

Hoy valida `v{N}/documents/*.txt` + `manifest.json` con **8 checks** (los 7
primeros solo miran los documentos; el 8.º, `check_manifest_sincronizado`,
necesita además el manifest). Tras la task 62:

- **Los checks 1-7 se conservan tal cual**, con la misma implementación pura: sin
  timestamps, sin `SPEAKER_XX:`, cabecera completa (los 7 campos), mínimo y máximo
  de palabras, sin duplicados por MD5, idiomas en `{es, en}`. Lo único que cambia
  es de dónde salen los `DocumentoLeido`: en vez de leer `documents/*.txt`, se
  listan las filas Silver y se descarga su objeto de `silver-teoria`.
  `path_relativo` pasa a ser el `object_path` de la fila (sigue identificando el
  documento en los mensajes de error).
- **Qué filas se validan: las vigentes**, una por clave de linaje (la más reciente
  por `created_at`). No todas. Los renderizados antiguos se conservan por diseño y
  pueden fallar legítimamente un check que se arregló después: validarlos dejaría
  el gate en rojo para siempre por documentos que ya nadie consume.
- **El check 8 se sustituye por `check_fila_objeto_coherente`.** El sustituto no
  es genérico: `manifest.json` era el **índice inmutable** que afirmaba "estos
  ficheros y no otros"; ahora el índice es la fila y el fichero es el objeto, así
  que el equivalente exacto es verificar la correspondencia fila ↔ objeto, en dos
  puntos:
  1. **El objeto existe** en `silver-teoria` bajo el `object_path` de la fila y se
     descarga sin 404. Es justamente el huérfano que `document_store` decidió
     tolerar al escribir el objeto antes que la fila (§DocumentStore, "orden");
     este check es donde se detecta.
  2. **El contenido es el que la fila dice**: `md5(bytes descargados) == fila.hash_md5`.
     Esto es **más fuerte** que el check 8 original, que solo comparaba conjuntos
     de nombres y nunca miró el contenido.
- **Qué NO comprueba la task 62, y por qué:** la completitud Bronze↔Silver ("¿todo
  Bronze tiene su Silver?") **no** es un check de este script. Un original
  capturado y todavía no procesado es un estado transitorio **normal** (la captura
  ocurre antes del Parser justamente para eso), y convertirlo en fallo pondría el
  gate en rojo en cada run parcial. La topología cross-capa —Silver sin Bronze de
  origen, Bronze sin objeto, claves de idempotencia duplicadas— es el checklist de
  la **task 68**, que corre sobre el sistema entero y no antes de cada commit. La
  frontera es: **task 62 valida contenido, task 68 valida topología.**
- **El modo contra ruta local se mantiene** (`python scripts/validate_corpus.py
  <ruta>`) para poder testear los checks con fixtures sin red, y los exit codes
  (0/1) y el formato de salida no cambian.

## Stack

`markitdown` (conversión PDF/DOCX → Markdown, ver §Parser), `pytesseract` +
`pdf2image` (OCR *fallback* para páginas escaneadas), `ebooklib` (EPUB, si no
migra a markitdown), `langdetect`, `deep-translator` (DeepL free /
LibreTranslate), `APScheduler`, `google-api-python-client` (Drive real, en uso
desde P23 vía `src/utils/drive_sync.py` — ya no es una dependencia reservada),
`supabase` (Bronze/Silver/Gold vía `src/utils/document_store.py` y
`src/theory/teoria_store.py`, P25). Coste cero salvo los embeddings de la
ingesta Gold, excepción ya documentada en `docs/specs/llm-policy.md`.
**Sin LLM** — ver [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md).

## Riesgos propios de este flujo

| Riesgo | Mitigación |
|--------|-----------|
| PDFs escaneados con OCR de baja calidad | Tesseract + revisión de muestra; API externa solo si es inaceptable |
| Traducción automática de teoría de baja calidad | Solo traducir teoría, conservar ejemplos en original; revisar muestra |
| Corpus real más pequeño de lo esperado post-limpieza | Medir con `scripts/stats_report.py` antes de comprometerse |
| El sync automático de Drive sobrescribe material sagrado de `data/raw/` | El sync escribe SOLO en `data/staging/theory/` (caché reconstruible); `data/raw/` no es destino de escritura de ninguna etapa (P23) |
| Un fichero de Drive con MIME inesperado rompe el pipeline | La query filtra por los 5 MIMEs de `MIMES_TEORIA`; el resto ni se lista. Un fichero staged sin Parser conocido cae en `ResultadoPipeline.ignorados`, no en `fallidos` (ver `pipeline.py`) |
| Material curado a mano en `data/raw/` procesado sin captura (modo solo-local sin flag) queda sin fila Bronze | Backfill re-ejecutable (tasks 66/67) sobre todo `data/raw/**`, misma clave `hash_md5` que la captura del pipeline (no duplica); la validación de topología de la task 68 detecta las filas Silver huérfanas |
| Una regeneración deliberada de Silver duplica el gasto de embeddings en Gold | La clave Silver es el hash del `.md`: si el resultado no cambia, la captura es no-op y la ingesta no vuelve a embeber (§Storage). El gasto solo se paga cuando el contenido cambió de verdad |
| Tras varias regeneraciones, el RAG recupera chunks de renderizados antiguos | "Vigente" = última fila Silver por clave de linaje; el consumo filtra por su `version_corpus`. La poda de Gold es una decisión aparte, fuera de P25 (§Idempotencia y versionado) |
| Un objeto en el bucket sin su fila (fallo entre la subida y el `INSERT`) | Huérfano tolerado a propósito (`src/utils/SPEC.md` §DocumentStore, orden objeto→fila): es invisible y el reintento lo cura porque la clave es determinista. El caso inverso (fila sin objeto) lo detecta `check_fila_objeto_coherente` (task 62) |
