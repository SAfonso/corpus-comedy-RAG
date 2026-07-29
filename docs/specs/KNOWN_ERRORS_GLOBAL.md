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

---

## Re-ejecutar `schema.sql` NO añade columnas nuevas a una tabla que ya existe (`create table if not exists` es un no-op sobre ella)

**Fecha:** 2026-07-24
**Módulos implicados:** `src/jokes/schema.sql` (DDL compartido) ↔ cualquier task que amplíe una tabla ya aplicada en Supabase (ej. `src/theory/teoria_store.py`, task 21)
**Síntoma:** tras "aplicar `schema.sql` en Supabase" (pegar el fichero completo en el SQL Editor y ejecutar), el test de integración seguía en `pytest.skip`/`PGRST204` para `teoria_chunks.chunk_index` — la persona aplicándolo dio el paso por hecho (`create table if not exists teoria_chunks (...)` con `chunk_index` ya en la definición) pero la tabla `teoria_chunks` YA existía de la task 12, así que Postgres no re-evalúa las columnas de esa sentencia: la sigue tratando como "la tabla ya existe, nada que hacer" y la columna nueva simplemente no se crea. Confirmado directo contra la API: `column teoria_chunks.chunk_index does not exist` (código `42703`) al hacer un `select` explícito de esa columna.
**Causa:** `create table if not exists` en Postgres comprueba solo la EXISTENCIA de la tabla, nunca diffea columnas contra la definición del `CREATE TABLE` — es idempotente para "no falles si ya está creada", no para "sincroniza el esquema". Cualquier cambio a una tabla que YA se aplicó en una task anterior (añadir columna, constraint, índice) necesita una sentencia `ALTER TABLE` explícita, que `schema.sql` de este repo NO escribe (documenta el esquema deseado final, no migraciones incrementales) — la task que amplía la tabla debe dar la sentencia `ALTER` aparte al usuario, no asumir que "aplicar `schema.sql` de nuevo" es suficiente.
**Solución:** para `teoria_chunks.chunk_index` (task 21) hicieron falta, aparte de re-pegar `schema.sql`:
```sql
alter table teoria_chunks add column if not exists chunk_index int;
alter table teoria_chunks add constraint teoria_chunks_doc_version_chunk_key unique (doc_id, version_corpus, chunk_index);
```
**Regla para tasks futuras que modifiquen una tabla ya existente en `schema.sql`:** además de actualizar la definición del `CREATE TABLE` (documentación del esquema final), la task debe entregar explícitamente el `ALTER TABLE` equivalente en su PR/reporte (ej. en el cuerpo del PR, no solo en el fichero) — nunca asumir que "vuelve a aplicar `schema.sql`" cubre el caso de ampliar una tabla preexistente. Los tests de integración deben seguir capturando `PGRST204`/`42703` explícitamente y haciendo `skip` con instrucciones — nunca fallar en seco ni mockear la ausencia de columna.

---

## `load_dotenv()` sin ruta explícita, ejecutado dentro de un worktree anidado bajo el repo, carga el `.env` REAL del checkout principal (credenciales de producción)

**Fecha:** 2026-07-29
**Módulos implicados:** `src/theory/teoria_store.py`/`src/jokes/supabase_store.py` (ambos llaman `load_dotenv()` sin argumentos al importarse) ↔ cualquier sub-sesión del harness que trabaje en un worktree bajo `.claude/worktrees/<id>/` dentro del propio repo (`theory/`, `jokes/`, cualquier script que los importe).
**Síntoma:** al ejecutar `python scripts/validate_corpus.py` (sin argumento, modo Supabase por defecto, task 62) DENTRO de un worktree aislado que NO tiene su propio `.env`, se esperaba `TeoriaStoreError` ("SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas") — en su lugar, el script hizo una llamada de red REAL contra el proyecto Supabase de producción y devolvió `postgrest.exceptions.APIError: {'message': "Could not find the table 'public.teoria_documentos' in the schema cache", 'code': 'PGRST205', ...}` (la tabla vive en `silver`, no en `public`, porque el `.env` cargado no fija `SUPABASE_SCHEMA_MODE=p25`). Ninguna variable `SUPABASE_*` estaba en el entorno de shell ni en el worktree.
**Causa:** `python-dotenv`'s `load_dotenv()` sin ruta busca un `.env` recorriendo directorios HACIA ARRIBA desde el `cwd` del proceso hasta encontrar uno. Un worktree vive en `<repo>/.claude/worktrees/<id>/` — un subdirectorio del propio checkout principal — así que esa búsqueda ascendente encuentra el `.env` real de `<repo>/.env` (con `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` de producción) aunque el worktree no tenga ni haya creado ningún `.env` propio. "No hay `.env` en este worktree" NO implica "sin credenciales": cualquier import de un módulo que llame `load_dotenv()` (patrón usado tanto en `teoria_store.py` como en `supabase_store.py`) puede alcanzar producción sin que el agente lo pida explícitamente.
**Solución / mitigación:** no hay cambio de código de este repo para "arreglarlo" (es el comportamiento documentado de `python-dotenv`, y los scripts de producción SÍ necesitan encontrar el `.env` real cuando se ejecutan desde la raíz — cambiarlo rompería ese caso). Regla operativa para cualquier sub-sesión que verifique manualmente un script que importe `teoria_store`/`supabase_store` dentro de un worktree: **antes de ejecutar el script directamente (sin store/cliente inyectado), asumir que SÍ puede alcanzar producción** si el checkout principal tiene un `.env` real — no dar por hecho que "sin credenciales locales" es sinónimo de "llamada bloqueada". Las operaciones de solo lectura (`select`) contra una tabla/esquema que no está expuesto son inofensivas (fallan con `PGRST205`/`PGRST204`, no tocan datos), pero cualquier verificación manual que pudiera ESCRIBIR debe inyectar un `store`/cliente doble, nunca confiar en la ausencia de `.env` local del worktree como aislamiento de red.
