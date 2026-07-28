# Chistes — contrato compartido (Flujos B y C)

> Spec de `src/jokes/` (nivel raíz: `silver.py`, `reconciliacion.py`,
> `supabase_store.py`, compartidos entre B y C). Para lo específico de cada
> flujo, ver [`src/jokes/telegram/SPEC.md`](telegram/SPEC.md) (Flujo B) y
> [`src/jokes/historico/SPEC.md`](historico/SPEC.md) (Flujo C). Contexto
> general en [`docs/specs/00-overview.md`](../../docs/specs/00-overview.md) y
> política LLM en [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md).

Este documento cubre **solo lo que Telegram (Flujo B) e Histórico (Flujo C)
comparten** porque tratan la misma unidad (`propio*`) con el mismo código:
Silver, Reconciliación, Taxonomías, versionado por chiste y el esquema de
Supabase. No se duplica en los specs de cada flujo — si tu tarea toca
`silver.py`, `reconciliacion.py` o `supabase_store.py`, este es tu spec.

## Silver (estructuración por LLM)

El LLM (modelo barato tipo Haiku vía API — ver `docs/specs/llm-policy.md`)
produce, por chiste:

| Campo                  | Descripción                                              |
|------------------------|-----------------------------------------------------------|
| `tema`                 | Tema del chiste → mapea a `tema_id` (ver §Taxonomías)     |
| `estructura_detectada` | setup/punchline, callback, misdirection… → `tecnica_id`  |
| `estado`               | `idea_suelta \| con_estructura \| rematado`               |
| `sugerencias_mejora`   | Propuestas de mejora (generación)                          |
| `chiste_normalizado`   | Reescritura conservando timing (NO elimina muletillas)    |

`sugerencias_mejora` y `chiste_normalizado` son **generativos**, no
clasificatorios: no existe un criterio externo que verifique "es un buen
chiste" (fuera del alcance de P16, ver `docs/specs/llm-policy.md`). Se generan
una vez y salen tal cual hacia revisión humana vía `chistes_revisiones`/`estado`
— nunca se reintentan en loop buscando que el LLM "mejore" su propia
propuesta sin supervisión.

Silver se invoca igual desde Telegram (Flujo B) e Histórico (Flujo C); la
única diferencia es de dónde viene el texto de entrada.

## Taxonomías (temas, técnicas, fuentes)

- **Fuente de verdad:** tablas relacionales editables en Supabase (`temas`,
  `tecnicas`, `fuentes`).
- Al clasificar, el LLM **mapea** a IDs existentes.
- **Resolución en loop acotado (≤3 intentos, P16 — ver `docs/specs/llm-policy.md`):**
  si el primer mapeo no encuentra ID existente, la siguiente vuelta **inyecta
  la taxonomía real** (`temas`/`tecnicas` tal cual está en Supabase) como
  contexto y reintenta el mapeo. El criterio de parada sigue siendo binario y
  externo al LLM (¿el ID propuesto existe en la tabla, sí o no?) — el loop no
  reduce precisión, solo evita candidatos espurios por variación léxica
  ("misdirection" vs "quiebro" vs "giro" para la misma técnica).
- Agotados los intentos sin match, se encola en `candidatos_taxonomia` para
  **revisión humana** — no crea la fila.
- El LLM **nunca** crea taxonomía autónomamente, ni dentro ni fuera del loop
  (evita deriva semántica sin supervisión).
- Contraste deliberado con `tipo_fuente` (enum cerrado en código, ver
  `00-overview.md`): `tipo_fuente` es estructural y estable; temas/técnicas
  crecen con el uso.

## Versionado por chiste

No hay `v{N}` de corpus para chistes (a diferencia de teoría, ver
`src/theory/SPEC.md`). Cada chiste tiene **historia propia**:

- **Madurez** (mismo chiste evoluciona: idea → estructura → rematado): se
  modela con `chistes_revisiones` (append-only). Cada cambio añade una
  revisión con el **contenido** de esa versión, no solo un número.
  `chistes.version_actual` apunta a la vigente.
- **Reutilización** (coges una premisa o un remate para otro chiste): se
  modela con `chistes.chiste_origen_id`, que enlaza la variante con su
  ancestro. Es un chiste distinto, no una revisión.

Distinción clave: **revisión** = el mismo chiste lógico cambiando en el tiempo;
**variante** (`chiste_origen_id`) = un chiste nuevo que reaprovecha material de otro.

## Reconciliación y deduplicación

Mecanismo **híbrido** que decide, por cada chiste entrante, si es
IGUAL / CAMBIADO / NUEVO. Aplica a `propio*` (histórico↔histórico y
Telegram↔histórico):

```
hash(texto_normalizado) coincide       → IGUAL    → dedup (no inserta)
similitud embedding ∈ [~0.85, 1)        → CAMBIADO → nueva revisión del existente
similitud embedding < ~0.85             → NUEVO    → inserta chiste nuevo
```

- El **hash** captura duplicados exactos, barato y determinista.
- El **embedding** captura chistes cambiados (misma premisa, remate retocado)
  que el hash no ve. Reusa los embeddings ya almacenados en pgvector.
