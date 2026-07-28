# utils — código compartido

> Spec de `src/utils/`. Ver [`docs/specs/00-overview.md`](../../docs/specs/00-overview.md)
> para el contexto general.

`utils/` no tiene política propia — es la carpeta de implementaciones
reutilizables que consumen los flujos. No define reglas de negocio; solo
apunta a quién usa qué, para no asumir que algo aquí se usa simétricamente en
todos los flujos cuando no es así.

| Módulo | Qué hace | Quién lo consume | Spec del consumidor |
|--------|----------|-------------------|------------------------|
| `language_detector.py` | Detección de idioma | Flujo A (Teoría) — corpus bilingüe | `src/theory/SPEC.md` |
| `quality_scorer.py` | Puntuación 0–1 de densidad de contenido útil | Flujo A (Teoría) | `src/theory/SPEC.md` |
| `drive_sync.py` | Núcleo de sincronización de una carpeta de Google Drive real a un *staging* local (ver §DriveSync) | Flujo A (`src/theory/drive_sync.py`) **y** Flujo C (`src/jokes/historico/drive_source.py`) | `src/theory/SPEC.md`, `src/jokes/historico/SPEC.md` |
| `document_store.py` | Captura durable de un documento: objeto en el bucket privado + fila Bronze/Silver append-only (ver §DocumentStore) | Flujo A (tasks 59/61) **y** Flujo C (tasks 64/65), más el backfill legacy (task 66) | `src/theory/SPEC.md`, `src/jokes/historico/SPEC.md` |
| `llm/client.py` | Cliente LLM vía API (modelo barato) | Flujos B/C (Silver, Taxonomías) — **teoría NO usa LLM** | `src/jokes/SPEC.md`, [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md) |
| `llm/embeddings.py` | Cliente de embeddings | Flujos B/C (Reconciliación, retrieval RAG) | `src/jokes/SPEC.md` |

**Regla de dependencias:** `theory/` y `jokes/` no se importan entre sí; lo que
necesitan ambos vive aquí. `language_detector`/`quality_scorer` son consumo
exclusivo de teoría y `llm/*` es consumo exclusivo de chistes — para esos, la
carpeta es compartida en ubicación, no en uso. `drive_sync.py` (P23,
2026-07-27) es el **primer módulo de `utils/` con consumo real en los dos
lados**: teoría y el histórico leen ambos de carpetas de Drive distintas con
el mismo mecanismo, y es justo el caso que esta regla previene resolver
copiando código de un flujo al otro. `document_store.py` (P25, 2026-07-28) es
el segundo, por el mismo motivo: subir un documento a su bucket e insertar su
fila Bronze/Silver es idéntico en los dos flujos.

## DriveSync — núcleo compartido de sincronización con Drive (P23, 2026-07-27)

`src/utils/drive_sync.py` (implementación en la **task 43** — esta sección fija
su contrato) generaliza el `DriveSource` del Flujo C (task 30, P19) a un núcleo
**parametrizable por MIMEs**, para que el Flujo A pueda reutilizarlo sin
reimplementar auth, listado paginado, idempotencia ni staging.

Lo que era específico del histórico y aquí se parametriza es **solo una cosa**:
qué MIMEs se aceptan y a qué formato aterriza cada uno en local. Todo lo demás
(auth por cuenta de servicio, listado paginado, idempotencia por `fileId` +
`modifiedTime`, staging reconstruible, solo lectura) se conserva **tal cual**
está hoy en `src/jokes/historico/drive_source.py`: no se rediseña nada.

### Firma

```python
EXTENSION_POR_MIME: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/pdf": ".pdf",
    "application/epub+zip": ".epub",
    "text/plain": ".txt",
}
"""MIME de SALIDA -> extensión del fichero local. Ampliable por consumidor vía
`extension_por_mime` sin tocar este módulo."""

class DriveSync:
    def __init__(
        self,
        folder_id: str,                          # ID de la carpeta de Drive a sincronizar
        staging_dir: Path,                       # dir local de descarga (caché, NO sagrado)
        state_path: Path,                        # JSON de idempotencia (metadata de Drive)
        mimes_aceptados: dict[str, Optional[str]],  # mime_origen -> mime_export (None = get_media)
        credentials_path: Optional[Path] = None, # service account; por defecto GOOGLE_APPLICATION_CREDENTIALS
        service=None,                            # cliente de Drive inyectable (tests sin red)
        extension_por_mime: Optional[dict[str, str]] = None,  # amplía/sobrescribe EXTENSION_POR_MIME
    ): ...

    def sync(self) -> list[Path]:
        """Lista la carpeta `folder_id`, descarga a `staging_dir` SOLO los
        ficheros nuevos/modificados (idempotencia por `fileId` +
        `modifiedTime`) y devuelve sus paths locales. Nunca modifica nada en
        Drive (solo lectura)."""

    def sync_con_metadata(self) -> list[ArchivoSincronizado]:
        """Igual que `sync()`, pero devuelve además la metadata de Drive de
        cada fichero (ver §"Ampliación: metadata por fichero", task 57)."""
```

