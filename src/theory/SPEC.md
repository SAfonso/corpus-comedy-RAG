# Flujo A — Teoría (Drive → ficheros)

> Spec de `src/theory/`. Ver también [`docs/specs/00-overview.md`](../../docs/specs/00-overview.md)
> (contexto general, `tipo_fuente`, layout) y
> [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md) (regla "no LLM" para este flujo).

Flujo batch, 100% determinista, sin LLM. Es el pipeline original; se conserva
intacto salvo por el nuevo paso de ingesta al índice (ver §Storage).

## Cadena de componentes (el orden importa)

```
DriveMonitor → Parser → SubtypeDetector → Cleaner → LanguageDetector
  → LanguageNormalizer → QualityScorer → FormatNormalizer → /data/processed/v{N}/
```

- **DriveMonitor:** vigila Google Drive periódicamente; procesa solo ficheros
  nuevos (idempotencia por hash MD5, ver §Idempotencia).
- **Parser:** una función por tipo de fuente. `whisperx_parser` (.txt con
  `[timestamp] SPEAKER_XX: texto`), `pdf_parser` (Tesseract OCR si escaneado),
  `epub_parser`, `docx_parser`. Código común → `src/utils/` (ver `src/utils/SPEC.md`).
- **SubtypeDetector:** clasifica cada fragmento en `explicacion | ejemplo`.
  Ejecuta ANTES del Cleaner porque los ejemplos tienen reglas distintas.
- **Cleaner:** limpieza AGRESIVA (ver §Limpieza) para `subtipo=explicacion`;
  conserva el estilo oral en `subtipo=ejemplo`.
- **LanguageDetector / LanguageNormalizer:** corpus bilingüe (ver §Idioma):
  teoría se traduce a español, los ejemplos se conservan en idioma original.
- **QualityScorer:** puntuación 0–1 de densidad de contenido útil. Obligatorio.
- **FormatNormalizer:** salida uniforme `.txt` + cabecera YAML (ver §Metadatos).

**WhisperX — contrato del parser:** elimina timestamps y speaker tags del texto;
conserva el speaker dominante como metadato del documento; une líneas
consecutivas del mismo speaker; preserva el contenido, nunca lo interpreta.

## Storage

- `/data/processed/v{N}/` = fuente de verdad y entregable inmutable de teoría.
  Un `.txt` por documento en `/data/processed/v{N}/documents/`, más
  `manifest.json` (índice inmutable) y `stats.json`.
- Un paso de **ingesta** vuelca la teoría a Supabase (tabla `teoria_chunks`,
  ver `src/jokes/SPEC.md` §Esquema de tablas) como copia indexable — **no** es
  la fuente de verdad, esa sigue siendo el fichero `v{N}`.
- `pgvector` es el índice único de consulta del RAG (compartido con los
  chistes); toda consulta filtra por `tipo_fuente`.

## Limpieza

`externo*` (`teoria`, `transcripcion_curso`): AGRESIVA por defecto (elimina
muletillas, repeticiones, corrige errores obvios, separa en párrafos).
Excepción: `subtipo=ejemplo` conserva el estilo oral.

## Idioma

Corpus bilingüe explícito — teoría se traduce a español, los ejemplos se
conservan en idioma original. RAG configurado para multiidioma.

## Metadatos

YAML frontmatter en el `.txt`: `fuente`, `autor`, `idioma_original`,
`idioma_fragmento`, `subtipo`, + `tipo_fuente`, `licencia`.

## Idempotencia y versionado

`processed_files.json` (hash MD5) para idempotencia de fichero de origen.
Versionado `v{N}` **inmutable**: su `manifest.json` es inmutable una vez
generado, nunca se sobrescribe una versión. Es el único flujo con `v{N}` de
corpus (los chistes son un store vivo, sin snapshots — ver `src/jokes/SPEC.md`).

**Reanudación:** si el flujo falla a mitad, retoma desde el último fichero no
completado, sin reprocesar lo ya hecho.

## Stack

`pytesseract` + `pdf2image` (OCR), `ebooklib` (EPUB), `python-docx` (DOCX),
`langdetect`, `deep-translator` (DeepL free / LibreTranslate), `APScheduler`,
`google-api-python-client`. Coste cero. **Sin LLM** — ver
[`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md).

## Riesgos propios de este flujo

| Riesgo | Mitigación |
|--------|-----------|
| PDFs escaneados con OCR de baja calidad | Tesseract + revisión de muestra; API externa solo si es inaceptable |
| Traducción automática de teoría de baja calidad | Solo traducir teoría, conservar ejemplos en original; revisar muestra |
| Corpus real más pequeño de lo esperado post-limpieza | Medir con `scripts/stats_report.py` antes de comprometerse |
