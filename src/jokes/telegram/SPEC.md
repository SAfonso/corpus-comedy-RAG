# Flujo B — Chistes propios (Telegram, tiempo real)

> Spec de `src/jokes/telegram/`. Para Silver, Reconciliación, Taxonomías y el
> esquema de Supabase (compartidos con el Flujo C), ver
> [`src/jokes/SPEC.md`](../SPEC.md) — no se duplican aquí. Contexto general en
> [`docs/specs/00-overview.md`](../../../docs/specs/00-overview.md) y política
> LLM en [`docs/specs/llm-policy.md`](../../../docs/specs/llm-policy.md).

Ingesta incremental, un chiste = un evento. Arquitectura **Bronze → Silver**.
Lo que sigue es **específico de Telegram**; en cuanto el chiste sale del
Bronze, entra en el contrato compartido de `src/jokes/SPEC.md`.

## Bronze (raw, sagrado)

Cada mensaje de Telegram se persiste **literal**, sin tocar, con su metadata de
origen (`telegram_update_id`, `chat_id`, `timestamp`). El Bronze es la capa
inmutable equivalente a `/data/raw/` — nunca se reescribe.

**Idempotencia por evento:** dedup por `telegram_update_id`
(`INSERT ... ON CONFLICT (telegram_update_id) DO NOTHING`). Un reenvío o
reintento del webhook nunca duplica.

## Pre-limpieza mínima (NO destructiva)

Antes del Silver, solo transformaciones reversibles: `trim`, normalización
unicode, y strip de artefactos de plataforma (comandos de bot, menciones).
**El Cleaner agresivo de teoría NO se aplica** a `tipo_fuente=propio*`: un
chiste no debe perder muletillas si son parte del timing del remate.

## Salida

Tras el Silver (`src/jokes/SPEC.md` §Silver), cada chiste pasa por
**Reconciliación** (`src/jokes/SPEC.md` §Reconciliación: ¿es nuevo, un
duplicado, o una revisión de uno existente?) y se persiste en Supabase con
`tipo_fuente='propio'`, `licencia='comercializable'`, y su versión/linaje
(`src/jokes/SPEC.md` §Versionado). Sin `v{N}`: los chistes son un store vivo,
no snapshots de corpus.

## Idempotencia y versionado

Idempotencia **por evento** (`telegram_update_id`), no por documento (a
diferencia del Flujo C — ver `src/jokes/historico/SPEC.md`). Versionado por
chiste, sin `v{N}` (ver `src/jokes/SPEC.md` §Versionado).