- Los **umbrales son indicativos y afinables** con datos reales.
- Riesgo asumido: dos chistes distintos con premisa muy parecida podrían caer
  en la banda CAMBIADO (falso merge). Mitigación: umbral conservador y, si
  hace falta, cola de revisión para la banda dudosa (mejora futura).
- Este mecanismo **ya cumple** el criterio de parada verificable de P16 sin
  cambios (hash/umbral son externos al LLM) — ver `docs/specs/llm-policy.md`.

### Obtención de candidatos (`SupabaseStore.listar_candidatos_reconciliacion`)

`reconciliacion.py` es agnóstico de Supabase **por diseño** (task 15): su
`decidir_reconciliacion` / `reconciliar_chiste` reciben `candidatos` ya
resuelto como argumento (`list[dict]`), lo que mantiene el módulo testeable
sin red. **Quién obtiene esos `candidatos` es responsabilidad del caller vía
`supabase_store.py`** — este es el método que cierra ese hueco (implementación:
task 25).

**Firma:**

```python
def listar_candidatos_reconciliacion(
    self, tipo_fuente: str | Sequence[str]
) -> list[dict]
```

- **Parámetro `tipo_fuente`** — uno o varios valores del enum
  `TIPOS_FUENTE_CHISTE` (`propio` | `propio_historico`). Es el **caller** quien
  decide el alcance de la comparación (regla heredada de la decisión de task 15;
  ver la matriz de dedup de arriba): Flujo B (Telegram, `propio` entrante) y
  Flujo C (Histórico, `propio_historico` entrante) comparan hoy contra
  `propio_historico`. Aceptar también una secuencia deja abierto sin cambio de
  firma un futuro `propio*` (comparar contra ambos) sin cablear esa política
  dentro del método. Cada valor se valida contra el enum
  (`_validar_tipo_fuente_chiste`, reutilizado) — un `tipo_fuente` fuera de enum
  lanza `ValueError`, nunca degrada a query silenciosa.
- **Retorno** — `list[dict]`, una entrada por fila de `chistes` cuyo
  `tipo_fuente` cae en el alcance pedido, con **exactamente** las tres claves
  que `decidir_reconciliacion` consume:

  | Clave | Tipo | Uso en `decidir_reconciliacion` |
  |---|---|---|
  | `id` | `str` (uuid) | se copia a `ResultadoReconciliacion.chiste_id` (IGUAL/CAMBIADO) — **obligatoria** |
  | `hash_normalizado` | `str \| None` | comparación de dedup exacto (`candidato.get("hash_normalizado")`) |
  | `embedding` | `list[float] \| None` | similitud coseno; `None`/vacío se salta por entrada, no rompe |

  El método hace `select("id, hash_normalizado, embedding")` — **solo esas tres
  columnas**, no `select("*")`: minimiza transferencia y deja explícito el
  contrato con `reconciliacion.py`.

**El `embedding` se devuelve como `list[float]`** (no como el `text`/string que
PostgREST puede entregar para una columna `vector`): `similitud_coseno` itera
floats, así que si la fila trae el embedding serializado el método lo parsea a
`list[float]` antes de devolverlo (o lo deja en `None` si la columna está
vacía). Este es el único punto de adaptación entre la representación de pgvector
y lo que `reconciliacion.py` espera.

**Qué NO filtra (confirmado contra §Versionado y §Storage):**

- **Sin filtro de versión.** Cada fila de `chistes` es un chiste lógico cuyo
  `hash_normalizado`/`embedding` reflejan ya el contenido **vigente**
  (`version_actual`); la historia de revisiones vive aparte en
  `chistes_revisiones` (append-only) y **no** se consulta aquí. No hay, pues,
  nada que deduplicar por número de versión.
- **Variantes incluidas.** Una fila con `chiste_origen_id` no nulo es un chiste
  **distinto** (reutilización de material, §Versionado), no una revisión: debe
  poder ser candidato de reconciliación como cualquier otro. No se excluye.
- **Sin auto-exclusión.** La reconciliación ocurre **antes** del INSERT, así que
  un chiste entrante nuevo aún no está en `chistes`. En un reproceso idempotente,
  el mismo texto hará hash-match con su propia fila ya insertada → decisión
  IGUAL → dedup (no reinserta): ese es el resultado **deseado** (idempotencia),
  no un falso positivo a evitar.

