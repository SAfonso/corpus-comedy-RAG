# Comedy Corpus Pipeline — Overview y directriz de lectura

> **Estado:** v2 (multi-fuente) · **Metodología:** SDD — spec aprobada ANTES de
> escribir código. **Documento vivo:** decisiones P1–P14, más P15 (marcado
> histórico por color, 2026-07-06), P16 (loops LLM, 2026-07-09), P17
> (markitdown para Parser de teoría, 2026-07-21), P18 (DriveMonitor sobre
> carpeta local, Drive API real diferida, 2026-07-22) y P20 (candidatos de
> reconciliación filtrados por `tipo_fuente`, 2026-07-24). P19 queda reservado
> a una tarea concurrente del Flujo C (lectura desde carpeta Drive real).

Este documento es el **punto de entrada**. La spec completa ya no vive en un solo
fichero: está partida por módulo, colocada **dentro de `src/`**, junto al código
que gobierna cada una. Lee solo lo que tu tarea necesita.

## Directriz: qué leer según qué vas a tocar

| Si vas a tocar...                                                 | Spec | Errores conocidos |
|--------------------------------------------------------------------|--------|----------------------|
| `src/theory/**` (Flujo A, teoría)                                  | [`src/theory/SPEC.md`](../../src/theory/SPEC.md) + [`llm-policy.md`](llm-policy.md) (regla "no LLM") | [`src/theory/KNOWN_ERRORS.md`](../../src/theory/KNOWN_ERRORS.md) |
| `src/jokes/telegram/**` (Flujo B, Telegram)                        | [`src/jokes/telegram/SPEC.md`](../../src/jokes/telegram/SPEC.md) + [`src/jokes/SPEC.md`](../../src/jokes/SPEC.md) + [`llm-policy.md`](llm-policy.md) | [`src/jokes/telegram/KNOWN_ERRORS.md`](../../src/jokes/telegram/KNOWN_ERRORS.md) |
| `src/jokes/silver.py`, `reconciliacion.py`, `supabase_store.py` (compartido B/C) | [`src/jokes/SPEC.md`](../../src/jokes/SPEC.md) + [`llm-policy.md`](llm-policy.md) | [`src/jokes/KNOWN_ERRORS.md`](../../src/jokes/KNOWN_ERRORS.md) |
| `src/jokes/historico/**`, `scripts/marcar_remates.py` (Flujo C)    | [`src/jokes/historico/SPEC.md`](../../src/jokes/historico/SPEC.md) + [`src/jokes/SPEC.md`](../../src/jokes/SPEC.md) | [`src/jokes/historico/KNOWN_ERRORS.md`](../../src/jokes/historico/KNOWN_ERRORS.md) |
| `src/utils/**`                                                     | [`src/utils/SPEC.md`](../../src/utils/SPEC.md) (apunta al consumidor real) | [`src/utils/KNOWN_ERRORS.md`](../../src/utils/KNOWN_ERRORS.md) |
| Nueva fuente, `tipo_fuente`, layout global, regla de dependencias entre módulos | Este documento | [`KNOWN_ERRORS_GLOBAL.md`](KNOWN_ERRORS_GLOBAL.md) |

**No hace falta leer los cinco specs para tocar uno.** `src/jokes/telegram/SPEC.md`
e `historico/SPEC.md` remiten a `src/jokes/SPEC.md` para lo que comparten
(Silver, Reconciliación, Taxonomías) en vez de repetirlo — si tu tarea es
puramente de un flujo, con su spec + el compartido basta.

**Protocolo de errores conocidos:** antes de depurar un error por prueba y
error, busca primero en el `KNOWN_ERRORS.md` del módulo (tabla de arriba). Si
ya ocurrió, aplica la solución documentada. Si es nuevo, documéntalo ahí (o en
`KNOWN_ERRORS_GLOBAL.md` si cruza módulos) al resolverlo — regla completa en
`CLAUDE.md`.

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
  en `/data/processed/v{N}/`. Spec: `src/theory/SPEC.md`.
- **Flujo B — Chistes propios (Telegram):** ingesta en tiempo real, chiste a
  chiste, vía bot de Telegram. Arquitectura Bronze→Silver, estructuración por LLM.
  Spec: `src/jokes/telegram/SPEC.md` + `src/jokes/SPEC.md`.
- **Flujo C — Chistes históricos:** procesado retroactivo (batch) de textos
  propios ya escritos, con varios chistes por documento y remates marcados.
  Spec: `src/jokes/historico/SPEC.md` + `src/jokes/SPEC.md`.

**Regla invariante de todos los flujos:** el material original es sagrado
(`/data/raw/` para teoría, la capa Bronze para chistes). Nunca se modifica,
elimina ni sobrescribe. Todo el trabajo ocurre aguas abajo.

**Fuera de alcance (por ahora):**
- Grafo de conocimiento tipo GraphRAG / Leiden clustering — descartado por
  falta de volumen; se sustituye por un "grafo ligero" relacional
  (ver `src/jokes/SPEC.md`).
- Fine-tuning — se usa RAG multi-fuente en su lugar (decisión cerrada).

## 2. Fuentes y `tipo_fuente`

`tipo_fuente` es el discriminador que atraviesa todo el pipeline y el RAG. Es un
**enum cerrado** (valores fijos; añadir uno es un cambio de spec, no algo que el
LLM decida):

