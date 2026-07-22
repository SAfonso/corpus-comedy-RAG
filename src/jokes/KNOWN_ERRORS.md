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
