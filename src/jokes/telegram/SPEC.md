# Flujo B — Chistes propios (Telegram, tiempo real)

> Spec de `src/jokes/telegram/`. Para Silver, Reconciliación, Taxonomías y el
> esquema de Supabase (compartidos con el Flujo C), ver
> [`src/jokes/SPEC.md`](../SPEC.md) — no se duplican aquí. Contexto general en
> [`docs/specs/00-overview.md`](../../../docs/specs/00-overview.md) y política
> LLM en [`docs/specs/llm-policy.md`](../../../docs/specs/llm-policy.md).

Ingesta incremental, un chiste = un evento. Arquitectura **Bronze → Silver**.
Lo que sigue es **específico de Telegram**; en cuanto el chiste sale del
Bronze, entra en el contrato compartido de `src/jokes/SPEC.md`.

## Transporte — webhook (P22, 2026-07-27)

### Webhook, no polling

El Flujo B se alimenta de un **webhook** de la Bot API, no de polling
(`getUpdates`). El proyecto dispone de servidor y dominio propios (deploy en
las tasks 38-41), así que el polling solo compraría la **desventaja** de
mantener un proceso en bucle infinito para conseguir lo que el webhook da
gratis: un proceso más que supervisar y reiniciar, latencia acotada por el
intervalo de sondeo, y tráfico constante contra la API aunque no llegue nada.
Es además el patrón que Telegram recomienda en producción.

Efecto lateral favorable para el TDD del proyecto: con webhook el transporte
**es** un endpoint HTTP, testeable con el `TestClient` de FastAPI sin red y sin
levantar un Telegram falso — el mismo criterio de "sin red frágil" que sigue el
resto del contrato B/C.

`telegram_bot.py` (task 16) **no cambia**: el `Update` tiene la misma forma JSON
llegue por webhook o por polling (así está documentado en su propio docstring),
de modo que la elección de transporte no toca la lógica ya aprobada.

### Contrato HTTP

Dos endpoints, rutas y métodos **fijos** (no configurables):

| Método | Ruta | Auth | Responde |
|---|---|---|---|
| `POST` | `/telegram/webhook` | header `X-Telegram-Bot-Api-Secret-Token` | `200` tras Bronze (ver §Orquestación) |
| `GET`  | `/health` | ninguna | `200 {"status": "ok"}` |

**`POST /telegram/webhook`**

- **Cuerpo:** el JSON `Update` de la Bot API, exactamente el `dict` que
  `procesar_mensaje_telegram` ya consume. Cuerpo que no es JSON válido → `400`
  (no llega a Bronze).
- **Respuesta:** para toda petición autenticada, `200` con
  `{"ok": true, "estado": <estado>}`, donde `estado` ∈
  `aceptado | duplicado | ignorado_no_texto | ignorado_no_autorizado`.
  El `estado` es observabilidad (y el aserto natural de los tests de la task
  36); Telegram lo ignora, solo mira el código.
- **Regla dura:** *cualquier* respuesta que no sea 2xx hace que Telegram
  **reintente el mismo update**. Por eso "no autorizado", "no es un mensaje de
  texto" y "duplicado" responden **200**, no 4xx: son decisiones **terminales**
  del pipeline, no fallos de entrega, y devolver un error solo provocaría
  reintentos de algo que ya se decidió descartar. Los únicos códigos no-2xx del
  endpoint son el `403` del secret_token (un emisor no autenticado nunca debe
  obtener un 2xx) y el `400` de cuerpo ilegible.

**`GET /health`** — sin auth y **sin dependencias externas**: no consulta
Supabase ni Telegram, solo confirma que el proceso responde. Lo consumen el
`healthcheck` del contenedor (task 38) y el proxy (task 39); un health que
dependiera de Supabase reiniciaría el contenedor por una caída ajena. No
expone versión ni configuración.

### Validación del `secret_token`

