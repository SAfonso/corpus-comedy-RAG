# Errores conocidos — dependencias entre módulos

> Bitácora de errores que **cruzan más de un módulo**: contratos compartidos
> rotos, desalineación entre `theory/` y `jokes/`, cambios en `utils/` que
> rompen a un consumidor, discrepancias entre `tipo_fuente` y lo que asume un
> flujo, etc. **Antes de depurar un error por prueba y error, busca aquí si ya
> ocurrió** — si está documentado, aplica la solución directamente. Si no está,
> resuélvelo y **añade una entrada antes de dar la tarea por terminada** (regla
> en `CLAUDE.md`).
>
> Si el error vive dentro de un solo módulo, documéntalo en su
> `KNOWN_ERRORS.md` (`src/theory/`, `src/jokes/`, `src/jokes/telegram/`,
> `src/jokes/historico/`, `src/utils/`), no aquí.

## Formato de entrada

```
## <resumen corto del síntoma>
**Fecha:** YYYY-MM-DD
**Módulos implicados:** ej. src/jokes/historico/ ↔ src/jokes/ (contrato Silver)
**Síntoma:** mensaje de error / traceback relevante (lo mínimo para reconocerlo al grepear)
**Causa:** por qué ocurría (qué asunción de un módulo rompió el otro)
**Solución:** qué se cambió (referencia al commit si aplica)
```

---

## PostgREST devuelve `PGRST204` (no `PGRST205`) cuando falta una COLUMNA nueva de una tabla ya existente

**Fecha:** 2026-07-23
**Módulos implicados:** `src/theory/ingest_teoria.py`/`teoria_store.py` (task 21) ↔ `src/jokes/schema.sql` (DDL compartido, dueño formal en `src/jokes/`)
**Síntoma:** `postgrest.exceptions.APIError: {'message': "Could not find the 'chunk_index' column of 'teoria_chunks' in the schema cache", 'code': 'PGRST204', ...}` al hacer upsert contra una tabla que YA existía en Supabase (creada por un `schema.sql` de una task anterior) pero a la que esta task le añadió una columna nueva (`chunk_index`) que todavía no se ha aplicado a mano en el proyecto Supabase real.
**Causa:** mismo mecanismo que `PGRST205` (`src/jokes/KNOWN_ERRORS.md`) pero para el caso "tabla existe, columna no": PostgREST also traduce esto a su propio código, distinto del de tabla-inexistente. Cualquier módulo que amplíe una tabla ya aplicada de `schema.sql` (no solo la cree) puede toparse con este código, no solo `PGRST205`.
**Solución:** los tests de integración que dependan de una columna NUEVA de una tabla ya existente deben capturar `APIError` alrededor de la operación real (no solo alrededor de una query de limpieza previa que no toca la columna nueva) y comprobar `exc.code in ("PGRST205", "PGRST204")` para decidir el `pytest.skip(...)` — ver `tests/integration/test_ingest_teoria_live.py`. Confirmado empíricamente: `teoria_chunks` ya existía (de la task 12) sin `chunk_index`; el upsert de `TeoriaStore.guardar_chunk` devolvió `PGRST204` hasta aplicar el `schema.sql` actualizado.
