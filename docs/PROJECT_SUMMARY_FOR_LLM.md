# Comedy Corpus Pipeline — Resumen de arquitectura (para LLM)

> Generado como resumen de referencia rápida. La fuente de verdad sigue siendo
> `docs/specs/00-overview.md` y los `SPEC.md` por módulo — ante cualquier
> discrepancia con este documento, manda el `SPEC.md` correspondiente.

## 1. Qué es este proyecto

Pipeline de **ingesta, limpieza, estructuración y versionado de datos** para
alimentar un **RAG de comedia** (generación de material cómico asistido).
El corpus es **multi-fuente**: cada unidad de dato lleva un campo
`tipo_fuente` (enum cerrado) que permite hacer *retrieval* separado por
origen en el RAG downstream y combinar "ejemplos del autor" con "técnica de
fuentes externas" en el prompt final.

**Estado actual:** Fase 0 — spec aprobada, estructura de carpetas creada,
pre-implementación (SDD estricto: spec → tests con fixtures reales →
código). Metodología: TDD sobre fixtures reales de `/tests/fixtures/`.

## 2. Los tres flujos de datos

El pipeline no es un único pipeline lineal, son **tres orquestadores
independientes** que no se importan entre sí, comparten utilidades comunes
(`src/utils/`) y convergen en un único índice de consulta (pgvector /
Supabase) que el RAG consulta filtrando por `tipo_fuente`.

### Flujo A — Teoría (`src/theory/`)
Libros y cursos de comedia (Google Drive), procesado **batch, determinista,
coste 0, sin LLM**.

```
DriveMonitor → Parser → SubtypeDetector → Cleaner → LanguageDetector
  → LanguageNormalizer → QualityScorer → FormatNormalizer
  → /data/processed/v{N}/  ──(ingesta)──▶ Supabase (tabla teoria_chunks, índice)
```

- **DriveMonitor**: vigila Drive, idempotencia por hash MD5 de fichero
  (`processed_files.json`).
- **Parser**: uno por tipo de fuente — `whisperx_parser` (.txt con
  `[timestamp] SPEAKER_XX: texto`, quita timestamps/speaker tags, conserva
  speaker dominante como metadato), `pdf_parser` (OCR con Tesseract si está
  escaneado), `epub_parser`, `docx_parser`.
- **SubtypeDetector**: clasifica cada fragmento en `explicacion | ejemplo`.
  **Se ejecuta ANTES que el Cleaner** — decisión de diseño clave, porque los
  `ejemplo` tienen reglas de limpieza distintas (conservan estilo oral).
- **Cleaner**: limpieza AGRESIVA (muletillas, repeticiones, errores) solo
  para `subtipo=explicacion`.
- **LanguageDetector/Normalizer**: corpus bilingüe — la teoría se traduce a
  español, los ejemplos se conservan en idioma original.
- **QualityScorer**: score 0–1 de densidad de contenido útil (obligatorio).
- **FormatNormalizer**: salida `.txt` + cabecera YAML (`fuente`, `autor`,
  `idioma_original`, `idioma_fragmento`, `subtipo`, `tipo_fuente`, `licencia`).
- **Storage**: `/data/processed/v{N}/` es la fuente de verdad **inmutable**
  (`manifest.json` nunca se sobrescribe). Es el único flujo con versionado
  `v{N}` de corpus completo. Un paso de ingesta separado vuelca copia
  indexable a Supabase — el fichero `v{N}` sigue siendo la verdad.
- **Stack**: `pytesseract`+`pdf2image`, `ebooklib`, `python-docx`, `pymupdf`,
  `langdetect`, `deep-translator`, `APScheduler`, `google-api-python-client`.

### Flujo B — Chistes propios / Telegram (`src/jokes/telegram/`)
Ingesta en **tiempo real**, chiste a chiste, vía bot de Telegram.
Arquitectura **Bronze → Silver**.

```
TelegramBot → Bronze (raw, sagrado) → Pre-limpieza mínima (no destructiva)
  → Silver (LLM) → Reconciliación → Supabase (tipo_fuente='propio')
```

- **Bronze**: cada mensaje se persiste literal con metadata de origen
  (`telegram_update_id`, `chat_id`, `timestamp`). Inmutable, nunca se
  reescribe (equivalente a `/data/raw/` de teoría).
- Idempotencia **por evento** (`telegram_update_id`, `ON CONFLICT DO
  NOTHING`) — no por documento.
