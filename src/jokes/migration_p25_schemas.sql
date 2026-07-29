-- src/jokes/migration_p25_schemas.sql
--
-- CUTOVER de P25 para el proyecto Supabase REAL, que ya tiene datos de
-- producción en "public" (Flujo B activo: `chistes_telegram_bronze` recibe
-- tráfico, y las tablas de chistes/taxonomías tienen filas reales). Este
-- fichero es DISTINTO de `src/jokes/schema.sql`: ese es la definición para un
-- proyecto NUEVO (crea tablas ya en su schema final); este es el SQL exacto
-- para MOVER las tablas existentes sin perder ni una fila.
--
-- NO EJECUTAR TODAVÍA. Entregable de la task 53 = fichero SQL revisable. La
-- ejecución contra el proyecto real es scope de las tasks 55 (runbook,
-- orden exacto + verificación de conteos antes/después + rollback) y 56
-- (ejecución). Este fichero da el SQL; el runbook da el CUÁNDO y el CÓMO
-- verificar.
--
-- Por qué es seguro (mecánica de Postgres, `src/jokes/SPEC.md` §Storage):
--   - `ALTER TABLE ... SET SCHEMA ...` es una operación de METADATA: cambia a
--     qué schema pertenece la tabla en el catálogo, no toca ni una fila ni
--     reescribe el heap. Coste O(1) independiente del tamaño de la tabla.
--   - `ALTER TABLE ... RENAME TO ...` es igualmente metadata.
--   - Las constraints (PK, UNIQUE, FK) sobreviven porque en Postgres apuntan
--     a OIDs de columna/tabla, no a nombres cualificados por schema. En
--     concreto, la FK `teoria_chunks.fuente_id -> fuentes(id)` (hoy
--     intra-"public") sigue viva sin recrearse tras mover ambas tablas a
--     schemas distintos ("silver" y "gold").
--   - Precondición de orden: el runbook (task 55) hace este cutover con el
--     código ya desplegado en modo *schema-aware* pero apuntando todavía a
--     "public" (task 54), para que no haya ventana en la que el código
--     busque un schema que aún no existe. El flip de esa configuración y el
--     redeploy del webhook son pasos posteriores del runbook, no de este
--     fichero.
--
-- Verificación manual (conteos antes/después, "SELECT count(*) FROM ..."
-- pre y post cutover comparados fila a fila) es EXPLÍCITAMENTE scope del
-- runbook de la task 55 / su ejecución en la task 56 — no se incluye aquí
-- como SQL ejecutable porque no es un paso de la migración, es una
-- comprobación externa a ella. Se deja como comentario al final de este
-- fichero, sin ejecutar.

-- ---------------------------------------------------------------------------
-- 0) Schemas nuevos
-- ---------------------------------------------------------------------------

create schema if not exists bronze;
create schema if not exists silver;
create schema if not exists gold;

-- ---------------------------------------------------------------------------
-- 1) Mover + renombrar las tablas EXISTENTES (datos reales, cero filas
--    movidas: solo metadata). Orden: taxonomías primero por la misma razón
--    que en schema.sql (chistes las referencia por FK, y luego
--    teoria_chunks referencia fuentes); en el cutover el orden no es
--    estrictamente necesario porque las FKs ya existen y sobreviven al
--    SET SCHEMA, pero se conserva por claridad y para que este fichero lea
--    igual que schema.sql.
-- ---------------------------------------------------------------------------

-- silver.temas
alter table public.temas set schema silver;

-- silver.tecnicas
alter table public.tecnicas set schema silver;

-- silver.fuentes (referenciada por teoria_chunks.fuente_id — la FK sobrevive
-- al SET SCHEMA sin recrearse, ver nota de cabecera)
alter table public.fuentes set schema silver;

-- silver.chistes
alter table public.chistes set schema silver;

-- silver.chistes_revisiones
alter table public.chistes_revisiones set schema silver;

-- silver.candidatos_taxonomia
alter table public.candidatos_taxonomia set schema silver;

