# Política de LLM, coste y copyright

> Cross-cutting: se referencia desde `src/theory/SPEC.md` (regla "no LLM"),
> `src/jokes/SPEC.md` (Silver, Taxonomías), `src/jokes/telegram/SPEC.md` y
> `src/jokes/historico/SPEC.md` (Segmentador). Ver `00-overview.md` para el
> mapa completo de specs.

## Política LLM

- La regla "NO usar LLMs / NO APIs de pago" se mantiene como norma para
  `externo*` (teoría, batch grande, determinista) — ver `src/theory/SPEC.md`.
  Incluye `markitdown` (P17, conversión de Parser): solo su modo de
  conversión determinista, sin activar su plugin opcional de *captioning*
  de imágenes vía LLM.
- **Excepción acotada y documentada:** el Silver de chistes (`src/jokes/SPEC.md`)
  es imposible sin generación (`estructura_detectada`, `sugerencias_mejora`,
  `chiste_normalizado`). Usa un LLM barato vía API (tipo Haiku). Los embeddings
  de reconciliación/retrieval, ídem. Descartado LLM local por no disponer de GPU.

## Control de coste (histórico)

Volumen relevante en el Flujo C → estimación de tokens (dry-run), batching,
gate de coste antes del run completo, y caché / modelo más barato si hace
falta. Detalle en `src/jokes/historico/SPEC.md`.

## Loops LLM: criterio de parada verificable (P16, 2026-07-09)

Cuando un paso LLM puede reintentar automáticamente (sin humano en cada
vuelta), la condición de parada debe ser **verificable por algo externo al
propio LLM** — nunca "el LLM opina que ya está bien". En este proyecto:

- **Externo y verificable → apto para loop acotado:**
  ¿el ID propuesto existe en `temas`/`tecnicas`? (`src/jokes/SPEC.md`, §Taxonomías)
  ¿el hash o la similitud de embedding superan el umbral? (`src/jokes/SPEC.md`,
  §Reconciliación — ya cumple el patrón sin cambios). Son preguntas con
  respuesta binaria en Supabase, no en la cabeza de nadie.
- **Subjetivo / sin ancla externa → NO va en loop, va a revisión humana:**
  dónde empieza semánticamente un setup (`src/jokes/historico/SPEC.md`,
  §Segmentador), o si un `chiste_normalizado`/`sugerencias_mejora`
  (`src/jokes/SPEC.md`, §Silver) es "gracioso" o "queda bien". Iterar el LLM
  contra su propia opinión aquí no converge a una verdad, refuerza lo que ya
  "creía" en el primer intento.
- Todo loop acotado lleva **límite de iteraciones explícito** (≤3) y, agotado
  sin resolver, escala a cola de revisión humana — nunca reintenta indefinidamente.

## Copyright / licencia (P7)

- Restricción conocida: el material de pago (`externo*`) no puede redistribuirse
  tal cual si el producto se comercializa a terceros.
- **Sin enforcement por ahora** (no se planea comercializar): `licencia` es un
  campo de metadata con default seguro (`externo* → personal_only`,
  `propio* → comercializable`), pero **ninguna lógica lo aplica** en pipeline ni RAG.
- Reactivable en el futuro: si se comercializa, se añade el filtro por `licencia`
  en query time sin retro-etiquetar (el campo ya existe).
