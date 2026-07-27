# Comedy Corpus Pipeline — Overview y directriz de lectura

> **Estado:** v2 (multi-fuente) · **Metodología:** SDD — spec aprobada ANTES de
> escribir código. **Documento vivo:** decisiones P1–P14, más P15 (marcado
> histórico por color, 2026-07-06), P16 (loops LLM, 2026-07-09), P17
> (markitdown para Parser de teoría, 2026-07-21), P18 (DriveMonitor sobre
> carpeta local, Drive API real diferida, 2026-07-22), P19 (Flujo C lee de
> carpeta Drive real vía `drive_source.py`, 2026-07-24), P20 (candidatos de
> reconciliación filtrados por `tipo_fuente`, 2026-07-24), P21 (Flujo C:
> ejecución semanal desatendida vía GitHub Actions, `run_historico_semanal.yml`,
> 2026-07-26), P22 (Flujo B: transporte por webhook, 200 antes del tramo LLM,
> 2026-07-27) y P23 (Flujo A: Drive real deja de estar diferido — cierra P18 —
> con núcleo compartido `src/utils/drive_sync.py` y staging propio
> `data/staging/theory/`, 2026-07-27).

Este documento es el **punto de entrada**. La spec completa ya no vive en un solo
fichero: está partida por módulo, colocada **dentro de `src/`**, junto al código
que gobierna cada una. Lee solo lo que tu tarea necesita.

## Directriz: qué leer según qué vas a tocar

| Si vas a tocar...                                                 | Spec | Errores conocidos |
|--------------------------------------------------------------------|--------|----------------------|
| `src/theory/**` (Flujo A, teoría)                                  | [`src/theory/SPEC.md`](../../src/theory/SPEC.md) + [`llm-policy.md`](llm-policy.md) (regla "no LLM") | [`src/theory/KNOWN_ERRORS.md`](../../src/theory/KNOWN_ERRORS.md) |
| `src/jokes/telegram/**` (Flujo B, Telegram)                        | [`src/jokes/telegram/SPEC.md`](../../src/jokes/telegram/SPEC.md) + [`src/jokes/SPEC.md`](../../src/jokes/SPEC.md) + [`llm-policy.md`](llm-policy.md) | [`src/jokes/telegram/KNOWN_ERRORS.md`](../../src/jokes/telegram/KNOWN_ERRORS.md) |
| `src/jokes/silver.py`, `reconciliacion.py`, `supabase_store.py` (compartido B/C) | [`src/jokes/SPEC.md`](../../src/jokes/SPEC.md) + [`llm-policy.md`](llm-policy.md) | [`src/jokes/KNOWN_ERRORS.md`](../../src/jokes/KNOWN_ERRORS.md) |
| `src/jokes/historico/**`, `scripts/marcar_remates.py` (Flujo C)    | [`src/jokes/historico/SPEC.md`](../../src/jokes/historico/SPEC.md) + [`src/jokes/SPEC.md`](../../src/jokes/SPEC.md) | [`src/jokes/historico/KNOWN_ERRORS.md`](../../src/jokes/historico/KNOWN_ERRORS.md) |
| `src/utils/**`                                                     | [`src/utils/SPEC.md`](../../src/utils/SPEC.md) (apunta al consumidor real) | [`src/utils/KNOWN_ERRORS.md`](../../src/utils/KNOWN_ERRORS.md) |
| Nueva fuente, `tipo_fuente`, layout global, regla de dependencias entre módulos | Este documento | [`KNOWN_ERRORS_GLOBAL.md`](KNOWN_ERRORS_GLOBAL.md) |

**No hace falta leer los cinco specs para tocar uno.** `src/jokes/telegram/SPEC.md`
e `historico/SPEC.md` remiten a `src/jokes/SPEC.md` para lo que comparten
(Silver, Reconciliación, Taxonomías) en vez de repetirlo — si tu tarea es
puramente de un flujo, con su spec + el compartido basta.

**Protocolo de errores conocidos:** antes de depurar un error por prueba y
error, busca primero en el `KNOWN_ERRORS.md` del módulo (tabla de arriba). Si
ya ocurrió, aplica la solución documentada. Si es nuevo, documéntalo ahí (o en
`KNOWN_ERRORS_GLOBAL.md` si cruza módulos) al resolverlo — regla completa en
`CLAUDE.md`.

## 1. Propósito y alcance