-- bronze.chistes_telegram — este es el ÚNICO de los renombrados que además
-- cambia de NOMBRE (pierde el sufijo "_bronze", redundante una vez que la
-- capa es el schema). Es la tabla con más tráfico de producción del lote
-- (recibe updates de Flujo B en tiempo real vía procesado_at, tasks 46/47);
-- el SET SCHEMA + RENAME es igualmente metadata pura, pero se apunta aquí
-- porque es el caso donde una ventana de despliegue mal ordenada (código
-- viejo escribiendo contra "public.chistes_telegram_bronze" tras el rename)
-- se notaría antes que en el resto.
alter table public.chistes_telegram_bronze set schema bronze;
alter table bronze.chistes_telegram_bronze rename to chistes_telegram;

-- gold.teoria_chunks
alter table public.teoria_chunks set schema gold;

-- ---------------------------------------------------------------------------
-- 2) Tablas NUEVAS de documentos (P25, tasks 50/51/52) — no existen en
--    ningún proyecto todavía, así que no hay rename posible: es CREATE puro,
--    idéntico al de `src/jokes/schema.sql`. Se reproduce aquí (en vez de
--    solo referenciar el otro fichero) para que este SQL sea ejecutable de
--    una sola pasada en el SQL Editor sin tener que abrir dos ficheros a la
--    vez durante el cutover real.
-- ---------------------------------------------------------------------------

-- --- Teoría (Flujo A) ---

create table if not exists bronze.teoria_documentos (
  id             uuid primary key default gen_random_uuid(),
  bucket         text not null,
  object_path    text not null,
  drive_file_id  text,
  modified_time  text,
  hash_md5       text not null,
  origen         text not null check (origen in ('drive', 'local_legacy')),
  nombre         text not null,
  mime_type      text,
  tamano_bytes   bigint,
  tipo_fuente    text,
  licencia       text default 'personal_only',
  ruta_relativa  text,
  created_at     timestamptz not null default now()
);

create unique index if not exists bronze_teoria_documentos_drive_uniq
  on bronze.teoria_documentos (drive_file_id, modified_time)
  where drive_file_id is not null;

create unique index if not exists bronze_teoria_documentos_legacy_uniq
  on bronze.teoria_documentos (hash_md5)
  where drive_file_id is null;

create table if not exists silver.teoria_documentos (
  id                   uuid primary key default gen_random_uuid(),
  bucket               text not null,
  object_path          text not null,
  drive_file_id        text,
  modified_time        text,
  hash_md5             text not null,
  origen               text not null check (origen in ('drive', 'local_legacy')),
  nombre               text not null,
  mime_type            text,
  tamano_bytes         bigint,
  origen_hash_md5      text not null,
  origen_drive_file_id text,
  origen_modified_time text,
  fuente               text,
  autor                text,
  tipo_fuente          text,
  licencia             text default 'personal_only',
  idioma_original      text,
  traducido            boolean,
  num_fragmentos       int,
  quality_score        double precision,
  created_at           timestamptz not null default now()
);

create unique index if not exists silver_teoria_documentos_drive_uniq
  on silver.teoria_documentos (drive_file_id, modified_time)
  where drive_file_id is not null;

create unique index if not exists silver_teoria_documentos_legacy_uniq
  on silver.teoria_documentos (hash_md5)
  where drive_file_id is null;

-- --- Histórico (Flujo C) ---

create table if not exists bronze.historico_documentos (
  id             uuid primary key default gen_random_uuid(),
  bucket         text not null,
  object_path    text not null,
  drive_file_id  text,
  modified_time  text,
  hash_md5       text not null,
  origen         text not null check (origen in ('drive', 'local_legacy')),
  nombre         text not null,
  mime_type      text,
  tamano_bytes   bigint,
  mime_origen    text,
  created_at     timestamptz not null default now()
);

create unique index if not exists bronze_historico_documentos_drive_uniq
  on bronze.historico_documentos (drive_file_id, modified_time)
  where drive_file_id is not null;

create unique index if not exists bronze_historico_documentos_legacy_uniq
  on bronze.historico_documentos (hash_md5)
  where drive_file_id is null;

