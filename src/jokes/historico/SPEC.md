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

> Sección reescrita por la **task 52** para materializar
> [P25](../../../docs/specs/00-overview.md) en este flujo: el `.docx` original
> y el `.md` marcado dejan de vivir solo en Drive y en el staging local y pasan
> a **capturarse en Supabase** (bucket privado + fila append-only) vía el
> componente compartido `src/utils/document_store.py`
> ([`src/utils/SPEC.md`](../../utils/SPEC.md) §DocumentStore). Aquí se fija
> **dónde se engancha la captura y qué columnas propias añade este flujo**; el
> contrato del componente es de la task 50, el DDL de la task 53 y la
> implementación de las tasks 64 (Bronze) y 65 (Silver). Todo lo que va del
> `Loader` en adelante —hasta `silver.chistes`— **no cambia**.

**Fuente de los `.docx`:** una **carpeta de Google Drive real** (ver §Fuente de
entrada — carpeta Drive real). `drive_source.py` lista esa carpeta, descarga a
un *staging* local solo los `.docx` nuevos/modificados y entrega sus paths
locales a `marcar_remates.procesar_docx(...)` **sin tocar su firma**. El
resultado (`.md` marcados) es exactamente lo que consume el `Loader` de
siempre.

**Dónde vive de verdad el material (P25):** ni Drive ni `data/staging/historico/`
garantizan que el original sobreviva — Drive es un espacio de trabajo editable
(borrar ahí borra el único ejemplar) y el staging es caché reconstruible que el
sync **sobrescribe** en cada modificación. La garantía es
**`bronze.historico_documentos` + el bucket privado `bronze-historico`**, donde
el `.docx` entra **tal cual sale de Drive, con su color de fuente**. El color es
**dato, no formato** (P15): si se pierde el `.docx` se pierde la posibilidad de
revisar o rehacer el marcado, y ningún derivado (`.md`, chiste en Silver)
permite reconstruirlo. Por eso Bronze guarda el **binario**, no su traducción.

**Entrada del pipeline propiamente dicho:** `.md` ya marcados con
`[REMATE]…[/REMATE]` y `[CHISTOIDE]…[/CHISTOIDE]`, generados por
`marcar_remates.py`. El pipeline los trata como texto plano normal. Ese `.md`,
hasta ahora **transitorio** (un fichero en `carpeta_md`, caché reconstruible),
pasa además a persistirse en **`silver.historico_documentos`** + bucket
`silver-historico`. "Además" es literal: el `Loader` lo sigue leyendo del
staging local, no del bucket (ver §El Loader no lee del bucket).

**Cadena vigente:**

```
DriveSource.sync_con_metadata() → [.docx staged local + metadata de Drive]
  → document_store.capturar(capa="bronze", flujo="historico", …) → bronze.historico_documentos
  → marcar_remates.procesar_docx(ruta_docx, carpeta_salida) → [.md marcado]
  → document_store.capturar(capa="silver", flujo="historico", …) → silver.historico_documentos
  → Loader.load() → Segmentador → Silver → taxonomías → reconciliación → routing
     → silver.chistes                                    (SIN CAMBIOS)
```

**La captura ENVUELVE, no modifica.** Ni `marcar_remates.procesar_docx` ni
`Loader` cambian de firma ni de comportamiento — igual que P19 hizo con
`DriveSource`, la captura es un paso **añadido alrededor** de ellos:

- `procesar_docx(ruta_docx, carpeta_salida, sobrescribir=True) -> tuple[Path, str]`
  sigue siendo el contrato aprobado en la task 17, palabra por palabra. Lo único
  que cambia es que su llamador **deja de descartar el valor de retorno**: el
  `Path` devuelto es el `.md` que hay que capturar. Leerlo no es tocar la firma.
- `Loader` sigue leyendo una carpeta de `.md` y decidiendo por MD5 (task 18).
- El diagrama de §Fuente de entrada — carpeta Drive real documenta la cadena tal
  como la fijó P19; la forma vigente es la de arriba. Todo lo demás de esa
  sección (qué MIMEs se listan, export a `.docx`, auth por cuenta de servicio,
  staging, idempotencia por metadata) sigue vigente sin cambios.