El endpoint es público en Internet, así que se valida el header
**`X-Telegram-Bot-Api-Secret-Token`** —soportado nativamente por la Bot API, se
fija en `setWebhook` y Telegram lo envía en cada petición— contra
`TELEGRAM_WEBHOOK_SECRET_TOKEN`. Se elige frente a cualquier esquema de firma
propio precisamente porque no hay que calcular ni verificar nada: es una
comparación de strings.

- Comparación en **tiempo constante** (`hmac.compare_digest`).
- Header ausente o distinto → `403`, **sin parsear el cuerpo**, sin tocar
  Bronze y sin loguear el contenido de la petición.
- `TELEGRAM_WEBHOOK_SECRET_TOKEN` es **obligatoria**: si falta o está vacía la
  app **falla al arrancar**. Nunca se degrada a "sin secret configurado, acepto
  todo" — un fallo de configuración debe ser ruidoso al desplegar, no un
  endpoint abierto.
- Restricción de la Bot API sobre el valor: 1-256 caracteres de `A-Za-z0-9_-`.
- **Autentica al emisor, no autoriza el gasto:** dice "esto viene de nuestro
  bot", no "este `chat_id` puede consumir LLM". Eso último es la allowlist —
  son dos controles distintos y ambos se evalúan.

### Allowlist de `chat_id` (control de coste primario)

- **`TELEGRAM_ALLOWED_CHAT_IDS`**: enteros separados por comas, en variable de
  entorno. Se parsea **una vez al arrancar** a un `frozenset[int]`; un valor no
  parseable → error de arranque.
- Se evalúa **ANTES de Bronze** (orden exacto en §Orquestación): un `chat_id`
  no autorizado no escribe fila ni gasta un solo token.
- Vacía o ausente → **error de arranque**, igual que el secret. "Vacía = admito
  a todo el mundo" sería el fallo abierto que este control existe para evitar.
- **Por qué variable de entorno y no una tabla de Supabase:** (1) el chequeo va
  en el tramo síncrono que corre contra el reloj del reintento de Telegram, y
  debe preceder a Bronze — una tabla añadiría un round-trip y un modo de fallo
  nuevo (Supabase caído ⇒ no se puede evaluar el control ⇒ habría que cerrar
  igualmente); (2) es una lista de uno o dos ids, de un solo usuario, que no
  cambia casi nunca; (3) mantiene un `chat_id` personal fuera del repo. Coste
  asumido: cambiarla exige reiniciar el contenedor (`env_file`, task 38), no un
  `UPDATE`.
- No autorizado → `200` con `estado: ignorado_no_autorizado` + log `WARNING`
  con el `chat_id` (dato propio, auditable), **nunca** con el texto del mensaje.

### Por qué NO hay gate de coste batch en el Flujo B

`historico/coste.py` (task 26) es un **dry-run sobre un corpus finito y conocido
antes de arrancar**: cuenta documentos, estima tokens y aborta el run entero si
se pasa del umbral. Ninguna de sus tres premisas se sostiene en tiempo real —
no hay corpus que medir (el "lote" es un mensaje que acaba de llegar), no hay un
"antes del run" (el run **es** el evento) y abortar no sale gratis (Telegram
reintentaría). El control de coste del Flujo B es por tanto **preventivo por
diseño**, no un gate:

| Control | Qué acota | Dónde |
|---|---|---|
| Allowlist de `chat_id` | **quién** puede provocar gasto | tramo síncrono, antes de Bronze |
| Idempotencia por `telegram_update_id` | gasto **repetido** por el mismo evento | Bronze (`ON CONFLICT DO NOTHING`) |
| El tramo caro solo se agenda para updates aceptados y no duplicados | gasto por updates descartables | §Orquestación |

**Rate-limiting explícito (X mensajes/minuto por chat) queda fuera de esta
ronda**: con un único usuario autorizado y volumen bajo sería maquinaria contra
un escenario que no existe. Si algún día hace falta, su sitio es el **mismo
tramo síncrono, justo después de la allowlist y antes de Bronze**, y añadirlo no
cambia ningún contrato de esta spec.

### Registro del webhook (`setWebhook`)

