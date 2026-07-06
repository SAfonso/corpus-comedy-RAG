# Comedy Corpus Pipeline — Especificación

> **Estado:** v2 (multi-fuente) · amplía el alcance mono-fuente original.
> **Metodología:** SDD — esta spec se aprueba ANTES de escribir código.
> **Documento vivo:** las decisiones aquí provienen del interrogatorio P1–P14
> y de ampliaciones posteriores (P15: marcado histórico por color, 2026-07-06).

## 1. Propósito y alcance

Pipeline de ingesta, limpieza, estructuración y versionado de datos para el
Comedy RAG. Transforma fuentes heterogéneas en un corpus consultable,
**diferenciando el origen de cada fragmento** (`tipo_fuente`) para permitir
retrieval separado por fuente y combinarlo en el prompt final del RAG
("ejemplos del autor" vs "técnica de fuentes externas").

El pipeline cubre **tres flujos**, independientes en orquestación pero unidos
por un contrato común (`tipo_fuente`) y un índice de consulta compartido:

- **Flujo A — Teoría:** libros y cursos de comedia desde Google Drive (batch).
  Limpieza determinista agresiva, traducción, salida a ficheros versionados
  en `/data/processed/v{N}/`.
- **Flujo B — Chistes propios (Telegram):** ingesta en tiempo real, chiste a
  chiste, vía bot de Telegram. Arquitectura Bronze→Silver, estructuración por LLM.
- **Flujo C — Chistes históricos:** procesado retroactivo (batch) de textos
  propios ya escritos, con varios chistes por documento y remates marcados.

**Regla invariante de todos los flujos:** el material original es sagrado
(`/data/raw/` para teoría, la capa Bronze para chistes). Nunca se modifica,
elimina ni sobrescribe. Todo el trabajo ocurre aguas abajo.

**Fuera de alcance (por ahora):**
- Grafo de conocimiento tipo GraphRAG / Leiden clustering — descartado por
  falta de volumen; se sustituye por un "grafo ligero" relacional (§8, §9).
- Fine-tuning — se usa RAG multi-fuente en su lugar (decisión cerrada).

## 2. Fuentes y `tipo_fuente`

`tipo_fuente` es el discriminador que atraviesa todo el pipeline y el RAG. Es un
**enum cerrado** (valores fijos; añadir uno es un cambio de spec, no algo que el
LLM decida):

| `tipo_fuente`         | Qué es                          | Trigger             | Storage destino        | Limpieza            |
|-----------------------|---------------------------------|---------------------|------------------------|---------------------|
| `teoria`              | Libros de comedia               | Batch (Drive)       | Ficheros `v{N}` + índice | Agresiva (determ.) |
| `transcripcion_curso` | Cursos transcritos (WhisperX)   | Batch (Drive)       | Ficheros `v{N}` + índice | Agresiva (determ.) |
| `propio`              | Chistes propios en tiempo real  | Realtime (Telegram) | Supabase               | Propia (Bronze/Silver) |
| `propio_historico`    | Textos propios ya escritos      | Batch retroactivo   | Supabase               | Propia (Bronze/Silver) |

**Contrato con el RAG:** toda unidad indexada lleva `tipo_fuente`. El RAG hace
retrieval separado por fuente y combina en el prompt. Nunca se mezcla el origen
de forma implícita.

**Agrupaciones útiles** (no son valores del enum, son predicados):
- `propio*` = `{propio, propio_historico}` — comparten Silver, versionado por
  chiste y reconciliación (§10, §11).
- `externo*` = `{teoria, transcripcion_curso}` — comparten limpieza agresiva,
  traducción y salida a ficheros (Flujo A).

**Licencia por defecto** (ver §14, sin enforcement aún):
`externo* → personal_only`, `propio* → comercializable`. `licencia` es un campo
independiente de `tipo_fuente` (un libro de dominio público es `teoria` pero
`comercializable`).

## 3. Arquitectura general y layout del repo

Tres orquestadores independientes que comparten utilidades y convergen en el
índice de consulta. Ningún flujo importa la lógica de orquestación de otro.

