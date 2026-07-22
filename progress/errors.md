# Bitácora de errores del harness (leader/planner/implementer/reviewer)

> Registro de rechazos del reviewer y errores de proceso durante la ejecución
> del backlog (`feature_list.json`). Errores técnicos de código van en el
> `KNOWN_ERRORS.md` del módulo correspondiente, no aquí — esto es la bitácora
> del propio harness (rechazos, escaladas, cambios de scope).

## Formato de entrada

```
## Task <id> — <título corto>
**Fecha:** YYYY-MM-DD
**Rechazo #:** N (máx 3 antes de escalar)
**Motivo del reviewer:** criterio de CHECKPOINTS.md incumplido
**Acción del leader:** relanzar implementer | escalar a usuario
```

---

## Task 14 — taxonomias.py (mapeo con loop acotado P16)

**Fecha:** 2026-07-23
**Resultado:** APROBADA sin rechazos. `src/jokes/taxonomias.py`:
`resolver_taxonomia(texto, tipo, store, ...)` mapea `tema`/`estructura_detectada`
(strings libres de `silver.py`) a IDs reales de `temas`/`tecnicas`. Loop
acotado a 3 intentos: intento 1 sin contexto; intentos 2-3 inyectan la
taxonomía real completa de Supabase. Criterio de parada binario y externo
(`_encontrar_match`, función pura sin red — nunca decide el LLM). Agotados
los 3 intentos sin match, encola en `candidatos_taxonomia` vía
`supabase_store.py` (nunca crea fila de taxonomía directamente). Reusa
`src/utils/llm/client.py` y `supabase_store.py` sin duplicar lógica. Tests de
integración reales: siembran una fila real en `tecnicas` para el caso de
match y limpian después; fuerzan agotamiento para el caso de cola y limpian
el candidato creado — verificado por el leader que las tablas quedan a 0
filas residuales tras la corrida. 247/247 tests + 1 skip (DeepL) en verde,
sin regresión. PR #12 mergeado.

## Task 13 — silver.py (estructuración por LLM)

**Fecha:** 2026-07-23
**Resultado:** APROBADA sin rechazos. `src/jokes/silver.py`: dado el texto de
un chiste, produce `tema`/`estructura_detectada`/`estado` (enum estricto
`idea_suelta|con_estructura|rematado`)/`sugerencias_mejora`/`chiste_normalizado`
vía Gemini (SDK `google-genai`, salida JSON estructurada con `responseSchema`),
una sola llamada sin loop de reintento (P16). Cliente LLM genérico extraído a
`src/utils/llm/client.py` (compartido con la futura task 14) — ampliación de
scope documentada explícitamente en el PR, justificada por la regla "código
común → `src/utils/`". Limpia el marcado `[REMATE]`/`[CHISTOIDE]` de Histórico
antes del prompt. Test de integración real contra Gemini con el chiste real
de `Freskito-Informático.md` (verificado con ejemplo de respuesta real:
tema="Idiomas y Tinder", estado="rematado"). 216/216 tests + 1 skip (DeepL)
en verde — confirmado que los 2 tests de Supabase de la task 12 ya pasan en
verde (antes en skip) tras aplicar `schema.sql`. Verificado por el leader
(incluyendo grep de que no se importa `theory/`). PR #11 mergeado.

**Nota:** primer credencial de este bloque (Supabase/Telegram/Gemini)
verificada end-to-end también en producción real vía LLM — pgvector schema
fix aplicado en el commit previo (`4110a9b`) confirmado funcional.

## Task 12 — supabase_store.py + DDL

