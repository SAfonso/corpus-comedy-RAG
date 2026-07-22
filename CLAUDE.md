# Comedy Corpus Pipeline — Claude Code Instructions

## Propósito
Pipeline de ingesta, limpieza, estructuración y versionado de datos para el Comedy RAG.
Corpus **multi-fuente**: cada unidad lleva `tipo_fuente` para permitir retrieval
separado por origen en el RAG downstream. Tres flujos: **A — Teoría** (Drive,
batch, determinista), **B — Chistes propios** (Telegram, tiempo real) y
**C — Chistes históricos** (batch retroactivo). Ver `docs/specs/00-overview.md` §1.

## Fuente de verdad
La spec está partida por módulo, colocada junto al código que gobierna. **No
hace falta leer todas para trabajar en una — usa la tabla de abajo.** Ante
cualquier discrepancia entre este fichero y un `SPEC.md`, manda el `SPEC.md`.

## Routing — qué leer según qué vas a tocar

| Módulo | Spec | Errores conocidos |
|---|---|---|
| Flujo A — Teoría (`src/theory/`) | `src/theory/SPEC.md` | `src/theory/KNOWN_ERRORS.md` |
| Contrato compartido B/C — `src/jokes/`: `silver.py`, `reconciliacion.py`, `supabase_store.py` | `src/jokes/SPEC.md` | `src/jokes/KNOWN_ERRORS.md` |
| Flujo B — Telegram (`src/jokes/telegram/`) | `src/jokes/telegram/SPEC.md` + `src/jokes/SPEC.md` | `src/jokes/telegram/KNOWN_ERRORS.md` |
| Flujo C — Histórico (`src/jokes/historico/`, `scripts/marcar_remates.py`) | `src/jokes/historico/SPEC.md` + `src/jokes/SPEC.md` | `src/jokes/historico/KNOWN_ERRORS.md` |
| Código compartido (`src/utils/`) | `src/utils/SPEC.md` | `src/utils/KNOWN_ERRORS.md` |
| Coste/LLM/copyright, P16 (loops LLM) | `docs/specs/llm-policy.md` | — |
| Nueva fuente, `tipo_fuente`, layout global, dependencias entre módulos | `docs/specs/00-overview.md` | `docs/specs/KNOWN_ERRORS_GLOBAL.md` |

## Regla más importante
El material original es SAGRADO: `/data/raw/` (teoría) y la capa Bronze (chistes).
Nunca modificar, eliminar ni sobrescribir. Todo el trabajo ocurre aguas abajo.

## Regla de dependencias
`theory/` y `jokes/` NO se importan entre sí. Código común → `src/utils/`.
Silver/Reconciliación/Taxonomías se especifican una sola vez en `src/jokes/SPEC.md`
(compartidas B/C) — `telegram/SPEC.md` e `historico/SPEC.md` remiten ahí, no lo repiten.

## Protocolo de errores conocidos
Antes de depurar un error por prueba y error: **busca primero en el
`KNOWN_ERRORS.md` del módulo** (tabla de arriba). Si el error ya ocurrió, aplica
la solución documentada directamente en vez de probar cosas al azar. Si el
error involucra a más de un módulo (dependencia rota, contrato compartido),
consulta también `docs/specs/KNOWN_ERRORS_GLOBAL.md`.

Al resolver un error que **no** estaba documentado, **añade una entrada** al
`KNOWN_ERRORS.md` correspondiente (del módulo, o el global si cruza módulos)
antes de dar la tarea por terminada — formato fijo en la cabecera de cada fichero.

## Metodología: SDD + TDD
1. Lee el `SPEC.md` del módulo (tabla de arriba) antes de implementar.
2. Escribe tests primero con fixtures reales de `/tests/fixtures/` (nunca inventados).
3. Tests: `pytest tests/unit/ -v`, `pytest tests/integration/ -v`; antes de commit `python scripts/validate_corpus.py`.

## Harness de agentes — modo EJECUTOR

Este proyecto trabaja con un harness de agentes en modo **EJECUTOR**
(ejecución autónoma dentro del scope de cada tarea; escalar al usuario solo
ante ambigüedad de diseño o rechazo repetido).

| Agente | Modo | Fichero | Función |
|---|---|---|---|
| leader | DIRECTOR | `.claude/agents/leader.md` | Orquesta: asigna tareas del backlog, evalúa rechazos, escala al usuario |
| planner | ARQUITECTO | `.claude/agents/planner.md` | Descompone objetivos en tareas atómicas y asigna complejidad |
| implementer | BISTURÍ | `.claude/agents/implementer.md` | Implementa la tarea activa sin salirse del scope |
| reviewer | FISCAL | `.claude/agents/reviewer.md` | Aprueba/rechaza contra `CHECKPOINTS.md`, no contra opinión |
| integrator | NOTARIO | `.claude/agents/integrator.md` | Rama al iniciar la tarea, commit+push+PR al cerrarla (tras aprobación de FISCAL) |
| watchman | CENTINELA | `.claude/agents/watchman.md` | Verifica CI/merge y mergea en automático si está en verde; si falla, reabre el ciclo con FISCAL |

El rol de leader lo ejerce la **sesión principal** de Claude Code: es quien
invoca a planner, implementer, reviewer, integrator y watchman como
subagentes siguiendo `.claude/agents/leader.md`, una sub-sesión por tarea.
El leader no mantiene memoria conversacional larga entre tareas: su estado
vive en `progress/ledger.json` (decisiones + resumen de cada tarea cerrada),
y cada sub-sesión recibe solo el paquete de contexto mínimo de su tarea, no
el historial completo.

### Tareas atómicas y política de modelos
- Toda tarea del backlog es **atómica**: un entregable verificable, cabe en una
  sesión, scope cerrado. Un objetivo amplio ("monta el flujo B entero") no entra
  al backlog: primero pasa por el planner, que lo descompone.
- Cada tarea lleva `complejidad` en `feature_list.json`, y el leader la lanza
  con el modelo de su tier: `alta` (diseño, prompts, decisiones) → modelo
  potente (ej. Opus), `media` (implementación estándar) → modelo intermedio
  (ej. Sonnet), `baja` (mecánica) → modelo económico (ej. Haiku).

- **Backlog:** `feature_list.json` — trabajar siempre sobre la siguiente tarea
  `pending`; marcarla `done` solo tras aprobación del reviewer.
- **Criterios de validación:** `CHECKPOINTS.md` (criterios de aceptación + reglas del harness).
- **Mapa de roles:** `AGENTS.md`. **Verificación de entorno:** `bash init.sh`.
- Máximo 3 rechazos del reviewer sobre la misma tarea antes de escalar al usuario.
- El ciclo por tarea respeta la metodología de arriba: spec del módulo → tests
  con fixtures reales → implementación → reviewer.
