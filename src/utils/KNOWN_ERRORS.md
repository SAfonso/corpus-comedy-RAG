# Errores conocidos — utils (código compartido)

> Bitácora de errores ya vistos en `language_detector.py`, `quality_scorer.py`,
> `llm/client.py`, `llm/embeddings.py` y su solución. **Antes de depurar un
> error por prueba y error, busca aquí si ya ocurrió** — si está documentado,
> aplica la solución directamente. Si no está, resuélvelo y **añade una entrada
> antes de dar la tarea por terminada** (regla en `CLAUDE.md`).
>
> Un error aquí casi siempre afecta a más de un flujo (por eso el código vive en
> `utils/`) — si el síntoma solo aparece al consumirlo desde `theory/` o
> `jokes/`, documenta también en
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
