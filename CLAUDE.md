# Comedy Corpus Pipeline — Claude Code Instructions

## Fuente de verdad
La especificación completa está en **`/docs/specs/comedy-corpus-pipeline.md`** (v2 multi-fuente).
Léela antes de implementar. Este CLAUDE.md es la guía operativa resumida; ante
cualquier discrepancia, manda la spec.

## Propósito
Pipeline de ingesta, limpieza, estructuración y versionado de datos para el Comedy RAG.
Corpus **multi-fuente**: cada unidad lleva `tipo_fuente` para permitir retrieval
separado por origen en el RAG downstream.

Tres flujos (ver §3–§6 de la spec):
- **Flujo A — Teoría** (`src/theory/`): libros/cursos desde Drive, batch, determinista → ficheros `/data/processed/v{N}/`.
- **Flujo B — Chistes propios** (`src/jokes/`): Telegram, tiempo real, Bronze→Silver (LLM) → Supabase.
- **Flujo C — Chistes históricos** (`src/jokes/historico/`): batch retroactivo de textos propios → Supabase.

## Regla más importante
El material original es SAGRADO: `/data/raw/` (teoría) y la capa Bronze (chistes).
Nunca modificar, eliminar ni sobrescribir. Todo el trabajo ocurre aguas abajo.

## `tipo_fuente` (enum cerrado)
`teoria | transcripcion_curso | propio | propio_historico`
- `externo*` = {teoria, transcripcion_curso} → limpieza agresiva, ficheros `v{N}`.
- `propio*`  = {propio, propio_historico}     → Bronze/Silver, Supabase, versión por chiste.

## Layout del repo
```
src/utils/     # COMPARTIDO: language_detector, quality_scorer, llm/ (cliente + embeddings)
src/theory/    # Flujo A: drive_monitor, parsers/, cleaners/, detectors/, normalizers/, pipeline.py
src/jokes/     # Flujos B/C: telegram_bot, silver, reconciliacion, supabase_store, historico/
```
Regla de dependencias: `theory/` y `jokes/` NO se importan entre sí. Lo común va a `utils/`.

## Arquitectura del Flujo A (teoría)
DriveMonitor → Parser → SubtypeDetector → Cleaner → LanguageDetector → LanguageNormalizer → QualityScorer → FormatNormalizer → /data/processed/v{N}/ → (ingesta) → Supabase (índice)

El orden importa: SubtypeDetector ejecuta ANTES que el Cleaner, porque los
fragmentos "ejemplo" tienen reglas de limpieza distintas.

## Arquitectura de los Flujos B/C (chistes)
- **Bronze**: raw literal, sagrado. Telegram dedup por `telegram_update_id`.
- **Silver (LLM)**: produce `tema`, `estructura_detectada`, `estado`, `sugerencias_mejora`, `chiste_normalizado`.
- **Reconciliación**: hash exacto + similitud embedding → IGUAL (dedup) / CAMBIADO (revisión) / NUEVO.
- **Histórico**: parte de `.md` con marcadores `[REMATE]…[/REMATE]` y `[CHISTOIDE]…[/CHISTOIDE]`, generados por `scripts/marcar_remates.py` (automático, **por color**: `#FF0000`→REMATE, `#980000`→CHISTOIDE). Segmentador = `[REMATE]` como fin + LLM afina el inicio; `[CHISTOIDE]` NO es frontera (mini-remate interno), se conserva como metadato. Ver §7 de la spec.

## Metodología: SDD + TDD
1. Lee la spec relevante en /docs/specs/ antes de implementar.
2. Escribe tests primero con fixtures reales de /tests/fixtures/ (nunca inventados).
3. Tests: `pytest tests/unit/ -v`, `pytest tests/integration/ -v`; antes de commit `python scripts/validate_corpus.py`.

## Parsers (teoría): una función = un tipo de fuente
Nunca mezcles lógica de parsing de distintas fuentes. Código común → `src/utils/`.
- whisperx_parser.py → .txt con `[timestamp] SPEAKER_XX: texto`
- pdf_parser.py      → .pdf (probablemente escaneados → Tesseract OCR)
- epub_parser.py     → .epub
- docx_parser.py     → .docx

## WhisperX — contrato del parser
Formato de entrada: `[timestamp] SPEAKER_XX: texto`. El parser DEBE:
- Eliminar timestamps y speaker tags del texto.
- Conservar el speaker dominante como metadato del documento.
- Unir líneas del mismo speaker si son consecutivas.
- Preservar el contenido, nunca interpretarlo.

## Sobre la limpieza
- **`externo*` (teoría)**: limpieza AGRESIVA por defecto — eliminar muletillas
  ("o sea", "pues", "¿vale?", "bueno"...), repeticiones, corregir errores de
  transcripción obvios, separar en párrafos. Excepción: `subtipo=ejemplo`
  conserva el estilo oral (marcadores: "un ejemplo", "por ejemplo", "imaginad que"...).
- **`propio*` (chistes)**: el Cleaner agresivo NO aplica. Bronze raw +
  pre-limpieza mínima no destructiva; la normalización la hace el LLM en Silver
  conservando muletillas/timing si son parte del remate.

## Sobre el idioma
- Teoría: corpus bilingüe — teoría → traducir a español (DeepL free / LibreTranslate);
  ejemplos → conservar idioma original. RAG configurado para multiidioma.
- Chistes propios: conservar en idioma original, SIN traducir (el wordplay/timing no sobrevive).

## Metadatos (dos esquemas según destino)
- **Teoría** (YAML frontmatter): `fuente`, `autor`, `idioma_original`, `idioma_fragmento`,
  `subtipo` (explicacion|ejemplo), `tipo_fuente`, `licencia`.
- **Chistes** (columnas Supabase): `tipo_fuente`, `tema_id`, `tecnica_id`, `fuente_id`,
  `estado`, `version_actual`, `chiste_origen_id`, `licencia`.

## Idempotencia y versionado (por flujo)
- Teoría: `processed_files.json` (hash MD5). Versionado `v{N}` inmutable (solo teoría).
- Telegram: idempotencia por evento (`telegram_update_id`). Versión por chiste, sin `v{N}`.
- Histórico: hash MD5 de documento + reconciliación de chiste. Versión por chiste.
- El `manifest.json` de cada `v{N}` es inmutable. Si un flujo falla a mitad, retoma desde el último ítem no completado.

## Versionado por chiste
Sin `v{N}` de corpus para chistes. `chistes` (identidad + estado) +
`chistes_revisiones` (append-only, madurez) + `chiste_origen_id` (linaje de variante
que reutiliza premisa/remate).

## Taxonomías
`temas`, `tecnicas`, `fuentes` = tablas editables en Supabase (fuente de verdad).
El LLM mapea a IDs existentes; lo nuevo va a `candidatos_taxonomia` (revisión humana).
El LLM NO crea taxonomía por su cuenta.

## Stack
Teoría (coste cero): pytesseract + pdf2image (OCR), ebooklib (EPUB), python-docx (DOCX),
langdetect, deep-translator, APScheduler, google-api-python-client.
Chistes: Supabase (Postgres + pgvector), python-telegram-bot, cliente LLM vía API, embeddings.

**Regla de LLM:** NO usar LLMs para el procesado de teoría (determinista, coste cero).
**Excepción acotada:** el Silver de chistes SÍ usa un LLM barato vía API (imprescindible
para estructurar/generar). Ver §14 de la spec.

## Copyright
Restricción documentada (material de pago no redistribuible si se comercializa).
Sin enforcement por ahora: `licencia` es metadata con default seguro, sin lógica que la aplique.