| `tipo_fuente`         | Qué es                          | Trigger             | Storage destino        | Limpieza            |
|-----------------------|----------------------------------|----------------------|--------------------------|----------------------|
| `teoria`              | Libros de comedia               | Batch (Drive)       | Ficheros `v{N}` + índice | Agresiva (determ.) |
| `transcripcion_curso` | Cursos transcritos (WhisperX)   | Batch (Drive)       | Ficheros `v{N}` + índice | Agresiva (determ.) |
| `propio`              | Chistes propios en tiempo real  | Realtime (Telegram) | Supabase               | Propia (Bronze/Silver) |
| `propio_historico`    | Textos propios ya escritos      | Batch retroactivo   | Supabase               | Propia (Bronze/Silver) |

**Contrato con el RAG:** toda unidad indexada lleva `tipo_fuente`. El RAG hace
retrieval separado por fuente y combina en el prompt. Nunca se mezcla el origen
de forma implícita.

**Agrupaciones útiles** (no son valores del enum, son predicados):
- `propio*` = `{propio, propio_historico}` — comparten Silver, versionado por
  chiste y reconciliación (`src/jokes/SPEC.md`).
- `externo*` = `{teoria, transcripcion_curso}` — comparten limpieza agresiva,
  traducción y salida a ficheros (Flujo A).

**Licencia por defecto** (ver `llm-policy.md`, sin enforcement aún):
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

**Layout del repo** (la separación es estructural, no solo de carpetas; cada
carpeta con lógica propia trae su `SPEC.md`):

```
src/
├── utils/                 # COMPARTIDO entre flujos — SPEC.md
│   ├── language_detector.py
│   ├── quality_scorer.py
│   └── llm/                # cliente LLM (Silver) y embeddings
├── theory/                 # Flujo A — SPEC.md
│   ├── drive_monitor.py
│   ├── parsers/             # whisperx, pdf, epub, docx
│   ├── cleaners/
│   ├── normalizers/
│   └── pipeline.py
└── jokes/                  # Flujos B y C — SPEC.md (contrato compartido)
    ├── telegram/             # Flujo B (realtime) — SPEC.md
    │   └── telegram_bot.py
    ├── historico/            # Flujo C (batch) — SPEC.md
    │   ├── loader.py
    │   └── segmentador.py
    ├── silver.py              # Silver LLM (compartido B/C)
    ├── reconciliacion.py      # hash + embedding (compartido B/C)
    └── supabase_store.py

scripts/
├── run_pipeline.py           # Flujo A (teoría)
├── run_historico.py          # Flujo C (batch)
├── marcar_remates.py         # preprocesado automático por color (ver historico/SPEC.md)
├── validate_corpus.py
└── stats_report.py

docs/specs/
├── 00-overview.md            # este documento
└── llm-policy.md             # coste/LLM/copyright + P16 (cross-cutting)
```

**Regla de dependencias:** `theory/` y `jokes/` no se importan entre sí. Todo
código común vive en `utils/`. El Silver y la Reconciliación se comparten entre
los flujos B y C porque tratan la misma unidad (`propio*`), pero no con teoría
— por eso viven en `src/jokes/SPEC.md` (el nivel compartido), no duplicados en
`telegram/SPEC.md` e `historico/SPEC.md`.

## Idempotencia y versionado — comparativa entre flujos

| Flujo            | Idempotencia                              | Versionado             | Detalle |
|-------------------|---------------------------------------------|--------------------------|-----------|
| A — Teoría        | `processed_files.json` (hash MD5)         | `v{N}` inmutable         | `src/theory/SPEC.md` |
| B — Telegram      | Por evento (`telegram_update_id`)         | Por chiste                | `src/jokes/telegram/SPEC.md` |
| C — Histórico     | Hash MD5 del documento + reconciliación de chiste | Por chiste        | `src/jokes/historico/SPEC.md` |

- **`v{N}` inmutable aplica SOLO a teoría.** Su `manifest.json` es inmutable una
  vez generado; nunca se sobrescribe una versión.
- **Reanudación:** si cualquier flujo falla a mitad, retoma desde el último ítem
  no completado (fichero/documento/evento), sin reprocesar lo ya hecho.

**P20 (2026-07-24) — Candidatos de reconciliación filtrados por `tipo_fuente`.**
`reconciliacion.py` (task 15) es agnóstico de Supabase: recibe `candidatos`
como argumento. El método que los obtiene vive en
`SupabaseStore.listar_candidatos_reconciliacion(tipo_fuente)` (spec en
`src/jokes/SPEC.md` §Reconciliación; implementación en task 25). Devuelve
`list[dict]` con exactamente `id`/`hash_normalizado`/`embedding` (las tres
claves que `decidir_reconciliacion` consume), una entrada por fila de `chistes`
del alcance de `tipo_fuente` pedido — sin filtro de versión (cada fila ya es el
contenido vigente; las revisiones viven en `chistes_revisiones`) y **con**
variantes (`chiste_origen_id`, chistes distintos). Se decidió traer todas las
filas del `tipo_fuente` y comparar en Python (hash → coseno) en vez de una query
ANN nativa de pgvector: la ANN necesitaría el embedding entrante en el momento
del fetch, pero `reconciliar_chiste` lo calcula **después** de obtener los
candidatos (task 15, no se toca), y el volumen del corpus es bajo (GraphRAG
descartado por lo mismo, §1). La ANN queda como optimización futura compatible
con la interfaz (el método puede hacer el trabajo en SQL y seguir devolviendo
`list[dict]`).

## Metodología SDD + TDD (aplica a todo el proyecto)

1. Leer el spec del módulo que vayas a tocar (ver directriz arriba) antes de implementar.
2. Tests primero, con fixtures **reales** de `/tests/fixtures/` (nunca inventados).
3. `pytest tests/unit/ -v` y `tests/integration/ -v`; `validate_corpus.py` antes
   de commit.
