# Flujo C — Chistes históricos (batch retroactivo)

> Spec de `src/jokes/historico/` (y de `scripts/marcar_remates.py`, que
> alimenta este flujo aunque viva físicamente en `scripts/`). Para Silver,
> Reconciliación, Taxonomías y el esquema de Supabase (compartidos con el
> Flujo B), ver [`src/jokes/SPEC.md`](../SPEC.md) — no se duplican aquí.
> Contexto general en
> [`docs/specs/00-overview.md`](../../../docs/specs/00-overview.md) y política
> LLM/coste en [`docs/specs/llm-policy.md`](../../../docs/specs/llm-policy.md).

Procesado retroactivo de textos propios ya escritos, con varios chistes por
documento. Reutiliza el Silver y la Reconciliación del Flujo B (ver
`src/jokes/SPEC.md`); lo que sigue es **específico de Histórico**: la entrada
marcada por color y la segmentación.

## Preprocesado de marcado (`scripts/marcar_remates.py`)

Script **automático y determinista**, previo y desacoplado del pipeline.
El color ya existe en el documento fuente, así que la marcación se deriva de
él sin intervención humana (P15, 2026-07-06).

- **Motivo:** Markdown plano no conserva el color de texto. Por eso NO se
  parte de un `.md` ya convertido; se lee el documento **original con
  estilos** (`.docx` / Google Docs). El color de fuente vive a nivel de *run*
  en el XML del `.docx`, lo que permite detectarlo de forma determinista.
- **Mapa color → etiqueta** (el rojo **no es 1:1 con remate**; hay dos rojos
  con significado distinto):

  | Color fuente        | Etiqueta                     | Semántica                                                        |
  |----------------------|--------------------------------|---------------------------------------------------------------------|
  | `#FF0000` rojo puro  | `[REMATE]…[/REMATE]`          | Remate principal — **cierra** el chiste (frontera, ver §Segmentador) |
  | `#980000` burdeos    | `[CHISTOIDE]…[/CHISTOIDE]`     | Mini-remate interno, menos fuerza — **NO** cierra el chiste         |

  Cualquier otro color = texto normal, sin etiquetar. La clasificación es
  **por tono con margen**, no por igualdad exacta de hex (el color puede
  variar un par de dígitos entre documentos).
- **Reglas de marcado:**
  - Runs contiguos del mismo color se **fusionan** en un único span.
  - Un span rojo que cruza párrafos = **un solo tramo** (el marcador se
    mantiene abierto entre párrafos).
  - Las dos etiquetas **no se solapan**: al cambiar de color se cierra una y
    se abre la otra.
  - Espacios y puntuación quedan **fuera** de las etiquetas.
- **Cobertura de parseo:** además de párrafos, recorrer **tablas, hyperlinks y
  listas** (runs que el iterador ingenuo de `python-docx` no devuelve).
- **Validación round-trip (obligatoria):** nº de caracteres de cada color en
  el `.docx` == nº de caracteres dentro de la etiqueta correspondiente en el
  `.md`. Un descuadre indica runs perdidos (típicamente en tablas o
  hyperlinks) y **debe fallar** el marcado.
- **Salida:** `.md` con marcadores embebidos, que alimenta este flujo.
- **No integrado** en la arquitectura de teoría ni en la orquestación: se
  mantiene como paso previo desacoplado. Al ser automático, no añade fricción
  manual.

> **Prototipo:** validado primero en Google Colab
> (`notebooks/marcar_remates_colab.ipynb`) sobre documentos reales del
> histórico antes de bajarlo a `scripts/` (SDD: spec → tests con fixtures
> reales → implementación).

## Entrada y etapas

**Fuente de los `.docx`:** una **carpeta de Google Drive real** (ver §Fuente de
entrada — carpeta Drive real). `drive_source.py` lista esa carpeta, descarga a
un *staging* local solo los `.docx` nuevos/modificados y entrega sus paths
locales a `marcar_remates.procesar_docx(...)` **sin tocar su firma**. El
resultado (`.md` marcados) es exactamente lo que consume el `Loader` de
siempre.

**Entrada del pipeline propiamente dicho:** `.md` ya marcados con
`[REMATE]…[/REMATE]` y `[CHISTOIDE]…[/CHISTOIDE]`, generados por
`marcar_remates.py`. El pipeline los trata como texto plano normal.

**Etapas:**
0. **DriveSource (`drive_source.py`):** sincroniza la carpeta de Drive real a un
   *staging* local y devuelve los `.docx` nuevos/modificados (ver §Fuente de
   entrada — carpeta Drive real). Idempotencia por **metadata de Drive**
   (`fileId` + `modifiedTime`), independiente de la del Loader.
1. **marcar_remates (`scripts/marcar_remates.py`):** por cada `.docx` staged,
   `procesar_docx(ruta_docx, carpeta_salida)` produce el `.md` marcado. Firma
   **inalterada** (contrato aprobado en la task 17): DriveSource lo envuelve por
   fuera, entregándole un path local, nunca lo modifica por dentro.