### Por qué `mimes_aceptados` es un dict y no una lista + función de export

La alternativa era `mimes_aceptados: list[str]` + un *callback* de export
(`exportar_a: Callable[[str], Optional[str]]`). Se elige el **dict** porque:

- **Las claves se usan dos veces y deben ser las mismas:** el conjunto de MIMEs
  aceptados se inyecta literalmente en la query de `files().list` (`mimeType =
  '…' or …`) y también decide qué se hace con cada fichero devuelto. Con un dict
  no hay forma de que las dos listas se desincronicen; con lista + callback, sí
  (un MIME en la lista sin rama en el callback).
- **La decisión es un valor, no un comportamiento.** Lo único que varía por
  fichero es "descarga directa" vs "exporta a este MIME": un `Optional[str]`
  por clave lo expresa entero. Un callback compraría poder arbitrario (lógica
  por nombre de fichero, por tamaño…) que ningún consumidor necesita, a cambio
  de un contrato imposible de leer de un vistazo y de testear por separado.
- **Es declarativo e inspeccionable:** la configuración de cada flujo queda como
  una constante de módulo (`MIMES_HISTORICO`, `MIMES_TEORIA`) que se lee, se
  compara y se testea sin instanciar nada.

Extender = añadir una entrada al dict (y, si el MIME de salida es nuevo, su
extensión en `EXTENSION_POR_MIME` o en `extension_por_mime`). No hay que tocar
`DriveSync` para soportar un formato más.

### Comportamiento (heredado de `DriveSource`, sin cambios)

- **Listado:** query
  `'{folder_id}' in parents and trashed = false and (mimeType = 'A' or mimeType = 'B' …)`,
  con los MIMEs en el **orden de inserción** de `mimes_aceptados` (así el
  histórico obtiene una query byte a byte idéntica a la de hoy). Campos
  `"nextPageToken, files(id, name, mimeType, modifiedTime)"`, paginado con
  `nextPageToken`, resultado ordenado por `name` (orden determinista).
  No recursivo: `in parents` no entra en subcarpetas.
- **Descarga:** `mimes_aceptados[mime] is None` → `files().get_media(fileId=…)`;
  si tiene valor → `files().export(fileId=…, mimeType=<valor>)` (tipos nativos
  de Google, que no se pueden descargar en crudo).
- **Nombrado local:** `mime_salida = mimes_aceptados[mime_origen] or mime_origen`;
  la extensión sale de `EXTENSION_POR_MIME[mime_salida]` (más
  `extension_por_mime` si el consumidor lo pasa). Si el nombre en Drive **ya
  termina** en esa extensión (comparación *case-insensitive*) se respeta tal
  cual; si no, se le añade. Un Google Doc nativo llamado `Notas.txt` exportado a
  `.docx` aterriza como `Notas.txt.docx`: es **deliberado** — el nombre local
  tiene que terminar en la extensión de su contenido REAL, porque aguas abajo
  hay consumidores que enrutan por extensión (`src/theory/pipeline.py` elige
  Parser y deriva `tipo_fuente` de la extensión).
- **Validación de configuración (fail fast, en `__init__`):** si algún MIME de
  salida efectivo (`export or origen`) no tiene extensión conocida, `ValueError`
  inmediato. No se valida nada más en construcción — en particular, la ausencia
  de credenciales **debe seguir fallando en `sync()`** (`RuntimeError`), no en
  el constructor.
- **Idempotencia:** estado JSON `{fileId: {"name": …, "modifiedTime": …}}` en
  `state_path`. Se descarga si el `fileId` es nuevo o su `modifiedTime` difiere
  del registrado. **Formato de estado sin cambios** respecto al de hoy: los
  `data/state/*_drive.json` existentes siguen siendo válidos tras la extracción,
  no hay migración.
- **Staging:** `staging_dir` es **caché local reconstruible, NO material
  sagrado**. Lo sagrado es el original en Drive. Borrar `state_path` fuerza
  re-descarga completa en el siguiente `sync()` (el estado es una optimización,
  no fuente de verdad).
- **Auth desatendida:** cuenta de servicio (`credentials_path` o
  `GOOGLE_APPLICATION_CREDENTIALS`), scope `https://www.googleapis.com/auth/drive.readonly`,
  cliente construido **perezosamente** en el primer uso. Nunca OAuth interactivo
  (no hay navegador en un runner de CI). `service` inyectable → los tests no
  tocan la red.
