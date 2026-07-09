# [Movido] Comedy Corpus Pipeline — Especificación

> **Este documento ya no es la fuente de verdad.** Su contenido (v2,
> secciones §1–§15, decisiones P1–P16) se repartió en specs más pequeños,
> colocados junto al código que gobiernan, para no obligar a leer 480 líneas
> para tocar un solo módulo.
>
> **Empieza aquí:** [`docs/specs/00-overview.md`](00-overview.md) — incluye la
> directriz de qué spec leer según qué parte del código vayas a tocar.

## Mapa de los specs nuevos

| Spec | Contenido |
|------|-----------|
| [`docs/specs/00-overview.md`](00-overview.md) | Propósito, `tipo_fuente`, arquitectura general, layout del repo, directriz de lectura, metodología SDD/TDD |
| [`docs/specs/llm-policy.md`](llm-policy.md) | Coste/LLM, copyright, P16 (loops LLM con criterio de parada verificable) |
| [`src/theory/SPEC.md`](../../src/theory/SPEC.md) | Flujo A — Teoría |
| [`src/utils/SPEC.md`](../../src/utils/SPEC.md) | Código compartido: qué vive ahí y quién lo consume |
| [`src/jokes/SPEC.md`](../../src/jokes/SPEC.md) | Contrato compartido B/C: Silver, Taxonomías, Reconciliación, Versionado, esquema Supabase |
| [`src/jokes/telegram/SPEC.md`](../../src/jokes/telegram/SPEC.md) | Flujo B — Telegram (específico) |
| [`src/jokes/historico/SPEC.md`](../../src/jokes/historico/SPEC.md) | Flujo C — Histórico (específico), incluido `marcar_remates.py` |

Este fichero se conserva solo para que enlaces antiguos no rompan. El
histórico completo de la versión monolítica sigue disponible en `git log` de
este mismo path.