Pipeline de ingesta, limpieza, estructuración y versionado de datos para el
Comedy RAG. Transforma fuentes heterogéneas en un corpus consultable,
**diferenciando el origen de cada fragmento** (`tipo_fuente`) para permitir
retrieval separado por fuente y combinarlo en el prompt final del RAG
("ejemplos del autor" vs "técnica de fuentes externas").

El pipeline cubre **tres flujos**, independientes en orquestación pero unidos
por un contrato común (`tipo_fuente`) y un índice de consulta compartido:

- **Flujo A — Teoría:** libros y cursos de comedia desde Google Drive (batch).
  Limpieza determinista agresiva, traducción, salida a ficheros versionados
  en `/data/processed/v{N}/`. Spec: `src/theory/SPEC.md`.
- **Flujo B — Chistes propios (Telegram):** ingesta en tiempo real, chiste a
  chiste, vía bot de Telegram. Arquitectura Bronze→Silver, estructuración por LLM.
  Spec: `src/jokes/telegram/SPEC.md` + `src/jokes/SPEC.md`.
- **Flujo C — Chistes históricos:** procesado retroactivo (batch) de textos
  propios ya escritos, con varios chistes por documento y remates marcados.
  Spec: `src/jokes/historico/SPEC.md` + `src/jokes/SPEC.md`.

**Regla invariante de todos los flujos:** el material original es sagrado
(`/data/raw/` para teoría, la capa Bronze para chistes). Nunca se modifica,
elimina ni sobrescribe. Todo el trabajo ocurre aguas abajo.

**Fuera de alcance (por ahora):**
- Grafo de conocimiento tipo GraphRAG / Leiden clustering — descartado por
  falta de volumen; se sustituye por un "grafo ligero" relacional
  (ver `src/jokes/SPEC.md`).
- Fine-tuning — se usa RAG multi-fuente en su lugar (decisión cerrada).

## 2. Fuentes y `tipo_fuente`

`tipo_fuente` es el discriminador que atraviesa todo el pipeline y el RAG. Es un
**enum cerrado** (valores fijos; añadir uno es un cambio de spec, no algo que el
LLM decida):

| `tipo_fuente`         | Qué es                          | Trigger             | Storage destino        | Limpieza            |
|-----------------------|----------------------------------|----------------------|--------------------------|----------------------|
| `teoria`              | Libros de comedia               | Batch (Drive)       | Ficheros `v{N}` + índice | Agresiva (determ.) |
| `transcripcion_curso` | Cursos transcritos (WhisperX)   | Batch (Drive)       | Ficheros `v{N}` + índice | Agresiva (determ.) |
| `propio`              | Chistes propios en tiempo real  | Realtime (Telegram) | Supabase               | Propia (Bronze/Silver) |
| `propio_historico`    | Textos propios ya escritos      | Batch retroactivo   | Supabase               | Propia (Bronze/Silver) |

**Contrato con el RAG:** toda unidad indexada lleva `tipo_fuente`. El RAG hace
retrieval separado por fuente y combina en el prompt. Nunca se mezcla el origen
de forma implícita.

**Agrupaciones útiles** (no son valores del enum, son predicados):
- `propio*` = `{propio, propio_historico}` — comparten Silver, versionado por
  chiste y reconciliación (`src/jokes/SPEC.md`).
- `externo*` = `{teoria, transcripcion_curso}` — comparten limpieza agresiva,
  traducción y salida a ficheros (Flujo A).

**Licencia por defecto** (ver `llm-policy.md`, sin enforcement aún):
`externo* → personal_only`, `propio* → comercializable`. `licencia` es un campo
independiente de `tipo_fuente` (un libro de dominio público es `teoria` pero
`comercializable`).

## 3. Arquitectura general y layout del repo

Tres orquestadores independientes que comparten utilidades y convergen en el
índice de consulta. Ningún flujo importa la lógica de orquestación de otro.

```
Flujo A — Teoría (batch, determinista)
  DriveMonitor → Parser → SubtypeDetector → Cleaner → LanguageDetector
    → LanguageNormalizer → QualityScorer → FormatNormalizer
    → /data/processed/v{N}/  ──(ingesta)──▶ Supabase (índice)

Flujo B — Chistes propios / Telegram (realtime, LLM)
  TelegramBot → Bronze(raw) → PreLimpiezaMinima → Silver(LLM)
    → Reconciliación → Supabase

Flujo C — Chistes históricos (batch retroactivo, LLM)
  [script marcar_remates.py: docx→.md, marcado AUTOMÁTICO por color →
     [REMATE] (rojo #FF0000) + [CHISTOIDE] (burdeos #980000)]  (automático, previo)
  HistLoader → Segmentador([REMATE]=fin; [CHISTOIDE] no es frontera; + LLM)
    → Silver(LLM) → Reconciliación → Supabase
```

