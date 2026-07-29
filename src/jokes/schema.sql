-- src/jokes/schema.sql
--
-- DDL del contrato compartido B/C (Flujos Telegram + Histórico) MÁS las
-- tablas de documentos de Teoría (Flujo A) e Histórico (Flujo C) que P25
-- añade a este mismo fichero (ver nota de alcance más abajo). Boceto exacto
-- de `src/jokes/SPEC.md` §Storage, `src/utils/SPEC.md` §DocumentStore,
-- `src/theory/SPEC.md` §Storage y `src/jokes/historico/SPEC.md` §Entrada y
-- etapas — sin columnas inventadas.
--
-- P25 (task 53, 2026-07-29): las capas Bronze/Silver/Gold pasan de ser una
-- convención de nombres dentro de "public" a ser SCHEMAS de Postgres reales.
-- Este fichero es la versión para un PROYECTO NUEVO (crea los schemas y
-- todas las tablas ya en su schema final, con los nombres ya sin sufijo de
-- capa). Para el proyecto REAL, que ya tiene datos de producción en
-- "public" (Flujo B activo), el cutover se hace con el SQL de
-- `src/jokes/migration_p25_schemas.sql` (ALTER TABLE ... SET SCHEMA +
-- RENAME TO, metadata pura, no mueve filas) — no reejecutar este fichero
-- contra ese proyecto pensando que es equivalente al cutover.
--
-- APLICACIÓN MANUAL (task 12): el cliente supabase-py (`SUPABASE_SERVICE_KEY`
-- vía la API REST/PostgREST) puede hacer INSERT/SELECT/UPDATE sobre tablas ya
-- existentes, pero NO puede ejecutar DDL arbitrario (CREATE TABLE, CREATE
-- EXTENSION) contra esa API REST estándar. Este fichero se aplica a mano en
-- el SQL Editor del dashboard de Supabase (Project → SQL Editor → pegar y
-- ejecutar), o vía una herramienta de migraciones si se adopta más adelante.
-- Es idempotente SOLO para crear lo que todavía no existe (`IF NOT EXISTS`
-- en schemas, extensión, tablas e índices) — reejecutar el fichero completo
-- nunca duplica una tabla ni falla si ya está creada.
--
-- OJO — esto NO cubre ampliar una tabla que YA existe (columna o constraint
-- nuevos añadidos en una task posterior a la que creó la tabla):
-- `create table if not exists` comprueba solo si la tabla existe, nunca
-- diffea sus columnas contra la definición de abajo — si la tabla ya está
-- creada, la sentencia es un no-op completo y la columna nueva NO se añade
-- (confirmado: `teoria_chunks.chunk_index`, task 21, quedó sin crear tras
-- "reaplicar schema.sql" hasta correr un `ALTER TABLE` aparte — ver
-- `docs/specs/KNOWN_ERRORS_GLOBAL.md`). Toda task que amplíe una tabla
-- preexistente debe entregar el `ALTER TABLE` explícito (en el PR/reporte,
-- no solo actualizar la definición de aquí) además de mantener este fichero
-- como documentación del esquema deseado final.
--
-- Orden de creación: los tres schemas primero; luego, dentro de "silver",
-- las tablas de taxonomía (temas, tecnicas, fuentes) porque `chistes` las
-- referencia por FK; `chistes_revisiones` y `candidatos_taxonomia` van
-- después. `bronze.chistes_telegram` no depende de taxonomías. Las cuatro
-- tablas de documentos (bronze/silver × teoria/historico, tasks 50/51/52)
-- van después, sin dependencias entre sí. `gold.teoria_chunks` se incluye al
-- final porque tiene una FK cross-schema a `silver.fuentes` (ver nota más
-- abajo) y por tanto debe crearse después de que "silver" exista completo;
-- su cliente de acceso es scope de `src/theory/teoria_store.py` (task 21),
-- no de esta tarea.