2. **Loader (`loader.py`):** lee los `.md`. Idempotencia de documento por hash
   MD5 (ver §Idempotencia): un documento idéntico ya procesado se salta.
3. **Segmentador (`segmentador.py`):** `[REMATE]` = fin **determinista** de
   cada chiste; el LLM afina hacia atrás dónde empieza el setup por contenido
   semántico y descarta intros/transiciones que no son del chiste.
   `[CHISTOIDE]` **NO es frontera de chiste** (es un mini-remate interno que
   aligera una premisa larga): el Segmentador lo **ignora como fin** y lo
   **conserva como metadato de estructura** del chiste al que pertenece (útil
   para el Silver). Tratarlo como fin partiría chistes por la mitad.
   **Fuera del alcance de P16** (ver `docs/specs/llm-policy.md`): dónde
   "empieza de verdad" el setup no tiene criterio verificable externo (es
   juicio semántico) — este paso **no** lleva loop de reintento automático;
   su control de calidad es revisión humana muestral, no auto-convergencia.
4. **Silver:** mismo esquema que Telegram — ver `src/jokes/SPEC.md` §Silver.
5. **Reconciliación** → Supabase con `tipo_fuente='propio_historico'` — ver
   `src/jokes/SPEC.md` §Reconciliación.

**Re-ejecutable:** con el tiempo llegarán documentos nuevos que pueden traer
chistes iguales o cambiados. La reconciliación a nivel de chiste enruta cada
uno a IGUAL (dedup) / CAMBIADO (nueva revisión) / NUEVO. El hash de documento
evita reprocesar lo idéntico.

## Fuente de entrada — carpeta Drive real (P19, 2026-07-24)

A diferencia del Flujo A, cuya integración con Drive real está **diferida** y
apunta a carpetas locales (P18, ver `src/theory/SPEC.md` §DriveMonitor), el
Flujo C **sí lee de una carpeta de Google Drive real**. El motivo es
operativo: el histórico es material propio que sigue creciendo en Drive, y su
ejecución se disparará **semanalmente y desatendida** vía GitHub Actions
(task 31) — no hay nadie que copie `.docx` a mano a una carpeta local antes de
cada run, como sí ocurre hoy con los libros ya descargados de teoría.

La integración se encapsula en un componente nuevo, **`drive_source.py`**
(implementación en la task 30 — esta spec fija su contrato), que **envuelve por
fuera** la cadena ya aprobada sin tocar ninguna firma existente:

```
DriveSource.sync() → [.docx staged local] → marcar_remates.procesar_docx(ruta_docx, carpeta_salida)
   → [.md marcado] → Loader.load() → Segmentador → …
```

`marcar_remates.py` (task 17) y `loader.py` (task 18) **no cambian**:
`marcar_remates` sigue recibiendo un path local a un `.docx` y escribiendo un
`.md`; `Loader` sigue leyendo una carpeta de `.md`. Lo único que cambia es
**quién** deja los `.docx` en local: antes una persona a mano, ahora
`DriveSource`.

### Contrato de `drive_source.py`

Clase `DriveSource`, simétrica al `Loader` (parámetros inyectados, nunca
hardcodeados, para testear con `tmp_path` y credenciales/cliente *mockeados*):

```python
class DriveSource:
    def __init__(
        self,
        folder_id: str,          # ID de la carpeta de Drive del histórico
        staging_dir: Path,       # dir local donde se descargan los .docx
        state_path: Path,        # JSON de idempotencia de Drive (metadata)
        credentials_path: Path | None = None,  # service account; por defecto GOOGLE_APPLICATION_CREDENTIALS
    ): ...

    def sync(self) -> list[Path]:
        """Lista la carpeta de Drive, descarga a `staging_dir` SOLO los .docx
        nuevos/modificados (idempotencia por metadata, ver abajo) y devuelve
        la lista de paths locales de esos .docx nuevos/modificados —
        exactamente los que hay que volver a pasar por marcar_remates. Los
        .docx sin cambios desde el último run NO se descargan ni se devuelven.
        Nunca modifica los ficheros en Drive (material sagrado, solo lectura)."""
```