**Etapas** (la numeración **0-5 se conserva**: `historico/pipeline.py` y
`tests/unit/jokes/historico/test_pipeline.py` citan "§Entrada y etapas **0-5**",
y renumerar convertiría una task de solo-spec en un cambio que toca código. Las
dos capturas entran como **0b** y **1b**, pegadas a la etapa que envuelven):

0. **DriveSource (`drive_source.py`):** sincroniza la carpeta de Drive real a un
   *staging* local y devuelve los `.docx` nuevos/modificados (ver §Fuente de
   entrada — carpeta Drive real). Idempotencia por **metadata de Drive**
   (`fileId` + `modifiedTime`), independiente de la del Loader. Pasa a
   invocarse por `sync_con_metadata() -> list[ArchivoSincronizado]`
   ([`src/utils/SPEC.md`](../../utils/SPEC.md) §DriveSync, task 57) en vez de
   `sync() -> list[Path]`, porque la captura necesita el `fileId` y el
   `modifiedTime` que `sync()` descarta. `DriveSource` **hereda el método nuevo
   sin tocarse** (solo sobrescribe `__init__`; ver el contrato de regresión de
   la task 43): la task 64 no modifica `drive_source.py`.
0b. **Captura Bronze (task 64):** por cada `ArchivoSincronizado`,
   `document_store.capturar(ruta=a.path, capa="bronze", flujo="historico",
   drive_file_id=a.file_id, modified_time=a.modified_time, nombre=a.name,
   mime_type=a.mime_salida, extra={...})`. **Antes** de `marcar_remates`, no
   después: si el marcado falla (round-trip), el `.docx` ya está a salvo — que
   es justo el caso en que más falta hace conservarlo, porque significa que hay
   un fuente que revisar. Mismo criterio "guarda primero, procesa después" del
   Bronze de Telegram (P22).
1. **marcar_remates (`scripts/marcar_remates.py`):** por cada `.docx` staged,
   `procesar_docx(ruta_docx, carpeta_salida)` produce el `.md` marcado. Firma
   **inalterada** (contrato aprobado en la task 17): DriveSource lo envuelve por
   fuera, entregándole un path local, nunca lo modifica por dentro.
1b. **Captura Silver (task 65):** con el `Path` que devuelve `procesar_docx`,
   `document_store.capturar(ruta=ruta_md, capa="silver", flujo="historico",
   drive_file_id=a.file_id, modified_time=a.modified_time,
   nombre=ruta_md.name, mime_type="text/markdown", extra={...})` — el **mismo**
   par `(drive_file_id, modified_time)` que su Bronze (ver §Cómo referencia
   Silver a su Bronze). Solo se ejecuta si 0b terminó bien y si `procesar_docx`
   no lanzó: un `.md` que no existe no se captura, y un Silver sin su Bronze no
   se crea nunca (invariante que valida la task 68).
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
   `src/jokes/SPEC.md` §Reconciliación. Se subdivide en **5a** (obtener
   candidatos con `listar_candidatos_reconciliacion` + `reconciliar_chiste`),
   que se queda **en este flujo**, y **5b** (aplicar la decisión
   IGUAL/CAMBIADO/NUEVO sobre Supabase), que a partir de la task 34 **delega**
   en el componente compartido `src/jokes/routing.py` pasándole
   `tipo_fuente='propio_historico'` de forma explícita — ver `src/jokes/SPEC.md`
   §Routing. La delegación es una extracción sin cambio de comportamiento: la
   constante `TIPO_FUENTE` sigue viviendo en `historico/pipeline.py` (la usa 5a)
   y `ChisteRuteado` sigue exponiendo sus 5 campos, `inicio_localizado`
   incluido (bandera del Segmentador, propia de este flujo).

**Re-ejecutable:** con el tiempo llegarán documentos nuevos que pueden traer
chistes iguales o cambiados. La reconciliación a nivel de chiste enruta cada
uno a IGUAL (dedup) / CAMBIADO (nueva revisión) / NUEVO. El hash de documento
evita reprocesar lo idéntico.

### Punto de enganche exacto: una sola implementación, dos llamadores

Las etapas 0-2 están escritas **dos veces a propósito**, y esto condiciona dónde
puede vivir la captura:

