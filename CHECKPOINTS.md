# CHECKPOINTS.md — Criterios de validación

## Proyecto
Comedy Corpus Pipeline — ingesta, limpieza, estructuración y versionado de un
corpus multi-fuente para el Comedy RAG. Tres flujos: A (Teoría/Drive, batch
determinista), B (Chistes/Telegram, tiempo real), C (Históricos, batch retroactivo).

## Criterios de aceptación
- los tres flujos procesan el corpus y validate_corpus pasa sin errores
- Flujo A produce `/data/processed/v{N}/` inmutable con cabecera YAML completa
  (`fuente`, `autor`, `idioma_original`, `idioma_fragmento`, `subtipo`, `tipo_fuente`, `licencia`)
- Flujos B/C insertan en Supabase con `tipo_fuente` correcto y dedup por
  reconciliación (hash + embedding)
- `pytest tests/unit/ -v` y `pytest tests/integration/ -v` en verde, con
  fixtures reales de `/tests/fixtures/`

## Reglas del harness
- Material sagrado: `/data/raw/` y la capa Bronze nunca se modifican, eliminan ni sobrescriben
- `theory/` y `jokes/` no se importan entre sí — código común solo en `src/utils/`
- Sin LLM ni APIs de pago para `externo*` (teoría); loops LLM solo con criterio
  de parada verificable externamente (P16, máx 3 intentos)
- Tests primero, con fixtures reales — nunca inventadas
- Antes de depurar: consultar el `KNOWN_ERRORS.md` del módulo
- Bitácora de errores obligatoria en el `KNOWN_ERRORS.md` correspondiente

## Definición de done
Una tarea está done cuando el reviewer la aprueba contra los criterios anteriores.