**Layout del repo** (la separación es estructural, no solo de carpetas; cada
carpeta con lógica propia trae su `SPEC.md`):

```
src/
├── utils/                 # COMPARTIDO entre flujos — SPEC.md
│   ├── language_detector.py
│   ├── quality_scorer.py
│   ├── drive_sync.py       # núcleo de sync con Drive real (A y C) — P23
│   └── llm/                # cliente LLM (Silver) y embeddings
├── theory/                 # Flujo A — SPEC.md
│   ├── drive_sync.py        # especialización de teoría sobre utils/drive_sync
│   ├── drive_monitor.py
│   ├── parsers/             # whisperx, pdf, epub, docx
│   ├── cleaners/
│   ├── normalizers/
│   └── pipeline.py
└── jokes/                  # Flujos B y C — SPEC.md (contrato compartido)
    ├── telegram/             # Flujo B (realtime) — SPEC.md
    │   └── telegram_bot.py
    ├── historico/            # Flujo C (batch) — SPEC.md
    │   ├── loader.py
    │   └── segmentador.py
    ├── silver.py              # Silver LLM (compartido B/C)
    ├── reconciliacion.py      # hash + embedding (compartido B/C)
    └── supabase_store.py

scripts/
├── run_pipeline.py           # Flujo A (teoría)
├── run_historico.py          # Flujo C (batch)
├── marcar_remates.py         # preprocesado automático por color (ver historico/SPEC.md)
├── validate_corpus.py
└── stats_report.py

docs/specs/
├── 00-overview.md            # este documento
└── llm-policy.md             # coste/LLM/copyright + P16 (cross-cutting)
```

**Regla de dependencias:** `theory/` y `jokes/` no se importan entre sí. Todo
código común vive en `utils/`. El Silver y la Reconciliación se comparten entre
los flujos B y C porque tratan la misma unidad (`propio*`), pero no con teoría
— por eso viven en `src/jokes/SPEC.md` (el nivel compartido), no duplicados en
`telegram/SPEC.md` e `historico/SPEC.md`.

## Idempotencia y versionado — comparativa entre flujos

| Flujo            | Idempotencia                              | Versionado             | Detalle |
|-------------------|---------------------------------------------|--------------------------|-----------|
| A — Teoría        | `processed_files.json` (hash MD5) + metadata de Drive en modo Drive-real (P23) | `v{N}` inmutable         | `src/theory/SPEC.md` |
| B — Telegram      | Por evento (`telegram_update_id`)         | Por chiste                | `src/jokes/telegram/SPEC.md` |
| C — Histórico     | Hash MD5 del documento + reconciliación de chiste | Por chiste        | `src/jokes/historico/SPEC.md` |

- **`v{N}` inmutable aplica SOLO a teoría.** Su `manifest.json` es inmutable una
  vez generado; nunca se sobrescribe una versión.
- **Reanudación:** si cualquier flujo falla a mitad, retoma desde el último ítem
  no completado (fichero/documento/evento), sin reprocesar lo ya hecho.

**P19 (2026-07-24) — Flujo C lee de carpeta Drive real.** En su momento, a
diferencia del Flujo A —cuya integración con Drive real estaba diferida sobre
carpeta local (P18) hasta que P23 la cerró reutilizando este mismo
mecanismo—, el Flujo C **sí** consume una carpeta de Google Drive real vía un
componente nuevo `src/jokes/historico/drive_source.py`: lista la carpeta,
descarga a un *staging* local solo los `.docx` nuevos/modificados (idempotencia
por metadata de Drive `fileId` + `modifiedTime`, **capa independiente** de la
idempotencia MD5 del `Loader`) y los entrega a `marcar_remates.procesar_docx`
**sin cambiar su firma**. Motivo: el histórico crece en Drive y su run será
semanal y desatendido (GitHub Actions, task 31), sin nadie que copie `.docx` a
mano. Auth por **cuenta de servicio** (`GOOGLE_APPLICATION_CREDENTIALS`, scope
`drive.readonly`), nunca OAuth interactivo — compatible con CI. Folder ID
propio del histórico en `DRIVE_FOLDER_ID_HISTORICO` (separado del
`DRIVE_FOLDER_ID` de teoría; las credenciales sí se comparten). Detalle
completo en [`src/jokes/historico/SPEC.md`](../../src/jokes/historico/SPEC.md)
§Fuente de entrada — carpeta Drive real.