-- schema explícito a "public": pgvector puede instalarse en el schema
-- "extensions" en algunos proyectos Supabase, y si el search_path de la
-- sesión no lo incluye, el tipo "vector" no se resuelve aunque la extensión
-- ya exista (síntoma real visto al aplicar este fichero: 42704 "type vector
-- does not exist"). Forzar "public" evita depender del search_path.
--
-- P25 NO mueve esta extensión: sigue en "public" tal cual, aunque las tablas
-- que usan el tipo "vector" (`silver.chistes.embedding`,
-- `gold.teoria_chunks.embedding`) ya no vivan ahí. Esto funciona porque
-- "public" está en el search_path por defecto de `service_role`
-- (`"$user", public`) — pero es una EXPECTATIVA, no un hecho verificado en
-- este proyecto (`src/jokes/SPEC.md` §"pgvector sigue en public"). Si al
-- aplicar este DDL aparece 42704 sobre "vector" en una tabla de
-- bronze/silver/gold, la causa es el search_path de la sesión, y la
-- solución es cualificar el tipo (`public.vector`) o ampliar el
-- search_path — nunca reinstalar la extensión en otro schema (rompería las
-- tablas que ya la usan). Documentar en `src/jokes/KNOWN_ERRORS.md` si
-- llega a ocurrir.
create extension if not exists vector schema public;

-- ---------------------------------------------------------------------------
-- Schemas (P25): las capas son schemas reales, no sufijos en el nombre de la
-- tabla. `chistes_telegram_bronze` -> `bronze.chistes_telegram`, `chistes` ->
-- `silver.chistes`, `teoria_chunks` -> `gold.teoria_chunks`. Ninguna columna
-- cambia en esta reorganización, solo dónde vive la tabla (schema) y, en un
-- caso, cómo se llama.
-- ---------------------------------------------------------------------------

create schema if not exists bronze;
create schema if not exists silver;
create schema if not exists gold;

-- ---------------------------------------------------------------------------
-- Taxonomías editables (§Taxonomías) — fuente de verdad relacional en Supabase
-- ---------------------------------------------------------------------------

create table if not exists silver.temas (
  id         bigint generated always as identity primary key,
  nombre     text not null,
  created_at timestamptz not null default now()
);

create table if not exists silver.tecnicas (
  id         bigint generated always as identity primary key,
  nombre     text not null,
  created_at timestamptz not null default now()
);

create table if not exists silver.fuentes (
  id          bigint generated always as identity primary key,
  nombre      text not null,
  tipo_fuente text,
  licencia    text
);

-- ---------------------------------------------------------------------------
-- Chistes (propio | propio_historico) — topología híbrida, nativos en Supabase
-- ---------------------------------------------------------------------------

create table if not exists silver.chistes (
  id                uuid primary key default gen_random_uuid(),
  texto_normalizado text,
  hash_normalizado  text,                          -- dedup exacto (§Reconciliación)
  embedding         vector,                         -- similitud (§Reconciliación) + retrieval RAG
  tipo_fuente       text,                           -- propio | propio_historico
  tema_id           bigint references silver.temas(id),
  tecnica_id        bigint references silver.tecnicas(id),
  fuente_id         bigint references silver.fuentes(id),
  estado            text,                           -- idea_suelta|con_estructura|rematado
  version_actual    int,
  chiste_origen_id  uuid references silver.chistes(id),  -- linaje de variante (§Versionado)
  licencia          text default 'comercializable',
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- Madurez del mismo chiste lógico en el tiempo (§Versionado) — append-only.
create table if not exists silver.chistes_revisiones (
  id                   uuid primary key default gen_random_uuid(),
  chiste_id            uuid references silver.chistes(id),
  version              int,
  contenido            text,
  estructura_detectada jsonb,
  estado               text,
  sugerencias_mejora   text,
  created_at           timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Bronze de Telegram (Flujo B, task 16, telegram/SPEC.md §Bronze) — captura
-- literal e inmutable de cada mensaje, equivalente a /data/raw/ para teoría.
-- Solo esta tabla (no `silver.chistes`) tiene idempotencia por evento vía
-- UNIQUE en telegram_update_id + upsert con ignore_duplicates (ON CONFLICT
-- DO NOTHING).
--
-- Nombre: P25 retira el sufijo "_bronze" (`chistes_telegram_bronze` ->
-- `bronze.chistes_telegram`) porque la capa ya la dice el schema; repetirla
-- en el nombre es ruido que además se desincroniza si la tabla cambiara de
-- capa (`src/jokes/SPEC.md` §"Las capas son schemas, no sufijos").
-- ---------------------------------------------------------------------------

create table if not exists bronze.chistes_telegram (
  id                 uuid primary key default gen_random_uuid(),
  telegram_update_id bigint not null unique,        -- idempotencia por evento (§Bronze)
  chat_id            bigint,
  texto_raw          text not null,                 -- literal, sagrado, nunca se reescribe
  timestamp_telegram timestamptz,                   -- fecha del mensaje según Telegram
  procesado_at       timestamptz,                   -- bookkeeping: NULL hasta que pipeline completó (recuperación post-200, task 46/47)
  created_at         timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Cola de revisión humana para taxonomía sin match (§Taxonomías)
-- ---------------------------------------------------------------------------

create table if not exists silver.candidatos_taxonomia (
  id           bigint generated always as identity primary key,
  tipo         text,                                -- 'tema' | 'tecnica'
  texto        text,
  propuesto_por text,
  estado       text not null default 'pendiente',    -- pendiente|aceptado|rechazado
  created_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Documentos (P25) — captura durable en Supabase de los originales de los
-- Flujos A (Teoría) y C (Histórico), vía `src/utils/document_store.py`
-- (`DocumentStore.capturar()`, task 58). Las cuatro tablas comparten la
-- misma "base" de columnas que el componente escribe siempre (bucket,
-- object_path, drive_file_id, modified_time, hash_md5, origen, nombre,
-- mime_type, tamano_bytes) más las columnas propias de cada flujo/capa que
-- fijan `src/theory/SPEC.md` (tasks 51) y `src/jokes/historico/SPEC.md`
-- (task 52), pasadas vía el `extra` de `capturar()`.
--
-- `drive_file_id`/`modified_time` viajan NULLABLE: en modo Drive llevan el
-- par real (`fileId`/`modifiedTime` de la API); en modo legacy (material que
-- ya estaba en `/data/raw/` antes de Drive-real, sin metadata de Drive) van
-- NULL y la clave de idempotencia pasa a ser `hash_md5`.
--
-- `modified_time` se guarda como `text`, no `timestamptz`: el componente lo
-- trata expresamente como "el str RFC 3339 que da Drive, tal cual, sin
-- reparsear" (`src/utils/SPEC.md` §DocumentStore) porque ese mismo string
-- literal es uno de los dos segmentos que componen la clave del objeto en
-- el bucket (`drive/{drive_file_id}/{modified_time_compacto}/{nombre}`). Si
-- la columna fuese `timestamptz`, Postgres normalizaría la representación
-- (zona horaria, precisión de fracción de segundo) y un valor leído de vuelta
-- de la fila podría no coincidir byte a byte con el que se usó para
-- construir la clave del objeto — rompiendo justo la propiedad que motiva el
-- naming determinista. Ninguna de las cuatro specs (49-52) fija el tipo SQL
-- exacto de esta columna de forma explícita; esta es la decisión más
-- conservadora dado el "sin reparsear" repetido en `src/utils/SPEC.md`, y
-- queda documentada aquí y en el reporte de la task 53 por si el leader
-- prefiere zanjarlo con una enmienda de spec.
--
-- `origen` es `text` con los valores cerrados `'drive'` | `'local_legacy'`
-- (`DocumentStore.ORIGENES`); se documenta con CHECK explícito porque, a
-- diferencia de otros campos de estado de este fichero (`estado`,
-- `tipo_fuente`…), el propio DDL de P25 construye la unicidad alrededor de
-- ese valor (qué índice parcial aplica depende de si `drive_file_id` es NULL,
-- que es 1:1 con `origen`), así que un tercer valor rompería silenciosamente
-- esa correspondencia.
-- ---------------------------------------------------------------------------

-- --- Teoría (Flujo A) — `src/theory/SPEC.md` §Storage (task 51) ---

create table if not exists bronze.teoria_documentos (
  id             uuid primary key default gen_random_uuid(),
  bucket         text not null,                     -- 'bronze-teoria'
  object_path    text not null,                     -- clave del objeto en el bucket privado
  drive_file_id  text,                              -- NULL en modo legacy (7 libros + 25 transcripciones pre-Drive-real)
  modified_time  text,                              -- RFC 3339 tal cual de Drive, sin reparsear; NULL en legacy (ver nota de cabecera del bloque)
  hash_md5       text not null,                     -- MD5 del original; clave de idempotencia en legacy, verificación objeto<->fila en modo Drive
  origen         text not null check (origen in ('drive', 'local_legacy')),
  nombre         text not null,
  mime_type      text,
  tamano_bytes   bigint,
  tipo_fuente    text,                              -- 'teoria' | 'transcripcion_curso', derivado de la extensión (extra de teoría)
  licencia       text default 'personal_only',      -- atributo legal del ORIGINAL (extra de teoría)
  ruta_relativa  text,                              -- ruta relativa a la raíz de escaneo en legacy (atribución por ponente); NULL en modo Drive
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
  bucket               text not null,                -- 'silver-teoria'
  object_path          text not null,                -- clave del .md en el bucket privado
  drive_file_id        text,                          -- SIEMPRE NULL en esta tabla (decisión task 51: la fila Silver se escribe en modo legacy)
  modified_time        text,                          -- SIEMPRE NULL en esta tabla, mismo motivo
  hash_md5             text not null,                 -- hash del .md renderizado; clave real de idempotencia de esta tabla
  origen               text not null check (origen in ('drive', 'local_legacy')),  -- SIEMPRE 'local_legacy' aquí (ver nota abajo)
  nombre               text not null,
  mime_type            text,
  tamano_bytes         bigint,
  origen_hash_md5      text not null,                -- MD5 del ORIGINAL (fila Bronze de la que deriva) — columna de linaje que manda, NOT NULL
  origen_drive_file_id text,                          -- copia del par de Drive de la fila Bronze; NULL si el Bronze es legacy
  origen_modified_time text,
  fuente               text,                          -- título legible (ingest_teoria.buscar_o_crear_fuente)
  autor                text,                          -- placeholder P23, NULL hasta que haya fuente real de metadatos
  tipo_fuente          text,                          -- propagado desde Bronze
  licencia             text default 'personal_only',  -- propagada desde Bronze
  idioma_original      text,                          -- LanguageDetector
  traducido            boolean,                       -- si LanguageNormalizer tradujo al español
  num_fragmentos       int,
  quality_score        double precision,              -- media de score_quality (0-1) por fragmento del documento
  created_at           timestamptz not null default now()
);

-- Nota sobre unicidad en esta tabla concreta: `drive_file_id` es SIEMPRE NULL
-- aquí (decisión de la task 51, `src/theory/SPEC.md` §"La fila Silver no
-- reutiliza el par de Drive de su Bronze"), así que en la práctica solo
-- muerde el índice parcial "legacy" de abajo — que para esta tabla equivale a
-- `unique(hash_md5)`, tal y como fija esa spec explícitamente. El índice
-- "drive" se crea igual, por consistencia estructural con las otras tres
-- tablas de documentos y porque el modo Drive sigue siendo válido para el
-- componente compartido aunque este flujo no lo ejercite en esta tabla.
create unique index if not exists silver_teoria_documentos_drive_uniq
  on silver.teoria_documentos (drive_file_id, modified_time)
  where drive_file_id is not null;

create unique index if not exists silver_teoria_documentos_legacy_uniq
  on silver.teoria_documentos (hash_md5)
  where drive_file_id is null;

-- --- Histórico (Flujo C) — `src/jokes/historico/SPEC.md` §Entrada y etapas (task 52) ---

create table if not exists bronze.historico_documentos (
  id             uuid primary key default gen_random_uuid(),
  bucket         text not null,                     -- 'bronze-historico'
  object_path    text not null,                     -- clave del .docx original en el bucket privado
  drive_file_id  text,                              -- en la práctica siempre NOT NULL: Histórico no tiene material legacy (ver nota abajo)
  modified_time  text,                              -- RFC 3339 tal cual de Drive, sin reparsear
  hash_md5       text not null,                     -- MD5 del .docx original; verificación objeto<->fila
  origen         text not null check (origen in ('drive', 'local_legacy')),
  nombre         text not null,
  mime_type      text,                              -- siempre DOCX (mime_salida): el objeto guardado, no el de origen en Drive
  tamano_bytes   bigint,
  mime_origen    text,                              -- MIME de origen en Drive (extra de histórico): distingue .docx subido de export de Google Doc nativo
  created_at     timestamptz not null default now()
);

-- Nota: esta tabla NO lleva `tipo_fuente` ni `licencia` — `src/jokes/historico/SPEC.md`
-- confirma explícitamente que toda fila de aquí es `propio_historico` (una
-- columna constante para toda la tabla sería el schema repitiendo su propio
-- nombre) y que `origen` es siempre 'drive' en la práctica (el backfill
-- legacy, task 66, es solo de teoría). Se mantiene la columna `origen` con
-- el mismo dominio que el resto de tablas de documentos porque la fija el
-- componente compartido (`DocumentStore.ORIGENES`), no este flujo.
create unique index if not exists bronze_historico_documentos_drive_uniq
  on bronze.historico_documentos (drive_file_id, modified_time)
  where drive_file_id is not null;

create unique index if not exists bronze_historico_documentos_legacy_uniq
  on bronze.historico_documentos (hash_md5)
  where drive_file_id is null;

create table if not exists silver.historico_documentos (
  id             uuid primary key default gen_random_uuid(),
  bucket         text not null,                     -- 'silver-historico'
  object_path    text not null,                     -- clave del .md marcado en el bucket privado
  drive_file_id  text,                              -- mismo par que su fila Bronze (join directo, NO FK — ver nota abajo)
  modified_time  text,
  hash_md5       text not null,                     -- hash del .md marcado; verificación objeto<->fila
  origen         text not null check (origen in ('drive', 'local_legacy')),
  nombre         text not null,
  mime_type      text,
  tamano_bytes   bigint,
  n_remates      int,                               -- nº de [REMATE] del .md (extra de histórico): chistes esperados de este documento
  n_chistoides   int,                                -- nº de [CHISTOIDE] del .md (extra de histórico)
  created_at     timestamptz not null default now()
);

-- Nota: NO hay columna `bronze_id` en esta tabla. `src/jokes/historico/SPEC.md`
-- §"Cómo referencia Silver a su Bronze" es explícita en que el linaje
-- Bronze->Silver de este flujo es el MISMO par `(drive_file_id, modified_time)`
-- en las dos tablas (un JOIN directo), no una FK — una FK sería una segunda
-- fuente de verdad para la misma relación. Se prohíbe expresamente añadirla.
create unique index if not exists silver_historico_documentos_drive_uniq
  on silver.historico_documentos (drive_file_id, modified_time)
  where drive_file_id is not null;

create unique index if not exists silver_historico_documentos_legacy_uniq
  on silver.historico_documentos (hash_md5)
  where drive_file_id is null;

-- ---------------------------------------------------------------------------
-- Teoría (Flujo A) — capa Gold. Incluida aquí solo para tener el esquema
-- §Storage completo en un fichero; su cliente de acceso NO es scope de esta
-- tarea (ver task 21). No hay `gold` de chistes: `silver.chistes` ya es la
-- unidad indexable que consume el RAG (P25).
--
-- FK CROSS-SCHEMA: `fuente_id` referencia `silver.fuentes(id)` desde `gold`.
-- Funciona exactamente igual que una FK normal en Postgres (misma sintaxis,
-- misma integridad referencial); lo único que cambia es que la referencia se
-- cualifica siempre con el schema (`references silver.fuentes(id)`) para no
-- depender del search_path de la sesión que ejecuta el DDL — mismo criterio
-- que `create extension ... schema public` arriba. Por eso `silver.fuentes`
-- se crea ANTES que esta tabla en este fichero (`src/jokes/SPEC.md` §"FK
-- cross-schema").
-- ---------------------------------------------------------------------------

create table if not exists gold.teoria_chunks (
  id             uuid primary key default gen_random_uuid(),
  doc_id         text,                              -- silver.teoria_documentos.id (texto), tras P25 (task 61)
  version_corpus text,                              -- silver.teoria_documentos.hash_md5, tras P25 (task 61)
  chunk_index    int,                                -- posición del fragmento dentro del documento (task 21)
  contenido      text,
  embedding      vector,
  tipo_fuente    text,
  fuente_id      bigint references silver.fuentes(id),  -- FK CROSS-SCHEMA, ver nota arriba
  licencia       text default 'personal_only',
  unique (doc_id, version_corpus, chunk_index)        -- idempotencia de reingesta (task 21)
);

-- ---------------------------------------------------------------------------
-- Exposed schemas + GRANTs (P25, task 49) — paso MANUAL además de este SQL:
-- Supabase Dashboard -> Settings -> API -> "Exposed schemas" debe incluir
-- "bronze", "silver" y "gold" (por defecto solo "public" está expuesto); sin
-- eso, `.schema("bronze")` falla aunque la tabla exista y los datos estén
-- bien. Ese paso NO es SQL y no se puede automatizar desde este fichero.
--
-- El bloque de abajo sí es SQL y se ejecuta en el mismo SQL Editor, en la
-- misma pasada que el resto de este fichero.
-- ---------------------------------------------------------------------------

grant usage on schema bronze, silver, gold to service_role;
grant all on all tables in schema bronze, silver, gold to service_role;

-- La sentencia de abajo es la que evita el error recurrente: "grant ... on
-- all tables" solo afecta a las tablas que existen EN ESE MOMENTO, así que
-- una tabla creada después (p.ej. una migración futura) quedaría inaccesible
-- hasta repetir el GRANT a mano. `alter default privileges` lo resuelve de
-- una vez para las tablas futuras de estos tres schemas.
alter default privileges in schema bronze, silver, gold
  grant all on tables to service_role;

-- No se concede nada a "anon" ni a "authenticated": el corpus incluye
-- material con licencia "personal_only" (`docs/specs/llm-policy.md`) y
-- ningún cliente público lo consume. Exponer los schemas a PostgREST no debe
-- confundirse con abrirlos a la clave anónima.

-- Nota de secuencias: `temas`, `tecnicas`, `fuentes` y `candidatos_taxonomia`
-- usan `bigint generated always as identity`, cuya secuencia es interna a la
-- columna y NO requiere un GRANT propio (a diferencia de `serial`). Red de
-- seguridad si apareciera "permission denied for sequence" al aplicar esto
-- contra un proyecto real (documentar en KNOWN_ERRORS.md si llega a hacer
-- falta):
-- grant usage, select on all sequences in schema silver to service_role;

-- Recordatorio operativo (no ejecutable aquí, es responsabilidad del
-- runbook de cutover, task 55/56): tras mover o crear tablas, PostgREST
-- puede seguir sirviendo el esquema de caché viejo (síntoma: PGRST204
-- "could not find the column ... in the schema cache"). Se fuerza el
-- refresco con `notify pgrst, 'reload schema';` — comprobación obligatoria
-- antes de dar por bueno cualquier cutover contra un proyecto con datos
-- reales.