```
Flujo A — Teoría (batch, determinista)
  DriveMonitor → Parser → SubtypeDetector → Cleaner → LanguageDetector
    → LanguageNormalizer → QualityScorer → FormatNormalizer
    → /data/processed/v{N}/  ──(ingesta)──▶ Supabase (índice)

Flujo B — Chistes propios / Telegram (realtime, LLM)
  TelegramBot → Bronze(raw) → PreLimpiezaMinima → Silver(LLM)
    → Reconciliación → Supabase

Flujo C — Chistes históricos (batch retroactivo, LLM)
  [script marcar_remates.py: docx→.md, marcado AUTOMÁTICO por color →
     [REMATE] (rojo #FF0000) + [CHISTOIDE] (burdeos #980000)]  (automático, previo)
  HistLoader → Segmentador([REMATE]=fin; [CHISTOIDE] no es frontera; + LLM)
    → Silver(LLM) → Reconciliación → Supabase
```

**Layout del repo** (la separación es estructural, no solo de carpetas):

```
src/
├── utils/            # COMPARTIDO entre flujos
│   ├── language_detector.py
│   ├── quality_scorer.py
│   └── llm/          # cliente LLM (Silver) y embeddings
├── theory/           # Flujo A — orquestación teoría
│   ├── drive_monitor.py
│   ├── parsers/      # whisperx, pdf, epub, docx
│   ├── cleaners/
│   ├── normalizers/
│   └── pipeline.py
└── jokes/            # Flujos B y C — orquestación chistes
    ├── telegram_bot.py       # Flujo B (realtime)
    ├── historico/            # Flujo C (batch)
    │   ├── loader.py
    │   └── segmentador.py
    ├── silver.py             # Silver LLM (compartido B/C)
    ├── reconciliacion.py     # hash + embedding (compartido B/C)
    └── supabase_store.py

scripts/
├── run_pipeline.py           # Flujo A (teoría)
├── run_historico.py          # Flujo C (batch)
├── marcar_remates.py         # preprocesado automático por color (§7)
├── validate_corpus.py
└── stats_report.py
```

**Regla de dependencias:** `theory/` y `jokes/` no se importan entre sí. Todo
código común vive en `utils/`. El Silver y la Reconciliación se comparten entre
los flujos B y C porque tratan la misma unidad (`propio*`), pero no con teoría.

## 4. Flujo A — Teoría (Drive → ficheros)

Flujo batch, 100% determinista, sin LLM. Es el pipeline original; se conserva
intacto salvo por el nuevo paso de ingesta al índice (§8).

**Cadena de componentes** (el orden importa):

```
DriveMonitor → Parser → SubtypeDetector → Cleaner → LanguageDetector
  → LanguageNormalizer → QualityScorer → FormatNormalizer → /data/processed/v{N}/
```

- **DriveMonitor:** vigila Google Drive periódicamente; procesa solo ficheros
  nuevos (idempotencia por hash MD5, §13).
- **Parser:** una función por tipo de fuente. `whisperx_parser` (.txt con
  `[timestamp] SPEAKER_XX: texto`), `pdf_parser` (Tesseract OCR si escaneado),
  `epub_parser`, `docx_parser`. Código común → `src/utils/`.
- **SubtypeDetector:** clasifica cada fragmento en `explicacion | ejemplo`.
  Ejecuta ANTES del Cleaner porque los ejemplos tienen reglas distintas.
- **Cleaner:** limpieza AGRESIVA (§12) para `subtipo=explicacion`; conserva el
  estilo oral en `subtipo=ejemplo`.
- **LanguageDetector / LanguageNormalizer:** corpus bilingüe (§13): teoría se
  traduce a español, los ejemplos se conservan en idioma original.
- **QualityScorer:** puntuación 0–1 de densidad de contenido útil. Obligatorio.
- **FormatNormalizer:** salida uniforme `.txt` + cabecera YAML (§14, Opción B).

**WhisperX — contrato del parser:** elimina timestamps y speaker tags del texto;
conserva el speaker dominante como metadato del documento; une líneas
consecutivas del mismo speaker; preserva el contenido, nunca lo interpreta.

**Salida:** un `.txt` por documento en `/data/processed/v{N}/documents/`, más
`manifest.json` (índice inmutable) y `stats.json`. Esta salida es la **fuente de
verdad** de la teoría; su ingesta a Supabase (§8) es una copia indexable, no la
verdad.