**Trade-off — traer todo el `tipo_fuente` vs. ANN nativa de pgvector (decisión P20).**
El método trae **todas** las filas del `tipo_fuente` y `reconciliacion.py`
compara en Python (hash primero, luego coseno). La alternativa —una query ANN
`ORDER BY embedding <-> :entrante LIMIT K` que devuelva solo los K más
cercanos— **no** es compatible con el flujo actual sin tocar código congelado:
`reconciliar_chiste` obtiene los `candidatos` **antes** de calcular el embedding
del chiste entrante (el embedding se genera dentro de esa función, task 15, que
no se toca), de modo que en el momento del fetch **no existe** el vector `:entrante`
que la ANN necesita como query. Forzarlo obligaría a calcular el embedding fuera
y volver a calcularlo dentro (doble coste, rompe el orden hash-primero) o a
cambiar la interfaz de `decidir_reconciliacion` (prohibido). Dado el **bajo
volumen** explícito del corpus (GraphRAG descartado justo por eso, ver
`00-overview.md` §1), comparar en Python es más simple, determinista y 100%
testeable sin red. La ANN queda como **optimización futura** viable cuando el
volumen crezca: el método puede hacer el trabajo pesado en SQL y **seguir
devolviendo `list[dict]`** (la interfaz de `decidir_reconciliacion` no cambia),
pero requeriría reordenar el flujo para tener el embedding entrante disponible
en el fetch — fuera del alcance de hoy.

## Routing (IGUAL/CAMBIADO/NUEVO a Supabase)

> Componente compartido **`src/jokes/routing.py`**. Esta sección es su **spec**
> (task 33); la **extracción** del código desde `historico/pipeline.py` es la
> task 34 y el **segundo consumidor** (Flujo B) la task 35. Ninguna de las tres
> cambia el comportamiento observable del Flujo C — ver §"Contrato de regresión".

`reconciliacion.py` **decide, no persiste** (§Reconciliación): entrega un
`ResultadoReconciliacion` con `decision` ∈ `IGUAL|CAMBIADO|NUEVO` y deja el
INSERT/UPDATE al caller. Hoy ese caller es único —
`historico/pipeline.py::_rutear_a_supabase` (etapa 5, task 27)— y lleva el
`tipo_fuente` **hardcodeado** en una constante de módulo
(`TIPO_FUENTE = "propio_historico"`). El Flujo B necesita exactamente las
mismas tres ramas con `tipo_fuente='propio'`, así que la lógica se extrae a
`src/jokes/routing.py` **parametrizada por `tipo_fuente`**, en vez de
duplicarse en `telegram/pipeline.py`.

### Alcance — `routing.py` es el paso 5b, no el 5a

**El módulo compartido NO obtiene los candidatos ni llama a la
reconciliación**: recibe el `ResultadoReconciliacion` **ya resuelto** y se
limita a aplicarlo sobre Supabase. `listar_candidatos_reconciliacion` se queda
**en cada caller**. Tres motivos, en orden de peso:

1. **El `tipo_fuente` de lectura y el de escritura no son el mismo valor.** Al
   escribir, un chiste de Telegram se inserta con `tipo_fuente='propio'`; al
   leer candidatos, §"Obtención de candidatos" fija que un `propio` entrante
   compara hoy contra `propio_historico`. Una función compartida con un único
   parámetro `tipo_fuente` que hiciera las dos cosas cablearía a Flujo B a
   comparar contra `propio` — contradiciendo esa sección. Separarlas mantiene
   la política de alcance donde ya está especificada (y donde se cambiará si
   cambia), y no la reparte en dos sitios.
2. **`reconciliar_chiste` arrastra inyección LLM/embeddings.** Necesita
   `generar_embedding_fn`, y calcula el embedding del entrante *después* del
   fetch de candidatos (orden congelado, ver el trade-off P20 de arriba).
   Meterlo en `routing.py` metería clientes de embeddings en un módulo que hoy
   solo escribe filas, y arrastraría ese orden congelado a un tercer módulo.
3. **Coherencia con `telegram/SPEC.md` §Orquestación** (task 32), que ya lista
   el paso 9 (reconciliación, con candidatos de `listar_candidatos_reconciliacion`)
   y el paso 10 (routing) como **filas distintas con componentes distintos**.

| Entra en `routing.py` | Se queda en cada caller |
|---|---|
| Las 3 ramas IGUAL/CAMBIADO/NUEVO | `store.listar_candidatos_reconciliacion(...)` (5a) |
| `crear_chiste` / `crear_revision` / `obtener_chiste` / `actualizar_chiste` | `reconciliar_chiste(...)` (5a) |
| El empaquetado de `estructura_detectada` (`_estructura_revision`) | Silver, taxonomías y todo lo aguas arriba |
| El valor de `tipo_fuente` **de escritura**, como parámetro | La constante concreta (`propio_historico` / `propio`) |

### Interfaz pública

```python
# src/jokes/routing.py

@dataclass(frozen=True)
class ResultadoRuteo:
    decision: str                 # IGUAL | CAMBIADO | NUEVO
    chiste_id: Optional[str]
    tema_id: Optional[int]
    tecnica_id: Optional[int]


def rutear_chiste(
    store: Any,
    estructurado: ChisteEstructurado,
    recon: ResultadoReconciliacion,
    *,
    tipo_fuente: str,
    tema_id: Optional[int] = None,
    tecnica_id: Optional[int] = None,
) -> ResultadoRuteo
```

