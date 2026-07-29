# Limitaciones del entorno del agente

> No son errores de código (no van en ningún `KNOWN_ERRORS.md`) ni rechazos
> del harness (no van en `progress/errors.md`): son cosas que el agente
> (Claude Code, cualquier sesión/subagente) **no puede hacer por sí mismo en
> este entorno**, descubiertas al intentarlo. Antes de intentar uno de estos
> pasos por API/CLI, consulta esta lista — si ya está aquí, pide al usuario
> que lo ejecute él en vez de reintentar por otra vía.
>
> Al descubrir una limitación nueva de este tipo (no un bug, una capacidad
> ausente del entorno), añade una entrada aquí antes de dar la tarea por
> terminada — mismo criterio que el protocolo de `KNOWN_ERRORS.md` en
> `CLAUDE.md`, pero para "no puedo" en vez de "esto rompió".

## Formato de entrada

```
## <qué se intentó>
**Fecha:** YYYY-MM-DD
**Por qué falla:** causa (falta de credencial/tipo de acceso, no de código)
**Qué hace falta:** quién/qué puede hacerlo (usuario, credencial concreta)
```

---

## Ejecutar DDL contra Supabase (CREATE/ALTER/DROP TABLE, etc.)

**Fecha:** 2026-07-29
**Por qué falla:** `SUPABASE_SERVICE_KEY` en `.env` es la clave `service_role`
de la API REST (PostgREST) — permite INSERT/SELECT/UPDATE sobre tablas ya
existentes, pero PostgREST no ejecuta DDL. No hay ninguna connection string
de Postgres directa (`DATABASE_URL`/`postgresql://...`) en el `.env` de este
repo, ni CLI de `supabase` con el proyecto linkado.
**Qué hace falta:** el usuario pega el SQL en el SQL Editor del dashboard de
Supabase (https://supabase.com/dashboard), o proporciona una connection
string de Postgres con permisos DDL para esa sesión concreta.

## Cambiar "Exposed schemas" en Supabase (Settings → API)

**Fecha:** 2026-07-29
**Por qué falla:** es un toggle del dashboard web de Supabase, no expuesto
por PostgREST ni por ninguna API con la `service_role` key. Requiere login
en el dashboard (cuenta del proyecto), que el agente no tiene.
**Qué hace falta:** el usuario lo hace a mano en Dashboard → Settings → API
→ "Exposed schemas".

## Crear buckets de Storage en Supabase

**Fecha:** 2026-07-29
**Por qué falla:** mismo caso que "Exposed schemas" — la creación de buckets
vía Dashboard → Storage → New bucket no es una operación disponible con la
`service_role` key sola desde este entorno (existe API de Storage, pero no
hay credenciales/cliente configurado para ella en este repo más allá del
`service_role` REST, y el paso nunca se ha probado por esa vía).
**Qué hace falta:** el usuario los crea a mano, o confirma que se puede usar
la API de Storage con `SUPABASE_SERVICE_KEY` y lo probamos explícitamente.

## Redeploy del webhook en el VPS de producción

**Fecha:** 2026-07-29
**Por qué falla:** no hay acceso SSH configurado al VPS desde este entorno
(sin entrada en `~/.ssh/config`, sin credenciales). El redeploy (`docker
compose up -d --force-recreate`, `deploy/README.md` §6.3/§7) requiere
ejecutar comandos en esa máquina.
**Qué hace falta:** el usuario ejecuta el redeploy él mismo, o da acceso SSH
al VPS para esta sesión.