## 5. Flujo B — Chistes propios (Telegram, tiempo real)

Ingesta incremental, un chiste = un evento. Arquitectura **Bronze → Silver**.

### 5.1 Bronze (raw, sagrado)
Cada mensaje de Telegram se persiste **literal**, sin tocar, con su metadata de
origen (`telegram_update_id`, `chat_id`, `timestamp`). El Bronze es la capa
inmutable equivalente a `/data/raw/` — nunca se reescribe.

**Idempotencia por evento:** dedup por `telegram_update_id`
(`INSERT ... ON CONFLICT (telegram_update_id) DO NOTHING`). Un reenvío o
reintento del webhook nunca duplica.

### 5.2 Pre-limpieza mínima (NO destructiva)
Antes del Silver, solo transformaciones reversibles: `trim`, normalización
unicode, y strip de artefactos de plataforma (comandos de bot, menciones).
**El Cleaner agresivo de teoría NO se aplica** a `tipo_fuente=propio*`: un
chiste no debe perder muletillas si son parte del timing del remate.

### 5.3 Silver (estructuración por LLM)
El LLM (§14, modelo barato tipo Haiku vía API) produce, por chiste:

| Campo                  | Descripción                                              |
|------------------------|---------------------------------------------------------|
| `tema`                 | Tema del chiste → mapea a `tema_id` (§9)                 |
| `estructura_detectada` | setup/punchline, callback, misdirection… → `tecnica_id` |
| `estado`               | `idea_suelta \| con_estructura \| rematado`             |
| `sugerencias_mejora`   | Propuestas de mejora (generación)                       |
| `chiste_normalizado`   | Reescritura conservando timing (NO elimina muletillas)  |

El LLM **mapea** temas/técnicas a IDs existentes; lo no encontrado va a
`candidatos_taxonomia` para revisión humana, nunca crea taxonomía por su cuenta (§9).

### 5.4 Salida
Tras el Silver, cada chiste pasa por **Reconciliación** (§11: ¿es nuevo, un
duplicado, o una revisión de uno existente?) y se persiste en Supabase con
`tipo_fuente='propio'`, `licencia='comercializable'`, y su versión/linaje (§10).
Sin `v{N}`: los chistes son un store vivo, no snapshots de corpus.

## 6. Flujo C — Chistes históricos (batch retroactivo)

Procesado retroactivo de textos propios ya escritos, con varios chistes por
documento. Reutiliza el Silver y la Reconciliación del Flujo B; cambia la entrada
y añade segmentación.

**Entrada:** `.md` ya marcados con `[REMATE]…[/REMATE]` y `[CHISTOIDE]…[/CHISTOIDE]`,
generados por el script automático de §7. El pipeline los trata como texto plano normal.

**Etapas:**
1. **Loader:** lee los `.md`. Idempotencia de documento por hash MD5 (§13): un
   documento idéntico ya procesado se salta.
2. **Segmentador:** `[REMATE]` = fin **determinista** de cada chiste; el LLM
   afina hacia atrás dónde empieza el setup por contenido semántico y descarta
   intros/transiciones que no son del chiste (P10). `[CHISTOIDE]` **NO es frontera
   de chiste** (es un mini-remate interno que aligera una premisa larga): el
   Segmentador lo **ignora como fin** y lo **conserva como metadato de estructura**
   del chiste al que pertenece (útil para el Silver). Tratarlo como fin partiría
   chistes por la mitad.
3. **Silver:** mismo esquema que §5.3.
4. **Reconciliación (§11)** → Supabase con `tipo_fuente='propio_historico'`.

**Re-ejecutable:** con el tiempo llegarán documentos nuevos que pueden traer
chistes iguales o cambiados. La reconciliación a nivel de chiste (§11) enruta cada
uno a IGUAL (dedup) / CAMBIADO (nueva revisión) / NUEVO. El hash de documento evita
reprocesar lo idéntico.

**Gate de coste:** el histórico tiene volumen relevante → estimación de tokens
previa (dry-run), batching y gate de coste antes del run completo (§14).