| Parámetro | Origen | Uso |
|---|---|---|
| `store` | `SupabaseStore` (o doble en tests) | `crear_chiste`, `crear_revision`, `obtener_chiste`, `actualizar_chiste` |
| `estructurado` | `silver.ChisteEstructurado` | `chiste_normalizado`, `estado`, `sugerencias_mejora`, `estructura_detectada` |
| `recon` | `reconciliacion.ResultadoReconciliacion` | `decision`, `chiste_id`, `hash_normalizado`, `embedding` |
| `tipo_fuente` | **constante del flujo**, nunca del LLM | solo la rama NUEVO (`crear_chiste`); sustituye a la constante de módulo de Flujo C |
| `tema_id` / `tecnica_id` | `resolver_taxonomia` (§Taxonomías) | `None` = sin match (candidato encolado) → ver regla de no-sobrescritura |

Los parámetros de después de `store/estructurado/recon` son **keyword-only**:
`tema_id` y `tecnica_id` son dos `Optional[int]` contiguos e intercambiables por
posición, y un swap silencioso escribiría taxonomías cruzadas sin fallar.

**Validación de `tipo_fuente`:** `routing.py` **no** revalida contra
`TIPOS_FUENTE_CHISTE`. El gatekeeper único sigue siendo `supabase_store.py`
(`_build_chiste_payload` → `_validar_tipo_fuente_chiste`, `ValueError`), misma
regla de "una sola copia del enum" que §"Obtención de candidatos". Consecuencia
aceptada y explícita: un `tipo_fuente` inválido **solo** falla en la rama NUEVO
(las otras dos no lo usan). Es tolerable porque el valor es una constante de
código por flujo, nunca entrada de usuario.

### `inicio_localizado` NO viaja al módulo compartido

`ChisteRuteado.inicio_localizado` (Flujo C) es una bandera del **Segmentador**
de histórico (fallback conservador cuando el LLM alucinó dónde empieza el
setup, ver `historico/segmentador.py`), que se propaga hasta el resumen del run
para la revisión humana muestral. Telegram **no tiene Segmentador** — un
mensaje ya es la unidad —, así que el campo no significaría nada allí.

Decisión: **queda fuera de `ResultadoRuteo`**, en vez de generalizarse a un
`Optional[bool] = None`. El motivo decisivo es que `_rutear_a_supabase` **nunca
lo lee**: no participa en ninguna de las tres ramas, solo se copia a la salida.
Un parámetro que la función no usa, con un nombre que solo tiene sentido en uno
de los dos consumidores, es lastre en un contrato compartido; y un campo que en
Flujo B sería `None` para siempre invita a que alguien lo interprete como
"inicio no localizado" en vez de "no aplica".

**Flujo C lo conserva sin cambio observable**, añadiéndolo *después* de la
llamada. `historico/pipeline.py` mantiene su `ChisteRuteado` como **subclase**
del resultado compartido:

```python
# src/jokes/historico/pipeline.py (task 34)
@dataclass(frozen=True)
class ChisteRuteado(ResultadoRuteo):
    inicio_localizado: bool
```

Así los 4 campos genéricos no pueden derivar y el 5º sigue siendo de Flujo C.
La **restricción dura** que la task 34 debe respetar, sea cual sea el idioma de
implementación elegido: `ChisteRuteado` sigue siendo **importable desde
`src.jokes.historico.pipeline`**, con los **mismos 5 campos, mismo orden, mismo
tipo y mismo significado** (`decision`, `chiste_id`, `tema_id`, `tecnica_id`,
`inicio_localizado`) — construible tanto por keyword como por posición, y con
`inicio_localizado` **`bool` de verdad** (los tests afirman `is False`, no
`== False`). Si la herencia diera fricción, una dataclass independiente de 5
campos poblada desde el `ResultadoRuteo` devuelto es igual de válida.

### Las tres ramas (comportamiento idéntico al actual)

| Decisión | Escrituras | Detalle |
|---|---|---|
| **IGUAL** | ninguna | Dedup (§Reconciliación). Devuelve `chiste_id=recon.chiste_id` tal cual, incluso si es `None`. **No** toca Supabase: es lo que hace idempotente el reproceso. |
| **NUEVO** | `crear_chiste` + `crear_revision` | `crear_chiste(texto_normalizado, hash_normalizado, tipo_fuente, embedding, tema_id, tecnica_id, estado, version_actual=1)`; después `crear_revision(chiste_id=fila["id"], version=1, contenido, estructura_detectada, estado, sugerencias_mejora)`. `chiste_id` de salida = el `id` recién creado. |
| **CAMBIADO** | `crear_revision` + `actualizar_chiste` | Lee `obtener_chiste(recon.chiste_id)`, calcula `nueva_version = (version_actual or 1) + 1`, crea la revisión (append-only, §Versionado) y actualiza el chiste con `texto_normalizado`, `hash_normalizado`, `embedding`, `estado` y `version_actual`. |

**Regla de no-sobrescritura de taxonomías (solo CAMBIADO):** `tema_id` y
`tecnica_id` se añaden al `UPDATE` **únicamente si no son `None`**. Nunca se
pisa un ID ya existente en la fila con un `None` producido porque la resolución
encoló un candidato para revisión humana (§Taxonomías). En NUEVO sí se pasan
siempre (la fila no existía: `None` es la ausencia legítima).