Lo hace `scripts/set_telegram_webhook.py` (task 37), one-shot y re-ejecutable,
con tres parámetros:

- **`url`** = `TELEGRAM_WEBHOOK_URL` — URL pública completa, con puerto y ruta
  (`https://<dominio>:8443/telegram/webhook`).
- **`secret_token`** = `TELEGRAM_WEBHOOK_SECRET_TOKEN` — el mismo valor que
  valida el endpoint. Rotarlo = redeploy con el valor nuevo **y** volver a
  lanzar `setWebhook`.
- **`allowed_updates=["message"]`** — no es cosmético: Telegram deja de enviar
  tipos que el pipeline descartaría igual (`edited_message`, `callback_query`,
  ediciones de canal…), reduciendo tráfico y superficie de entrada.
  **`edited_message` está fuera de scope por decisión de producto**: un mensaje
  editado no es un evento Bronze nuevo (`_extraer_datos_mensaje` ya lo ignora,
  task 16). Si algún día entra, entra por spec, no por defecto del transporte.

**Puerto 8443, y no es arbitrario:** la Bot API solo acepta webhooks en 443, 80,
88 y 8443, y en el VPS Coolify ya ocupa 80 y 443 — 8443 es el único puerto del
conjunto permitido que queda libre. TLS válido es obligatorio (Telegram no
acepta HTTP plano); lo termina Caddy vía ACME (task 39).

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

## Orquestación end-to-end (P22, 2026-07-27)

La cadena completa, desde que Telegram llama al endpoint hasta Supabase:

```
POST /telegram/webhook
  └─ SÍNCRONO (antes del 200) ─────────────────────────────────────────────
     secret_token → parseo del Update → extracción (pura, sin I/O)
       → allowlist de chat_id → Bronze (idempotente) → pre-limpieza mínima
       → 200 OK
  └─ BACKGROUND (después del 200, BackgroundTasks) ─────────────────────────
     Silver (LLM) → resolución de taxonomías → reconciliación
       → routing.py (tipo_fuente='propio') → marcado de procesado_at
```

**Reparto de responsabilidades** — el endpoint no orquesta:
`telegram/webhook_app.py` (task 36) es solo transporte (auth, parseo, códigos
HTTP, agendar el background); toda la cadena vive en `telegram/pipeline.py`
(task 35), **importable y testeable sin red**, y por eso reutilizable tal cual
por el script de reproceso (task 47) sin pasar por HTTP.

### Tramo síncrono — corre ANTES del 200

| # | Paso | Componente | Notas |
|---|---|---|---|
| 1 | Validar `secret_token` | `webhook_app.py` | `403` y fin si falla (§Transporte) |
| 2 | Parsear el `Update` | `webhook_app.py` | `400` si no es JSON válido |
| 3 | Extraer `telegram_update_id`/`chat_id`/`texto_raw`/`timestamp` | `telegram_bot._extraer_datos_mensaje` | pura, **sin I/O**; `None` ⇒ `200 ignorado_no_texto` |
| 4 | **Allowlist de `chat_id`** | `pipeline.py` | no autorizado ⇒ `200 ignorado_no_autorizado`, **nada tocó Supabase** |
| 5 | **Bronze** + pre-limpieza | `telegram_bot.procesar_mensaje_telegram` | idempotente por `telegram_update_id`; devuelve `texto_limpio` |
| 6 | Responder `200` | `webhook_app.py` | `estado` según el resultado |

Todo lo síncrono es **barato y determinista**: una comparación de strings, un
parseo de `dict`, un `frozenset` en memoria y un `INSERT ... ON CONFLICT DO
NOTHING`. Ninguna llamada a un LLM, ningún embedding.

Tres precisiones que las tasks 35/36 no deben volver a decidir:

