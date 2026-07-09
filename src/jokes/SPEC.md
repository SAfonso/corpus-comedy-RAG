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