create table if not exists silver.historico_documentos (
  id             uuid primary key default gen_random_uuid(),
  bucket         text not null,
  object_path    text not null,
  drive_file_id  text,
  modified_time  text,
  hash_md5       text not null,
  origen         text not null check (origen in ('drive', 'local_legacy')),
  nombre         text not null,
  mime_type      text,
  tamano_bytes   bigint,
  n_remates      int,
  n_chistoides   int,
  created_at     timestamptz not null default now()
);

create unique index if not exists silver_historico_documentos_drive_uniq
  on silver.historico_documentos (drive_file_id, modified_time)
  where drive_file_id is not null;

create unique index if not exists silver_historico_documentos_legacy_uniq
  on silver.historico_documentos (hash_md5)
  where drive_file_id is null;

-- ---------------------------------------------------------------------------
-- 3) GRANTs a service_role (idénticos a schema.sql, necesarios también aquí
--    porque el proyecto real no ha tenido nunca privilegios en bronze/
--    silver/gold: hasta este cutover esos schemas no existían).
-- ---------------------------------------------------------------------------

grant usage on schema bronze, silver, gold to service_role;
grant all on all tables in schema bronze, silver, gold to service_role;
alter default privileges in schema bronze, silver, gold
  grant all on tables to service_role;

-- No se concede nada a "anon" ni a "authenticated" (material personal_only).

-- ---------------------------------------------------------------------------
-- 4) Refresco de caché de PostgREST — OBLIGATORIO tras mover/crear tablas.
--    Sin esto, PostgREST puede seguir sirviendo el esquema viejo y devolver
--    PGRST204 ("could not find the column ... in the schema cache") aunque
--    el DDL ya se haya aplicado correctamente.
-- ---------------------------------------------------------------------------

notify pgrst, 'reload schema';

-- ---------------------------------------------------------------------------
-- Pasos MANUALES que este fichero NO cubre (fuera de scope de la task 53):
--
--   - Exposed schemas: Supabase Dashboard -> Settings -> API -> "Exposed
--     schemas" -> añadir "bronze", "silver", "gold". No es SQL, no se puede
--     hacer desde este fichero.
--   - Verificación de conteos antes/después (runbook, tasks 55/56):
--
--       -- ANTES del cutover, contra "public":
--       -- select count(*) from public.chistes;
--       -- select count(*) from public.chistes_revisiones;
--       -- select count(*) from public.temas;
--       -- select count(*) from public.tecnicas;
--       -- select count(*) from public.fuentes;
--       -- select count(*) from public.candidatos_taxonomia;
--       -- select count(*) from public.chistes_telegram_bronze;
--       -- select count(*) from public.teoria_chunks;
--
--       -- DESPUÉS del cutover, contra los schemas nuevos, comparando fila a
--       -- fila contra los conteos de arriba (deben ser IDÉNTICOS: SET SCHEMA
--       -- + RENAME no crea ni destruye filas):
--       -- select count(*) from silver.chistes;
--       -- select count(*) from silver.chistes_revisiones;
--       -- select count(*) from silver.temas;
--       -- select count(*) from silver.tecnicas;
--       -- select count(*) from silver.fuentes;
--       -- select count(*) from silver.candidatos_taxonomia;
--       -- select count(*) from bronze.chistes_telegram;
--       -- select count(*) from gold.teoria_chunks;
--
--   - Flip del schema por defecto en `supabase_store.py` de "public" a
--     "bronze"/"silver"/"gold" (task 54 ya lo deja *schema-aware*; el flip
--     de configuración y el redeploy del webhook son pasos del runbook,
--     tasks 55/56, posteriores a este SQL).
--   - Rollback: dado que SET SCHEMA/RENAME es reversible con el ALTER TABLE
--     simétrico (RENAME TO original + SET SCHEMA public), el procedimiento
--     exacto de vuelta atrás y cuándo invocarlo es del runbook (task 55), no
--     de este fichero.
-- ---------------------------------------------------------------------------
