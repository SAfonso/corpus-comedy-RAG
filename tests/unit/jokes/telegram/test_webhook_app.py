"""Tests unitarios de `src/jokes/telegram/webhook_app.py` (task 36) — sin red.

Cubre el CONTRATO HTTP del transporte del Flujo B (`telegram/SPEC.md`
§"Contrato HTTP", §"Validación del secret_token", §"Orquestación
end-to-end"), usando `TestClient` de FastAPI/Starlette (sin abrir sockets
reales) y dobles inyectados vía la factory `crear_app`:

- `store`: un doble mínimo (nunca un `SupabaseStore` real / red).
- `procesar_update_sincrono` / `procesar_evento_background`: monkeypatcheados
  a nivel de módulo (`webhook_app.procesar_update_sincrono`, etc.) para
  aislar el transporte de la lógica ya testeada en
  `tests/unit/jokes/telegram/test_pipeline.py` (task 35, congelada).

Checklist de la task (ver también docstring de `webhook_app.py` para la
decisión de diseño de la factory):

- `403` si falta el header del secret o es incorrecto, y NADA del pipeline
  se invoca (ni siquiera se parsea el cuerpo).
- `400` si el cuerpo no es JSON válido, tampoco toca el pipeline.
- `200 {"ok": true, "estado": "aceptado"}` agenda el tramo background.
- `200` con `ignorado_no_autorizado` / `duplicado` NO agenda nada.
- `GET /health` — `200 {"status": "ok"}`, sin tocar `store` en absoluto.
- La factory falla (fail-fast) si `secret_token`/`allowlist` no se proveen y
  el entorno tampoco los tiene — nunca "endpoint abierto".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.jokes.telegram import webhook_app
from src.jokes.telegram.pipeline import ResultadoSincrono

SECRET = "test-secret-token-0123"
CHAT_AUTORIZADO = 555
ALLOWLIST = frozenset({CHAT_AUTORIZADO})

HEADER_SECRET = "X-Telegram-Bot-Api-Secret-Token"


class FakeStore:
    """Doble de `SupabaseStore` que solo registra llamadas — nunca red.

    No implementa ningún método real: si algo del pipeline intentara usarlo
    de verdad (en vez de pasar por los dobles de `procesar_update_sincrono`/
    `procesar_evento_background` inyectados por los tests) fallaría fuerte y
    visiblemente en vez de silenciosamente tocar red.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):  # pragma: no cover - solo dispara si se usa mal
        def _no_deberia_llamarse(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            raise AssertionError(
                f"FakeStore.{name} no debería invocarse en este test — "
                "el store real nunca debe tocarse sin red."
            )

        return _no_deberia_llamarse


def _update_valido(update_id: int = 1, chat_id: int = CHAT_AUTORIZADO) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "date": 1690000000,
            "text": "un chiste cualquiera",
        },
    }


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def app(store: FakeStore):
    return webhook_app.crear_app(
        store=store,
        secret_token=SECRET,
        allowlist=ALLOWLIST,
    )


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def _mock_pipeline_no_debe_llamarse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instala dobles que fallan fuerte si el transporte llega a invocarlos.

    Usado en los tests de 403/400: si el endpoint parseara el cuerpo o
    tocara el pipeline en esos casos, el test debe fallar.
    """

    def _sincrono_no_debe_llamarse(*args, **kwargs):
        raise AssertionError("procesar_update_sincrono no debería llamarse")

    def _background_no_debe_llamarse(*args, **kwargs):
        raise AssertionError("procesar_evento_background no debería llamarse")

    monkeypatch.setattr(
        webhook_app, "procesar_update_sincrono", _sincrono_no_debe_llamarse
    )
    monkeypatch.setattr(
        webhook_app, "procesar_evento_background", _background_no_debe_llamarse
    )


# ---------------------------------------------------------------------------
# 403 — secret_token ausente o incorrecto.
# ---------------------------------------------------------------------------


def test_403_si_falta_el_header_del_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_pipeline_no_debe_llamarse(monkeypatch)

    respuesta = client.post("/telegram/webhook", json=_update_valido())

    assert respuesta.status_code == 403


def test_403_si_el_header_del_secret_es_incorrecto(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_pipeline_no_debe_llamarse(monkeypatch)

    respuesta = client.post(
        "/telegram/webhook",
        json=_update_valido(),
        headers={HEADER_SECRET: "token-incorrecto"},
    )

    assert respuesta.status_code == 403


def test_403_no_toca_el_store(
    client: TestClient, store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_pipeline_no_debe_llamarse(monkeypatch)

    client.post("/telegram/webhook", json=_update_valido())

    assert store.calls == []


# ---------------------------------------------------------------------------
# 400 — cuerpo no es JSON válido.
# ---------------------------------------------------------------------------


def test_400_si_el_cuerpo_no_es_json_valido(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_pipeline_no_debe_llamarse(monkeypatch)

    respuesta = client.post(
        "/telegram/webhook",
        content=b"esto no es json{{{",
        headers={
            HEADER_SECRET: SECRET,
            "Content-Type": "application/json",
        },
    )

    assert respuesta.status_code == 400


# ---------------------------------------------------------------------------
# 200 — autenticado + JSON válido, los 4 estados síncronos.
# ---------------------------------------------------------------------------


def test_200_aceptado_agenda_el_tramo_background(
    client: TestClient, store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    llamadas_background = []

    def _sincrono_fake(update, store_arg, allowlist_arg):
        assert store_arg is store
        assert allowlist_arg == ALLOWLIST
        return ResultadoSincrono(
            estado="aceptado", texto_limpio="texto limpio", fila_bronze_id=42
        )

    def _background_fake(texto_limpio, fila_bronze_id, store_arg, **kwargs):
        llamadas_background.append((texto_limpio, fila_bronze_id, store_arg))

    monkeypatch.setattr(webhook_app, "procesar_update_sincrono", _sincrono_fake)
    monkeypatch.setattr(webhook_app, "procesar_evento_background", _background_fake)

    respuesta = client.post(
        "/telegram/webhook",
        json=_update_valido(),
        headers={HEADER_SECRET: SECRET},
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True, "estado": "aceptado"}
    # TestClient ejecuta las BackgroundTasks antes de devolver el control aquí.
    assert llamadas_background == [("texto limpio", 42, store)]


def test_200_ignorado_no_autorizado_no_agenda_background(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _sincrono_fake(update, store_arg, allowlist_arg):
        return ResultadoSincrono(estado="ignorado_no_autorizado")

    def _background_no_debe_llamarse(*args, **kwargs):
        raise AssertionError("procesar_evento_background no debería agendarse")

    monkeypatch.setattr(webhook_app, "procesar_update_sincrono", _sincrono_fake)
    monkeypatch.setattr(
        webhook_app, "procesar_evento_background", _background_no_debe_llamarse
    )

    respuesta = client.post(
        "/telegram/webhook",
        json=_update_valido(chat_id=999),
        headers={HEADER_SECRET: SECRET},
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True, "estado": "ignorado_no_autorizado"}


def test_200_duplicado_no_agenda_background(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _sincrono_fake(update, store_arg, allowlist_arg):
        return ResultadoSincrono(estado="duplicado", texto_limpio="texto limpio")

    def _background_no_debe_llamarse(*args, **kwargs):
        raise AssertionError("procesar_evento_background no debería agendarse")

    monkeypatch.setattr(webhook_app, "procesar_update_sincrono", _sincrono_fake)
    monkeypatch.setattr(
        webhook_app, "procesar_evento_background", _background_no_debe_llamarse
    )

    respuesta = client.post(
        "/telegram/webhook",
        json=_update_valido(),
        headers={HEADER_SECRET: SECRET},
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True, "estado": "duplicado"}


def test_200_ignorado_no_texto_no_agenda_background(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _sincrono_fake(update, store_arg, allowlist_arg):
        return ResultadoSincrono(estado="ignorado_no_texto")

    def _background_no_debe_llamarse(*args, **kwargs):
        raise AssertionError("procesar_evento_background no debería agendarse")

    monkeypatch.setattr(webhook_app, "procesar_update_sincrono", _sincrono_fake)
    monkeypatch.setattr(
        webhook_app, "procesar_evento_background", _background_no_debe_llamarse
    )

    respuesta = client.post(
        "/telegram/webhook",
        json={"update_id": 5, "callback_query": {"id": "x"}},
        headers={HEADER_SECRET: SECRET},
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True, "estado": "ignorado_no_texto"}


# ---------------------------------------------------------------------------
# GET /health — sin auth, sin tocar el store.
# ---------------------------------------------------------------------------


def test_health_200_sin_auth(client: TestClient) -> None:
    respuesta = client.get("/health")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok"}


def test_health_no_toca_el_store(client: TestClient, store: FakeStore) -> None:
    client.get("/health")

    assert store.calls == []


# ---------------------------------------------------------------------------
# Arranque fail-fast de la factory — sin fallback "endpoint abierto".
# ---------------------------------------------------------------------------


def test_crear_app_falla_sin_secret_token_ni_en_argumento_ni_en_entorno(
    monkeypatch: pytest.MonkeyPatch, store: FakeStore
) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "555")

    with pytest.raises(Exception):
        webhook_app.crear_app(store=store)


def test_crear_app_falla_con_secret_token_vacio_en_entorno(
    monkeypatch: pytest.MonkeyPatch, store: FakeStore
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "555")

    with pytest.raises(Exception):
        webhook_app.crear_app(store=store)


def test_crear_app_falla_sin_allowlist_ni_en_argumento_ni_en_entorno(
    monkeypatch: pytest.MonkeyPatch, store: FakeStore
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", SECRET)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)

    with pytest.raises(Exception):
        webhook_app.crear_app(store=store)


def test_crear_app_falla_con_allowlist_vacia_en_entorno(
    monkeypatch: pytest.MonkeyPatch, store: FakeStore
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", SECRET)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")

    with pytest.raises(Exception):
        webhook_app.crear_app(store=store)


def test_crear_app_falla_con_allowlist_no_parseable(
    monkeypatch: pytest.MonkeyPatch, store: FakeStore
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", SECRET)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "555,no-es-un-entero")

    with pytest.raises(Exception):
        webhook_app.crear_app(store=store)


def test_crear_app_ok_con_allowlist_del_entorno_parseada_a_frozenset(
    monkeypatch: pytest.MonkeyPatch, store: FakeStore
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", SECRET)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "555, 777")

    app_construida = webhook_app.crear_app(store=store)

    assert app_construida is not None
