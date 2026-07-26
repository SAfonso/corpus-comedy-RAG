# Errores conocidos — Flujo C (Histórico)

> Bitácora de errores ya vistos en este módulo (incluido `scripts/marcar_remates.py`)
> y su solución. **Antes de depurar un error por prueba y error, busca aquí si ya
> ocurrió** — si está documentado, aplica la solución directamente. Si no está,
> resuélvelo y **añade una entrada antes de dar la tarea por terminada** (regla
> en `CLAUDE.md`).
>
> Errores en Silver/Reconciliación/Taxonomías (compartidos con Telegram) van en
> [`src/jokes/KNOWN_ERRORS.md`](../KNOWN_ERRORS.md), no aquí. Errores que cruzan
> módulos van en
> [`docs/specs/KNOWN_ERRORS_GLOBAL.md`](../../../docs/specs/KNOWN_ERRORS_GLOBAL.md).

## Formato de entrada

```
## <resumen corto del síntoma>
**Fecha:** YYYY-MM-DD
**Fichero:** ruta/al/fichero.py
**Síntoma:** mensaje de error / traceback relevante (lo mínimo para reconocerlo al grepear)
**Causa:** por qué ocurría
**Solución:** qué se cambió (referencia al commit si aplica)
```

---

## `Loader.load()` fuera de `run_historico_pipeline` compromete su estado y deja el run real sin nada que procesar
**Fecha:** 2026-07-26
**Fichero:** `scripts/run_historico.py` (task 28)
**Síntoma:** un caller que necesita `documentos` (`list[{"name","content"}]`)
ANTES de invocar `run_historico_pipeline` — p.ej. para evaluar el gate de
coste de `historico/coste.py` (task 26) — y para ello llama a
`Loader.load()` directamente contra el `loader_state_path` de producción, ve
que el run real que viene después (`run_historico_pipeline`) no procesa
NINGÚN documento, aunque el gate haya dicho "adelante" y los `.md` sigan en
disco. No hay excepción ni traceback: `ResultadoHistorico.documentos` llega
vacío en silencio.
**Causa:** `Loader.load()` (`src/jokes/historico/loader.py`) escribe el MD5
de cada documento nuevo/modificado en `state_path` de forma **incondicional**
en cuanto lo lee — no espera a que ese documento termine de procesarse. Es la
misma "escritura prematura" que `historico/pipeline.py` ya resuelve
internamente (ver su comentario "Reanudación por documento": carga
`estado_comprometido`, llama a `loader.load()`, y REVIERTE el fichero a
`estado_comprometido` antes de procesar nada). Si un caller EXTERNO llama a
`Loader.load()` sobre el mismo `state_path` sin hacer ese mismo revert, dicha
llamada "gasta" la idempotencia del documento: la siguiente vez que
`run_historico_pipeline` cargue su propio `estado_comprometido` y llame a
`loader.load()` internamente, el MD5 ya estará registrado y el documento se
saltará como si ya estuviera procesado — pero nunca llegó a pasar por
Segmentador/Silver/Reconciliación/Supabase.
**Solución:** cualquier caller que necesite adelantar `Loader.load()` fuera
de `run_historico_pipeline` (p.ej. `scripts/run_historico.py`,
`_documentos_pendientes_para_gate`) debe replicar el mismo patrón
"leer estado previo → `Loader.load()` → reescribir el estado previo tal cual
estaba" para revertir la escritura prematura antes de devolver el control.
Con eso, la llamada interna de `run_historico_pipeline` ve exactamente los
mismos documentos pendientes que vio el caller externo. No es necesario
aplicar el mismo cuidado a `DriveSource.sync()`: esa capa ya es idempotente
por sí sola (dedup por `fileId`+`modifiedTime`), llamarla dos veces con el
mismo estado no oculta trabajo pendiente — solo devuelve lista vacía la
segunda vez, lo cual es inofensivo porque los `.docx`/`.md` ya quedaron en
disco (staging/carpeta_md, caché reconstruible, no material sagrado).