- **Solo lectura:** únicamente `files().list`, `files().get_media` y
  `files().export`. Nunca `create`/`update`/`delete`.
- **Colisión de nombres:** dos ficheros de Drive cuyo nombre local resuelto
  coincide (p.ej. `a.docx` subido y un Google Doc llamado `a`) se pisan en
  `staging_dir` — limitación conocida y heredada, igual que hoy. La clave de
  idempotencia sigue siendo el `fileId`, así que el estado es correcto; lo que
  se pierde es un fichero en el staging. Mitigación: nombres distintos en Drive.

### Restricción dura para la task 43 (extracción sin cambio de comportamiento)

`src/jokes/historico/drive_source.py` pasa a **delegar** en `DriveSync` —
recomendado como subclase fina que fija
`mimes_aceptados={MIME_DOCX: None, MIME_GDOC: MIME_DOCX}` y llama a
`super().__init__(...)`. Mismo patrón que la task 34 con `src/jokes/routing.py`:
es una extracción, no un rediseño.

**`tests/unit/jokes/historico/test_drive_source.py` (aprobado en la task 30)
tiene que quedar en verde SIN modificarse.** De ahí salen estas invariantes,
que la task 43 no puede romper:

1. `DriveSource`, `MIME_DOCX` y `MIME_GDOC` siguen siendo importables desde
   `src.jokes.historico.drive_source` (re-exportarlos desde `utils` vale; que
   desaparezcan del namespace, no).
2. Constructor con los mismos nombres de parámetro y el mismo orden posicional:
   `(folder_id, staging_dir, state_path, credentials_path=None, service=None)`.
   `mimes_aceptados` **no** se le añade a `DriveSource` — lo fija él.
3. `files().list` se invoca **solo** con `q=`, `fields=` y `pageToken=`. El
   doble de prueba tiene exactamente esa firma: añadir `pageSize=`,
   `supportsAllDrives=` o cualquier otro kwarg lo rompe.
4. `files().get_media(fileId=…)` y `files().export(fileId=…, mimeType=…)` con
   esos nombres de kwarg.
5. La query contiene `'<folder_id>' in parents`, `trashed = false` y ambos MIMEs.
6. Sin `service` ni credenciales, `sync()` lanza `RuntimeError` sin tocar la red;
   el constructor **no** lanza.
7. `state_path` inexistente en el primer run: no falla, se crea.

La task 43 se da por buena cuando `pytest tests/unit/ -v` pasa con ese fichero
intacto y con los tests nuevos del núcleo compartido.

### Ampliación: metadata por fichero (P25, 2026-07-28 — task 57)

[P25](../../docs/specs/00-overview.md) convierte la capa Bronze en Supabase en
la garantía de durabilidad del material original, con clave de idempotencia
**`(drive_file_id, modified_time)`** (ver §DocumentStore abajo y
[`src/jokes/SPEC.md`](../jokes/SPEC.md) §Storage para el patrón de acceso). Esa
clave la conoce `DriveSync` y **hoy la tira a la basura**: `sync()` devuelve
solo `list[Path]`, así que un consumidor que quiera capturar el fichero en
Bronze no tiene de dónde sacar el `fileId` ni el `modifiedTime`.

**Esto es propagación, no una llamada nueva a la API.** `_listar_archivos()` ya
pide `fields="nextPageToken, files(id, name, mimeType, modifiedTime)"` y `sync()`
ya usa `archivo["id"]`/`archivo["modifiedTime"]` para decidir si descarga y para
escribir el estado JSON. Los cuatro campos están en memoria en el mismo bucle
que produce el `Path` que se devuelve. La ampliación **no** toca auth, listado,
paginado, query, idempotencia, staging ni el formato del estado: solo deja de
descartar lo que ya tenía.

#### Contrato

```python
@dataclass(frozen=True)
class ArchivoSincronizado:
    path: Path          # destino local en staging_dir (lo que hoy devuelve sync())
    file_id: str        # `id` de Drive        -> Bronze.drive_file_id
    name: str           # `name` de Drive (nombre lógico, SIN la extensión añadida por el nombrado local)
    modified_time: str  # `modifiedTime` RFC 3339 tal cual lo da la API, sin reparsear
    mime_type: str      # `mimeType` de ORIGEN en Drive
    mime_salida: str    # MIME real del contenido en `path` (= mimes_aceptados[mime_type] or mime_type)
```

- `sync() -> list[Path]` **no cambia de firma ni de comportamiento**. Pasa a ser
  un envoltorio de una línea: `return [a.path for a in self.sync_con_metadata()]`.
- `sync_con_metadata() -> list[ArchivoSincronizado]` es el método **nuevo** y el
  que lleva la implementación (el bucle de descarga vive ahí, una sola vez).