- **Pre-limpieza mínima**: solo trim, normalización unicode, strip de
  artefactos de plataforma. El Cleaner agresivo de teoría **nunca** se
  aplica a chistes (`propio*`) — perdería el timing del remate.
- A partir de aquí entra en el **contrato compartido** de `src/jokes/SPEC.md`
  (Silver + Reconciliación, ver §3).
- Sin `v{N}`: los chistes son un store vivo en Supabase, no snapshots.

### Flujo C — Chistes históricos (`src/jokes/historico/` + `scripts/marcar_remates.py`)
Procesado **batch retroactivo** de textos propios ya escritos, con varios
chistes por documento.

```
scripts/marcar_remates.py (automático, previo, desacoplado):
  .docx/Google Docs con estilos → detecta color de fuente por *run* en el XML
    #FF0000 (rojo puro) → [REMATE]...[/REMATE]   (cierra el chiste, frontera)
    #980000 (burdeos)   → [CHISTOIDE]...[/CHISTOIDE] (mini-remate interno, NO es frontera)
  → .md con marcadores embebidos

HistLoader (loader.py, idempotencia por hash MD5 de documento)
  → Segmentador (segmentador.py):
       [REMATE] = fin determinista del chiste;
       LLM afina hacia atrás dónde empieza el setup (semántico, descarta intros);
       [CHISTOIDE] se ignora como frontera, se conserva como metadato de estructura
  → Silver (mismo que Flujo B) → Reconciliación → Supabase (tipo_fuente='propio_historico')
```

- **marcar_remates.py** es un script **automático y determinista** (P15,
  2026-07-06), previo y desacoplado de la orquestación del pipeline. Parte
  del `.docx`/Google Docs original (no de un `.md` ya convertido) porque
  Markdown plano no conserva color. Clasificación **por tono con margen**
  (no igualdad exacta de hex). Runs contiguos del mismo color se fusionan;
  un span puede cruzar párrafos; las etiquetas no se solapan. Cobertura de
  parseo: párrafos, tablas, hyperlinks y listas. **Validación round-trip
  obligatoria** (nº de caracteres por color en `.docx` == nº de caracteres
  dentro de la etiqueta en `.md`; si no cuadra, falla el marcado).
  Prototipado primero en `notebooks/marcar_remates_colab.ipynb`.
- **Segmentador**: el criterio "dónde empieza el setup" es juicio semántico
  sin ancla externa verificable → **fuera de alcance de P16** (no lleva
  loop de reintento automático; control de calidad = revisión humana
  muestral).
- Idempotencia por **hash MD5 del documento** (no por evento, a diferencia
  del Flujo B).
- **Coste**: volumen relevante → dry-run de estimación de tokens, batching y
  gate de coste antes del run completo (detalle en `docs/specs/llm-policy.md`).

## 3. Contrato compartido B/C (`src/jokes/`: `silver.py`, `reconciliacion.py`, `supabase_store.py`)

Telegram (B) e Histórico (C) comparten el mismo código para todo lo que pasa
**después** de la entrada específica de cada flujo, porque ambos tratan la
misma unidad (`propio*`). Especificado **una sola vez** en `src/jokes/SPEC.md`
— `telegram/SPEC.md` e `historico/SPEC.md` remiten ahí, no lo repiten.

### Silver (`silver.py`) — estructuración por LLM
LLM barato tipo Haiku vía API produce, por chiste:

| Campo | Descripción |
|---|---|
| `tema` | → mapea a `tema_id` (taxonomía) |
| `estructura_detectada` | setup/punchline, callback, misdirection… → `tecnica_id` |
| `estado` | `idea_suelta \| con_estructura \| rematado` |
| `sugerencias_mejora` | generativo, va a revisión humana, nunca se reintenta en loop |
| `chiste_normalizado` | reescritura que conserva el timing, NO elimina muletillas |

### Taxonomías (temas, técnicas, fuentes)
Tablas relacionales editables en Supabase (`temas`, `tecnicas`, `fuentes`) son
la fuente de verdad. El LLM mapea a IDs existentes; si no encuentra match,
**loop acotado ≤3 intentos** (P16) inyectando la taxonomía real como contexto
— criterio de parada binario y externo (¿el ID existe en la tabla?). Agotados
los intentos, se encola en `candidatos_taxonomia` para revisión humana. El
LLM **nunca** crea taxonomía autónomamente.

### Reconciliación y deduplicación (`reconciliacion.py`)
Mecanismo híbrido hash + embedding, decide por cada chiste entrante:

