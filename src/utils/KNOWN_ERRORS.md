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

## `generar_json` revienta con `TypeError` críptico cuando Gemini no devuelve texto

**Fecha:** 2026-08-01
**Fichero:** `src/utils/llm/client.py`
**Síntoma:** `TypeError: the JSON object must be str, bytes or bytearray, not NoneType` propagándose desde `_parsear_json_respuesta` (vía `json.loads`), sin ningún contexto de por qué. Visto en producción durante la primera pasada real de `run_historico.py` (task 67): 5 de 103 documentos fallaron con este error.
**Causa:** `respuesta.text` del SDK `google-genai` puede ser `None` sin que `generate_content` lance excepción propia — ocurre cuando `finish_reason` del candidato no es `STOP` (bloqueo de seguridad, corte por `MAX_TOKENS`, `RECITATION`, etc.) o, según se confirmó al reproducir el caso real contra la API (mismo fichero, mismo texto, reintentado inmediatamente después), también de forma **transitoria/rara sin causa de contenido aparente** — los 23 chistes del documento que había fallado se procesaron sin problema al repetir la llamada. `generar_json` pasaba `respuesta.text` directo a `_parsear_json_respuesta`, que solo espera `str` y no distingue "no vino texto" de "vino texto pero no es JSON válido".
**Solución:** nueva función pura `_validar_texto_respuesta(texto, *, finish_reasons=None, prompt_feedback=None)` en `client.py`, llamada en `generar_json` antes de `_parsear_json_respuesta`: si `texto is None`, lanza `LLMClientError` con el `finish_reason` de cada candidato y el `prompt_feedback` del SDK (cuando estén disponibles), en vez de dejar que reviente como `TypeError` sin contexto. No es un fix del problema de fondo (P16: sin loop de reintento) — es una mejora de observabilidad: si vuelve a pasar, el mensaje dice el motivo real en vez de obligar a reproducirlo a mano. Tests en `tests/unit/utils/test_llm_client.py::TestValidarTextoRespuesta`. Los 9 documentos fallidos de la task 67 se dividen en 5 de este bug + 4 transitorios (3 `statement timeout` de Postgres, 1 `503 UNAVAILABLE`, 1 timeout de red) — pendientes de reprocesar en una segunda pasada de `run_historico.py` (idempotente, solo reintenta lo no persistido).
