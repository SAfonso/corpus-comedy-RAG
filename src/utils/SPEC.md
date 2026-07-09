# utils — código compartido

> Spec de `src/utils/`. Ver [`docs/specs/00-overview.md`](../../docs/specs/00-overview.md)
> para el contexto general.

`utils/` no tiene política propia — es la carpeta de implementaciones
reutilizables que consumen los flujos. No define reglas de negocio; solo
apunta a quién usa qué, para no asumir que algo aquí se usa simétricamente en
todos los flujos cuando no es así.

| Módulo | Qué hace | Quién lo consume | Spec del consumidor |
|--------|----------|-------------------|------------------------|
| `language_detector.py` | Detección de idioma | Flujo A (Teoría) — corpus bilingüe | `src/theory/SPEC.md` |
| `quality_scorer.py` | Puntuación 0–1 de densidad de contenido útil | Flujo A (Teoría) | `src/theory/SPEC.md` |
| `llm/client.py` | Cliente LLM vía API (modelo barato) | Flujos B/C (Silver, Taxonomías) — **teoría NO usa LLM** | `src/jokes/SPEC.md`, [`docs/specs/llm-policy.md`](../../docs/specs/llm-policy.md) |
| `llm/embeddings.py` | Cliente de embeddings | Flujos B/C (Reconciliación, retrieval RAG) | `src/jokes/SPEC.md` |

**Regla de dependencias:** `theory/` y `jokes/` no se importan entre sí; lo que
necesitan ambos (si algo llegara a necesitarlo) vive aquí. Hoy, en la práctica,
`language_detector`/`quality_scorer` son consumo exclusivo de teoría y
`llm/*` es consumo exclusivo de chistes — la carpeta es compartida en
ubicación, no en uso actual.