- Las subclases (`DriveSource`, `DriveSyncTeoria`) heredan las dos sin tocarse:
  solo sobrescriben `__init__`. Las tasks 59/64 consumen `sync_con_metadata()`
  directamente sobre ellas.

#### Por qué método nuevo y no "que `sync()` devuelva el objeto rico"

La alternativa evaluada era que `sync()` devolviese `list[ArchivoSincronizado]`
con un tipo que se comportase como `Path` allí donde ya se usa como tal (p.ej.
subclase de `pathlib.Path`, viable en 3.12+). Se descarta con evidencia del
código real, no por gusto:

- **Los tests comparan la lista entera por igualdad contra `Path`**, no por
  atributos: `assert resultado == [staging_dir / "chiste1.docx"]` aparece así en
  los tres ficheros aprobados —
  `tests/unit/utils/test_drive_sync.py` (líneas 166, 184, 206, 225, 241, 260),
  `tests/unit/theory/test_drive_sync.py` (170, 183, 197, 212, 230) y
  `tests/unit/jokes/historico/test_drive_source.py` (101, 119, 159). Que esa
  igualdad siga siendo cierta con una subclase de `Path` depende de detalles
  internos de `PurePath.__eq__`/`__hash__` de CPython: funcionaría hoy y sería
  una bomba de relojería en cada actualización de intérprete. El contrato de
  regresión de la task 43 (invariantes 1-7 arriba) se defendió a base de dejar
  esos ficheros **intactos**; no se va a poner esa garantía a depender de una
  sutileza del runtime.
- **Los consumidores tratan el resultado como `Path` de verdad**, no como algo
  opaco: `src/jokes/historico/pipeline.py:399-401` hace `Path(p).name` sobre cada
  elemento y se lo pasa a `procesar_docx_fn(docx, carpeta_md)` (que acaba en
  `python-docx`); `scripts/run_historico.py:272` igual. `src/theory/pipeline.py`
  ni siquiera usa el valor de retorno: llama a `drive_sync.sync()` por su efecto
  (poblar el staging) y luego escanea `drive_sync.staging_dir` con `DriveMonitor`.
- **El contrato duck-typed está escrito en tres sitios como `list[Path]`**
  (`src/theory/pipeline.py:18` y `:286`, `src/jokes/historico/pipeline.py:353`)
  y hay dobles de prueba que lo implementan
  (`tests/unit/theory/test_pipeline.py:344`). Cambiar el tipo de retorno obliga a
  revisar los dobles; añadir un método no obliga a nada.

Resumen: el coste de un método nuevo es una línea de envoltorio; el de cambiar
el tipo de retorno es un contrato de regresión ya defendido, tres consumidores y
un doble de prueba. Se elige el método nuevo.

#### Detalles que el implementer de la task 57 no puede improvisar

- **`modified_time` viaja como el `str` RFC 3339 que da Drive**
  (`"2026-07-28T10:15:00.000Z"`), sin convertir a `datetime` ni normalizar. Es la
  misma cadena que ya se guarda en el estado JSON y la misma que va a la columna
  `modified_time` de Bronze: si `DriveSync` la reformatease, el estado de
  idempotencia local y la clave de Bronze podrían dejar de coincidir carácter a
  carácter para el mismo fichero. La conversión a `timestamptz` la hace Postgres
  al insertar, no Python.
- **`name` es el nombre en Drive, no el nombre local.** El nombre local ya está
  en `path.name` y puede llevar una extensión añadida (`Notas.txt` →
  `Notas.txt.docx`, ver §"Nombrado local"). Los dos hacen falta: el lógico para
  la fila, el local para leer los bytes.
- **`mime_salida` se incluye aunque P25 solo pida cuatro campos** porque es lo
  que describe el contenido que realmente hay en `path` (un Google Doc nativo
  aterriza como `.docx`: su `mime_type` de origen es
  `application/vnd.google-apps.document`, pero el objeto que se sube al bucket es
  un DOCX). Sin él, el consumidor tendría que releer `mimes_aceptados` desde
  fuera para saber el `content-type` de la subida — es decir, reimplementar una
  decisión que ya toma `DriveSync` en `_nombre_local()`. Un campo derivado es más
  barato que una regla duplicada.
