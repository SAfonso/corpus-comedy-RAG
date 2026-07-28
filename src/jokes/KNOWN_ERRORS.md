# Errores conocidos — Chistes, contrato compartido (B/C)

> Bitácora de errores ya vistos en `silver.py`, `reconciliacion.py`,
> `supabase_store.py` y su solución. **Antes de depurar un error por prueba y
> error, busca aquí si ya ocurrió** — si está documentado, aplica la solución
> directamente. Si no está, resuélvelo y **añade una entrada antes de dar la
> tarea por terminada** (regla en `CLAUDE.md`).
>
> Errores específicos de un flujo van en su propio módulo
> ([`src/jokes/telegram/KNOWN_ERRORS.md`](telegram/KNOWN_ERRORS.md) o
> [`src/jokes/historico/KNOWN_ERRORS.md`](historico/KNOWN_ERRORS.md)), no aquí.
> Errores que cruzan módulos (dependencia rota, contrato compartido) van en
> [`docs/specs/KNOWN_ERRORS_GLOBAL.md`](../../docs/specs/KNOWN_ERRORS_GLOBAL.md).

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

## PostgREST devuelve `PGRST205` (no `42P01`) cuando una tabla no existe

**Fecha:** 2026-07-23
**Fichero:** `tests/integration/test_supabase_store_live.py`, `src/jokes/supabase_store.py`
**Síntoma:** `postgrest.exceptions.APIError: {'message': "Could not find the table 'public.<tabla>' in the schema cache", 'code': 'PGRST205', ...}` al llamar a `store.listar_temas()` / cualquier operación sobre una tabla del `schema.sql` (task 12) todavía no aplicada en Supabase.
**Causa:** el cliente `supabase-py`/`postgrest-py` habla exclusivamente con la API REST (PostgREST), nunca con Postgres directo. Cuando una tabla no existe (o existe pero PostgREST no ha refrescado su cache de esquema tras un `CREATE TABLE` reciente), PostgREST **no** deja pasar el código nativo de Postgres `42P01` ("undefined_table") — siempre traduce el error a su propio código `PGRSTxxx` (`PGRST205` = "tabla no encontrada en el cache de esquema"). Asumir `42P01` (como haría un driver Postgres directo, ej. `psycopg2`) hace que el `skip` esperado en el test de integración no se dispare y el test falle en vez de saltar con mensaje claro.
**Solución:** `test_supabase_store_live.py` detecta `exc.code == "PGRST205"` (no `42P01`) para decidir el `pytest.skip(...)` con instrucciones de aplicar `src/jokes/schema.sql` en el SQL Editor de Supabase. Confirmado empíricamente contra el proyecto Supabase real de esta task: con las tablas aún sin crear, `listar_temas()` y el insert de prueba en `candidatos_taxonomia` devuelven `PGRST205`, y el test hace skip correctamente tras el fix.

---

## `schema.sql` desincronizado con el proyecto Supabase real — columna existe en el repo pero no en la BBDD

**Fecha:** 2026-07-28
**Fichero:** `src/jokes/schema.sql`, `src/jokes/supabase_store.py::marcar_telegram_bronze_procesado`
**Síntoma:** a diferencia de una tabla ausente (ver entrada de arriba, `PGRST205`), una **columna** ausente sí devuelve el código nativo de Postgres tal cual, sin traducir: `{'code': '42703', 'message': 'column chistes_telegram_bronze.procesado_at does not exist', ...}` al hacer un `select` explícito de esa columna o al intentar un `update`/`insert` que la referencie.
**Causa:** `schema.sql` es la fuente de verdad **del repositorio**, pero aplicarlo contra el proyecto Supabase real es un paso manual (SQL Editor del dashboard) que no ocurre solo con el commit. La columna `procesado_at` se añadió a `chistes_telegram_bronze` en la task 46 (recuperación post-200), pero esa `ALTER TABLE` nunca se ejecutó contra la base de datos real — quedó desincronizada hasta el primer despliegue en producción real (Flujo B, tasks 40/41), donde `marcar_telegram_bronze_procesado` (llamada al final del tramo background, tras Silver/taxonomías/reconciliación) fallaba con este error.
**Solución:** ejecutar en el SQL Editor de Supabase (proyecto → SQL Editor → New query) el `ALTER TABLE` correspondiente a cada columna nueva de `schema.sql` que aún no se haya aplicado, ej.: `alter table chistes_telegram_bronze add column if not exists procesado_at timestamptz;`. Regla general para evitar que se repita: cualquier cambio a `schema.sql` debe ir acompañado de aplicarlo contra el proyecto real antes de dar la tarea por cerrada — no basta con que el fichero del repo esté actualizado. Ver `deploy/README.md` §9 para el mismo diagnóstico en el contexto del runbook de despliegue.
