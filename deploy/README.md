# Runbook operativo: Deploy del webhook de Telegram (Flujo B)

> **Referencia:** Task 41, Flujo B (ingesta de chistes en tiempo real vía Telegram)
>
> Documento vivo que describe el procedimiento manual de bootstrap en un VPS
> limpio, el primer arranque, y la operación posterior. Procedimiento validado
> en producción contra `turrabot.machango.org`.

**Cambios recientes:**
- Decisión P22 (`docs/specs/00-overview.md`): webhook en puerto 8443 (no 443/80; Coolify ocupa esos). TLS automático vía Caddy + ACME DNS-01 con Porkbun.
- Decisión P24 (`docs/specs/00-overview.md`): deploy continuo por SSH + GitHub Actions (`.github/workflows/deploy_telegram_webhook.yml`, task 40).

---

## Índice

1. [Bootstrap inicial (VPS limpio)](#1-bootstrap-inicial-vps-limpio)
2. [Crear el `.env` de producción](#2-crear-el-env-de-producción)
3. [Primer arranque](#3-primer-arranque)
4. [Registrar el webhook contra Telegram](#4-registrar-el-webhook-contra-telegram)
5. [Verificación con getWebhookInfo](#5-verificación-con-getwebhookinfo)
6. [Rotación del secret](#6-rotación-del-secret)
7. [Despliegues posteriores (automático vía CI)](#7-despliegues-posteriores-automático-vía-ci)
8. [Dónde mirar logs](#8-dónde-mirar-logs)
9. [Solución de problemas comunes](#9-solución-de-problemas-comunes)
10. [Migración a 3 schemas (bronze/silver/gold) — runbook de cutover](#10-migración-a-3-schemas-bronzesilvergold--runbook-de-cutover)

---

## 1. Bootstrap inicial (VPS limpio)

Ejecuta estos comandos la **primera vez** en un VPS virgen sin el repositorio clonado.

```bash
ssh <usuario>@<host>
mkdir -p ~/corpusRAG && cd ~/corpusRAG
git clone https://github.com/SAfonso/corpus-comedy-RAG.git .
```

**Notas:**
- Reemplaza `<usuario>` y `<host>` con tus credenciales SSH reales.
- El `.` al final del `git clone` clona el contenido directamente en `~/corpusRAG`, no en una carpeta anidada.
- Requiere acceso SSH configurado en GitHub (clave SSH o HTTPS si prefieres).

---

## 2. Crear el `.env` de producción

El `.env` vive en la **raíz del checkout** (`~/corpusRAG/.env`), **no dentro de `deploy/`**. Es el mismo `.env` que leen otros scripts del pipeline (`scripts/run_historico.py`, etc.) vía `python-dotenv`.

### 2.1 Punto de partida

```bash
cd ~/corpusRAG
cp .env.example .env
chmod 600 .env
```

### 2.2 Variables obligatorias para Flujo B

Edita `~/corpusRAG/.env` (usa `nano` o tu editor preferido) y rellena **como mínimo** estas variables. El resto de `.env.example` puede quedar vacío — **excepto el LLM/embeddings de más abajo, que sí son obligatorias** aunque no bloqueen el arranque del contenedor (fallan más tarde, en el tramo background):

#### **Supabase** (almacenamiento Bronze)
```
SUPABASE_URL=https://<tu-proyecto>.supabase.co
SUPABASE_SERVICE_KEY=<clave-service-role>
```

Obtén estos valores del dashboard de Supabase:
- URL: **Settings** → **API** → **URL**
- Clave service_role: **Settings** → **API** → **Service Role** (nunca uses la `anon` key para escritura)

#### **Telegram Bot** (identificador del bot)
```
TELEGRAM_BOT_TOKEN=<token-bot-de-botfather>
```

Obtenlo de [BotFather](https://t.me/botfather) en Telegram. Ejemplo: `123456789:ABCdefGHIjklmnopQRSTuvwxyzABC-DE`

#### **Telegram Webhook Secret** (secreto compartido, NO es el token del bot)

**IMPORTANTE:** Este NO es el `TELEGRAM_BOT_TOKEN`. Es un secreto que **generas tú**, compartido solo entre tu servidor y Telegram para validar que las peticiones provienen realmente de Telegram (header `X-Telegram-Bot-Api-Secret-Token`).

```bash
openssl rand -hex 32
```

Copia la salida (ejemplo: `a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6...`) y pégala en `.env`:

```
TELEGRAM_WEBHOOK_SECRET_TOKEN=<salida-de-openssl-rand>
```

**Advertencia:** Si confundes este valor con `TELEGRAM_BOT_TOKEN`, el webhook no funcionará. El error será `ConfiguracionInvalidaError: TELEGRAM_WEBHOOK_SECRET_TOKEN no configurada o vacía` al arrancar el contenedor. Ver [Solución de problemas](#9-solución-de-problemas-comunes).

#### **Telegram Allowed Chat IDs** (allowlist de chats autorizados)

Enteros separados por comas, sin espacios. Por ejemplo: `123456789,987654321`

Para obtener tu `chat_id`:
1. Abre Telegram y envía cualquier mensaje al bot (debe ser desde dentro de un chat privado o grupo).
2. En el VPS, ejecuta:
   ```bash
   curl -s "https://api.telegram.org/bot$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)/getUpdates" | python3 -m json.tool
   ```
3. Busca en el JSON `message.chat.id`. Si la salida es `"result": []` vacío, es porque aún no le has escrito nada al bot — escribe algo y vuelve a intentar.

Rellena:
```
TELEGRAM_ALLOWED_CHAT_IDS=<lista-de-chat-ids>
```

#### **Telegram Webhook URL** (dirección pública del endpoint)

```
TELEGRAM_WEBHOOK_URL=https://<dominio>:8443/telegram/webhook
```

Reemplaza `<dominio>` con tu dominio real (ej: `turrabot.machango.org`). El puerto **8443 es fijo** — ver `src/jokes/telegram/SPEC.md` §"Registro del webhook" y `docs/specs/00-overview.md` P22 para el razonamiento.

#### **LLM y embeddings** (obligatorias — Silver no completa sin esto)

```
LLM_API_KEY=<tu-clave>
LLM_MODEL=<tu-modelo>
EMBEDDINGS_API_KEY=<tu-clave>
EMBEDDINGS_MODEL=<tu-modelo>
```

**No son opcionales para Flujo B**, aunque el contenedor arranque sin ellas: `src/jokes/silver.py::estructurar_chiste` necesita el LLM para estructurar cada chiste, y la reconciliación necesita el embedding para dedup por similitud. Sin estas variables, el webhook responde 200 y guarda en Bronze con normalidad, pero el tramo background falla siempre con `LLMClientError: LLM_API_KEY / LLM_MODEL no configuradas` y la fila queda con `procesado_at IS NULL` indefinidamente hasta que se corrija y se reprocese (§9).

**Nota sobre cuota:** si usas el tier gratuito de Gemini, el límite es bajo (15 requests/min por modelo) — reprocesar varios mensajes seguidos puede agotarlo (`429 RESOURCE_EXHAUSTED`). No es un error de configuración, solo espera el `retryDelay` que indica el propio error (normalmente 50-60s) y reintenta.

#### **Porkbun API Keys** (para TLS automático vía DNS-01)

Necesarias para que Caddy emita certificados ACME. Se generan en [Porkbun](https://porkbun.com):
1. Entra en tu cuenta → **Account** → **API Access**.
2. Si está deshabilitado a nivel de cuenta, habilítalo.
3. Crea una nueva API Key.
4. **Importante:** Habilita el acceso API **específicamente para tu dominio** (ej: `machango.org`) — por defecto también está deshabilitado. Busca en la lista de dominios, click en el engranaje, habilita API access.
5. El secret de la API **solo se muestra una vez** — cópialo junto a la key.

```
PORKBUN_API_KEY=<tu-api-key>
PORKBUN_API_SECRET_KEY=<tu-secret>
```

### 2.3 Verificación

Una vez rellenadas las variables, guarda el archivo y verifica que está legible solo por el propietario:

```bash
ls -la .env    # debe mostrar `-rw-------` (permisos 600)
```

---

## 3. Primer arranque

Desde la carpeta `deploy/` del checkout:

```bash
cd ~/corpusRAG/deploy
docker compose up -d --build
docker compose ps
```

Espera a que ambos contenedores (webhook y caddy) estén en estado **Up** (no Restarting).

### 3.0 Abrir el puerto 8443 en el firewall de red del proveedor

**Paso fácil de olvidar** (bloqueó el primer despliegue real, 2026-07-28): que `docker compose ps` muestre el puerto publicado (`0.0.0.0:8443->8443/tcp`) NO significa que sea alcanzable desde internet — muchos proveedores de VPS filtran todo el tráfico entrante salvo 22/80/443 por defecto en un firewall de red **externo al propio servidor** (no es `ufw` ni `iptables`, es un panel web del proveedor). En Hetzner Cloud: **Firewalls** → crear o editar el firewall asignado al servidor → **Inbound** → regla `TCP`, puerto `8443`, source `0.0.0.0/0`. Puedes crear un firewall nuevo solo para esta regla y asignarlo al mismo servidor sin tocar el que ya usa Coolify — los firewalls asignados a un servidor se suman (unión de reglas), no hace falta duplicar nada.

Verifica desde **fuera** del VPS (tu propia máquina, no por SSH):
```bash
curl -v https://<dominio>:8443/health
```
Si da timeout total (no rechaza, simplemente no responde nunca), es casi siempre este firewall externo, no el contenedor ni Docker.

### 3.1 Verificar logs

En los logs, busca estos patrones clave:

**Caddy (TLS):**
```bash
docker compose logs caddy --tail=50
```

Busca la línea:
```
certificate obtained successfully
```

Si NO aparece y ves errores de Porkbun, **soluciones comunes:**
- Las `PORKBUN_API_KEY` / `PORKBUN_API_SECRET_KEY` son incorrectas o expiradas.
- El acceso API no está habilitado a nivel de **dominio específico** en Porkbun (es distinto de habilitar a nivel de cuenta).
- El dominio no está registrado en Porkbun o el DNS no apunta ahí.

**Webhook (aplicación):**
```bash
docker compose logs webhook --tail=50
```

Busca que la aplicación haya arrancado sin errores. Si sale en crash-loop (Restarting):
- Error típico: `ConfiguracionInvalidaError: TELEGRAM_WEBHOOK_SECRET_TOKEN no configurada o vacía` → La variable no está en el `.env` o está vacía. Edita `.env`, rellena con `openssl rand -hex 32`, y recrear:
  ```bash
  docker compose up -d --force-recreate webhook
  ```

---

## 4. Registrar el webhook contra Telegram

El script `scripts/set_telegram_webhook.py` registra el webhook ante la API de Telegram. **Nota importante:** este script está en el HOST, no dentro del contenedor — el Dockerfile del webhook solo copia `src/`, nunca `scripts/`.

Además, el script **no carga `.env` automáticamente** (no hace `load_dotenv()`). Lee `os.environ` directamente, así que debes exportar las variables primero usando una **subshell con `set -a`** (evita contaminar la sesión interactiva):

```bash
cd ~/corpusRAG
python3 -m venv .venv   # solo la primera vez
source .venv/bin/activate
pip install -r requirements.txt
(set -a; source .env; python scripts/set_telegram_webhook.py)
```

**Salida esperada:** el JSON de salida debe terminar con:
```json
"verificacion": {"ok": true, ...}
```

Y el proceso debe salir con código **0**. Si sale con error, revisa:
- `TELEGRAM_BOT_TOKEN` es correcto.
- `TELEGRAM_WEBHOOK_URL` es exacto (incluyendo puerto 8443).
- El certificado TLS de Caddy se ha emitido correctamente (revisa logs de Caddy del paso 3.1).
- Telegram puede alcanzar el endpoint (hay tráfico de red hacia el VPS en el puerto 8443).

---

## 5. Verificación con getWebhookInfo

Verifica que el webhook está registrado en Telegram con los parámetros correctos:

```bash
curl -s "https://api.telegram.org/bot$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)/getWebhookInfo" | python3 -m json.tool
```

En el JSON, confirma que:
- `"url"` coincide exactamente con tu `TELEGRAM_WEBHOOK_URL` del `.env` (ej: `https://turrabot.machango.org:8443/telegram/webhook`).
- **No hay `"last_error_message"`** (o está vacío/null). Si hay error, indica un problema de conectividad o certificado.

Ejemplo de respuesta OK:
```json
{
  "ok": true,
  "result": {
    "url": "https://turrabot.machango.org:8443/telegram/webhook",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "max_connections": 40,
    "allowed_updates": ["message"]
  }
}
```

---

## 6. Rotación del secret

Si necesitas cambiar `TELEGRAM_WEBHOOK_SECRET_TOKEN` (rotación de seguridad, sospecha de fuga, etc.):

### 6.1 Generar nuevo secret
```bash
openssl rand -hex 32
```

### 6.2 Actualizar `.env`
```bash
# Edita manualmente .env o usa:
sed -i "s/^TELEGRAM_WEBHOOK_SECRET_TOKEN=.*/TELEGRAM_WEBHOOK_SECRET_TOKEN=<nuevo-secret>/" ~/corpusRAG/.env
```

### 6.3 Recrear el contenedor
```bash
cd ~/corpusRAG/deploy
docker compose up -d --force-recreate webhook
```

Espera a que esté `Up` (revisa con `docker compose ps`).

### 6.4 Re-registrar contra Telegram
```bash
cd ~/corpusRAG
(set -a; source .env; python scripts/set_telegram_webhook.py)
```

El script es re-ejecutable — `setWebhook` sobrescribe el registro anterior sin duplicar nada. No hace falta borrar con `--delete` primero.

---

## 7. Despliegues posteriores (automático vía CI)

Una vez completado el bootstrap, cualquier push a `main` que modifique código de Flujo B dispara `.github/workflows/deploy_telegram_webhook.yml` (task 40):

1. Corre la gate de tests: `pytest tests/unit/jokes/telegram/` y `pytest tests/unit/jokes/` (Silver, Reconciliación compartida).
2. Si pasan, se conecta al VPS por SSH y ejecuta:
   ```bash
   cd ~/corpusRAG
   git pull
   cd deploy
   docker compose up -d --build
   ```

**Requisitos:**
- SSH keys configurados en el VPS (sin contraseña o usando SSH agent).
- El workflow tiene acceso a los secretos `SSH_USER`, `SSH_HOST`, `SSH_PRIVATE_KEY` (configurados en GitHub → Settings → Secrets).

No hace falta repetir el bootstrap manual salvo que se **reinstale el VPS desde cero** (en cuyo caso vuelves al paso 1).

---

## 8. Dónde mirar logs

### Logs del webhook (aplicación FastAPI)
```bash
cd ~/corpusRAG/deploy
docker compose logs webhook -f
```

Muestra logs del tramo síncrono (respuesta 200) y del procesamiento en background (Silver/Reconciliación) que corre después de devolver.

Busca patrones como:
- `message_id=<id>` para rastrear un mensaje específico.
- `tipo_fuente=propio` (esperado, Flujo B).
- Excepciones o stack traces si algo falla.

### Logs de Caddy (TLS / proxy)
```bash
cd ~/corpusRAG/deploy
docker compose logs caddy -f
```

Busca errores de certificado, fallos de renovación ACME, o problemas de routing.

### Logs de Docker Compose en general
```bash
cd ~/corpusRAG/deploy
docker compose logs -f
```

Muestra ambos simultáneamente.

---

## 9. Solución de problemas comunes

### El webhook no recibe mensajes
**Síntomas:** envías un mensaje al bot en Telegram pero no llega nada al VPS.

**Verificar:**
1. ¿Está el contenedor `webhook` en estado `Up`?
   ```bash
   docker compose ps
   ```
2. ¿Está Caddy sirviendo TLS en 8443?
   ```bash
   docker compose logs caddy | grep "certificate obtained"
   ```
3. ¿El webhook está registrado en Telegram?
   ```bash
   curl -s "https://api.telegram.org/bot<token>/getWebhookInfo" | python3 -m json.tool
   ```
4. ¿El `chat_id` desde el que escribes está en `TELEGRAM_ALLOWED_CHAT_IDS`?
   ```bash
   grep TELEGRAM_ALLOWED_CHAT_IDS .env
   ```
5. **`getWebhookInfo` muestra `"last_error_message": "Connection timed out"` y `pending_update_count > 0`** — el contenedor y el registro están bien, pero algo delante del VPS bloquea el tráfico entrante al puerto 8443. Comprobado en producción (2026-07-28): con `docker-proxy` escuchando en `0.0.0.0:8443` (`sudo ss -tlnp | grep 8443`) y sin reglas de bloqueo en `iptables` (`DOCKER-USER`/`DOCKER-FORWARD` limpias), la causa era el **firewall de red del proveedor** (Hetzner Cloud Firewall), que por defecto solo abre 22/80/443. Solución: crear una regla inbound `TCP 8443` desde `0.0.0.0/0` en el firewall de Hetzner asignado al servidor (puede ser un firewall nuevo y separado del de Coolify — las reglas de varios firewalls asignados al mismo servidor se suman, no hace falta duplicar las reglas existentes). Verificar con `curl -v https://<dominio>:8443/health` desde fuera del VPS; si responde `200`, Telegram reintentará solo y entregará el backlog (`pending_update_count` baja a `0`) sin tener que re-registrar el webhook.

### `ConfiguracionInvalidaError: TELEGRAM_WEBHOOK_SECRET_TOKEN no configurada`
**Causa:** La variable no está en el `.env` o está vacía.

**Solución:**
```bash
nano .env  # edita manualmente
# O: echo "TELEGRAM_WEBHOOK_SECRET_TOKEN=$(openssl rand -hex 32)" >> .env
docker compose up -d --force-recreate webhook
docker compose logs webhook --tail=20
```

### Caddy no emite certificado (timeout DNS-01)
**Causa típica:** las claves de Porkbun son incorrectas o el acceso API no está habilitado para el dominio.

**Verificar en el dashboard de Porkbun:**
1. ¿Las claves `PORKBUN_API_KEY` / `PORKBUN_API_SECRET_KEY` son válidas?
2. ¿Está habilitado API Access a nivel de **dominio específico** (no solo de cuenta)?
3. ¿El registro DNS ya existe y apunta al VPS?

**Forzar reintentar:**
```bash
docker compose down caddy
docker compose up -d caddy
docker compose logs caddy --tail=50
```

### Mensajes llegan pero no se procesan (no aparecen en Supabase)
**Posible causa:** fallo en Silver, Reconciliación o inserción en Supabase.

**Verificar:**
```bash
docker compose logs webhook -f
# Busca líneas del mensaje o exceptions
```

Si ves errores de Supabase (ej: `RLS policy`, `connection refused`), verifica:
- `SUPABASE_URL` y `SUPABASE_SERVICE_KEY` son correctos.
- La tabla `chistes_telegram_bronze` existe en Supabase.
- El schema.sql se ha aplicado correctamente.

**Dos causas reales vistas en el primer despliegue en producción (2026-07-28)** — el mensaje SÍ llega a Bronze (el diseño de recuperación, task 46/47, garantiza que esto nunca se pierde: la fila queda con `procesado_at IS NULL` y el error se loguea), pero el tramo Silver falla:

1. **`LLMClientError: LLM_API_KEY / LLM_MODEL no configuradas en el entorno (.env)`** — el bootstrap del `.env` (§2.2) marcaba estas variables como "opcionales, no bloquean Flujo B", lo cual es **incorrecto**: Silver necesita el LLM para estructurar el chiste (`src/jokes/silver.py::estructurar_chiste`), así que `LLM_API_KEY`/`LLM_MODEL` (y `EMBEDDINGS_API_KEY`/`EMBEDDINGS_MODEL` para la reconciliación por similitud) **son obligatorias** para que el tramo background complete con éxito, aunque el webhook arranque igual sin ellas. Solución: añadirlas al `.env` del VPS y `docker compose up -d --force-recreate webhook`.
2. **`google.genai.errors.ClientError: 429 RESOURCE_EXHAUSTED`** — el tier gratuito de Gemini limita a 15 requests/min por modelo. Con varios mensajes reprocesándose seguidos (cada uno hace 1-2 llamadas: estructurar + resolver taxonomía) se agota rápido. No es un error de configuración — el propio mensaje de Google incluye `retryDelay` (unos 50-60s). Solución: esperar ese tiempo y volver a correr `scripts/reprocesar_bronze_pendiente.py` (es re-ejecutable, solo reintenta lo que sigue con `procesado_at IS NULL`).

**Recuperar mensajes que quedaron con `procesado_at IS NULL`** (por cualquiera de las dos causas de arriba, o cualquier otro fallo transitorio del tramo background):
```bash
cd ~/corpusRAG
(set -a; source .env; python scripts/reprocesar_bronze_pendiente.py)
```

### `column chistes_telegram_bronze.procesado_at does not exist` (o cualquier columna de `schema.sql` ausente)
**Causa:** `schema.sql` es la fuente de verdad del esquema en el repo, pero **no se aplica solo — cada cambio de esquema requiere ejecutarlo a mano** en el SQL Editor del dashboard de Supabase del proyecto real. Si se añadió una columna a `schema.sql` en un commit posterior a la última vez que se aplicó el esquema completo (ej. `procesado_at`, añadida en la task 46 para la recuperación post-200), la base de datos real queda desincronizada — visto en el primer despliegue en producción (2026-07-28), causaba un `42703` de PostgREST al intentar leer/escribir esa columna.

**Solución:** en el SQL Editor de Supabase (proyecto → SQL Editor → New query), ejecutar el `ALTER TABLE` correspondiente a la columna que falte, ej.:
```sql
alter table chistes_telegram_bronze add column if not exists procesado_at timestamptz;
```
Antes de dar por cerrado cualquier cambio a `schema.sql`, comprobar que se aplicó también contra el proyecto Supabase real — no solo que el fichero del repo lo tenga.

### El contenedor de webhook está en crash-loop sin mensaje claro
```bash
docker compose logs webhook --tail=100
```

Si el log está vacío o truncado, prueba aumentar el tail o buscar líneas de Python con error:
```bash
docker compose logs webhook | grep -i error
```

---

## 10. Migración a 3 schemas (bronze/silver/gold) — runbook de cutover

**Referencia:** Task 55, decisión P25 (`docs/specs/00-overview.md` §P25), especificación de schema en `src/jokes/SPEC.md` §"Acceso por schema" y migración SQL en `src/jokes/migration_p25_schemas.sql`.

Este runbook consolida el orden exacto de pasos para ejecutar el cutover de la base de datos de Supabase del proyecto real desde un esquema único `public` a tres esquemas: `bronze` (crudo), `silver` (procesado), `gold` (estructurado). **No ejecutar nada desde este runbook — es solo documentación del orden operativo y verificación. La ejecución real es task 56.** Todo está ya diseñado en las tareas previas (53 y 54).

### Paso 1: Verificar que el código está desplegado en modo schema-aware (task 54)

La rama `main` debe tener mergeado el código de la task 54, que añade soporte para `.schema(...)` a `src/jokes/supabase_store.py` y `src/theory/teoria_store.py` mediante la variable de entorno `SUPABASE_SCHEMA_MODE`.

**Mientras esta variable esté ausente o fijada a `"public"`, el comportamiento es idéntico al anterior** — cero riesgo de cambio observable. El deploy automático (§7) que corra tras el merge de la task 54 ya habrá actualizado el código en producción si está activo; si no, fuerza un redeploy:

```bash
cd ~/corpusRAG/deploy
git pull
docker compose up -d --build
```

### Paso 2: Aplicar el SQL de cutover contra Supabase real

En el dashboard de Supabase (proyecto → **SQL Editor** → **New query**), copia y ejecuta **completo** el contenido de `src/jokes/migration_p25_schemas.sql`:

```bash
cat src/jokes/migration_p25_schemas.sql
```

Luego pega todo en el SQL Editor y ejecuta. Este SQL:
- Crea los tres schemas (`bronze`, `silver`, `gold`).
- Mueve las 8 tablas existentes de `public` a sus schemas finales con `ALTER TABLE ... SET SCHEMA ...` + `RENAME TO` (operación pura de metadata, cero copias de datos).
- Crea las 4 tablas nuevas de documentos (`bronze.teoria_documentos`, `silver.teoria_documentos`, `bronze.historico_documentos`, `silver.historico_documentos`).
- Aplica `GRANT` a `service_role` para acceso desde PostgREST.
- Ejecuta `notify pgrst, 'reload schema'` para forzar que PostgREST recargue el caché.

**No copia ni una fila — es solo reordenamiento de metadata.** Remite a `src/jokes/migration_p25_schemas.sql` para el detalle de cada paso.

### Paso 3: Verificación de conteos antes/después

Ejecuta los `SELECT count(*)` como comentarios al final de `src/jokes/migration_p25_schemas.sql` (líneas ~239-259) en **dos pasadas**:

**Antes del cutover** (contra `public.*` con la base de datos sin tocar):
```sql
select count(*) from public.chistes;
select count(*) from public.chistes_revisiones;
select count(*) from public.temas;
select count(*) from public.tecnicas;
select count(*) from public.fuentes;
select count(*) from public.candidatos_taxonomia;
select count(*) from public.chistes_telegram_bronze;
select count(*) from public.teoria_chunks;
```

Anota cada fila.

**Después del cutover** (ejecuta el SQL del paso 2 primero, luego estos):
```sql
select count(*) from silver.chistes;
select count(*) from silver.chistes_revisiones;
select count(*) from silver.temas;
select count(*) from silver.tecnicas;
select count(*) from silver.fuentes;
select count(*) from silver.candidatos_taxonomia;
select count(*) from bronze.chistes_telegram;
select count(*) from gold.teoria_chunks;
```

**Los números deben ser IDÉNTICOS fila a fila.** Si alguno difiere, detente — no continúes hasta que coincidan exactamente.

### Paso 4: Crear los 4 buckets privados en Supabase Storage

En el dashboard de Supabase (proyecto → **Storage** → **New bucket**), crea estos 4 buckets, marcando cada uno como **Private** (no público):

1. `bronze-teoria` — crudo de libros, apuntes, transcripciones WhisperX (Flujo A).
2. `silver-teoria` — limpio, traducido y normalizado (Flujo A).
3. `bronze-historico` — documentos originales con color de fuente (Flujo C).
4. `silver-historico` — marcados con `[REMATE]`/`[CHISTOIDE]` (Flujo C).

**Razón:** separar por capa (regenerable vs. original) y por flujo mantiene la diferencia expresada en la estructura de datos y permite gestión diferenciada de permisos (`personal_only` incluida en el material). Ver `docs/specs/00-overview.md` líneas ~509-519.

### Paso 5: Exponer los schemas y aplicar GRANTs manualmente en el dashboard

**Este paso NO se puede hacer vía SQL** — es configuración de PostgREST:

1. En el dashboard → **Settings** → **API** → **Exposed schemas**.
2. Añade `bronze`, `silver` y `gold` a la lista (si no están ya).
3. Guarda.

Los `GRANT` ya están ejecutados por el SQL del paso 2, pero sin esta exposición de schemas en el dashboard, PostgREST rechazará cualquier `.schema("bronze")` con un error de autorización.

### Paso 6: Flip de la variable `SUPABASE_SCHEMA_MODE` en el `.env` de producción

Cambia en `~/corpusRAG/.env`:

```bash
# De (ausente o "public"):
# SUPABASE_SCHEMA_MODE=public

# A:
SUPABASE_SCHEMA_MODE=p25
```

El valor exacto `"p25"` activa el mapeo fijado en `_SCHEMA_TABLAS` de `src/jokes/supabase_store.py` y `src/theory/teoria_store.py`. Cualquier valor distinto de `"public"` resuelve tablas con `.schema(...)`.

### Paso 7: Redeploy del webhook para recoger el nuevo `.env`

El webhook lee `.env` al arrancar. Fuerza una recreación del contenedor:

```bash
cd ~/corpusRAG/deploy
docker compose up -d --force-recreate webhook
docker compose ps
```

Espera a que `webhook` esté en estado `Up` (no `Restarting`).

### Paso 8: Verificación post-cutover

**Verificar que el webhook sigue funcionando:**

Obtén un nuevo mensaje de Telegram del bot (envía un mensaje desde un chat autorizado). Confirma en los logs:

```bash
cd ~/corpusRAG/deploy
docker compose logs webhook -f
```

Busca líneas con el `message_id` o `chat_id` — debe insertar en `bronze.chistes_telegram` (ya no `public.chistes_telegram_bronze`).

**Verificar con tests:**

```bash
cd ~/corpusRAG
# Fuerza que los tests usen el nuevo schema mode
export SUPABASE_SCHEMA_MODE=p25
pytest tests/integration/test_supabase_store_live.py -v
```

(Si no tienes credenciales de Supabase real en el entorno de test, salta este paso — es solo para verificación en desarrollo contra una copia real.)

### Paso 9: Rollback (si es necesario)

Si algo falla en los pasos posteriores y necesitas revertir:

**SQL de vuelta atrás** (ejecuta en el SQL Editor, en este orden exacto — INVERSO al del cutover):

```sql
-- Revertir el orden exacto de movimiento de tablas (inverso)
alter table gold.teoria_chunks set schema public;

alter table bronze.chistes_telegram rename to chistes_telegram_bronze;
alter table bronze.chistes_telegram_bronze set schema public;

alter table silver.chistes_revisiones set schema public;
alter table silver.chistes set schema public;
alter table silver.candidatos_taxonomia set schema public;
alter table silver.fuentes set schema public;
alter table silver.tecnicas set schema public;
alter table silver.temas set schema public;

-- Las 4 tablas nuevas (teoria_documentos, historico_documentos) quedan huérfanas,
-- pero no hay datos que recuperar aún (se crean vacías en el cutover).
-- Opcionalmente, si quieres limpiar completamente:
-- drop table if exists bronze.teoria_documentos;
-- drop table if exists silver.teoria_documentos;
-- drop table if exists bronze.historico_documentos;
-- drop table if exists silver.historico_documentos;

-- Refresco de caché PostgREST
notify pgrst, 'reload schema';
```

**Revertir el `.env`:**

```bash
# Edita .env y cambia:
SUPABASE_SCHEMA_MODE=public
# O simplemente comenta/borra la línea si no estaba antes del cutover
```

**Redeploy del webhook:**

```bash
cd ~/corpusRAG/deploy
docker compose up -d --force-recreate webhook
```

**Nota sobre las tablas nuevas:** como se crean con `CREATE TABLE IF NOT EXISTS` sin datos durante el cutover, no hay que recuperar nada de ellas. Si el rollback es completo, quedan vacías en sus schemas nuevos — no interfieren con la operación, pero si quieres limpiar también, los `DROP TABLE` están comentados arriba (opcionalmente a discreción de quien ejecute, no es un paso obligatorio).

---

## Referencias

- **Docker Compose:** `deploy/docker-compose.yml` (servicios, volúmenes, env_file)
- **Caddy / TLS:** `deploy/Caddyfile` y `deploy/Dockerfile.caddy`
- **Webhook (aplicación):** `src/jokes/telegram/webhook_app.py`
- **Spec técnico:** `src/jokes/telegram/SPEC.md` §"Transporte" + `docs/specs/00-overview.md` P22, P24
- **Registro de webhook:** `scripts/set_telegram_webhook.py` (task 37)

---

**Última actualización:** 2026-07-28 (task 41)