**`estructura_detectada`:** Silver produce un string libre y
`chistes_revisiones.estructura_detectada` es `jsonb` (§Storage). El helper que
hoy vive en `historico/pipeline.py` (`_estructura_revision`) **se mueve con la
función** —es privado y solo lo usan las ramas NUEVO/CAMBIADO— y sigue
produciendo exactamente `{"descripcion": <estructura_detectada de Silver>,
"tecnica_id": <id resuelto o None>}`.

### Contrato de regresión (task 34 no puede romperlo)

La extracción es **mecánica**: mismo cuerpo, mismas llamadas al `store`, mismo
orden de escrituras. Lo único que cambia es de dónde sale el `tipo_fuente`
(parámetro en vez de constante de módulo) y dónde se pega `inicio_localizado`.

| Se extrae literalmente | Cambia |
|---|---|
| Cuerpo de `_rutear_a_supabase` (3 ramas) | `TIPO_FUENTE` → parámetro `tipo_fuente` |
| `_estructura_revision` | `inicio_localizado` sale de la firma y del resultado compartido |
| Orden y argumentos de las llamadas al `store` | `historico/pipeline.py` delega en vez de implementar |

`historico/pipeline.py` conserva su constante `TIPO_FUENTE = "propio_historico"`
(la sigue necesitando en la etapa 5a para `listar_candidatos_reconciliacion`) y
se la pasa a `rutear_chiste`. Los tests de la task 27
(`tests/unit/jokes/historico/test_pipeline.py`) son el **contrato de regresión**:
deben seguir en verde **sin modificarse** — incluidos los que afirman
`tipo_fuente == "propio_historico"` en la fila creada, el `assert` del doble de
store sobre el `tipo_fuente` de los candidatos, la versión `N+1` en CAMBIADO, la
ausencia de `tema_id`/`tecnica_id` en el `UPDATE` sin match y
`inicio_localizado is False`. Igual para `scripts/run_historico.py` (task 28),
que serializa los 5 campos de `ChisteRuteado` en su resumen JSON.

### Consumidores

| Consumidor | Task | `tipo_fuente` | Nota |
|---|---|---|---|
| `src/jokes/historico/pipeline.py` (etapa 5b) | 34 | `propio_historico` | Delega; comportamiento observable intacto |
| `src/jokes/telegram/pipeline.py` (paso 10 del tramo background) | 35 | `propio` | Consumidor nuevo; ver `telegram/SPEC.md` §Orquestación |

## Limpieza, idioma y metadatos (chistes)

**Limpieza:** Bronze raw + pre-limpieza mínima + normalización por LLM que
**preserva el timing**. El Cleaner agresivo de teoría **nunca** se aplica a
`tipo_fuente=propio*`.

**Idioma:** se conservan en su **idioma original, sin traducir** (el wordplay
y el timing no sobreviven a la traducción automática).

**Metadatos** (columnas Supabase): `tipo_fuente`, `tema_id`, `tecnica_id`,
`fuente_id`, `estado`, `version_actual`, `chiste_origen_id`, `licencia`.

## Storage — schemas, tablas y acceso

> Sección reescrita por la **task 49** para materializar
> [P25](../../docs/specs/00-overview.md) (`bronze`/`silver`/`gold` como
> **schemas de Postgres reales**). Esta sección es la **referencia técnica**
> que citan el DDL y la migración (task 53), el paso a cliente *schema-aware*
> (task 54), el runbook de cutover (task 55) y su ejecución (task 56). Aquí se
> fija **qué schema le corresponde a cada tabla y cómo se accede**; no se
> escribe DDL ni código.

**Topología híbrida:** los chistes viven nativos en Supabase, como filas
(a diferencia de teoría, que además maneja documentos completos —crudo y
limpio— cuyo destino lo fija `src/theory/SPEC.md`, task 51). `pgvector` es el
índice único de consulta del RAG, compartido con teoría; toda consulta filtra
por `tipo_fuente`.

**"Grafo ligero" relacional (no GraphRAG):** las relaciones se modelan con
columnas explícitas (`tema_id`, `tecnica_id`, `fuente_id`), combinando filtro
relacional + ranking vectorial. No hay clustering Leiden ni grafo de conocimiento.

### Las capas son schemas, no sufijos en el nombre

Hasta P25, "Bronze" y "Silver" eran una **convención de nombres** dentro de un
único schema `public` (`chistes_telegram_bronze`). Ahora la capa es el
**schema** y el nombre de la tabla pierde el sufijo redundante:
`bronze.chistes_telegram`. **Convenio de nombres:** el nombre de la tabla no
repite su capa — la capa ya la dice el schema, y un
`bronze.chistes_telegram_bronze` sería ruido que además se desincroniza si la
tabla cambiara de capa. Regla ya cerrada en P25: no se redecide aquí.

Ninguna columna cambia en esta reorganización. Lo único que cambia de las
tablas de este módulo es **dónde viven** (schema) y, en un caso, **cómo se
llaman**.

