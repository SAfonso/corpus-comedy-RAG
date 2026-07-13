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

_Sin incidencias registradas todavía._
