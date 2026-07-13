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

_Sin incidencias registradas todavía._