### Mapeo final tabla → schema (alcance de este spec)

| Schema   | Tabla (nombre nuevo)         | Nombre actual en `public`  | Escribe / lee                                                        |
|----------|------------------------------|----------------------------|----------------------------------------------------------------------|
| `bronze` | `bronze.chistes_telegram`    | `chistes_telegram_bronze`  | Flujo B (`telegram/SPEC.md` §Bronze) vía `supabase_store.py`         |
| `silver` | `silver.chistes`             | `chistes`                  | B y C (§Routing) vía `supabase_store.py`                              |
| `silver` | `silver.chistes_revisiones`  | `chistes_revisiones`       | B y C (§Versionado, append-only)                                      |
| `silver` | `silver.temas`               | `temas`                    | B y C (§Taxonomías) + edición humana                                  |
| `silver` | `silver.tecnicas`            | `tecnicas`                 | B y C (§Taxonomías) + edición humana                                  |
| `silver` | `silver.fuentes`             | `fuentes`                  | B y C (§Taxonomías); **también** la referencia Teoría (ver FK abajo)  |
| `silver` | `silver.candidatos_taxonomia`| `candidatos_taxonomia`     | B y C (§Taxonomías, cola de revisión humana)                          |
| `gold`   | `gold.teoria_chunks`         | `teoria_chunks`            | Flujo A (`src/theory/teoria_store.py`), **no** `supabase_store.py`    |

Notas de lectura:

- **`bronze.chistes_telegram` es la única tabla Bronze de este módulo.** El
  Flujo C no tiene Bronze *de chistes*: su Bronze son **documentos**
  (`bronze.historico_documentos`, en Storage), especificado en
  [`src/jokes/historico/SPEC.md`](historico/SPEC.md) (task 52), no aquí. Igual
  para `bronze.teoria_documentos`/`silver.teoria_documentos`
  ([`src/theory/SPEC.md`](../theory/SPEC.md), task 51) y el componente
  compartido que los escribe ([`src/utils/SPEC.md`](../utils/SPEC.md) §DocumentStore,
  task 50). Este spec **no** define esas cuatro tablas.
- **No hay `gold` de chistes.** `silver.chistes` ya es la unidad indexable que
  consume el RAG; no existe una capa de consumo aparte (P25).
- **`gold.teoria_chunks` es contenido de Teoría, pero su DDL vive en este
  `schema.sql`** — igual que hoy: "`teoria_chunks` se incluye aquí para tener
  el esquema completo en un solo fichero", con su cliente de acceso fuera de
  este módulo (`src/theory/teoria_store.py`, task 21). Se mantiene esa
  convención: `supabase_store.py` **no** la expone y sigue sin importar nada de
  `src/theory/` (regla de dependencias de `CLAUDE.md`).
- **Ambos renombrados son de metadata.** `ALTER TABLE ... SET SCHEMA` +
  `ALTER TABLE ... RENAME TO` no mueven filas ni reescriben la tabla: los datos
  de producción de `chistes_telegram_bronze` y de las tablas de chistes
  sobreviven intactos (orden exacto y verificación: runbook de la task 55).

### Esquema de columnas (idéntico al actual, solo cualificado por schema)

```sql
silver.chistes (
  id                uuid primary key,
  texto_normalizado text,
  hash_normalizado  text,              -- dedup exacto (§Reconciliación)
  embedding         vector,            -- similitud (§Reconciliación) + retrieval RAG
  tipo_fuente       text,              -- propio | propio_historico
  tema_id           bigint references silver.temas(id),
  tecnica_id        bigint references silver.tecnicas(id),
  fuente_id         bigint references silver.fuentes(id),
  estado            text,              -- idea_suelta|con_estructura|rematado
  version_actual    int,
  chiste_origen_id  uuid references silver.chistes(id),  -- linaje de variante (§Versionado)
  licencia          text default 'comercializable',
  created_at, updated_at timestamptz
)
silver.chistes_revisiones (
  id, chiste_id uuid references silver.chistes(id),
  version int, contenido text,
  estructura_detectada jsonb, estado text, sugerencias_mejora text,
  created_at timestamptz              -- append-only (madurez, §Versionado)
)
silver.temas      (id, nombre, created_at)          -- editable (§Taxonomías)
silver.tecnicas   (id, nombre, created_at)          -- editable (§Taxonomías)
silver.fuentes    (id, nombre, tipo_fuente, licencia)
silver.candidatos_taxonomia (
  id, tipo text,                       -- 'tema' | 'tecnica'
  texto text, propuesto_por text,
  estado text default 'pendiente',     -- pendiente|aceptado|rechazado
  created_at timestamptz
)
bronze.chistes_telegram (              -- captura literal e inmutable (telegram/SPEC.md §Bronze)
  id                 uuid primary key,
  telegram_update_id bigint not null unique,  -- idempotencia por evento
  chat_id            bigint,
  texto_raw          text not null,   -- literal, sagrado, nunca se reescribe
  timestamp_telegram timestamptz,
  procesado_at       timestamptz,     -- NULL hasta que el pipeline completó (tasks 46/47)
  created_at         timestamptz
)
gold.teoria_chunks (                   -- ingesta de teoría, ver src/theory/SPEC.md
  id, doc_id, version_corpus,
  chunk_index int, contenido text, embedding vector,
  tipo_fuente text,
  fuente_id bigint references silver.fuentes(id),   -- FK CROSS-SCHEMA, ver abajo
  licencia default 'personal_only',
  unique (doc_id, version_corpus, chunk_index)
)
```

