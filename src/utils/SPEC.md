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
| `llm/client.py` | Cliente LLM vía API (modelo barato) | Flujos B/C (Silver, Taxonomías) — **teoría NO usa LLM** | `src/jokes/SPEC.md`, [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md) |
| `llm/embeddings.py` | Cliente de embeddings | Flujos B/C (Reconciliación, retrieval RAG) | `src/jokes/SPEC.md` |

**Regla de dependencias:** `theory/` y `jokes/` no se importan entre sí; lo que
necesitan ambos vive aquí. `language_detector`/`quality_scorer` son consumo
exclusivo de teoría y `llm/*` es consumo exclusivo de chistes — para esos, la
carpeta es compartida en ubicación, no en uso. `drive_sync.py` (P23,
2026-07-27) es el **primer módulo de `utils/` con consumo real en los dos
lados**: teoría y el histórico leen ambos de carpetas de Drive distintas con
el mismo mecanismo, y es justo el caso que esta regla previene resolver
copiando código de un flujo al otro.

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
