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