- `src/jokes/historico/pipeline.py:399-405` — `run_historico_pipeline`, el run real.
- `scripts/run_historico.py:272-277` — `_documentos_pendientes_para_gate`, que
  las ejecuta **antes** para poder materializar `documentos` y evaluar el gate
  de coste sin haber gastado nada (razonado en el docstring de ese módulo,
  §"El reto de diseño").

**Trampa que la task 64 no puede pasar por alto:** en la ruta normal (el CLI),
la que sincroniza y marca de verdad es la **primera** pasada, la del gate. Para
cuando `run_historico_pipeline` llama a su propio `sync…()`, `DriveSource` ya ha
comprometido el `modifiedTime` de esos ficheros y devuelve **lista vacía** —
está escrito en el propio `run_historico.py` ("el segundo `.sync()` no los
vuelve a stagear"). Una captura enganchada **solo** dentro de
`run_historico_pipeline` no se ejecutaría **nunca** cuando el flujo se invoca
como está previsto que se invoque (semanalmente, desatendido, vía
`scripts/run_historico.py`), y el fallo sería **silencioso**: el pipeline
terminaría en verde con Bronze vacío.

Regla, por tanto: la secuencia 0 → 0b → 1 → 1b vive en **una sola función**
(ubicación natural: `historico/pipeline.py`, junto a `FalloMarcado`, que
`run_historico.py` ya importa de ahí), y **los dos** llamadores la usan —
`run_historico.py` sustituyendo su copia en línea por una llamada. Nombre y
firma exactos son de la task 64; lo que esta spec fija es que **no puede haber
dos implementaciones** de la captura, y que la que se ejecute es la del llamador
que realmente sincroniza. La doble pasada sigue siendo inofensiva aunque
alguien invoque `run_historico_pipeline` directamente: `capturar()` es
idempotente por `(drive_file_id, modified_time)`, así que una segunda pasada
sobre el mismo fichero devuelve `ya_existia=True` sin subir ni insertar nada.

**La captura ocurre también en `--dry-run`.** `--dry-run` significa "no gastes
dinero de LLM", no "no toques nada": esa ruta ya descarga de Drive y ya escribe
`.md` en disco. Capturar ahí es deliberado — si el gate aborta el run por
presupuesto, el `.docx` **ya está a salvo**. Lo contrario ataría la durabilidad
del material original a que hubiera presupuesto para procesarlo, que es
exactamente la dependencia que P25 viene a romper.

**Fallos de captura:** se reportan por documento (junto a `marcado_fallidos`, en
`ResultadoHistorico` y en el resumen JSON) y **no abortan el batch** — mismo
criterio que ya rige para los fallos de marcado. Su recuperación **no necesita
mecanismo nuevo**: `DriveSource` ya comprometió el `modifiedTime`, así que ese
`.docx` no se re-descarga solo; borrar el `state_path` de `DriveSource` fuerza
la re-descarga completa y la re-captura, y **nada se duplica** porque las dos
idempotencias son independientes ([`src/utils/SPEC.md`](../../utils/SPEC.md)
§"Qué NO hace `DocumentStore`": "borrar el JSON fuerza re-descarga pero no
duplica Bronze"). Tampoco se vuelve a pagar LLM: el MD5 del `Loader` no ha
cambiado, así que no entra ningún documento a la cadena cara y el gate estima
prácticamente cero.

**Relación con §Idempotencia en capas:** esa tabla enumera las tres capas
**locales** y sigue siendo correcta — son las que deciden qué se descarga, qué
se regenera y qué se procesa. La captura añade una cuarta idempotencia cuyo
estado no es un fichero local sino una fila en Supabase, y que **no decide nada
del pipeline**: solo evita subir dos veces el mismo objeto. No se fusiona con
ninguna de las otras tres.

### Columnas propias de este flujo (vía `extra` de `capturar()`)

`document_store.py` ya es dueño de `bucket`, `object_path`, `drive_file_id`,
`modified_time`, `hash_md5`, `origen`, `nombre`, `mime_type` y `tamano_bytes`
— **no se repiten aquí y `extra` no puede pisarlas** (es un
`DocumentStoreError`). Lo que sigue es lo que Histórico añade encima.

**`bronze.historico_documentos`:**

| Columna (`extra`) | Tipo | Por qué |
|---|---|---|
| `mime_origen` | `text` | MIME **de origen en Drive** (`ArchivoSincronizado.mime_type`), que no es el del objeto guardado (`mime_type` = `mime_salida`, siempre DOCX). Distingue un `.docx` **subido** —cuyos bytes son el original literal— de un **Google Doc nativo**, cuyos bytes son un **export generado por Google en el momento de la captura**. Sin esta columna, un re-export del mismo Doc sin modificar que produjese bytes distintos (otra versión del exportador) parecería corrupción del `hash_md5` en vez de lo que es. Es la única salvedad honesta al "tal cual sale de Drive". |

- **El color de fuente NO lleva columna.** Vive dentro de los bytes
  (`w:rPr/w:color` a nivel de *run*) y ahí se queda: Bronze guarda el fichero
  crudo y su trabajo es exactamente ese. Cualquier columna derivada (nº de
  caracteres en rojo, paleta detectada…) sería un **resumen** del dato que puede
  divergir del fichero sin que nada lo señale, y no ahorraría nada: quien quiera
  auditar el marcado descarga el `.docx` y lo vuelve a pasar por
  `marcar_remates`. Se deja escrito porque es **la razón de ser** de que Bronze
  exista en este flujo, y la ausencia de columna podría leerse como un olvido.
- **`origen` es siempre `'drive'` en la práctica.** Histórico no tiene material
  *legacy*: todo entra por la carpeta de Drive, y el backfill retroactivo
  (task 66) es solo de teoría — a Histórico le corresponde una pasada normal de
  `run_historico.py` con la captura activada (task 67). La variante
  `local_legacy` (`drive_file_id NULL` + `hash_md5`) existe en el componente y
  en el DDL compartido, pero **no** se espera que este flujo la use.
- **Sin `tipo_fuente`.** Todo lo que entra en esta tabla es
  `propio_historico`: una columna cuyo valor es constante para toda la tabla es
  el schema repitiendo su propio nombre. (En teoría sí tiene sentido, porque
  ahí conviven `teoria` y `transcripcion_curso` en la misma tabla.) El
  `tipo_fuente` que importa es el del **chiste**, y sigue viviendo donde ya
  vive: la constante `TIPO_FUENTE` de `historico/pipeline.py`, escrita en
  `silver.chistes` (§Etapa 5).

**`silver.historico_documentos`:**

| Columna (`extra`) | Tipo | Por qué |
|---|---|---|
| `n_remates` | `int` | Nº de `[REMATE]` del `.md`. No es adorno: `coste.py` documenta que el nº de ventanas del Segmentador **es exactamente** el nº de `[REMATE]`, y que ese mismo número es el proxy de llamadas a Silver. Es decir, es el número que dice **cuántos chistes debería haber producido este documento**, y convierte "¿aterrizó entero?" en una consulta SQL contra `silver.chistes` en vez de una lectura manual del bucket. Un documento con `n_remates = 0` es sospechoso por sí solo. |
| `n_chistoides` | `int` | Nº de `[CHISTOIDE]`. Mismo motivo, para la otra etiqueta: es metadato de estructura que el Segmentador conserva (§Etapa 3) y su ausencia total tras un cambio de marcado es la señal barata de que se ha perdido el burdeos. |

Los dos se cuentan sobre el texto del `.md` con un `str.count("[REMATE]")` /
`str.count("[CHISTOIDE]")` en el llamador. **No** se parsean del mensaje de
texto que `procesar_docx` devuelve como segundo elemento de la tupla (`"OK -
REMATE … chars, CHISTOIDE … chars"`): ese mensaje es para un humano y acoplarse
a su formato sería convertir una cadena de log en contrato. Tampoco se importan
los ayudantes privados de `marcar_remates.py`.

El **hash del `.md`** ya lo calcula y guarda `capturar()` (`hash_md5`, siempre,
también en modo Drive) — es el handle de verificación objeto ↔ fila y de
comparación round-trip; **no** hace falta columna propia para eso.

### Cómo referencia Silver a su Bronze

Por el **mismo par `(drive_file_id, modified_time)`**, no por una FK. El par
identifica una versión del **documento** a lo largo de las dos capas, así que el
linaje Bronze→Silver es un `join` directo
([`src/utils/SPEC.md`](../../utils/SPEC.md) §"Silver también entra por aquí").
La task 65 pide que la fila Silver "referencie a su Bronze de origen": **eso es
el par**, y la task 53 **no** debe añadir una columna `bronze_id` a
`silver.historico_documentos` para este flujo. Motivo: el par ya está indexado
como único y es obligatorio en ambas capas, mientras que una FK sería una
segunda fuente de verdad para la misma relación — la clase de columna que acaba
apuntando a otra fila que la que dice el par, sin que nada lo detecte. La
invariante "toda fila Silver tiene su Bronze" (task 68) se comprueba con ese
`join`, y la garantiza el orden de las etapas: 1b no se ejecuta si 0b no ha
terminado bien.

### Regenerar el `.md` marcado sin que cambie el `.docx` es un no-op — y aquí se acepta

`capturar()` usa el mismo `(drive_file_id, modified_time)` para Bronze y para
Silver, de modo que **volver a capturar el `.md` de un `.docx` que no ha
cambiado no escribe nada**: el `SELECT` de idempotencia encuentra fila y
retorna. El límite está documentado en
[`src/utils/SPEC.md`](../../utils/SPEC.md) §"Silver también entra por aquí" y se
delega su resolución a la **task 51** (dueña del linaje Bronze→Silver de
teoría). **Este flujo no monta mecanismo propio.** No por inercia — por estas
cuatro razones, en este orden:

1. **El caso de uso apenas existe.** Un `.md` marcado solo cambiaría sin que
   cambie el `.docx` si cambiase `marcar_remates.py`, y ese algoritmo está
   cerrado desde la task 17, es determinista y no tiene prompt que ajustar. Es
   la diferencia con teoría, donde el Silver es producto de limpieza/traducción
   —potencialmente con LLM— y **se espera** que se reajuste.
2. **La regresión peligrosa no es silenciosa.** Si un cambio del parser dejara
   de recorrer algún tipo de *run* (tablas, hyperlinks, listas), la
   **validación round-trip obligatoria falla y no se escribe `.md`** — el
   resultado es "sin Silver nuevo", nunca "Silver silenciosamente distinto".
   Queda un solo hueco real: cambiar el **margen de tono** que clasifica rojo
   puro vs. burdeos produciría un reparto `[REMATE]`/`[CHISTOIDE]` distinto que
   **sí** pasa el round-trip (los recuentos se derivan de la misma
   clasificación a ambos lados). Es un hueco estrecho y de una sola causa
   identificada.
3. **El daño está acotado y no corrompe el corpus.** `silver.chistes` **no** se
   deriva de `silver.historico_documentos`: sale del `.md` del staging local vía
   `Loader` (ver abajo). Un `.md` regenerado distinto cambia su MD5, el `Loader`
   lo reprocesa y la reconciliación por chiste actualiza el corpus **con o sin**
   fila Silver nueva. Lo único que queda desactualizado es la copia de auditoría
   del documento. Es un dato viejo, no un dato falso sobre el corpus.
4. **Y es reconstruible a coste cero.** El `.md` marcado es una **función pura y
   determinista** del `.docx` que está en Bronze: `marcar_remates` sobre el
   binario del bucket lo reproduce bit a bit, sin LLM, sin red y sin
   ambigüedad. Aquí Silver es **caché de conveniencia**, no fuente de verdad —
   otra vez la diferencia con teoría, donde re-derivar cuesta dinero y no
   garantiza el mismo resultado.

**Lo que se descarta explícitamente**, para que la task 65 no lo reabra: una
columna de "versión de `marcar_remates`" mantenida a mano. Sería un campo cuya
veracidad depende de que alguien se acuerde de subirla al tocar el algoritmo —
un versionado que nadie bumpea no señala staleness, la **afirma** falsamente, y
es justo el patrón de "dos fuentes de verdad para el mismo hecho" que
`document_store.py` evita al derivar `origen` de `drive_file_id` en vez de
aceptarlo como parámetro. Si la task 51 acaba introduciendo un mecanismo de
versión de proceso **en el componente compartido**, Histórico lo hereda gratis
y esta decisión se revisa entonces; lo que no se hace es adelantar aquí una
copia divergente del mismo mecanismo.

### El Loader no lee del bucket

`Loader.load()` sigue leyendo los `.md` de `carpeta_md` (staging local),
exactamente como hoy. Se evaluó que leyera de `silver-historico` y **se
descarta**:

- **Fusionaría dos idempotencias que la spec mantiene separadas.** El MD5 del
  `Loader` pasaría a depender de un contenido congelado por
  `(drive_file_id, modified_time)`, y §Idempotencia en capas es explícita en que
  las capas no se fusionan ni asumen el estado de las otras.
- **Convertiría el defecto anterior en corrupción.** Como regenerar Silver es un
  no-op, un `Loader` que leyera del bucket procesaría el `.md` **viejo**
  teniendo el nuevo en disco: el problema pasaría de "una copia de auditoría
  desactualizada" a "el corpus se construye sobre una versión obsoleta". Hoy el
  staging local es la copia fresca por construcción.
- **Rompería el gate de coste y los tests.** `Loader` y `marcar_remates` son las
  dos etapas **puras, sin red** de este flujo — se usan reales tanto en
  producción como en tests unitarios, y `_documentos_pendientes_para_gate` las
  ejecuta para estimar coste antes de tocar nada caro. Meterle a `Loader` una
  dependencia de Supabase obligaría a credenciales y dobles de prueba en la
  única parte de la cadena que hoy no los necesita.

Fuera de scope de las tasks 64/65, por tanto: no se toca `loader.py` ni nada
aguas abajo.

### Dos capas Silver, dos granularidades distintas

No confundir las dos cosas que se llaman "Silver" en este flujo:

| | `silver.historico_documentos` | `silver.chistes` |
|---|---|---|
| Unidad | **documento completo** (el `.md` marcado) | **chiste individual**, segmentado y estructurado |
| Dónde | objeto en el bucket `silver-historico` + fila índice | filas nativas en Postgres |
| Quién la define | esta spec (task 52) | [`src/jokes/SPEC.md`](../SPEC.md) §Storage/§Silver — compartida con Flujo B |
| Quién la escribe | `document_store.capturar(capa="silver")` (task 65) | `routing.py` vía `supabase_store.py` (§Etapa 5), **sin cambios** |
| Novedad de P25 | **sí**: antes era un fichero transitorio | **no**: se produce hoy exactamente igual |

Son capas de granularidad distinta, no dos versiones de lo mismo, y **no se
sustituyen**: `silver.chistes` sigue siendo el producto del flujo y la unidad
que indexa el RAG. Lo único que P25 añade es que el documento intermedio del que
salen esos chistes deja de ser transitorio.

### El gate de coste no aplica a la captura

Subir bytes a un bucket **no consume tokens de LLM**: la captura Bronze/Silver
no pasa por `historico/coste.py` ni entra en su estimación. `coste.py` sigue
midiendo exactamente lo mismo que hoy —el tramo Segmentador + Silver de chiste,
a partir de los documentos que devuelve `Loader.load()`— y el gate sigue
decidiendo lo mismo. Se deja escrito para la task 64 porque el orden de las
etapas invita al malentendido: la captura ocurre **antes** del gate en el
tiempo, pero **fuera** de él en la contabilidad (y sigue ocurriendo aunque el
gate deniegue el run, ver arriba).

## Fuente de entrada — carpeta Drive real (P19, 2026-07-24)

Cuando se escribió P19, el Flujo A tenía su integración con Drive real
**diferida** sobre carpetas locales (P18) y el Flujo C fue el primero en leer
de una **carpeta de Google Drive real**. Desde **P23** (2026-07-27) el Flujo A
también lee de Drive real y **reutiliza este mismo mecanismo** a través del
núcleo compartido `src/utils/drive_sync.py` (ver `src/utils/SPEC.md` §DriveSync
y `src/theory/SPEC.md` §Fuente de entrada): `drive_source.py` pasa a **delegar**
en él sin cambio de comportamiento (task 43) — el contrato de abajo sigue
siendo el de este flujo, palabra por palabra. El motivo original era
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