**P20 (2026-07-24) — Candidatos de reconciliación filtrados por `tipo_fuente`.**
`reconciliacion.py` (task 15) es agnóstico de Supabase: recibe `candidatos`
como argumento. El método que los obtiene vive en
`SupabaseStore.listar_candidatos_reconciliacion(tipo_fuente)` (spec en
`src/jokes/SPEC.md` §Reconciliación; implementación en task 25). Devuelve
`list[dict]` con exactamente `id`/`hash_normalizado`/`embedding` (las tres
claves que `decidir_reconciliacion` consume), una entrada por fila de `chistes`
del alcance de `tipo_fuente` pedido — sin filtro de versión (cada fila ya es el
contenido vigente; las revisiones viven en `chistes_revisiones`) y **con**
variantes (`chiste_origen_id`, chistes distintos). Se decidió traer todas las
filas del `tipo_fuente` y comparar en Python (hash → coseno) en vez de una query
ANN nativa de pgvector: la ANN necesitaría el embedding entrante en el momento
del fetch, pero `reconciliar_chiste` lo calcula **después** de obtener los
candidatos (task 15, no se toca), y el volumen del corpus es bajo (GraphRAG
descartado por lo mismo, §1). La ANN queda como optimización futura compatible
con la interfaz (el método puede hacer el trabajo en SQL y seguir devolviendo
`list[dict]`).

**P21 (2026-07-26) — Flujo C: ejecución semanal desatendida vía GitHub
Actions.** Cierra la promesa de P19 ("su run será semanal y desatendido...
task 31"): `.github/workflows/run_historico_semanal.yml` (primer workflow del
repo — no hay CI de tests configurado, esta tarea es solo el cron de
producción del Flujo C) invoca `scripts/run_historico.py` (task 28) sin
reimplementar ninguna de sus etapas. Decisiones no obvias:
- **Cron:** sábado 03:00 UTC (`0 3 * * 6`). Evita horas pico de la API de
  Drive/LLM (fin de semana laboral) y deja el domingo entero como margen para
  investigar un fallo antes de que arranque la semana laboral el lunes.
- **`workflow_dispatch`** añadido como segundo trigger (además del cron), con
  un input opcional `dry_run` — permite lanzar el workflow a mano (primer run,
  verificación puntual) sin esperar al sábado, y probar solo el gate de coste
  sin ejecutar el pipeline completo si `dry_run: true`.
- **Credenciales de Drive (restricción ya fijada en
  `src/jokes/historico/SPEC.md` §"Auth desatendida"):** cuenta de servicio,
  nunca OAuth interactivo (no hay navegador en un runner). Un secreto de
  GitHub Actions es un string, no un fichero, así que el JSON completo de la
  cuenta de servicio vive en el secreto `GOOGLE_SERVICE_ACCOUNT_JSON`; un paso
  del workflow lo escribe a `$RUNNER_TEMP/google-service-account.json` y
  exporta `GOOGLE_APPLICATION_CREDENTIALS` apuntando ahí — mismo mecanismo por
  defecto que ya usa `run_historico.py` (su docstring, §Credenciales), sin
  necesidad de pasar `--credentials-path`.