**Regla de escritura de Bronze (invariante, `CLAUDE.md`):**
`bronze.chistes_telegram` es **append-only**. La única escritura posterior al
INSERT admitida es el `UPDATE` de bookkeeping de `procesado_at` (tasks 46/47),
que **no toca `texto_raw`** ni ninguna otra columna de contenido.

### FK cross-schema: `gold.teoria_chunks.fuente_id` → `silver.fuentes(id)`

`fuentes` se queda en `silver` (es taxonomía editable del contrato B/C,
§Taxonomías) y `teoria_chunks` se va a `gold`, así que la FK que hoy es
intra-`public` pasa a **cruzar schemas**. Detalle que el implementer del DDL
(task 53) necesita saber:

- **En Postgres una FK cross-schema funciona exactamente igual que una normal**
  — misma sintaxis, misma semántica de integridad referencial, mismo coste. No
  hay restricción alguna sobre que referenciante y referenciado vivan en
  schemas distintos (sí la hay entre bases de datos distintas, que no es el
  caso). Lo único que cambia es que la referencia debe ir **cualificada con el
  schema** (`references silver.fuentes(id)`) salvo que `silver` esté en el
  `search_path` de la sesión que ejecuta el DDL. **Se cualifica siempre**, de
  forma explícita, para no depender del `search_path` — mismo criterio que ya
  se aplicó a `create extension ... schema public` en `schema.sql` (donde
  depender del `search_path` ya produjo un fallo real, 42704).
- **Orden de creación:** `silver.fuentes` debe existir **antes** que
  `gold.teoria_chunks`. El orden de `schema.sql` (taxonomías primero) ya lo
  garantiza, pero ahora el motivo es más fácil de pasar por alto porque las dos
  tablas están en bloques de schema distintos del fichero.
- **El renombrado no rompe la FK.** `ALTER TABLE ... SET SCHEMA` conserva las
  constraints existentes (apuntan a OIDs, no a nombres), así que mover
  `fuentes` a `silver` y `teoria_chunks` a `gold` mantiene la FK viva sin
  recrearla. La cualificación de arriba aplica a la creación **desde cero** en
  un proyecto nuevo, no al cutover.

### `pgvector` sigue en `public` — verificar el `search_path`

P25 **no** mueve la extensión: `create extension if not exists vector schema
public` se mantiene tal cual. Consecuencia: el tipo `vector` que usan
`silver.chistes.embedding` y `gold.teoria_chunks.embedding` vive en `public`
aunque las tablas ya no.

Esto funciona **siempre que `public` esté en el `search_path` de la sesión**.
El rol `service_role` de Supabase lo tiene por defecto (`"$user", public`), así
que la expectativa es que no haga falta nada extra. **Pero es una expectativa,
no un hecho verificado en este proyecto**: el síntoma exacto de que falle ya se
ha visto aquí (`42704: type "vector" does not exist`, documentado en el
comentario de cabecera de `schema.sql`), solo que por el motivo simétrico (la
extensión instalada fuera de `public`).

**Acción para el implementer de las tasks 53/56:** al aplicar el DDL, si
apareciera `42704` sobre el tipo `vector` en una tabla de `bronze`/`silver`/
`gold`, la causa es el `search_path`, y la solución es **cualificar el tipo**
(`embedding public.vector`) o añadir `public` al `search_path` de la sesión —
nunca reinstalar la extensión en otro schema (rompería las tablas que ya la
usan). Si aparece, se documenta en `src/jokes/KNOWN_ERRORS.md`.

### Acceso por schema con `supabase-py` (contrato para `supabase_store.py`)

`supabase_store.py` es el **único punto de acceso** a las tablas de este
contrato, y hoy hace `self.client.table("chistes")` — que va implícitamente
contra `public`, el único schema que PostgREST expone por defecto. El cliente
`supabase-py` admite seleccionar el schema **antes** de la tabla:

```python
self.client.schema("bronze").table("chistes_telegram")   # en vez de .table("chistes_telegram_bronze")
self.client.schema("silver").table("chistes")            # en vez de .table("chistes")
```

`.schema(...)` devuelve un cliente PostgREST apuntando a ese schema (envía las
cabeceras `Accept-Profile`/`Content-Profile`); el resto de la cadena
(`select`/`insert`/`update`/`upsert`/`eq`/`is_`/`execute`) **no cambia**.

**Contrato que fija este spec** — qué schema le corresponde a cada método:

