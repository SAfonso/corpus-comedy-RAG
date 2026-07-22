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

- **DriveMonitor:** vigila una carpeta de origen y procesa solo ficheros
  nuevos (idempotencia por hash MD5, ver §Idempotencia). **De momento apunta a
  `data/raw/books/`/`data/raw/notes/` local, no a la API de Drive real — ver
  §DriveMonitor abajo (P18).**
- **Parser:** una función por tipo de fuente. `whisperx_parser` (.txt con
  `[timestamp] SPEAKER_XX: texto`), `pdf_parser`/`docx_parser` (markitdown,
  con Tesseract OCR de fallback si está escaneado — ver §Parser abajo),
  `epub_parser`. Código común → `src/utils/` (ver `src/utils/SPEC.md`).
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

## Parser — decisión markitdown (P17, 2026-07-21)

`pdf_parser` y `docx_parser` usan [`markitdown`](https://github.com/microsoft/markitdown)
(Microsoft) para convertir el original a Markdown, en vez de extracción manual
con `pymupdf`/`python-docx`. `epub_parser` migra igual si markitdown cubre
EPUB con calidad suficiente; si no, se mantiene `ebooklib`.

- **Dónde ocurre:** DESPUÉS de Bronze — el original en `/data/raw/` es
  intocable (sagrado). markitdown sustituye la implementación **interna** del
  Parser, no el material de entrada: la cadena sigue siendo
  `DriveMonitor → Parser (fichero original sagrado → texto) → SubtypeDetector → ...`.
- **Por qué:** la salida Markdown (headings, listas, tablas) es más legible
  que un `.txt` plano para quien mantiene y depura el pipeline — incluido el
  propio agente de código — y evita reimplementar a mano la extracción de
  estructura del documento.
- **Lo que NO cambia:** markitdown solo re-extrae texto ya presente en el
  fichero (PDFs con texto nativo, DOCX). Para los PDFs realmente escaneados
  (riesgo pendiente de verificar, ver `docs/CORPUS_INVENTORY.md`) sigue
  haciendo falta OCR: Tesseract se mantiene como *fallback*, invocado cuando
  markitdown no extrae texto útil de una página.
- **`whisperx_parser` no cambia:** su entrada ya es `.txt` plano generado en
  Colab, no un formato que markitdown convierta.
- **Sin LLM:** se usa solo el modo de conversión determinista de markitdown;
  su plugin opcional de *captioning* de imágenes vía LLM no se activa —
  mantiene la regla "sin LLM" de teoría (ver `docs/specs/llm-policy.md`).

## DriveMonitor — alcance diferido (P18, 2026-07-22)

La integración real con la API de Google Drive (`GOOGLE_APPLICATION_CREDENTIALS`,
`DRIVE_FOLDER_ID`) **se difiere**. De momento `DriveMonitor` vigila e itera
sobre `data/raw/books/` y `data/raw/notes/` como si ya fueran la carpeta
sincronizada — mismo contrato de idempotencia por hash MD5
(`processed_files.json`), mismo resto de la cadena sin cambios.

- **Por qué:** los libros ya están descargados a mano en local; no hace falta
  la API de Drive para que el resto del Flujo A (Parser → ... →
  `FormatNormalizer`) funcione y se pueda probar de principio a fin.
- **Lo que cambia en la implementación:** `DriveMonitor` se implementa contra
  un `Path` de carpeta local en vez de contra `google-api-python-client`. La
  interfaz pública (qué entrega a `Parser`) no cambia, así que migrar a Drive
  real más adelante no debería tocar nada aguas abajo.
- **Reactivación futura:** cuando se decida activar Drive de verdad, se
  añaden `GOOGLE_APPLICATION_CREDENTIALS`/`DRIVE_FOLDER_ID` al `.env` y se
  cambia la fuente de `DriveMonitor` — el resto de la cadena queda intacto.

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

`markitdown` (conversión PDF/DOCX → Markdown, ver §Parser), `pytesseract` +
`pdf2image` (OCR *fallback* para páginas escaneadas), `ebooklib` (EPUB, si no
migra a markitdown), `langdetect`, `deep-translator` (DeepL free /
LibreTranslate), `APScheduler`, `google-api-python-client`. Coste cero.
**Sin LLM** — ver [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md).

## Riesgos propios de este flujo

| Riesgo | Mitigación |
|--------|-----------|
| PDFs escaneados con OCR de baja calidad | Tesseract + revisión de muestra; API externa solo si es inaceptable |
| Traducción automática de teoría de baja calidad | Solo traducir teoría, conservar ejemplos en original; revisar muestra |
| Corpus real más pequeño de lo esperado post-limpieza | Medir con `scripts/stats_report.py` antes de comprometerse |