- **La allowlist se evalúa antes de Bronze, y el paso 3 no la debilita.** La
  extracción precede a la allowlist solo porque hace falta el `chat_id` para
  evaluarla, y es una función **pura sin red ni escritura** (task 16): en el
  momento del chequeo **nada** ha tocado Supabase, que es lo que el control
  garantiza. `pipeline.py` reutiliza `_extraer_datos_mensaje` del propio paquete
  en vez de reparsear el `Update` por su cuenta — es privado de módulo, no de
  paquete, y reusarlo **no modifica** `telegram_bot.py` (congelado, task 16).
- **La pre-limpieza mínima corre en el tramo síncrono**, no en el background,
  porque vive dentro de `procesar_mensaje_telegram` (paso 5) y es pura y de
  coste microscópico. Su `texto_limpio` **viaja como argumento** al tramo
  background; este no la recalcula.
- **Un duplicado no agenda el background.** Si Bronze deduplica
  (`es_duplicado=True`, el `telegram_update_id` ya estaba), el tramo caro
  **no se lanza**: es el segundo control de coste de §Transporte y hace que un
  reenvío de Telegram nunca pague LLM dos veces.

### Tramo background — corre DESPUÉS del 200

Mecanismo: **`BackgroundTasks` de FastAPI** — misma app, mismo proceso, se
ejecuta tras enviar la respuesta. **No hay cola externa** (Celery/Redis/RQ) a
propósito: un solo usuario y volumen bajo no justifican otro servicio que
desplegar y vigilar, y la orquestación ya es importable, así que migrar a una
cola en el futuro no tocaría esta cadena.

| # | Paso | Componente | `src/jokes/SPEC.md` |
|---|---|---|---|
| 7 | Silver (estructuración por LLM) sobre `texto_limpio` | `silver.estructurar_chiste` (task 13) | §Silver |
| 8 | Resolución de taxonomías (loop acotado ≤3, P16) | `resolver_taxonomia` (task 14) | §Taxonomías |
| 9 | Reconciliación IGUAL/CAMBIADO/NUEVO | `reconciliar_chiste` (task 15) con candidatos de `SupabaseStore.listar_candidatos_reconciliacion` (task 25) | §Reconciliación |
| 10 | Routing de la decisión a Supabase, `tipo_fuente='propio'` | `src/jokes/routing.py` (tasks 33/34) | §Reconciliación / §Storage |
| 11 | Marcar el evento como completado (`procesado_at`) | `pipeline.py` | ver nota de recuperación |

- **Alcance de los candidatos de reconciliación:** lo fija
  `src/jokes/SPEC.md` §Reconciliación → "Obtención de candidatos" (hoy, para un
  `propio` entrante, `propio_historico`). Esta spec **no lo redefine**: si la
  política cambia, cambia allí.
- **`tipo_fuente='propio'`** y `licencia='comercializable'` (§Salida) son
  constantes del Flujo B; nunca las decide el LLM.
- **Por qué el 200 no espera al Silver:** Telegram reintenta el update si no
  recibe un 2xx pronto, y el tramo 7-10 son varios segundos de llamadas de red a
  terceros (LLM + embeddings + Supabase). Responder al final provocaría
  reintentos del mismo update y ejecuciones solapadas del tramo caro — la
  idempotencia de Bronze las cortaría, pero el 200 temprano evita de entrada esa
  carrera en vez de depender de que la red la resuelva.
- **Los errores del tramo background nunca alteran la respuesta HTTP**, que ya
  se envió: se loguean y el evento queda con `procesado_at` NULL.
- **Bronze sigue siendo sagrado también aquí:** el tramo background no reescribe
  `texto_raw` ni ninguna columna del mensaje original; el único apunte que
  vuelve a la tabla Bronze es el bookkeeping de `procesado_at`.

### Nota — recuperación de fallos post-200 (detalle en las tasks 46/47)

Consecuencia asumida de responder 200 antes del tramo caro: **si el background
falla o el proceso muere entre medias, Telegram no reintenta** (ya recibió su
2xx) y el mensaje se queda en Bronze sin llegar a `chistes`. El mecanismo de
recuperación es una columna **`procesado_at` (nullable)** en
`chistes_telegram_bronze` —que el paso 11 fija solo al completar la cadena con
éxito— más un script de reproceso de los eventos con `procesado_at` NULL. Aquí
solo se deja constancia de que ese mecanismo **existe y es la contrapartida
obligatoria del 200 temprano**; su diseño (columna + §Recuperación de fallos) es
la task 46 y el script `scripts/reprocesar_bronze_pendiente.py` es la task 47.