- **Qué lista:** los ficheros de tipo documento de la carpeta `folder_id`. Dos
  MIME distintos, ambos aterrizan como `.docx` en `staging_dir`:
  - `.docx` subidos
    (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`)
    → descarga directa (`files.get_media`).
  - Google Docs nativos (`application/vnd.google-apps.document`) → **export** a
    `.docx` (`files.export`, `mimeType=…wordprocessingml.document`). El export
    a `.docx` **conserva el color de fuente a nivel de run** (`w:rPr/w:color`),
    que es justo lo que `marcar_remates` necesita para el marcado por color; un
    export a `.md`/texto plano lo perdería (por eso se exporta a `.docx`, no a
    Markdown). Ficheros de otros MIME (hojas, PDFs, imágenes) se ignoran.
- **Idempotencia de Drive (capa propia):** estado persistido en `state_path`
  (JSON `{fileId: {"name": …, "modifiedTime": …}}`). Se descarga un fichero
  solo si su `fileId` es nuevo **o** su `modifiedTime` (RFC 3339, metadata de
  Drive) difiere del registrado. Criterio `fileId` + `modifiedTime`, **no** MD5
  del contenido: `modifiedTime` se obtiene de la metadata **sin descargar** el
  fichero, así que evita la descarga misma (que es el trabajo caro de esta
  capa), a diferencia del MD5 del `Loader`, que necesita el fichero ya en local
  para calcularse. Es el análogo mental del MD5 por documento del `Loader`,
  pero aplicado a metadata remota. `fileId` (no el nombre) es la clave estable:
  renombrar en Drive no fuerza redescarga; editar el contenido sí (cambia
  `modifiedTime`).
- **Staging local:** `staging_dir` es una **caché local reconstruible**, NO
  material sagrado. Lo sagrado es el original en Drive (y la capa Bronze aguas
  abajo); el staging puede borrarse y volverse a poblar con otro `sync()`. Si
  se borra el `state_path`, el siguiente `sync()` se re-descarga todo (correcto:
  el estado es solo una optimización, no fuente de verdad). Ubicación por
  defecto sugerida: `data/staging/historico/` (no versionada en git).
- **Auth desatendida (restricción de CI, task 31):** **cuenta de servicio**
  (service account) vía `GOOGLE_APPLICATION_CREDENTIALS` — el mismo mecanismo y
  variable que ya prevé el Flujo A (P18), porque es el mismo proyecto de Google
  y una sola cuenta de servicio puede tener acceso de lectura a ambas carpetas.
  Scope mínimo `https://www.googleapis.com/auth/drive.readonly`. **Nunca** flujo
  OAuth interactivo (browser popup): no funcionaría en un runner de GitHub
  Actions. En CI el JSON de la cuenta de servicio se inyecta por secreto.
- **Carpeta separada de la de teoría:** el histórico usa su **propia** variable
  de entorno para el folder ID, `DRIVE_FOLDER_ID_HISTORICO`, distinta de
  `DRIVE_FOLDER_ID` (que es la carpeta de libros/teoría del Flujo A). Son
  carpetas de Drive **distintas**; compartir una sola variable las confundiría.
  Las **credenciales** (`GOOGLE_APPLICATION_CREDENTIALS`) sí se comparten
  (misma service account); solo el folder ID se separa.

### Idempotencia en capas (independientes)

Cada capa se salta su propio trabajo repetido; **no se fusionan**:

| Capa | Pregunta | Clave de idempotencia | Estado |
|------|----------|------------------------|--------|
| DriveSource | ¿qué `.docx` **descargar**? | `fileId` + `modifiedTime` (metadata Drive) | `state_path` de DriveSource |
| marcar_remates | ¿qué `.md` **(re)generar**? | existencia del `.md` de salida (`--no-sobrescribir`) | el propio `.md` en disco |
| Loader | ¿qué `.md` **procesar** aguas abajo? | MD5 del `.md` | `state_path` del Loader |

Son independientes por diseño: un `.docx` sin cambios en Drive no se descarga
(capa 1 lo salta) y por tanto su `.md` tampoco se regenera; pero aunque una
capa superior decidiera rehacer trabajo (p.ej. se borra el staging y se
re-descarga un `.docx` idéntico), la capa del `Loader` seguiría saltándose el
`.md` resultante si su MD5 no cambió. Ninguna capa asume el estado de otra; el
`state_path` de DriveSource y el del `Loader` son ficheros **separados**.

## Idempotencia y versionado

Hash MD5 del **documento** (no evento, a diferencia del Flujo B — ver
`src/jokes/telegram/SPEC.md`) + reconciliación de chiste. Versionado por
chiste, sin `v{N}` (ver `src/jokes/SPEC.md` §Versionado). La idempotencia del
`Loader` (MD5 del `.md`) es **independiente** de la de `DriveSource` (metadata
de Drive) — ver §Fuente de entrada — carpeta Drive real, «Idempotencia en
capas».

## Coste

Volumen relevante → estimación de tokens previa (dry-run), batching y gate de
coste antes del run completo. Detalle en `docs/specs/llm-policy.md`.

## Riesgos propios de este flujo

| Riesgo | Mitigación |
|--------|-----------|
| Marcado por color pierde runs (tablas/hyperlinks) o confunde tonos de rojo | Validación round-trip obligatoria; clasificación por tono con margen; regenerar `.md` desde el `.docx` fuente (el original es la verdad) |
| Coste de tokens del histórico mayor de lo previsto | Dry-run de estimación + gate antes del run completo |
| Export de Google Docs a `.docx` pierde el color de fuente y rompe el marcado | Exportar a `.docx` (no a Markdown/texto), que conserva `w:color` a nivel de run; la validación round-trip obligatoria de `marcar_remates` detecta cualquier pérdida y falla en vez de emitir un `.md` corrupto |
| Auth de Drive incompatible con CI desatendido (OAuth interactivo) | Cuenta de servicio (`GOOGLE_APPLICATION_CREDENTIALS`) con scope `drive.readonly`, JSON inyectado por secreto en el runner — nunca OAuth con browser popup |