## 7. Preprocesado de marcado (`scripts/marcar_remates.py`)

Script **automático y determinista**, previo y desacoplado del pipeline (P14).
Sustituye el marcado manual original: el color ya existe en el documento fuente, así
que la marcación se deriva de él sin intervención humana (P15, 2026-07-06).

- **Motivo:** Markdown plano no conserva el color de texto. Por eso NO se parte de
  un `.md` ya convertido; se lee el documento **original con estilos**
  (`.docx` / Google Docs). El color de fuente vive a nivel de *run* en el XML del
  `.docx`, lo que permite detectarlo de forma determinista.
- **Mapa color → etiqueta** (revisa P9: el rojo **ya no es 1:1 con remate**; hay dos
  rojos con significado distinto):

  | Color fuente        | Etiqueta                     | Semántica                                                        |
  |---------------------|------------------------------|------------------------------------------------------------------|
  | `#FF0000` rojo puro | `[REMATE]…[/REMATE]`         | Remate principal — **cierra** el chiste (frontera, §6)           |
  | `#980000` burdeos   | `[CHISTOIDE]…[/CHISTOIDE]`   | Mini-remate interno, menos fuerza — **NO** cierra el chiste      |

  Cualquier otro color = texto normal, sin etiquetar. La clasificación es **por tono
  con margen**, no por igualdad exacta de hex (el color puede variar un par de dígitos
  entre documentos).
- **Reglas de marcado:**
  - Runs contiguos del mismo color se **fusionan** en un único span.
  - Un span rojo que cruza párrafos = **un solo tramo** (el marcador se mantiene
    abierto entre párrafos).
  - Las dos etiquetas **no se solapan**: al cambiar de color se cierra una y se abre
    la otra.
  - Espacios y puntuación quedan **fuera** de las etiquetas.
- **Cobertura de parseo:** además de párrafos, recorrer **tablas, hyperlinks y listas**
  (runs que el iterador ingenuo de `python-docx` no devuelve).
- **Validación round-trip (obligatoria):** nº de caracteres de cada color en el `.docx`
  == nº de caracteres dentro de la etiqueta correspondiente en el `.md`. Un descuadre
  indica runs perdidos (típicamente en tablas o hyperlinks) y **debe fallar** el marcado.
- **Salida:** `.md` con marcadores embebidos, que alimenta el Flujo C.
- **No integrado** en la arquitectura de teoría ni en la orquestación: se mantiene como
  paso previo desacoplado (P14). Al ser automático, ya no añade fricción manual.

> **Prototipo:** se valida primero en Google Colab sobre documentos reales del histórico
> antes de bajarlo a `scripts/` (SDD: spec → tests con fixtures reales → implementación).

## 8. Storage y contrato RAG multi-fuente

**Topología híbrida (P3):**
- `/data/processed/v{N}/` = fuente de verdad y entregable inmutable de teoría.
- Un paso de **ingesta** vuelca la teoría a Supabase como copia indexable.
- Los chistes viven nativos en Supabase.
- **pgvector = índice único de consulta.** Toda consulta del RAG filtra por
  `tipo_fuente` para hacer retrieval separado por fuente.

**"Grafo ligero" relacional (no GraphRAG):** las relaciones se modelan con columnas
explícitas (`tema_id`, `tecnica_id`, `fuente_id`), combinando filtro relacional +
ranking vectorial. No hay clustering Leiden ni grafo de conocimiento.

**Esquema de tablas (boceto, se refina en implementación):**