```
hash(texto_normalizado) coincide       → IGUAL    → dedup (no inserta)
similitud embedding ∈ [~0.85, 1)        → CAMBIADO → nueva revisión del existente
similitud embedding < ~0.85             → NUEVO    → inserta chiste nuevo
```

Umbrales indicativos/afinables. Ya cumple el criterio de parada verificable
de P16 sin cambios (hash/umbral son externos al LLM).

### Versionado por chiste
No hay `v{N}` de corpus para chistes. Dos mecanismos distintos:
- **Madurez** (mismo chiste evoluciona en el tiempo): `chistes_revisiones`
  (append-only), `chistes.version_actual` apunta a la vigente.
- **Reutilización** (variante que reaprovecha una premisa/remate de otro):
  `chistes.chiste_origen_id` enlaza con el ancestro — es un chiste distinto.

### Esquema Supabase (boceto)
Topología híbrida: chistes viven nativos en Supabase; `pgvector` es el
índice único de consulta del RAG (compartido con teoría), toda consulta
filtra por `tipo_fuente`. "Grafo ligero" relacional (columnas FK), **no**
GraphRAG/Leiden clustering (descartado por falta de volumen).

Tablas: `chistes`, `chistes_revisiones`, `temas`, `tecnicas`, `fuentes`,
`candidatos_taxonomia`, `teoria_chunks` (ingesta de teoría). Ver
`src/jokes/SPEC.md` §Storage para el DDL completo.

## 4. `tipo_fuente` — el contrato transversal

Enum cerrado (cambiarlo es cambio de spec, no algo que decida el LLM):

| `tipo_fuente` | Qué es | Trigger | Storage | Limpieza | Idioma | Licencia default |
|---|---|---|---|---|---|---|
| `teoria` | Libros de comedia | Batch (Drive) | Ficheros `v{N}` + índice | Agresiva determinista | Traducido a español | `personal_only` |
| `transcripcion_curso` | Cursos transcritos (WhisperX) | Batch (Drive) | Ficheros `v{N}` + índice | Agresiva determinista | Traducido a español | `personal_only` |
| `propio` | Chistes en tiempo real | Realtime (Telegram) | Supabase | Mínima + LLM (preserva timing) | Original, sin traducir | `comercializable` |
| `propio_historico` | Textos propios ya escritos | Batch retroactivo | Supabase | Mínima + LLM (preserva timing) | Original, sin traducir | `comercializable` |

Agrupaciones (predicados, no valores del enum): `externo*` = `{teoria,
transcripcion_curso}`; `propio*` = `{propio, propio_historico}`.

`licencia` es independiente de `tipo_fuente` (ej. libro de dominio público
es `teoria` pero `comercializable`). **Sin enforcement todavía** — campo de
metadata con default seguro, reactivable si se comercializa.

## 5. Regla de oro: material sagrado

`/data/raw/` (teoría) y la capa **Bronze** (chistes) son material original
**sagrado**: nunca se modifican, eliminan ni sobrescriben. Todo el trabajo
ocurre aguas abajo. El corpus **no se versiona en git** (`data/` en
`.gitignore` — copyright, tamaño, privacidad).

## 6. Regla de dependencias entre módulos

`theory/` y `jokes/` **no se importan entre sí**. Código común →
`src/utils/`. En la práctica hoy: `language_detector.py`/`quality_scorer.py`
son consumo exclusivo de teoría; `llm/client.py`/`llm/embeddings.py` son
consumo exclusivo de chistes (B/C) — `utils/` es compartida en ubicación, no
necesariamente en uso actual.

## 7. Política de LLM y loops (P16)

- Regla general: **no LLM / no APIs de pago** para `externo*` (teoría) —
  determinista, coste 0.
- Excepción acotada: **Silver** de chistes (imposible sin generación) y
  embeddings de reconciliación/retrieval usan un LLM barato vía API (tipo
  Haiku). Sin LLM local (no hay GPU).
- **Criterio de parada de loops LLM (P16)**: solo se permite reintento
  automático si el criterio de parada es **verificable externamente**
  (binario, no "el LLM opina que ya está bien"):
  - ✅ Apto para loop acotado (≤3 intentos): mapeo de taxonomía (¿el ID
    existe en Supabase?), reconciliación (¿hash/similitud superan umbral?).
  - ❌ No apto, va a revisión humana: dónde empieza semánticamente un setup
    (Segmentador, Flujo C), si un `chiste_normalizado`/`sugerencias_mejora`
    "queda bien" (Silver) — no hay ancla externa, iterar solo refuerza la
    primera opinión del LLM.