**Dependencias externas de esta orquestación que NO se diseñan aquí:** el
componente compartido `src/jokes/routing.py` (paso 10) —su extracción desde
`historico/pipeline.py` y su interfaz parametrizada por `tipo_fuente`— es la
task 33 (spec) y la task 34 (implementación). Esta spec solo fija **que** el
Flujo B rutea a través de ese módulo compartido, no cómo se llama su función ni
qué argumentos recibe.

## Recuperación de fallos

La decisión de responder `200` a Telegram **antes** de ejecutar el tramo caro
(Silver → taxonomías → reconciliación → routing, paso 7-11) tiene una
contrapartida obligatoria: si el tramo background falla o el proceso muere
entre medias, **Telegram no reintenta** (ya recibió su 2xx) y el mensaje queda
atrapado en Bronze sin llegar a `chistes`. Este mecanismo es la solución.

### Columna `procesado_at` en `chistes_telegram_bronze`

- **Qué es:** campo nullable de tipo `timestamptz` en la tabla Bronze. `NULL`
  significa "evento insertado en Bronze pero el tramo caro no ha completado";
  un timestamp significa "la cadena entera (paso 11) terminó con éxito".
- **Cómo se marca:** `telegram/pipeline.py` (task 35) es responsable de fijar
  `procesado_at` a un timestamp **solo cuando** el paso 11 (routing a Supabase
  completado sin excepción) termina. Si cualquier paso de 7-11 falla, el campo
  queda `NULL` y se loga la excepción — no se intenta un UPDATE parcial.
- **Por qué al final y no al principio:** si se marcara en el paso 5 (Bronze),
  un fallo posterior nunca se detectaría — el punto entero de la columna es
  **señalar precisamente lo contrario**, que el pipeline aún está incompleto.
  Solo el paso 11 garantiza que todo terminó, de modo que solo ahí se marca.
- **Respeta la regla de Bronze inmutable:** esta columna **no reescribe**
  `texto_raw` ni ningún dato del mensaje original; es un campo de **estado de
  progreso** agregado a la tabla, usando el mismo patrón que otros mecanismos
  de idempotencia del proyecto (`processed_files.json` en Flujo A, metadata de
  Drive en `drive_source.py`) — la idea es separación clara: dato vs. estado.

### Script de reproceso — `scripts/reprocesar_bronze_pendiente.py` (task 47)

Encargado de recuperar los eventos atrapados (aquellos con `procesado_at IS
NULL`). No se diseña aquí en detalle (es la task 47), pero el contrato es fijo:

- Selecciona de `chistes_telegram_bronze WHERE procesado_at IS NULL`.
- Para cada evento, **reutiliza la misma cadena** que usa el webhook:
  `telegram/pipeline.py` es importable y testeable sin HTTP, así que el script
  la llama directamente (no reinventa la lógica de Silver/Reconciliación/Routing).
- Marca `procesado_at` al completar cada evento con éxito — es el **mismo punto
  de marcado** que usa el webhook (paso 11), no un mecanismo distinto.

### Ausencia de reintentos automáticos

Esta tarea **no introduce un cron ni un retry-loop automático**: no hay job
schedulado que ejecute `reprocesar_bronze_pendiente.py` de forma repetida. El
reproceso es un script manual o programado aparte, fuera de este alcance. Si
algún día entra, entra por spec (nueva sección `##` en este fichero), no por
defecto del transporte.

## Idempotencia y versionado

Idempotencia **por evento** (`telegram_update_id`), no por documento (a
diferencia del Flujo C — ver `src/jokes/historico/SPEC.md`). Versionado por
chiste, sin `v{N}` (ver `src/jokes/SPEC.md` §Versionado).
