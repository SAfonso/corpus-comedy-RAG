# deploy/Dockerfile — Flujo B: imagen del servicio webhook (task 38).
#
# Sirve src/jokes/telegram/webhook_app.py con uvicorn en modo factory (patrón
# fijado en esa spec, task 36): la app solo se construye al arrancar el
# proceso — falla rápido (fail-fast) si faltan TELEGRAM_WEBHOOK_SECRET_TOKEN /
# TELEGRAM_ALLOWED_CHAT_IDS / credenciales de Supabase, nunca al importar el
# módulo. Este Dockerfile no debe capturar ni suavizar ese fallo.
#
# Puerto interno: 8000. Fijado aquí porque docker-compose.yml (mismo commit)
# y el Caddyfile de la task 39 tienen que usar exactamente el mismo valor.
#
# Dependencias — requirements.txt COMPLETO, no un subset reducido:
# se revisaron los imports transitivos reales de webhook_app.py ->
# pipeline.py -> (reconciliacion, routing, silver, taxonomias,
# supabase_store) -> utils/llm/{client,embeddings}. En import-time solo
# hacen falta fastapi + python-dotenv, pero crear_app() (sin argumentos,
# como la sirve este contenedor) construye un SupabaseStore de producción
# real al arrancar, y silver.py/taxonomias.py hacen un import perezoso de
# `google-genai` en cuanto llega el primer chiste — ambos paquetes (y sus
# dependencias transitivas: gotrue, postgrest-py, storage3, realtime, etc.
# para supabase-py) tienen que estar instalados igualmente. Recortar el
# requirements.txt a mano es fácil de dejar corto sin darse cuenta, y el
# ahorro de tamaño de imagen no compensa el riesgo de un
# ModuleNotFoundError en producción. Trade-off aceptado: la imagen también
# carga markitdown/pdf2image/pytesseract/ebooklib/google-api-python-client
# (Flujo A / Flujo C) que este servicio nunca importa — más pesada, cero
# riesgo de import roto. Revisar si el tamaño llega a ser un problema real.
FROM python:3.12-slim

WORKDIR /app

# Capa de dependencias separada del código: cachea el pip install mientras
# no cambie requirements.txt, aunque cambie src/.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Solo el código fuente — nada de data/, tests/, docs/, etc. (excluido del
# contexto de build por deploy/Dockerfile.dockerignore, ver ese fichero para
# por qué no se llama deploy/.dockerignore).
COPY src/ ./src

# Usuario no-root — sencillo en python:3.12-slim (Debian, useradd disponible
# de base), sin fricción añadida al build.
RUN useradd --create-home --uid 1000 webhook \
    && chown -R webhook:webhook /app
USER webhook

# Documental (no publica nada por sí solo): docker-compose.yml no mapea este
# puerto al host — lo alcanzará el servicio `caddy` de la task 39 dentro de
# la red por defecto de Compose.
EXPOSE 8000

CMD ["uvicorn", "src.jokes.telegram.webhook_app:crear_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
