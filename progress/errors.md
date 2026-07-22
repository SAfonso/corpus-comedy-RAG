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