```sql
chistes (
  id                uuid primary key,
  texto_normalizado text,
  hash_normalizado  text,              -- dedup exacto (§11)
  embedding         vector,            -- similitud (§11) + retrieval RAG
  tipo_fuente       text,              -- propio | propio_historico
  tema_id           bigint references temas(id),
  tecnica_id        bigint references tecnicas(id),
  fuente_id         bigint references fuentes(id),
  estado            text,              -- idea_suelta|con_estructura|rematado
  version_actual    int,
  chiste_origen_id  uuid references chistes(id),  -- linaje de variante (§10)
  licencia          text default 'comercializable',
  created_at, updated_at timestamptz
)
chistes_revisiones (
  id, chiste_id uuid references chistes(id),
  version int, contenido text,
  estructura_detectada jsonb, estado text, sugerencias_mejora text,
  created_at timestamptz              -- append-only (madurez, §10)
)
temas      (id, nombre, created_at)               -- editable (§9)
tecnicas   (id, nombre, created_at)               -- editable (§9)
fuentes    (id, nombre, tipo_fuente, licencia)
candidatos_taxonomia (
  id, tipo text,                       -- 'tema' | 'tecnica'
  texto text, propuesto_por text,
  estado text default 'pendiente',     -- pendiente|aceptado|rechazado
  created_at timestamptz
)
teoria_chunks (                        -- ingesta de teoría (§8)
  id, doc_id, version_corpus,          -- v{N} de procedencia
  contenido text, embedding vector,
  tipo_fuente text, fuente_id, licencia default 'personal_only'
)
```

## 9. Taxonomías (temas, técnicas, fuentes)

- **Fuente de verdad:** tablas relacionales editables en Supabase (`temas`,
  `tecnicas`, `fuentes`).
- Al clasificar, el LLM **mapea** a IDs existentes.
- Si detecta algo que no existe, lo encola en `candidatos_taxonomia` para
  **revisión humana** — no crea la fila.
- El LLM **nunca** crea taxonomía autónomamente (evita deriva: "misdirection" vs
  "quiebro" vs "giro").
- Contraste deliberado con `tipo_fuente` (§2), que sí es enum cerrado en código:
  `tipo_fuente` es estructural y estable; temas/técnicas crecen con el uso.

## 10. Versionado por chiste

No hay `v{N}` de corpus para chistes. Cada chiste tiene **historia propia**:

- **Madurez** (mismo chiste evoluciona: idea → estructura → rematado): se modela
  con `chistes_revisiones` (append-only). Cada cambio añade una revisión con el
  **contenido** de esa versión, no solo un número. `chistes.version_actual` apunta
  a la vigente.
- **Reutilización** (coges una premisa o un remate para otro chiste): se modela
  con `chistes.chiste_origen_id`, que enlaza la variante con su ancestro. Es un
  chiste distinto, no una revisión.

Distinción clave: **revisión** = el mismo chiste lógico cambiando en el tiempo;
**variante** (`chiste_origen_id`) = un chiste nuevo que reaprovecha material de otro.

## 11. Reconciliación y deduplicación de chistes

Mecanismo **híbrido (P12)** que decide, por cada chiste entrante, si es
IGUAL / CAMBIADO / NUEVO. Aplica a `propio*` (histórico↔histórico y
Telegram↔histórico):

```
hash(texto_normalizado) coincide       → IGUAL    → dedup (no inserta)
similitud embedding ∈ [~0.85, 1)        → CAMBIADO → nueva revisión (§10) del existente
similitud embedding < ~0.85             → NUEVO    → inserta chiste nuevo
```

- El **hash** captura duplicados exactos, barato y determinista.
- El **embedding** captura chistes cambiados (misma premisa, remate retocado) que
  el hash no ve. Reusa los embeddings ya almacenados en pgvector.
- Los **umbrales son indicativos y afinables** con datos reales.
- Riesgo asumido: dos chistes distintos con premisa muy parecida podrían caer en
  la banda CAMBIADO (falso merge). Mitigación: umbral conservador y, si hace falta,
  cola de revisión para la banda dudosa (mejora futura).

## 12. Limpieza, idioma y metadatos (políticas por flujo)

**Limpieza:**
- `externo*` (teoría): AGRESIVA por defecto (elimina muletillas, repeticiones,
  corrige errores obvios, separa en párrafos). Excepción: `subtipo=ejemplo`
  conserva el estilo oral.
- `propio*` (chistes): Bronze raw + pre-limpieza mínima + normalización por LLM
  que **preserva el timing**. El Cleaner agresivo nunca se aplica.

**Idioma:**
- Teoría: corpus bilingüe explícito — teoría se traduce a español, los ejemplos
  se conservan en idioma original. RAG configurado para multiidioma.
- Chistes propios: se conservan en su **idioma original, sin traducir** (el
  wordplay y el timing no sobreviven a la traducción automática).