## 8. Estructura de carpetas

```
CLAUDE.md                         # instrucciones operativas para Claude Code
README.md                         # entrada humana, con diagrama de arquitectura
ROADMAP_DATA_PIPELINE.md          # roadmap de Fase 0
requirements.txt / .env.example

docs/
├── specs/
│   ├── 00-overview.md            # punto de entrada de la spec, tipo_fuente, layout global
│   ├── llm-policy.md             # coste/LLM/copyright + P16 (loops)
│   ├── KNOWN_ERRORS_GLOBAL.md    # errores que cruzan módulos
│   └── comedy-corpus-pipeline.md # [movido/histórico] spec original pre-split
├── assets/architecture.svg       # diagrama visual del pipeline
├── reference/whisperx_transcribe_colab.py  # transcripción vídeo→texto (Colab GPU, fuera del pipeline)
└── CORPUS_INVENTORY.md           # inventario del corpus

src/
├── utils/                        # COMPARTIDO — SPEC.md, KNOWN_ERRORS.md
│   ├── language_detector.py
│   ├── quality_scorer.py
│   └── llm/{client.py, embeddings.py}
├── theory/                       # Flujo A — SPEC.md, KNOWN_ERRORS.md
│   ├── drive_monitor.py
│   ├── pipeline.py
│   ├── parsers/{whisperx,pdf,epub,docx}_parser.py
│   ├── cleaners/transcript_cleaner.py
│   ├── detectors/subtype_detector.py
│   ├── enrichers/metadata_tagger.py
│   └── normalizers/{format,language}_normalizer.py
└── jokes/                        # contrato compartido B/C — SPEC.md, KNOWN_ERRORS.md
    ├── silver.py
    ├── reconciliacion.py
    ├── supabase_store.py
    ├── telegram/                 # Flujo B — SPEC.md, KNOWN_ERRORS.md
    │   └── telegram_bot.py
    └── historico/                # Flujo C — SPEC.md, KNOWN_ERRORS.md
        ├── loader.py
        └── segmentador.py

scripts/
├── run_pipeline.py                # orquesta Flujo A
├── run_historico.py               # orquesta Flujo C
├── marcar_remates.py              # preprocesado automático por color (Flujo C)
├── validate_corpus.py             # correr antes de cada commit
└── stats_report.py

notebooks/marcar_remates_colab.ipynb  # prototipo del marcado por color

tests/
├── unit/{jokes/, theory/}
├── integration/
└── fixtures/                      # fixtures reales, nunca inventadas

data/                              # NO versionado en git (.gitignore)
├── raw/{books, notes, transcriptions/{Demy,Pinol,Tomas}}   # sagrado
├── processed/                      # salida Flujo A, v{N}
└── state/                          # ficheros de idempotencia (processed_files.json, etc.)
```

## 9. Stack tecnológico

- **Teoría (coste 0)**: `pytesseract` + `pdf2image` (OCR), `ebooklib`
  (EPUB), `python-docx` (DOCX), `pymupdf` (PDF), `langdetect`,
  `deep-translator`, `APScheduler`, `google-api-python-client`.
- **Chistes**: Supabase (Postgres + `pgvector`), `python-telegram-bot`,
  cliente LLM vía API (modelo barato), cliente de embeddings.
- **Transcripción vídeo→texto**: WhisperX en Google Colab con GPU — paso
  previo de captación, fuera del pipeline determinista.

## 10. Metodología de trabajo (SDD + TDD)

1. Leer el `SPEC.md` del módulo a tocar (tabla de routing en `CLAUDE.md`)
   antes de implementar.
2. Tests primero, con fixtures reales de `/tests/fixtures/` (nunca
   inventadas).
3. `pytest tests/unit/ -v` y `pytest tests/integration/ -v`;
   `python scripts/validate_corpus.py` antes de cada commit.
4. Antes de depurar por prueba y error, mirar primero el `KNOWN_ERRORS.md`
   del módulo (o `docs/specs/KNOWN_ERRORS_GLOBAL.md` si el error cruza
   módulos). Al resolver un error nuevo, documentarlo ahí.

## 11. Fuera de alcance (decisiones cerradas)

- GraphRAG / clustering tipo Leiden — descartado por falta de volumen; se
  usa un "grafo ligero" relacional (columnas FK) en su lugar.
- Fine-tuning — se usa RAG multi-fuente en su lugar.
