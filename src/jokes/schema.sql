-- src/jokes/schema.sql
--
-- DDL del contrato compartido B/C (Flujos Telegram + Histórico). Boceto
-- exacto de `src/jokes/SPEC.md` §Storage — esquema de tablas, sin columnas
-- inventadas.
--
-- APLICACIÓN MANUAL (task 12): el cliente supabase-py (`SUPABASE_SERVICE_KEY`
-- vía la API REST/PostgREST) puede hacer INSERT/SELECT/UPDATE sobre tablas ya
-- existentes, pero NO puede ejecutar DDL arbitrario (CREATE TABLE, CREATE
-- EXTENSION) contra esa API REST estándar. Este fichero se aplica a mano en
-- el SQL Editor del dashboard de Supabase (Project → SQL Editor → pegar y
-- ejecutar), o vía una herramienta de migraciones si se adopta más adelante.
-- Es idempotente SOLO para crear lo que todavía no existe (`IF NOT EXISTS`
-- en extensión y tablas) — reejecutar el fichero completo nunca duplica una
-- tabla ni falla si ya está creada.
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
-- Orden de creación: tablas de taxonomía primero (temas, tecnicas, fuentes)
-- porque `chistes` las referencia por FK; `chistes_revisiones` y
-- `candidatos_taxonomia` van después. `teoria_chunks` se incluye aquí para
-- tener el esquema completo en un solo fichero (SPEC.md la documenta junto al
-- resto de §Storage), pero su cliente de acceso es scope de la task 21
-- (ingesta de teoría), no de esta tarea.

-- schema explícito a "public": pgvector puede instalarse en el schema
-- "extensions" en algunos proyectos Supabase, y si el search_path de la
-- sesión no lo incluye, el tipo "vector" no se resuelve aunque la extensión
-- ya exista (síntoma real visto al aplicar este fichero: 42704 "type vector
-- does not exist"). Forzar "public" evita depender del search_path.
create extension if not exists vector schema public;

-- ---------------------------------------------------------------------------
-- Taxonomías editables (§Taxonomías) — fuente de verdad relacional en Supabase
-- ---------------------------------------------------------------------------

create table if not exists temas (
  id         bigint generated always as identity primary key,
  nombre     text not null,
  created_at timestamptz not null default now()
);

create table if not exists tecnicas (
  id         bigint generated always as identity primary key,
  nombre     text not null,
  created_at timestamptz not null default now()
);

create table if not exists fuentes (
  id          bigint generated always as identity primary key,
  nombre      text not null,
  tipo_fuente text,
  licencia    text
);

-- ---------------------------------------------------------------------------
-- Chistes (propio | propio_historico) — topología híbrida, nativos en Supabase
-- ---------------------------------------------------------------------------

create table if not exists chistes (
  id                uuid primary key default gen_random_uuid(),
  texto_normalizado text,
  hash_normalizado  text,                          -- dedup exacto (§Reconciliación)
  embedding         vector,                         -- similitud (§Reconciliación) + retrieval RAG
  tipo_fuente       text,                           -- propio | propio_historico
  tema_id           bigint references temas(id),
  tecnica_id        bigint references tecnicas(id),
  fuente_id         bigint references fuentes(id),
  estado            text,                           -- idea_suelta|con_estructura|rematado
  version_actual    int,
  chiste_origen_id  uuid references chistes(id),    -- linaje de variante (§Versionado)
  licencia          text default 'comercializable',
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- Madurez del mismo chiste lógico en el tiempo (§Versionado) — append-only.
create table if not exists chistes_revisiones (
  id                   uuid primary key default gen_random_uuid(),
  chiste_id            uuid references chistes(id),
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
-- Solo esta tabla (no `chistes`) tiene idempotencia por evento vía UNIQUE en
-- telegram_update_id + upsert con ignore_duplicates (ON CONFLICT DO NOTHING).
-- ---------------------------------------------------------------------------

create table if not exists chistes_telegram_bronze (
  id                 uuid primary key default gen_random_uuid(),
  telegram_update_id bigint not null unique,        -- idempotencia por evento (§Bronze)
  chat_id            bigint,
  texto_raw          text not null,                 -- literal, sagrado, nunca se reescribe
  timestamp_telegram timestamptz,                   -- fecha del mensaje según Telegram
  created_at         timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Cola de revisión humana para taxonomía sin match (§Taxonomías)
-- ---------------------------------------------------------------------------

create table if not exists candidatos_taxonomia (
  id           bigint generated always as identity primary key,
  tipo         text,                                -- 'tema' | 'tecnica'
  texto        text,
  propuesto_por text,
  estado       text not null default 'pendiente',    -- pendiente|aceptado|rechazado
  created_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Teoría (Flujo A) — incluida aquí solo para tener el esquema §Storage
-- completo en un fichero; su cliente de acceso NO es scope de esta tarea
-- (ver task 21).
-- ---------------------------------------------------------------------------

create table if not exists teoria_chunks (
  id             uuid primary key default gen_random_uuid(),
  doc_id         text,                              -- path relativo dentro de v{N}/ (manifest.json)
  version_corpus text,                              -- v{N} de procedencia (src/theory/SPEC.md)
  chunk_index    int,                                -- posición del fragmento dentro del documento (task 21)
  contenido      text,
  embedding      vector,
  tipo_fuente    text,
  fuente_id      bigint references fuentes(id),
  licencia       text default 'personal_only',
  unique (doc_id, version_corpus, chunk_index)        -- idempotencia de reingesta (task 21)
);