**Metadatos — dos esquemas según destino:**
- Teoría (YAML frontmatter en el `.txt`): `fuente`, `autor`, `idioma_original`,
  `idioma_fragmento`, `subtipo`, + `tipo_fuente`, `licencia`.
- Chistes (columnas Supabase, §8): `tipo_fuente`, `tema_id`, `tecnica_id`,
  `fuente_id`, `estado`, `version_actual`, `chiste_origen_id`, `licencia`.

## 13. Idempotencia y versionado por flujo

| Flujo            | Idempotencia                              | Versionado             |
|------------------|-------------------------------------------|------------------------|
| A — Teoría       | `processed_files.json` (hash MD5)         | `v{N}` inmutable       |
| B — Telegram     | Por evento (`telegram_update_id`)         | Por chiste (§10)       |
| C — Histórico    | Hash MD5 del documento + reconciliación de chiste (§11) | Por chiste (§10) |

- **`v{N}` inmutable aplica SOLO a teoría.** Su `manifest.json` es inmutable una
  vez generado; nunca se sobrescribe una versión.
- **Reanudación:** si cualquier flujo falla a mitad, retoma desde el último ítem
  no completado (fichero/documento/evento), sin reprocesar lo ya hecho.

## 14. Coste / LLM y copyright

**Política LLM:**
- La regla "NO usar LLMs / NO APIs de pago" se mantiene como norma para
  `externo*` (teoría, batch grande, determinista).
- **Excepción acotada y documentada:** el Silver de chistes (§5.3) es imposible
  sin generación (`estructura_detectada`, `sugerencias_mejora`,
  `chiste_normalizado`). Usa un LLM barato vía API (tipo Haiku). Los embeddings
  de reconciliación/retrieval, ídem. Descartado LLM local por no disponer de GPU.

**Control de coste (histórico, §6):** volumen relevante → estimación de tokens
(dry-run), batching, gate de coste antes del run completo, y caché / modelo más
barato si hace falta.

**Copyright / licencia (P7):**
- Restricción conocida: el material de pago (`externo*`) no puede redistribuirse
  tal cual si el producto se comercializa a terceros.
- **Sin enforcement por ahora** (no se planea comercializar): `licencia` es un
  campo de metadata con default seguro (`externo* → personal_only`,
  `propio* → comercializable`), pero **ninguna lógica lo aplica** en pipeline ni RAG.
- Reactivable en el futuro: si se comercializa, se añade el filtro por `licencia`
  en query time sin retro-etiquetar (el campo ya existe).

## 15. Stack, metodología y riesgos

**Stack (coste cero donde es posible):**
- Teoría: `pytesseract` + `pdf2image` (OCR), `ebooklib` (EPUB), `python-docx`
  (DOCX), `langdetect`, `deep-translator` (DeepL free / LibreTranslate),
  `APScheduler`, `google-api-python-client`.
- Chistes: Supabase (Postgres + `pgvector`), `python-telegram-bot`, cliente de
  LLM vía API (Silver, modelo barato), cliente de embeddings.

**Metodología SDD + TDD:**
1. Leer la spec relevante (este documento / `/docs/specs/`) antes de implementar.
2. Tests primero, con fixtures **reales** de `/tests/fixtures/` (nunca inventados).
3. `pytest tests/unit/ -v` y `tests/integration/ -v`; `validate_corpus.py` antes
   de commit.

**Riesgos:**

| Riesgo | Mitigación |
|--------|-----------|
| Falso merge en reconciliación (premisas parecidas) | Umbral conservador; cola de revisión para banda dudosa |
| Coste de tokens del histórico mayor de lo previsto | Dry-run de estimación + gate antes del run completo |
| Marcado por color pierde runs (tablas/hyperlinks) o confunde tonos de rojo | Validación round-trip obligatoria (§7); clasificación por tono con margen; regenerar `.md` desde el `.docx` fuente (el original es la verdad) |
| Copyright si algún día se comercializa | Campo `licencia` ya presente; activar filtro en query |
| Traducción automática de teoría de baja calidad | Solo traducir teoría, conservar ejemplos en original; revisar muestra |
| Corpus real más pequeño de lo esperado post-limpieza | Medir con `stats_report.py` antes de comprometerse |
