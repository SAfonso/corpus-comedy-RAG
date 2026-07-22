# Errores conocidos — Flujo A (Teoría)

> Bitácora de errores ya vistos en este módulo y su solución. **Antes de depurar
> un error por prueba y error, busca aquí si ya ocurrió** — si está documentado,
> aplica la solución directamente. Si no está, resuélvelo y **añade una entrada
> antes de dar la tarea por terminada** (regla en `CLAUDE.md`).
>
> Errores que cruzan módulos (dependencia rota, contrato compartido) van en
> [`docs/specs/KNOWN_ERRORS_GLOBAL.md`](../../docs/specs/KNOWN_ERRORS_GLOBAL.md), no aquí.

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

## `DeeplTranslator` (deep_translator) devuelve 403/`AuthorizationException` con API key válida

**Fecha:** 2026-07-22
**Fichero:** `src/theory/normalizers/language_normalizer.py`
**Síntoma:** `deep_translator.exceptions.AuthorizationException: Unauthorized access with the api key ...` al llamar `DeeplTranslator(...).translate(...)`, incluso con `DEEPL_API_KEY` válida (verificada por separado con una llamada `requests` directa a la misma API, que sí autentica correctamente).
**Causa:** `deep_translator` 1.11.4 (última versión publicada en PyPI a fecha de esta tarea, sin release posterior) autentica contra la API de DeepL enviando `auth_key` como parámetro de query en una petición `GET`. DeepL deprecó ese método de autenticación ("legacy auth") en noviembre de 2025 (ver [breaking change de DeepL](https://developers.deepl.com/docs/resources/breaking-changes-change-notices/november-2025-deprecation-of-legacy-auth-methods)) y ahora exige la cabecera `Authorization: DeepL-Auth-Key <key>`. Es un bug de la librería de terceros, no de la API key ni de nuestro código.
**Solución:** No se parchea `deep_translator` (fuera de scope de la tarea) ni se sustituye por una llamada HTTP manual (sería desviarse del stack documentado en `src/theory/SPEC.md` §Stack sin decisión explícita). Se mantiene `DeeplTranslator` como traductor por defecto de `language_normalizer.py`, inyectable (`normalize_language(..., traductor=...)`) para poder testear la lógica de decisión sin red. El test de integración (`tests/integration/test_language_normalizer_deepl.py`) captura las excepciones de `deep_translator` relevantes (`AuthorizationException`, `ApiKeyException`, `RequestError`, `ServerException`, `TooManyRequests`, `BaseError`) además de `OSError`, y hace `pytest.skip(...)` con el motivo explícito si la llamada real falla — mismo patrón que `test_pdf_parser_smoke.py`/`test_epub_parser_smoke.py`. Si en el futuro se necesita traducción real funcionando en este entorno, la alternativa es una llamada `requests` directa con la cabecera `Authorization: DeepL-Auth-Key`, pero es un cambio de stack que debe decidirse explícitamente, no un fix silencioso.