- **`sync()` y `sync_con_metadata()` no se llaman las dos en la misma corrida.**
  Ambas consumen y reescriben el mismo estado de idempotencia, así que la segunda
  llamada devuelve `[]` (los ficheros ya no están "pendientes"). No es un defecto
  nuevo — es la misma semántica que ya tiene llamar dos veces a `sync()`, y de la
  que `scripts/run_historico.py` ya se defiende explícitamente (§"el segundo
  `.sync()` no los vuelve a stagear", líneas 36/63/72) — pero conviene dejarlo
  escrito ahora que hay dos puertas a la misma habitación.
- **Los tests nuevos de la task 57 son del método nuevo.** Los tres ficheros de
  regresión citados arriba siguen en verde **sin modificarse**; si alguno hay que
  tocarlo, la ampliación se ha desviado a un cambio de comportamiento y hay que
  replantearla.

## DocumentStore — captura durable de documentos (P25, 2026-07-28)

`src/utils/document_store.py` (implementación en la **task 58** — esta sección
fija su contrato) es el componente que materializa la regla de `CLAUDE.md`
"el material original es sagrado **y quien lo garantiza es la capa Bronze en
Supabase**" para los documentos de los Flujos A y C: sube el fichero a su
**bucket privado** e inserta la **fila Bronze/Silver append-only** que lo
indexa.

Vive en `utils/` por el mismo motivo que `drive_sync.py`: la operación es
idéntica en teoría y en el histórico, y la [regla de dependencias](../../CLAUDE.md)
prohíbe resolverla copiando código de un flujo al otro o importando `theory/`
desde `jokes/`. Cada flujo lo consume por su lado (tasks 59/61 y 64/65) y el
backfill legacy también (task 66).

**Relación con las otras specs (referencia, no duplicación):** el mapeo global
de capas a schemas y la decisión de los cuatro buckets los fija
[P25](../../docs/specs/00-overview.md); el patrón de acceso por schema
(`client.schema("bronze").table(...)`), los GRANTs y los "exposed schemas"
—paso **manual**, no código— los fija
[`src/jokes/SPEC.md`](../jokes/SPEC.md) §Storage y aplican igual aquí; el DDL de
las cuatro tablas de documentos es de la task 53, y qué columnas propias añade
cada flujo, de las tasks 51 (teoría) y 52 (histórico). Esta sección solo fija
**el contrato del componente que escribe**.

### Destinos: cuatro, fijos, en una tabla explícita

La combinación (capa × flujo) determina bucket **y** tabla:

| Capa | Flujo | Bucket (Storage) | Tabla (Postgres) | Contenido |
|---|---|---|---|---|
| `bronze` | `teoria` | `bronze-teoria` | `bronze.teoria_documentos` | PDF/EPUB/DOCX/TXT crudo (libros, apuntes, transcripciones WhisperX) |
| `silver` | `teoria` | `silver-teoria` | `silver.teoria_documentos` | `.md` limpio/traducido/normalizado |
| `bronze` | `historico` | `bronze-historico` | `bronze.historico_documentos` | `.docx` original **con su color de fuente** |
| `silver` | `historico` | `silver-historico` | `silver.historico_documentos` | `.md` marcado `[REMATE]`/`[CHISTOIDE]` |

Nombres ya cerrados en P25: **no se redeciden aquí**. La correspondencia se
escribe como **constante de módulo** (un `dict[(capa, flujo)] -> Destino`), no
se deriva con manipulación de cadenas: el bucket usa guión (`bronze-teoria`) y
el schema/tabla guión bajo (`bronze.teoria_documentos`), y un `capa + "-" +
flujo` funcionaría hasta el día en que alguien renombre uno de los dos lados y
el código empiece a escribir en un bucket inexistente sin que nada lo detecte
en revisión. Una capa o un flujo desconocidos son `DocumentStoreError`
inmediato (fail fast), no un bucket inventado sobre la marcha.

### Interfaz pública

```python
# src/utils/document_store.py

CAPAS = ("bronze", "silver")
FLUJOS = ("teoria", "historico")
ORIGENES = ("drive", "local_legacy")

@dataclass(frozen=True)
class Destino:
    bucket: str
    schema: str
    tabla: str

DESTINOS: dict[tuple[str, str], Destino]   # (capa, flujo) -> Destino, la tabla de arriba

@dataclass(frozen=True)
class ResultadoCaptura:
    destino: Destino
    object_path: str      # clave del objeto dentro del bucket
    hash_md5: str
    origen: str           # 'drive' | 'local_legacy'
    ya_existia: bool      # True = idempotencia: no se subió ni se insertó nada
    fila_id: Optional[str]  # id de la fila (nueva o preexistente), si la tabla lo devuelve

class DocumentStoreError(RuntimeError):
    """Config ausente, destino desconocido, argumentos incoherentes o fallo de captura."""

def crear_cliente(): ...    # cliente supabase-py real desde SUPABASE_URL/SUPABASE_SERVICE_KEY

class DocumentStore:
    def __init__(self, client=None): ...   # inyectable; si se omite, crear_cliente()

    def capturar(
        self,
        ruta: Path,                            # fichero local cuyos bytes se capturan
        capa: str,                             # 'bronze' | 'silver'
        flujo: str,                            # 'teoria' | 'historico'
        drive_file_id: Optional[str] = None,   # None => modo legacy
        modified_time: Optional[str] = None,   # RFC 3339 tal cual de Drive
        nombre: Optional[str] = None,          # nombre lógico; por defecto ruta.name
        mime_type: Optional[str] = None,       # content-type del objeto
        extra: Optional[dict] = None,          # columnas propias del flujo (tasks 51/52)
    ) -> ResultadoCaptura: ...
```

- **Cliente inyectable**, mismo patrón que `SupabaseStore` (`src/jokes/supabase_store.py`)
  y que el `service` de `DriveSync`: en producción no se pasa y se construye uno
  real; en tests se inyecta un doble con la interfaz de `supabase-py`
  (`.schema().table().insert/select/eq/is_/execute` y `.storage.from_().upload()`)
  y **ni un test unitario toca la red**.
- **`crear_cliente()` se duplica aquí a propósito.** `src/jokes/supabase_store.py`
  ya tiene una función equivalente (~10 líneas: leer `SUPABASE_URL`/
  `SUPABASE_SERVICE_KEY`, error explícito si faltan, `create_client`), pero
  importarla desde `utils/` invertiría el orden de capas — `utils/` pasaría a
  depender de `jokes/`, y por transitividad `theory/` acabaría importando de
  `jokes/`, que es exactamente lo que la regla de dependencias impide. Mover la
  función a `utils/` y re-exportarla desde `supabase_store.py` sería lo limpio,
  pero es refactorizar un módulo con tráfico de producción (Flujo B) a cambio de
  cero funcionalidad y fuera del scope de la task 58. Si aparece un tercer
  consumidor, ese es el momento de consolidarla.
- **`origen` NO es un parámetro:** se deriva de `drive_file_id`
  (`'drive'` si viene, `'local_legacy'` si es `None`). Dos fuentes de verdad
  para el mismo hecho es como se acaba con una fila `origen='drive'` y
  `drive_file_id NULL`, que no cae bajo ninguno de los dos índices únicos
  parciales de P25 y por tanto se puede duplicar sin límite.

### Validación de argumentos (fail fast, antes de tocar red)

| Condición | Resultado |
|---|---|
| `(capa, flujo)` no está en `DESTINOS` | `DocumentStoreError` |
| `ruta` no existe o no es fichero | `DocumentStoreError` |
| `drive_file_id` presente y `modified_time` ausente | `DocumentStoreError` — la clave de idempotencia es el **par**; medio par no identifica una versión |
| `drive_file_id` ausente y `modified_time` presente | `DocumentStoreError` — un `modified_time` sin `fileId` es metadata de Drive de un fichero que no viene de Drive |
| `extra` intenta escribir una columna que gestiona el propio componente | `DocumentStoreError` |

La última merece justificación: `extra` existe porque las columnas propias de
cada flujo (p.ej. el `tipo_fuente` de teoría) las fijan las tasks 51/52 y este
componente no las conoce ni debe conocerlas — se pasan tal cual al `INSERT`.
Pero un `extra` sin límites permitiría a un consumidor colar su propio
`hash_md5` o `origen` y romper desde fuera las invariantes que esta sección
garantiza. El componente es dueño de `bucket`, `object_path`, `drive_file_id`,
`modified_time`, `hash_md5`, `origen`, `nombre`, `mime_type` y `tamano_bytes`;
`extra` es para todo lo demás.

### Naming del objeto dentro del bucket

```
drive/{drive_file_id}/{modified_time_compacto}/{nombre_saneado}
local_legacy/{hash_md5}/{nombre_saneado}
```

`modified_time_compacto` = el RFC 3339 de Drive en forma básica sin separadores
(`2026-07-28T10:15:00.000Z` → `20260728T101500Z`); `nombre_saneado` = el nombre
lógico con todo lo que no sea `[A-Za-z0-9._-]` sustituido por `_`.

Ejemplos:
`drive/1AbC…xyz/20260728T101500Z/Curso_de_Demy.docx`,
`local_legacy/9f2a…/Bases_de_la_comedia.pdf`.

Por qué así:

- **La clave del objeto ES la clave de idempotencia de la fila.** Los dos
  primeros segmentos son literalmente `(drive_file_id, modified_time)` (o el
  `hash_md5` en legacy), así que "una fila = un objeto" es una propiedad
  estructural inspeccionable en el dashboard, no una convención que haya que
  creerse. Y el **append-only sale gratis**: una edición en Drive cambia
  `modified_time`, luego cambia el prefijo, luego el objeto nuevo **no puede**
  aterrizar encima del anterior. Es justo lo contrario de lo que hace hoy
  `staging_dir`, que resuelve el destino por nombre y sobrescribe (evidencia en
  P25).
- **Es determinista.** Un reintento tras un fallo parcial recalcula exactamente
  la misma clave y reescribe sus propios bytes, en vez de dejar un segundo
  objeto huérfano. Un path con UUID o timestamp de captura no tendría esa
  propiedad: cada reintento pagaría cuota de Storage y dejaría basura
  indistinguible del objeto bueno.
- **No colisiona entre ficheros distintos.** `drive_file_id` es único en Drive,
  así que dos ficheros con el mismo nombre (la [colisión conocida de
  `staging_dir`](#comportamiento-heredado-de-drivesource-sin-cambios)) aterrizan
  en prefijos distintos: **el bucket no hereda esa limitación**. En legacy el
  prefijo es el hash del contenido: dos ficheros con nombres que el saneado
  vuelve iguales tienen contenidos distintos → hashes distintos → prefijos
  distintos; y si el contenido es el mismo, es el mismo documento y la
  idempotencia lo salta antes de subirlo.
- **El nombre va de hoja y conserva la extensión.** Un humano que abra el bucket
  ve `Curso_de_Demy.docx` y no una cadena opaca; ninguna lógica del pipeline
  enruta por esta cadena (para eso está la fila), pero la inspección manual es
  el modo en que se audita una capa que por diseño nadie borra nunca.

### Comportamiento de `capturar()`

1. **Valida** los argumentos (tabla de arriba) y resuelve el `Destino`.
2. **Calcula `hash_md5`** del contenido de `ruta` — **siempre**, no solo en
   legacy. En legacy es la clave de idempotencia; en modo Drive es la
   verificación de integridad objeto ↔ fila que necesita la task 62 (el sustituto
   del check 8 "manifest sincronizado" de `validate_corpus.py`). El fichero se
   lee una vez de todos modos para subirlo.
3. **Comprueba idempotencia** con un `SELECT` sobre la tabla destino:
   por `(drive_file_id, modified_time)` en modo Drive, por `hash_md5` con
   `drive_file_id IS NULL` en legacy. Si ya hay fila →
   `ResultadoCaptura(ya_existia=True)` y **retorno inmediato: no sube el objeto y
   no inserta nada**. Este es el caso común (la task 59 corre en cada ejecución
   del pipeline de teoría), y saltarse la subida es lo que evita pagar cuota de
   Storage por cada re-ejecución.
4. **Sube el objeto** al bucket, en la clave calculada arriba, con su
   `content-type`.
5. **Inserta la fila** con las columnas propias + `extra`.
6. Devuelve `ResultadoCaptura(ya_existia=False, ...)`.

**Orden: primero el objeto, después la fila.** Es una decisión, y el huérfano
que se elige tolerar es el **objeto sin fila**:

- Una fila sin objeto es una **mentira indexada**: nada la distingue de una fila
  buena, y el fallo aparece mucho más tarde y en otro sitio (un lector de Silver
  se come un 404 al descargar; la verificación fila ↔ objeto de la task 62 falla
  sin saber por qué). Un objeto sin fila es **invisible**: cuesta unos MB de
  cuota y nada más, porque ningún consumidor lee el bucket listándolo — todos
  entran por la tabla.
- **El reintento lo cura solo**, precisamente porque la clave es determinista: la
  siguiente pasada no encuentra fila (paso 3), reescribe los mismos bytes en la
  misma clave y ahora sí inserta. Resultado final: un objeto, una fila. Con el
  orden inverso, el reintento encontraría la fila, se daría por idempotente y el
  objeto no existiría **nunca**.
- **Hay precedente en este repo:** el Bronze del Flujo B es "guarda primero,
  procesa después" (P22) — la captura durable ocurre antes que cualquier cosa que
  pueda fallar, y el bookkeeping (`procesado_at`, tasks 46/47) viene detrás. Aquí
  la forma es la misma: bytes primero, metadata después.
- **Escribir en la clave del objeto no viola el append-only.** El append-only es
  sobre **versiones**, y la versión está codificada en la clave: reescribir la
  misma clave con los mismos bytes es el reintento, no una versión nueva. Lo que
  está prohibido —y esta convención de naming hace imposible— es que una versión
  nueva pise a la anterior.

**Carrera entre dos capturas simultáneas del mismo fichero:** el `SELECT` del
paso 3 y el `INSERT` del paso 5 no son atómicos, así que dos procesos pueden
verse ambos "sin fila" e intentar insertar. La garantía real son los **dos
índices únicos parciales** de P25 (`(drive_file_id, modified_time) WHERE
drive_file_id IS NOT NULL` y `(hash_md5) WHERE drive_file_id IS NULL`, DDL en la
task 53): el perdedor recibe una violación de unicidad. **Contrato:
`DocumentStore` trata esa violación como `ya_existia=True`, no como error** — el
resultado observable es el correcto (una fila, un objeto) y elevarla convertiría
una carrera benigna en un fallo de pipeline. La comprobación del paso 3 es una
optimización para no subir el objeto; la fuente de verdad de la unicidad es la
base de datos.

### Silver también entra por aquí — y su límite conocido

Las tablas Silver de documentos se escriben con el **mismo** `capturar()`
(`capa="silver"`), y la fila Silver lleva el **mismo** `(drive_file_id,
modified_time)` que la versión Bronze de la que deriva: así el par identifica una
versión del documento a lo largo de las dos capas y el linaje es una unión
directa, sin columna de enlace inventada aquí.

Consecuencia que el implementer debe conocer: **regenerar Silver sin que cambie
el original es un no-op**. Si se cambia el prompt de limpieza o el normalizador y
se vuelve a capturar el mismo `(drive_file_id, modified_time)`, el paso 3
encuentra fila y no escribe nada — el `.md` nuevo no llega al bucket. Es
coherente con el modelo (la clave identifica la versión del **original**, no la
del proceso), pero significa que "versionar el resultado de la transformación"
**no está resuelto por este componente**. Quién lo resuelve y cómo (columna de
versión de proceso, clave compuesta distinta para Silver, o regeneración
explícita) es de la **task 51**, que es la dueña del linaje Bronze→Silver de
teoría. Queda escrito aquí para que no se descubra por sorpresa en la task 61.

### Qué NO hace `DocumentStore`

Delimitación explícita para el implementer de la task 58 — todo lo de esta lista
está fuera de scope y ya tiene dueño:

- **No autentica contra Drive, no lista carpetas, no descarga nada de Drive.**
  Eso es `drive_sync.py` (§DriveSync), y sigue siéndolo.
- **No decide si algo hay que sincronizar.** Esa idempotencia —"¿este fichero
  cambió en Drive desde la última pasada?"— la resuelve `DriveSync` con su estado
  JSON **antes** de llamar aquí. `DocumentStore` recibe ficheros que ya se han
  declarado capturables y solo responde a otra pregunta distinta: "¿esta versión
  ya está guardada en Supabase?". Son dos idempotencias con dos estados
  distintos (un JSON local reconstruible vs. una tabla en Supabase) y **no se
  sustituyen la una a la otra**: borrar el JSON fuerza re-descarga pero no
  duplica Bronze, porque el paso 3 lo detiene.
- **No define el DDL de las cuatro tablas** (task 53) ni las columnas propias de
  cada flujo (tasks 51/52). Asume que existen, como `supabase_store.py` asume
  `schema.sql`.
- **No crea buckets.** Los cuatro se crean **a mano** en el dashboard, mismo
  estatus que aplicar `schema.sql` o los "exposed schemas"/GRANTs de
  [`src/jokes/SPEC.md`](../jokes/SPEC.md) §Storage (la API con
  `SUPABASE_SERVICE_KEY` podría crearlos, pero infraestructura creada de
  refilón por un pipeline es infraestructura que nadie ha revisado — y estos
  buckets **tienen que ser privados**: el corpus lleva material `personal_only`,
  ver [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md)). Si el bucket
  no existe, `DocumentStoreError` con mensaje explícito, no creación silenciosa.
- **No hace `UPDATE` ni `DELETE` de filas, ni borra objetos.** No expone ningún
  método que lo permita. La capa es append-only y el componente no ofrece la
  herramienta para violarla.
- **No parsea, limpia, traduce ni chunkea.** Recibe bytes y los guarda.
- **No gestiona RLS ni políticas de acceso** (manual, como los GRANTs).
- **No lee `SUPABASE_*` si se le inyecta `client`** — un test unitario no
  necesita `.env`.

### Tests de la task 58 (sin red, con fixtures reales)

`DocumentStore` es una clase fina sobre funciones puras, igual que
`supabase_store.py`: la resolución del `Destino`, la construcción del
`object_path`, el saneado del nombre, la derivación de `origen`, el cálculo del
`hash_md5` y la validación de argumentos son **funciones de módulo sin red**, y
ahí es donde vive la cobertura unitaria. La clase se testea con un doble
inyectado que registre qué bucket, qué clave, qué schema, qué tabla y qué
payload recibió. Los ficheros de entrada salen de `/tests/fixtures/` (nunca
inventados), y hay que cubrir explícitamente: modo Drive, modo legacy, segunda
captura idéntica (`ya_existia=True`, **sin** subida), `modified_time` distinto
(fila y objeto nuevos, el anterior intacto) y violación de unicidad en el INSERT
tratada como `ya_existia=True`.