**Fecha:** 2026-07-23
**Resultado:** APROBADA sin rechazos. `src/jokes/schema.sql` (DDL idempotente,
`IF NOT EXISTS`, para aplicar a mano en el SQL Editor de Supabase — el
cliente `supabase-py` vía REST/PostgREST no puede ejecutar DDL) con las
tablas exactas del boceto de `src/jokes/SPEC.md` §Storage: `temas`,
`tecnicas`, `fuentes`, `chistes`, `chistes_revisiones`, `candidatos_taxonomia`
+ `teoria_chunks` (incluida por completitud, cliente fuera de scope hasta
task 21). `src/jokes/supabase_store.py`: CRUD vía `supabase-py`, lógica de
validación de payloads aislada y testeada sin red (38 tests). Test de
integración real contra el proyecto Supabase del usuario (conexión
verificada) — hace `skip` claro porque `schema.sql` aún no se ha aplicado
(error `PGRST205` de PostgREST, documentado como hallazgo nuevo en
`src/jokes/KNOWN_ERRORS.md`, creado en esta tarea). 181/181 tests + 3 skips
esperados (1 DeepL + 2 Supabase-sin-tablas) en verde, sin regresión.
Verificado por el leader. PR #10 mergeado.

**Pendiente de acción del usuario:** aplicar `src/jokes/schema.sql` en el SQL
Editor de Supabase para que las tasks 13-16/19/21 puedan correr integración
real contra las tablas en vez de skip.

## Task 20 — scripts/validate_corpus.py

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. Implementa los 8 checks de
`ROADMAP_DATA_PIPELINE.md` (§Checks de Validación del Corpus Final): sin
timestamps, sin speaker tags, cabecera completa (7 campos), min/max palabras
(100/50.000), sin duplicados MD5, idiomas permitidos (es/en), manifest
sincronizado con los ficheros reales. Alcance limitado a Flujo A (Flujos B/C
aún bloqueados por credenciales — ver hallazgo de la task 9 y bloqueo abierto
más abajo). Cada check testeado en caso feliz y de fallo, todos derivados de
una estructura `v1/` real generada con la cadena completa aprobada
(parser→detector→cleaner→normalizer→format_normalizer), mutada para forzar
cada fallo — sin fixtures inventadas desde cero. Exit code 1 + mensaje claro
cuando no hay ninguna versión que validar (verificado manualmente contra el
repo real, que aún no tiene ningún `v{N}` generado). 143/143 tests + 1 skip
conocido (DeepL) en verde, sin regresión. Verificado por el leader
(incluyendo ejecución manual del script). PR #9 mergeado.

## Task 18 — loader.py (Flujo C)

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. `src/jokes/historico/loader.py`: clase
`Loader` con `folder`/`state_path` inyectados, hash MD5 en streaming del
documento completo (no evento, a diferencia del Flujo B), estado persistido
en JSON propio (sin importar `drive_monitor.py` de `theory/` — lógica
duplicada deliberadamente, ~10 líneas, para respetar la regla de no-import
entre `theory/`/`jokes/`). Segunda llamada sin cambios devuelve lista vacía
(idempotencia verificada). Test con fixture real (`Freskito-Informático.md`
copiado a `tmp_path`). 118/118 tests + 1 skip conocido (DeepL) en verde, sin
regresión. Verificado por el leader. PR #8 mergeado.

## Task 17 — scripts/marcar_remates.py

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. Migración fiel del prototipo ya
validado en `notebooks/marcar_remates_colab.ipynb`: lee `word/document.xml`
crudo (cubre párrafos/tablas/hyperlinks/listas), clasifica color por tono
(`#FF0000`→`REMATE`, `#980000`→`CHISTOIDE`), fusiona runs contiguos (incluso
cruzando párrafos), valida round-trip (caracteres alfanuméricos por color
== caracteres dentro de la etiqueta) y falla sin escribir `.md` si no cuadra.
Sin dependencias nuevas (solo stdlib). Salida **byte-a-byte idéntica** al
`Freskito-Informático.md` real de referencia. Round-trip testeado en caso
feliz y de fallo (runs perdidos simulados sobre datos derivados del `.docx`
real). Se aprovechó para comitear por fin los fixtures `Freskito-Informático.*`
(quedaron sin comitear desde la task 6). Gap documentado: fixture real sin
tablas/hyperlinks, cobertura resuelta estructuralmente pero sin verificación
end-to-end de ese caso. 111/111 tests + 1 skip conocido (DeepL) en verde, sin
regresión. Verificado por el leader: `git status` limpio tras el merge. PR #7
mergeado.

