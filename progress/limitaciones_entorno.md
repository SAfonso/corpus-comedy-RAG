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

## Ejecutar `scripts/run_historico.py` contra el Drive real del histórico (task 67)

**Fecha:** 2026-07-30
**Por qué falla:** dos capas de la misma causa. (1) En local,
`GOOGLE_APPLICATION_CREDENTIALS=./secrets/google-service-account.json` (`.env`)
apunta a un fichero que no existe en este entorno — `secrets/` ni siquiera
está creado. (2) El workflow `run_historico_semanal.yml` (`workflow_dispatch`
con input `dry_run`, pensado exactamente para este caso) tampoco sirve de
alternativa: `gh secret list` solo devuelve `SSH_HOST`/`SSH_PORT`/
`SSH_PRIVATE_KEY`/`SSH_USER` (los del deploy al VPS) — ninguno de los
secrets que ese workflow espera (`GOOGLE_SERVICE_ACCOUNT_JSON`,
`DRIVE_FOLDER_ID_HISTORICO`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
`LLM_API_KEY`, `LLM_MODEL`, `EMBEDDINGS_API_KEY`, `EMBEDDINGS_MODEL`, las tres
del gate de coste) está configurado en el repo de GitHub todavía — el cron
semanal (sábados 03:00 UTC) nunca ha corrido con éxito por lo mismo.
**Qué hace falta:** el usuario, o bien (a) pega el JSON completo de la cuenta
de servicio de Google en `secrets/google-service-account.json` local (fichero
gitignored) para correr `run_historico.py` en esta sesión, o bien (b) da de
alta los secrets que lista arriba en Settings → Secrets and variables →
Actions del repo de GitHub, para que tanto el cron semanal como un
`workflow_dispatch` manual funcionen sin intervención local.