- **Resto de secrets:** nombres 1:1 con las variables de Flujo C en
  `.env.example` (`DRIVE_FOLDER_ID_HISTORICO`, `SUPABASE_URL`,
  `SUPABASE_SERVICE_KEY`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDINGS_API_KEY`,
  `EMBEDDINGS_MODEL`, y las tres opcionales del gate de coste
  `HISTORICO_COSTE_MAX_TOKENS`/`HISTORICO_COSTE_MAX_EUR`/
  `HISTORICO_COSTE_EUR_POR_MILLON_TOKENS`) — única excepción la credencial de
  Drive, por el motivo de arriba.
- **Exit codes 0/1/2 de `run_historico.py`** (task 28): el job falla
  visiblemente (sin `continue-on-error`) tanto en `1` (fallo) como en `2`
  (abort por coste) — un abort por coste sigue siendo una señal que requiere
  atención humana (ajustar umbrales o esperar), no debe pasar desapercibido en
  verde. Se distingue en el Job Summary (`2` se anota explícitamente como
  "abort por coste", no un fallo del pipeline en sí) para que quien revise el
  run no confunda ambos casos, pero el job se marca en rojo en los dos.
- **Persistencia de estado entre runs:** un runner de GitHub Actions es
  efímero, así que sin ayuda cada run semanal repetiría `DriveSource.sync()` y
  `Loader.load()` desde cero (correcto igualmente por la idempotencia en
  capas del Flujo C — ver comparativa de arriba — pero re-descargando/
  re-marcando todo). El workflow usa `actions/cache` sobre
  `data/staging/historico/` y `data/state/historico_drive.json` +
  `data/state/historico_loader.json` (rutas por defecto de
  `src/jokes/historico/pipeline.py`), con clave única por `run_id` y
  `restore-keys` de prefijo para recuperar el estado del run anterior más
  reciente. Limitación conocida y asumida: la cache de GitHub Actions no es
  persistencia garantizada (política LRU, límite de 10GB por repo) — un
  cache-miss no rompe la corrección del pipeline (las capas de idempotencia
  siguen siendo válidas), solo le hace repetir trabajo ya hecho.
- **Setup de Python:** `actions/setup-python` + `pip install -r
  requirements.txt` directo (sin `.venv`, a diferencia de `init.sh` en local
  — el runner ya es un entorno aislado y efímero, no hace falta el aislamiento
  extra que `init.sh` necesita por el `externally-managed-environment` de
  Python en el host de desarrollo).

**P22 (2026-07-27) — Flujo B: transporte por webhook y 200 antes del tramo
LLM.** El Flujo B se alimenta de un **webhook** de la Bot API
(`POST /telegram/webhook` en una app FastAPI, task 36), no de polling
`getUpdates`: con servidor y dominio propios (deploy en tasks 38-41), el polling
solo compraría la desventaja de un proceso en bucle infinito —uno más que
supervisar, latencia atada al intervalo de sondeo— para obtener lo que el
webhook da gratis, y el webhook es además el patrón que Telegram recomienda en
producción. `telegram_bot.py` (task 16) no cambia: el `Update` tiene la misma
forma JSON por cualquiera de las dos vías. Decisiones no obvias:
- **El endpoint responde `200` inmediatamente tras Bronze**, y ejecuta el tramo
  caro (Silver → taxonomías → reconciliación → routing) en `BackgroundTasks`.
  Motivo: Telegram reintenta el update si no recibe un 2xx pronto, y ese tramo
  son varios segundos de red contra terceros. Contrapartida asumida: si el
  background falla después del 200, Telegram **no** reintenta — de ahí la
  columna `procesado_at` (nullable) en `chistes_telegram_bronze` + script de
  reproceso (tasks 46/47), contrapartida obligatoria, no opcional.
- **Autenticación por `secret_token`** (header
  `X-Telegram-Bot-Api-Secret-Token`, soportado nativamente por la Bot API,
  comparado en tiempo constante): sin cálculo de firmas propias. Falta o no
  coincide → `403` sin parsear el cuerpo.
- **Control de coste sin gate batch.** `historico/coste.py` (Flujo C) es un
  dry-run sobre un corpus finito **antes** del run; en tiempo real no hay corpus
  que medir ni un "antes" (el run es el evento). Los controles primarios son
  preventivos: **allowlist de `chat_id`** (variable de entorno
  `TELEGRAM_ALLOWED_CHAT_IDS`, evaluada **antes de Bronze**, fallo de arranque
  si falta — nunca fallo abierto) e **idempotencia por `telegram_update_id`**
  (un duplicado ni siquiera agenda el tramo LLM). Rate-limiting explícito queda
  **fuera de esta ronda** (un solo usuario, volumen bajo); su sitio natural, si
  hiciera falta, es el mismo tramo síncrono tras la allowlist.
- **Códigos HTTP:** cualquier respuesta no-2xx hace reintentar a Telegram, así
  que "no autorizado", "no es texto" y "duplicado" responden `200` (son
  decisiones terminales, no fallos de entrega). Puerto público **8443**: la Bot
  API solo admite 443/80/88/8443 y Coolify ya ocupa 80 y 443 en el VPS.
- **Orquestación fuera del endpoint:** la cadena vive en `telegram/pipeline.py`
  (task 35), importable y testeable sin red, para que el reproceso de la task 47
  la reutilice sin pasar por HTTP; el routing a Supabase se apoya en el módulo
  compartido `src/jokes/routing.py` (tasks 33/34).

Detalle completo en
[`src/jokes/telegram/SPEC.md`](../../src/jokes/telegram/SPEC.md) §Transporte y
§Orquestación end-to-end.

**P23 (2026-07-27) — Flujo A: Drive real deja de estar diferido (cierra P18).**
`DriveMonitor` dejaba de apuntar a la API de Drive "de momento" (P18) y vigilaba
`data/raw/books/`/`data/raw/notes/` en local. El Flujo C ya resolvió el mismo
problema con Drive real (P19), así que el Flujo A **reutiliza ese núcleo** en
vez de reimplementarlo: `src/jokes/historico/drive_source.py` se generaliza a
`src/utils/drive_sync.py` (`DriveSync`, task 43), parametrizado por
`mimes_aceptados: dict[mime_origen -> mime_export | None]` — un dict y no una
lista + callback porque las claves alimentan a la vez la query de `files().list`
y la decisión `get_media` vs `export`, y así no se pueden desincronizar. Es la
primera pieza de `src/utils/` con consumo real en los dos lados de la regla de
dependencias (`theory/` y `jokes/` no se importan entre sí). La extracción es
sin cambio de comportamiento, mismo patrón que la task 34 con `routing.py`: los
tests de la task 30 deben quedar en verde **sin modificarse**. Decisiones no
obvias:
- **Staging propio, `data/staging/theory/`, NUNCA `data/raw/`.** Un sync
  automático sobrescribe por definición, y `/data/raw/` es material sagrado
  (`CLAUDE.md`, §1 de este documento). P23 fija la lectura correcta de la regla:
  lo sagrado es **el único ejemplar que existe del original**. En modo
  Drive-real ese ejemplar vive en Drive y el fichero local es una caché
  reconstruible (igual que `data/staging/historico/`); en modo solo-local sigue
  siendo el fichero de `data/raw/`, curado a mano y sin otra copia.
- **Modo dual, no migración.** `DriveMonitor` **no cambia de interfaz** (sigue
  siendo MD5 sobre un `Path`); lo único que cambia es qué carpeta vigila:
  `data/staging/theory/` con el sync activado, `data/raw/books/`+`notes/` sin
  él. El modo solo-local es el **defecto** y queda intacto — sirve al corpus ya
  descargado a mano (`docs/CORPUS_INVENTORY.md`) y permite desarrollar y testear
  sin credenciales. Activación por inyección en `run_pipeline` y flag
  `--sync-drive` en el CLI (task 45).
- **MIMEs de teoría:** PDF, DOCX, EPUB y TXT se descargan directos; los **Google
  Docs nativos se exportan a `.docx`**, no a texto plano. Coincide con el
  histórico pero por motivos propios: el `.docx` cae en `docx_parser` →
  markitdown (P17), que conserva estructura, y en teoría la extensión `.txt`
  está reservada al `whisperx_parser` (`src/theory/pipeline.py` deriva Parser y
  `tipo_fuente` de la extensión), así que un export a `.txt` misenrutaría el
  documento y lo etiquetaría como `transcripcion_curso`.
- **`DRIVE_FOLDER_ID` se mantiene** (existía desde Fase 0, reservada para esto);
  no se renombra a `DRIVE_FOLDER_ID_TEORIA`. La asimetría con
  `DRIVE_FOLDER_ID_HISTORICO` es deliberada: renombrar solo compraría simetría
  cosmética e invalidaría P19, `.env.example` y los `.env` ya rellenados. Las
  credenciales (`GOOGLE_APPLICATION_CREDENTIALS`, cuenta de servicio, scope
  `drive.readonly`, nunca OAuth interactivo) sí se comparten con el Flujo C.

Detalle completo en [`src/theory/SPEC.md`](../../src/theory/SPEC.md) §Fuente de
entrada — Drive real y modo dual, y en
[`src/utils/SPEC.md`](../../src/utils/SPEC.md) §DriveSync.

## Metodología SDD + TDD (aplica a todo el proyecto)

1. Leer el spec del módulo que vayas a tocar (ver directriz arriba) antes de implementar.
2. Tests primero, con fixtures **reales** de `/tests/fixtures/` (nunca inventados).
3. `pytest tests/unit/ -v` y `tests/integration/ -v`; `validate_corpus.py` antes
   de commit.
