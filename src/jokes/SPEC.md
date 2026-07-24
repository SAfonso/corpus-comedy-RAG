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

## Limpieza, idioma y metadatos (chistes)

**Limpieza:** Bronze raw + pre-limpieza mínima + normalización por LLM que
**preserva el timing**. El Cleaner agresivo de teoría **nunca** se aplica a
`tipo_fuente=propio*`.

**Idioma:** se conservan en su **idioma original, sin traducir** (el wordplay
y el timing no sobreviven a la traducción automática).

**Metadatos** (columnas Supabase): `tipo_fuente`, `tema_id`, `tecnica_id`,
`fuente_id`, `estado`, `version_actual`, `chiste_origen_id`, `licencia`.

## Storage — esquema de tablas (boceto, se refina en implementación)

**Topología híbrida:** los chistes viven nativos en Supabase (a diferencia de
teoría, que tiene ficheros `v{N}` como fuente de verdad — ver
`src/theory/SPEC.md`). `pgvector` es el índice único de consulta del RAG,
compartido con teoría; toda consulta filtra por `tipo_fuente`.

**"Grafo ligero" relacional (no GraphRAG):** las relaciones se modelan con
columnas explícitas (`tema_id`, `tecnica_id`, `fuente_id`), combinando filtro
relacional + ranking vectorial. No hay clustering Leiden ni grafo de conocimiento.

```sql
chistes (
  id                uuid primary key,
  texto_normalizado text,
  hash_normalizado  text,              -- dedup exacto (§Reconciliación)
  embedding         vector,            -- similitud (§Reconciliación) + retrieval RAG
  tipo_fuente       text,              -- propio | propio_historico
  tema_id           bigint references temas(id),
  tecnica_id        bigint references tecnicas(id),
  fuente_id         bigint references fuentes(id),
  estado            text,              -- idea_suelta|con_estructura|rematado
  version_actual    int,
  chiste_origen_id  uuid references chistes(id),  -- linaje de variante (§Versionado)
  licencia          text default 'comercializable',
  created_at, updated_at timestamptz
)
chistes_revisiones (
  id, chiste_id uuid references chistes(id),
  version int, contenido text,
  estructura_detectada jsonb, estado text, sugerencias_mejora text,
  created_at timestamptz              -- append-only (madurez, §Versionado)
)
temas      (id, nombre, created_at)               -- editable (§Taxonomías)
tecnicas   (id, nombre, created_at)               -- editable (§Taxonomías)
fuentes    (id, nombre, tipo_fuente, licencia)
candidatos_taxonomia (
  id, tipo text,                       -- 'tema' | 'tecnica'
  texto text, propuesto_por text,
  estado text default 'pendiente',     -- pendiente|aceptado|rechazado
  created_at timestamptz
)
teoria_chunks (                        -- ingesta de teoría, ver src/theory/SPEC.md
  id, doc_id, version_corpus,          -- v{N} de procedencia
  contenido text, embedding vector,
  tipo_fuente text, fuente_id, licencia default 'personal_only'
)
```

## Stack

Supabase (Postgres + `pgvector`), cliente de LLM vía API (Silver, modelo
barato), cliente de embeddings. Ver `docs/specs/llm-policy.md` para la
política de uso de LLM.

## Riesgos propios de este contrato compartido

| Riesgo | Mitigación |
|--------|-----------|
| Falso merge en reconciliación (premisas parecidas) | Umbral conservador; cola de revisión para banda dudosa |