## Task 11 — FormatNormalizer + salida v{N} inmutable

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. `src/theory/normalizers/format_normalizer.py`:
YAML frontmatter por documento (`fuente`/`autor`/`tipo_fuente`/`licencia` a
nivel documento + lista `fragmentos` con `subtipo`/`idioma_original`/
`idioma_fragmento` por fragmento, en orden posicional con los párrafos del
cuerpo) — cubre los 7 campos de `CHECKPOINTS.md`. Emisor YAML propio (sin
añadir `pyyaml`, no estaba en requirements). `manifest.json` + `stats.json`
(usa `score_quality` de la task 10). Inmutabilidad `v{N}`: una versión con
`manifest.json` ya escrito nunca se sobrescribe — `generar_version()` sin
argumento crea automáticamente `v{N+1}`; con versión explícita ya finalizada,
lanza `VersionInmutableError`. Testeado con la cadena real completa
(whisperx_parser → subtype_detector → transcript_cleaner → language_normalizer)
sobre `tmp_path`, nunca sobre `/data/processed/` real. 84/84 tests + 1 skip
conocido (DeepL) en verde, sin regresión. Verificado por el leader: sin
residuos en `data/processed/` real. PR #6 mergeado.

## Task 10 — QualityScorer

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. `src/utils/quality_scorer.py`: media
ponderada de longitud (saturación suave, peso 0.4), diversidad léxica/TTR
(peso 0.3) y ratio de contenido vs. palabras funcionales (peso 0.3),
normalizada a [0,1]. Verificado con fragmentos reales del corpus (párrafo de
teoría ≈0.69 vs. frase de saludo corta ≈0.56; vacío/puntuación → 0.0 exacto).
65/65 tests + 1 skip conocido (DeepL) en verde, sin regresión. PR #5 mergeado.

## Task 9 — LanguageDetector + LanguageNormalizer

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. `src/utils/language_detector.py`
(`detect_language`, vía `langdetect`) + `src/theory/normalizers/language_normalizer.py`
(`normalize_language`, `_necesita_traduccion` como función pura sin red,
traductor inyectable). `subtipo=ejemplo` nunca se traduce; `subtipo=explicacion`
se traduce a español solo si el idioma detectado no es ya español. 53/53 unit
+ 4/4 integration (1 skip documentado) en verde. PR #4 mergeado.

**Hallazgo importante (no bloqueante para esta tarea, sí para producción):**
`deep_translator` 1.11.4 (última en PyPI) autentica contra DeepL con `auth_key`
por query param — DeepL deprecó ese método en noviembre 2025 y ahora exige
cabecera `Authorization: DeepL-Auth-Key`. La `DEEPL_API_KEY` de `.env` es
válida (confirmado con llamada `requests` directa), pero la librería falla con
`AuthorizationException`. Documentado en `src/theory/KNOWN_ERRORS.md`. La
lógica de decisión de `language_normalizer.py` está aislada y testeada sin
red, así que no bloquea el cierre de la task 9 — pero SÍ bloqueará la
traducción real de los libros en inglés del corpus hasta que se resuelva
(esperar parche de `deep_translator`, o cambiar a llamada HTTP directa con la
cabecera correcta — cambio de stack que requiere decisión explícita). Revisar
antes/durante la task 11 (FormatNormalizer + salida real del corpus).