| Método de `SupabaseStore`                       | Schema   | Tabla                 |
|--------------------------------------------------|----------|-----------------------|
| `crear_chiste`, `obtener_chiste`, `actualizar_chiste`, `listar_candidatos_reconciliacion` | `silver` | `chistes`             |
| `crear_revision`, `listar_revisiones`            | `silver` | `chistes_revisiones`  |
| `listar_temas`, `crear_tema`                     | `silver` | `temas`               |
| `listar_tecnicas`, `crear_tecnica`               | `silver` | `tecnicas`            |
| `listar_fuentes`, `crear_fuente`                 | `silver` | `fuentes`             |
| `crear_candidato_taxonomia`, `listar_candidatos_taxonomia`, `actualizar_candidato_taxonomia` | `silver` | `candidatos_taxonomia` |
| `guardar_mensaje_telegram_bronze`, `marcar_telegram_bronze_procesado`, `listar_telegram_bronze_pendientes` | `bronze` | `chistes_telegram`    |
| — (`gold.teoria_chunks` no se expone aquí)       | `gold`   | `teoria_chunks` → `src/theory/teoria_store.py` |

**Qué NO fija este spec.** *Cómo* se refactoriza `supabase_store.py` para
cumplir esta tabla —constante de módulo, variable de entorno, helper privado,
firma de los métodos— es decisión del implementer de la **task 54**, dentro de
su scope. La única restricción que viene de aquí, además del mapeo de arriba,
es la que ya lleva esa task en el backlog: el **valor por defecto sigue
apuntando a `public`** hasta el cutover, para que el código *schema-aware*
pueda desplegarse antes de mover las tablas y no exista una ventana en la que
el código busque un schema que todavía no existe. El **flip** de ese valor por
defecto es un paso del runbook (task 55), no del refactor.

Los nombres de tabla que hoy aparecen literales en tests y scripts
(`chistes_telegram_bronze` en `tests/unit/jokes/test_supabase_store.py`,
`tests/integration/test_telegram_bot_live.py`, `deploy/README.md`) se
actualizan con la task 54/55 correspondiente, no aquí.

### Exposed schemas + GRANTs — paso MANUAL, no código

Igual que `schema.sql` se aplica a mano en el SQL Editor (la API REST con
`SUPABASE_SERVICE_KEY` no ejecuta DDL), la habilitación de los schemas nuevos
es **manual**. Sin estos dos pasos, `.schema("bronze")` falla aunque la tabla
exista y los datos estén bien:

1. **Exponer los schemas en PostgREST.** Supabase Dashboard → **Settings → API
   → "Exposed schemas"**: añadir `bronze`, `silver` y `gold` (por defecto solo
   `public` está expuesto). Un schema no expuesto devuelve error de PostgREST
   ante cualquier petición, no una tabla vacía.
2. **Conceder privilegios a `service_role`** (el rol de
   `SUPABASE_SERVICE_KEY`, el único que usa este pipeline), en el SQL Editor:

   ```sql
   grant usage on schema bronze, silver, gold to service_role;
   grant all on all tables in schema bronze, silver, gold to service_role;
   alter default privileges in schema bronze, silver, gold
     grant all on tables to service_role;
   ```

   La tercera sentencia es la que evita el error recurrente: `grant ... on all
   tables` solo afecta a las tablas **que existen en ese momento**, así que una
   tabla creada después (p. ej. las de documentos de las tasks 51/52) quedaría
   inaccesible hasta repetir el GRANT a mano. `alter default privileges` lo
   resuelve de una vez para las futuras.

**Notas de verificación para quien ejecute el cutover (tasks 55/56):**

- **Secuencias:** `temas`, `tecnicas`, `fuentes` y `candidatos_taxonomia` usan
  `bigint generated always as identity`, cuya secuencia es interna a la columna
  y **no** requiere un GRANT propio (a diferencia de `serial`). Si aun así
  apareciera un `permission denied for sequence`, la red de seguridad es
  `grant usage, select on all sequences in schema silver to service_role;` —
  documentar en `KNOWN_ERRORS.md` si llega a hacer falta.
- **Caché de esquema de PostgREST:** tras mover o crear tablas puede seguir
  respondiendo con el esquema viejo (síntoma conocido: `PGRST204 ... could not
  find the column ... in the schema cache`, ver
  `docs/specs/KNOWN_ERRORS_GLOBAL.md`). Se fuerza el refresco con
  `notify pgrst, 'reload schema';` — comprobación obligatoria antes de dar el
  cutover por bueno.
- **No se concede nada a `anon` ni a `authenticated`.** El corpus incluye
  material con licencia `personal_only` (`docs/specs/llm-policy.md`) y ningún
  cliente público lo consume: exponer los schemas a PostgREST no debe
  confundirse con abrirlos a la clave anónima.

## Stack

Supabase (Postgres + `pgvector`), cliente de LLM vía API (Silver, modelo
barato), cliente de embeddings. Ver `docs/specs/llm-policy.md` para la
política de uso de LLM.

## Riesgos propios de este contrato compartido

| Riesgo | Mitigación |
|--------|-----------|
| Falso merge en reconciliación (premisas parecidas) | Umbral conservador; cola de revisión para banda dudosa |
