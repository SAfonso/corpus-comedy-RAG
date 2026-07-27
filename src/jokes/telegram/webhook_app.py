"""webhook_app — Flujo B: transporte HTTP del webhook de Telegram (task 36).

Contrato (`src/jokes/telegram/SPEC.md` §"Contrato HTTP",
§"Validación del secret_token", §"Allowlist de chat_id",
§"Orquestación end-to-end"): este módulo es **solo transporte** — auth del
header, parseo del cuerpo, códigos HTTP y agendar el tramo caro en
`BackgroundTasks`. Toda la cadena de negocio (extracción, allowlist, Bronze,
Silver, taxonomías, reconciliación, routing) vive en `telegram/pipeline.py`
(task 35, **congelado** — no se reimplementa nada de eso aquí).

## Decisión de diseño: factory `crear_app`, SIN singleton de módulo `app`

El patrón obvio para una app FastAPI sería `app = crear_app()` a nivel de
módulo, para que `uvicorn src.jokes.telegram.webhook_app:app` la sirva
directo. Se descarta aquí a propósito: `crear_app()` sin argumentos resuelve
`TELEGRAM_WEBHOOK_SECRET_TOKEN` / `TELEGRAM_ALLOWED_CHAT_IDS` del entorno y
construye un `SupabaseStore` real (que a su vez exige
`SUPABASE_URL`/`SUPABASE_SERVICE_KEY`) — si esa construcción ocurriera a
nivel de módulo, el simple `import` de este fichero (lo que hace pytest al
recolectar tests, o cualquier tooling que inspeccione el módulo) exigiría
credenciales de producción reales, exactamente lo que la task pide evitar
("deben construirse de forma que sean inyectables/mockeables en los tests
sin tener que setear variables de entorno reales ni tocar red").

En su lugar, el único símbolo exportado para producción es la factory
`crear_app` misma, pensada para el flag `--factory` de uvicorn:

    uvicorn src.jokes.telegram.webhook_app:crear_app --factory --host 0.0.0.0 --port 8000

Con `--factory`, uvicorn invoca `crear_app()` una vez al arrancar el
proceso — sigue siendo fail-fast de verdad (si falta el secret o la
allowlist, el proceso muere antes de servir tráfico, con traceback claro),
pero el `import` del módulo en sí no tiene efectos secundarios. El wiring
exacto del contenedor (`Dockerfile`/`CMD`, task 38) no es scope de esta
tarea; aquí solo se deja la factory lista para consumirlo así.

## Inyección de `store` / config — mismo patrón que los scripts CLI del repo

Igual que `scripts/run_historico.py` / `scripts/run_pipeline.py` resuelven
sus dependencias vía parámetros opcionales que caen a un default de
producción si se omiten, `crear_app(store=None, secret_token=None,
allowlist=None)`: cualquier argumento que se pase explícito gana; el que se
omita (`None`) se resuelve del entorno (y la app falla al construirse si no
está disponible — nunca un fallback "abierto"). Los tests siempre pasan los
tres explícitos, así que nunca necesitan variables de entorno reales ni red
(`tests/unit/jokes/telegram/test_webhook_app.py`).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from src.jokes.telegram.pipeline import (
    procesar_evento_background,
    procesar_update_sincrono,
)

logger = logging.getLogger(__name__)

HEADER_SECRET_TOKEN = "X-Telegram-Bot-Api-Secret-Token"


class ConfiguracionInvalidaError(RuntimeError):
    """Lanzado por `crear_app` si `secret_token`/`allowlist` no se pueden
    resolver (ni por argumento ni por entorno) — arranque fail-fast, nunca
    un endpoint que degrada a "abierto" (`telegram/SPEC.md`)."""


def _resolver_secret_token(secret_token: Optional[str]) -> str:
    """`secret_token` explícito gana; si no, `TELEGRAM_WEBHOOK_SECRET_TOKEN`.

    Vacío o ausente en ambos sitios -> `ConfiguracionInvalidaError` (fail-fast
    de arranque, `telegram/SPEC.md` §"Validación del secret_token")."""
    valor = secret_token if secret_token is not None else os.environ.get(
        "TELEGRAM_WEBHOOK_SECRET_TOKEN"
    )
    if not valor:
        raise ConfiguracionInvalidaError(
            "TELEGRAM_WEBHOOK_SECRET_TOKEN no configurada o vacía. La app no "
            "arranca sin ella — nunca se degrada a 'sin secret configurado, "
            "acepto todo' (telegram/SPEC.md §Validación del secret_token)."
        )
    return valor


def _resolver_allowlist(allowlist: Optional[frozenset]) -> frozenset:
    """`allowlist` explícita gana; si no, se parsea `TELEGRAM_ALLOWED_CHAT_IDS`
    (enteros separados por comas) una vez, a `frozenset[int]`.

    Ausente, vacía, o con un valor no parseable como entero ->
    `ConfiguracionInvalidaError`. "Vacía = admito a todos" es exactamente el
    fallo que este control existe para evitar (`telegram/SPEC.md`
    §"Allowlist de chat_id") — nunca se convierte en ese fallback."""
    if allowlist is not None:
        resuelta = frozenset(allowlist)
    else:
        crudo = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS")
        if not crudo:
            raise ConfiguracionInvalidaError(
                "TELEGRAM_ALLOWED_CHAT_IDS no configurada o vacía. Es el "
                "control de coste primario del Flujo B — nunca se admite "
                "'vacío = todos' (telegram/SPEC.md §Allowlist de chat_id)."
            )
        try:
            resuelta = frozenset(
                int(fragmento.strip())
                for fragmento in crudo.split(",")
                if fragmento.strip()
            )
        except ValueError as exc:
            raise ConfiguracionInvalidaError(
                "TELEGRAM_ALLOWED_CHAT_IDS contiene un valor no parseable "
                f"como entero: {crudo!r}"
            ) from exc

    if not resuelta:
        raise ConfiguracionInvalidaError(
            "TELEGRAM_ALLOWED_CHAT_IDS resolvió a una allowlist vacía. "
            "'Vacía = admito a todos' es el fallo que este control existe "
            "para evitar (telegram/SPEC.md §Allowlist de chat_id)."
        )
    return resuelta


def _resolver_store(store: Optional[Any]) -> Any:
    """`store` explícito gana (typicamente un doble de test); si no, un
    `SupabaseStore` real (`src/jokes/supabase_store.py`), construido una
    sola vez al arrancar la app, no en cada request."""
    if store is not None:
        return store
    from src.jokes.supabase_store import SupabaseStore  # import perezoso: evita red en tests

    return SupabaseStore()


def crear_app(
    store: Optional[Any] = None,
    secret_token: Optional[str] = None,
    allowlist: Optional[frozenset] = None,
) -> FastAPI:
    """Factory de la app FastAPI del webhook — ver docstring del módulo.

    Fail-fast: `secret_token`/`allowlist` se resuelven (argumento o entorno)
    y `store` se construye ANTES de registrar las rutas; cualquier fallo de
    configuración lanza `ConfiguracionInvalidaError` aquí mismo, antes de que
    la app pueda servir una sola petición.
    """
    secret_token_resuelto = _resolver_secret_token(secret_token)
    allowlist_resuelta = _resolver_allowlist(allowlist)
    store_resuelto = _resolver_store(store)

    app = FastAPI(title="Comedy Corpus — Flujo B webhook")

    @app.get("/health")
    async def health() -> dict:
        """Sin auth, sin dependencias externas — nunca toca `store`
        (`telegram/SPEC.md` §Contrato HTTP: lo consume el healthcheck del
        contenedor, no debe reiniciar por una caída de Supabase ajena)."""
        return {"status": "ok"}

    @app.post("/telegram/webhook")
    async def webhook(request: Request, background_tasks: BackgroundTasks):
        # Paso 1 — secret_token, tiempo constante. Ausente o distinto -> 403
        # SIN parsear el cuerpo, sin tocar Bronze, sin loguear la petición.
        header_recibido = request.headers.get(HEADER_SECRET_TOKEN)
        if not header_recibido or not hmac.compare_digest(
            header_recibido, secret_token_resuelto
        ):
            return JSONResponse(status_code=403, content={"detail": "forbidden"})

        # Paso 2 — cuerpo como JSON del Update. No válido -> 400, no llega a Bronze.
        cuerpo_crudo = await request.body()
        try:
            update = json.loads(cuerpo_crudo)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(status_code=400, content={"detail": "invalid json body"})

        # Paso 3 — tramo síncrono completo (extracción -> allowlist -> Bronze),
        # delegado tal cual a pipeline.py (congelado, task 35).
        resultado = procesar_update_sincrono(update, store_resuelto, allowlist_resuelta)

        # Paso 5 — SOLO 'aceptado' agenda el tramo caro, DESPUÉS del 200.
        if resultado.estado == "aceptado":
            background_tasks.add_task(
                procesar_evento_background,
                resultado.texto_limpio,
                resultado.fila_bronze_id,
                store_resuelto,
            )

        # Paso 4 — 200 universal para toda petición autenticada + JSON válido.
        return {"ok": True, "estado": resultado.estado}

    return app