## Task 8 — Cleaner agresivo (subtipo=explicacion)

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. `src/theory/cleaners/transcript_cleaner.py`:
elimina muletillas reales del fixture ("o sea", "bueno,", "¿vale?", "¿no?",
"digamos", "esto es", "eh", "básicamente,") y repeticiones consecutivas
(palabra/cláusula) solo para `subtipo=explicacion`; `subtipo=ejemplo` se
conserva carácter a carácter (excepción explícita de la spec). Añade
`agrupar_en_parrafos`. Determinista, sin LLM. **Gap de scope documentado**
(mismo patrón que el OCR de `pdf_parser`): "corrige errores obvios de
transcripción" queda fuera del MVP por no existir fixture real de errores
sobre el que construir la heurística sin inventarla. 37/37 tests en verde
(33 unit + 4 integration), sin regresión. Verificado de forma independiente
por el leader. PR #3 mergeado.

## Task 7 — SubtypeDetector (explicacion|ejemplo)

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos. `src/theory/detectors/subtype_detector.py`:
`FragmentoSubtipo(texto, subtipo)` + `detect_subtypes(texto) -> list[FragmentoSubtipo]`,
granularidad por frase, heurística de marcadores lingüísticos de
`docs/CORPUS_INVENTORY.md` Bloque 5 ("un ejemplo", "por ejemplo", "algo así
como", case-insensitive), 100% determinista sin LLM. Test contra
`tests/fixtures/sample_transcript.txt` (real, vía `parse_whisperx_transcript`).
28/28 tests en verde (24 unit + 4 integration), sin regresión en tasks 1-6.
Verificado de forma independiente por el leader (re-ejecución de la suite +
`git show --stat` del commit: solo los 2 ficheros de scope). PR #2 mergeado.

## Task 6 — docx_parser (markitdown, P17)

**Fecha:** 2026-07-22
**Resultado:** APROBADA sin rechazos (ciclo ejecutado en una sola sub-sesión,
NOTARIO→BISTURÍ→FISCAL→NOTARIO→CENTINELA, modelo económico por complejidad
baja). `docx_parser.py` sigue el patrón de `epub_parser.py` (sin OCR
fallback — `.docx` no es formato escaneado), `MarkItDown(enable_plugins=False)`.
19/19 unit + 4/4 integration en verde. PR #1 mergeado a `main`.

**Fixture real bloqueada inicialmente:** no existe ningún `.docx` nativo en
el corpus (`data/raw/books/` solo tiene `.pdf`/`.epub`) ni en `tests/fixtures/`.
Se descartó un fixture propuesto por el usuario (`Freskito-Informático.docx`)
por resultar ser material del Flujo C (monólogo con remates marcados en
color, detectado al inspeccionar `word/document.xml`: contenía tokens
`[REMATE]` ya procesados por `notebooks/marcar_remates_colab.ipynb`) — mezclarlo
en Flujo A habría violado la regla de no-import entre `theory/`/`jokes/`.
Se resolvió creando `tests/fixtures/comedy_bible_excerpt.docx`: texto REAL
extraído con `markitdown` de un pasaje en prosa de
`data/raw/books/Judy_Carter_The_Comedy_Bible.pdf` (libro real del corpus),
re-empaquetado a `.docx` con `python-docx` — no es contenido inventado, solo
un cambio de contenedor porque el formato nativo no existía todavía en el
corpus.

**Error de proceso detectado tras el cierre (no de código):** el commit de
la sub-sesión (`9414844`) incluyó `docx_parser.py` y el test pero NO el
fixture `comedy_bible_excerpt.docx` (se quedó fuera del `git add` con scope
acotado a "solo los ficheros de la tarea", y el fixture no se consideró
parte de ese scope aunque el test dependía de él). Rompía el test en un
checkout limpio. **Solución:** commit de seguimiento (`2c1a119`) añadiendo
el fixture. **Lección para próximas tareas:** al acotar `git add` al scope
de la tarea, verificar explícitamente que cualquier fixture nuevo referenciado
por un test nuevo queda incluido, no solo el código y el test.

## Task 5 — epub_parser (markitdown, P17)

**Fecha:** 2026-07-22
**Resultado:** APROBADA por el reviewer sin rechazos. `epub_parser.py` usa
solo `MarkItDown(enable_plugins=False)`, sin `ebooklib` (confirmado que
markitdown cubre EPUB con calidad suficiente: 210k-378k caracteres en los 3
EPUBs reales). Sin fixture pequeña en `tests/fixtures/` por tamaño
(851KB-2.5MB) — test de integración contra los EPUBs reales de
`data/raw/books/` (mismo patrón que pdf_parser). 17/17 unit + 4/4 integration
en verde.

## Incidente — reset externo del árbol de trabajo (2026-07-22, ~12:52)

Un `git checkout`/`reset` ejecutado contra el árbol de trabajo (no por el
harness) revirtió a su estado vacío original los ficheros trackeados de las
tasks 2, 3 y 4 ya aprobadas (`src/theory/drive_monitor.py`,
`src/theory/parsers/whisperx_parser.py`, `src/theory/parsers/pdf_parser.py`,
`tests/unit/theory/test_whisperx_parser.py`) y `feature_list.json` volvió a
"pending" en las 21 tareas. Los ficheros de test nuevos/untracked de las tasks
2 y 4 no se vieron afectados (git checkout no toca untracked).

**Recuperación:** se reanudaron los tres agentes implementer originales
(seguían teniendo el contexto completo de lo que escribieron) para que
reescribieran el mismo código ya aprobado sin rediseñar nada. Verificado de
nuevo: 17/17 unit + 4/4 integration en verde. `feature_list.json` restaurado
(tasks 1-4 `done`).

**Causa raíz no confirmada** — ninguno de estos ficheros se había comiteado
todavía en esta sesión, así que quedaban expuestos a cualquier `git
checkout`/`reset`/`stash` sobre el árbol de trabajo. Recomendación: comitear
cada task aprobada (o al menos con más frecuencia) para que un incidente
similar no vuelva a perder trabajo ya revisado.

## Task 4 — pdf_parser (markitdown, P17)

**Fecha:** 2026-07-22
**Resultado:** APROBADA por el reviewer sin rechazos. `parse_pdf` usa
`MarkItDown(enable_plugins=False)` como camino primario, con
`_necesita_ocr_fallback` (función pura) decidiendo el fallback a
pdf2image+pytesseract. 17/17 unit + 2/2 integration en verde.
**Gap conocido (no bloqueante):** ningún PDF real del corpus tiene páginas
escaneadas, así que la ejecución real del fallback OCR no tiene fixture real
— solo la lógica de decisión está testeada. Documentado en docstrings del
propio código/test (el reviewer confirmó que no encaja en el formato de
`KNOWN_ERRORS.md`, que es para bugs resueltos, no gaps de cobertura declarados).

## Task 3 — whisperx_parser

**Fecha:** 2026-07-22
**Resultado:** APROBADA por el reviewer sin rechazos. `parse_whisperx_transcript`
limpia timestamps/tags, une bloques consecutivos del mismo speaker y calcula
el dominante por texto acumulado (no por orden de aparición) — verificado con
extracto real multi-speaker de `data/raw/transcriptions/Tomas/`. 9/9 tests en
verde, sin regresión en task 2.

## Task 2 — DriveMonitor local

**Fecha:** 2026-07-22
**Resultado:** APROBADA por el reviewer sin rechazos. `src/theory/drive_monitor.py`
implementado (carpeta y `state_path` inyectados, hash MD5 en streaming, nunca
escribe sobre el original), 5/5 tests en verde con fixtures reales
(`sample_transcript.pdf`/`.txt`). Sin scope creep verificado por `git status`.

## Task 1 — Inicializar entorno

**Fecha:** 2026-07-22
**Resultado:** APROBADA por el reviewer sin rechazos. `.env` completo desde
`.env.example`, `.venv/` con dependencias instaladas, `bash init.sh` exit 0.
Único AVISO no bloqueante: "no tests ran" (esperado, aún no hay suites — se
implementan en tasks posteriores vía TDD).
