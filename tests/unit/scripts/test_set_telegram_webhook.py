"""Tests para `scripts/set_telegram_webhook.py` (registro one-shot del
webhook de Telegram, Flujo B, task 37).

Contrato (`scripts/set_telegram_webhook.py` docstring, `src/jokes/telegram/
SPEC.md` §"Registro del webhook (setWebhook)"): registro one-shot
re-ejecutable sobre la Bot API de Telegram, vía un cliente HTTP inyectable
(`http_post_fn`) — NUNCA red real en estos tests. Las llamadas reales, si
algún día se necesitan, vivirían en `tests/integration/` (fuera de scope de
esta task; ver el límite duro explícito de la task: no se ejecuta este
script contra la API real de Telegram bajo ninguna circunstancia).
"""
import json

import pytest

from scripts.set_telegram_webhook import construir_resumen, main


# ---------------------------------------------------------------------------
# Doble de prueba del cliente HTTP — nunca red real.
# ---------------------------------------------------------------------------


class _HttpPostEspia:
    """Doble de `http_post_fn`. `respuestas` mapea nombre de método de la Bot
    API (`setWebhook`, `deleteWebhook`, `getWebhookInfo`) a la respuesta JSON
    (ya parseada) que devolvería. `excepcion_en`, si se indica, hace que la
    llamada a ESE método lance en vez de devolver."""

    def __init__(self, respuestas: dict, excepcion_en: str | None = None):
        self.respuestas = respuestas
        self.excepcion_en = excepcion_en
        self.llamadas: list[dict] = []

    def __call__(self, url: str, payload: dict) -> dict:
        metodo = url.rsplit("/", 1)[-1]
        self.llamadas.append({"url": url, "payload": payload, "metodo": metodo})
        if self.excepcion_en == metodo:
            raise RuntimeError(f"fallo de red simulado en {metodo}")
        return self.respuestas[metodo]


_URL_ESPERADA = "https://turrabot.machango.org:8443/telegram/webhook"


def _args_registro(extra: list | None = None) -> list:
    base = [
        "--bot-token",
        "tok-de-mentira",
        "--url",
        _URL_ESPERADA,
        "--secret-token",
        "secreto-super-largo-1234",
    ]
    return base + (extra or [])


# ---------------------------------------------------------------------------
# construir_resumen — forma pura del JSON, y que el secret_token nunca
# viaja en claro.
# ---------------------------------------------------------------------------


def test_construir_resumen_forma_basica():
    resumen = construir_resumen(
        "set",
        url_solicitada=_URL_ESPERADA,
        allowed_updates=["message"],
        secret_token="secreto-super-largo-1234",
        resultado_api={"ok": True, "result": True},
        verificacion={"ok": True, "url_esperada": _URL_ESPERADA, "url_obtenida": _URL_ESPERADA},
    )

    assert resumen["accion"] == "set"
    assert resumen["url_solicitada"] == _URL_ESPERADA
    assert resumen["allowed_updates"] == ["message"]
    assert resumen["secret_token_enmascarado"] == "***1234"
    assert "secreto-super-largo-1234" not in resumen["secret_token_enmascarado"]
    assert resumen["error_configuracion"] is None


def test_construir_resumen_secreto_ausente_es_none():
    resumen = construir_resumen("delete")
    assert resumen["secret_token_enmascarado"] is None


# ---------------------------------------------------------------------------
# Registro exitoso con verificación -> exit 0
# ---------------------------------------------------------------------------


def test_registro_exitoso_con_verificacion_exit_0(capsys):
    espia = _HttpPostEspia(
        respuestas={
            "setWebhook": {"ok": True, "result": True},
            "getWebhookInfo": {
                "ok": True,
                "result": {"url": _URL_ESPERADA, "allowed_updates": ["message"]},
            },
        }
    )

    codigo = main(_args_registro(), http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 0
    assert [l["metodo"] for l in espia.llamadas] == ["setWebhook", "getWebhookInfo"]

    resumen = json.loads(salida.out)
    assert resumen["accion"] == "set"
    assert resumen["verificacion"] == {
        "ok": True,
        "url_esperada": _URL_ESPERADA,
        "url_obtenida": _URL_ESPERADA,
    }
    assert resumen["resultado_api"] == {"ok": True, "result": True}


# ---------------------------------------------------------------------------
# setWebhook con ok: false -> exit 1, sin intentar verificar
# ---------------------------------------------------------------------------


def test_setwebhook_ok_false_exit_1_sin_verificar(capsys):
    espia = _HttpPostEspia(
        respuestas={
            "setWebhook": {"ok": False, "description": "Bad Request: invalid url"},
        }
    )

    codigo = main(_args_registro(), http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 1
    # No se llama a getWebhookInfo si setWebhook ya fallo.
    assert [l["metodo"] for l in espia.llamadas] == ["setWebhook"]

    resumen = json.loads(salida.out)
    assert resumen["resultado_api"] == {"ok": False, "description": "Bad Request: invalid url"}
    assert resumen["verificacion"] is None


# ---------------------------------------------------------------------------
# setWebhook ok: true pero getWebhookInfo no coincide -> exit 1
# ---------------------------------------------------------------------------


def test_verificacion_no_coincide_exit_1(capsys):
    espia = _HttpPostEspia(
        respuestas={
            "setWebhook": {"ok": True, "result": True},
            "getWebhookInfo": {"ok": True, "result": {"url": "https://otra-url.example/otra-ruta"}},
        }
    )

    codigo = main(_args_registro(), http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 1
    resumen = json.loads(salida.out)
    assert resumen["verificacion"] == {
        "ok": False,
        "url_esperada": _URL_ESPERADA,
        "url_obtenida": "https://otra-url.example/otra-ruta",
    }
    # El "aceptado por la API" queda visible en el resumen aunque no verifique.
    assert resumen["resultado_api"]["ok"] is True


# ---------------------------------------------------------------------------
# --delete exitoso -> exit 0
# ---------------------------------------------------------------------------


def test_delete_exitoso_exit_0(capsys):
    espia = _HttpPostEspia(
        respuestas={
            "deleteWebhook": {"ok": True, "result": True},
            "getWebhookInfo": {"ok": True, "result": {"url": ""}},
        }
    )

    codigo = main(["--delete", "--bot-token", "tok-de-mentira"], http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 0
    assert [l["metodo"] for l in espia.llamadas] == ["deleteWebhook", "getWebhookInfo"]

    resumen = json.loads(salida.out)
    assert resumen["accion"] == "delete"
    assert resumen["url_solicitada"] is None
    assert resumen["allowed_updates"] is None
    assert resumen["verificacion"] == {"ok": True, "url_esperada": "", "url_obtenida": ""}

    # deleteWebhook no manda url/secret_token/allowed_updates.
    llamada_delete = espia.llamadas[0]
    assert llamada_delete["payload"] == {}


def test_delete_no_requiere_url_ni_secret_token(capsys):
    """Modo --delete solo exige TELEGRAM_BOT_TOKEN (ver docstring del modulo)."""
    espia = _HttpPostEspia(
        respuestas={
            "deleteWebhook": {"ok": True, "result": True},
            "getWebhookInfo": {"ok": True, "result": {"url": ""}},
        }
    )

    codigo = main(["--delete", "--bot-token", "tok-de-mentira"], http_post_fn=espia)

    assert codigo == 0


# ---------------------------------------------------------------------------
# Falta de configuración -> exit 2, sin llamar a la API
# ---------------------------------------------------------------------------


def test_falta_bot_token_exit_2_sin_llamar_api(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    espia = _HttpPostEspia(respuestas={})

    codigo = main(["--url", _URL_ESPERADA, "--secret-token", "x"], http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 2
    assert espia.llamadas == []
    resumen = json.loads(salida.out)
    assert resumen["error_configuracion"] is not None
    assert resumen["resultado_api"] is None
    assert resumen["verificacion"] is None


def test_falta_url_o_secret_en_modo_registro_exit_2_sin_llamar_api(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    espia = _HttpPostEspia(respuestas={})

    codigo = main(["--bot-token", "tok-de-mentira"], http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 2
    assert espia.llamadas == []
    resumen = json.loads(salida.out)
    assert "TELEGRAM_WEBHOOK_URL" in resumen["error_configuracion"]
    assert "TELEGRAM_WEBHOOK_SECRET_TOKEN" in resumen["error_configuracion"]


def test_falta_bot_token_en_modo_delete_exit_2_sin_llamar_api(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    espia = _HttpPostEspia(respuestas={})

    codigo = main(["--delete"], http_post_fn=espia)

    assert codigo == 2
    assert espia.llamadas == []


# ---------------------------------------------------------------------------
# allowed_updates exactamente ["message"] en el payload enviado
# ---------------------------------------------------------------------------


def test_allowed_updates_exactamente_message_en_payload(capsys):
    espia = _HttpPostEspia(
        respuestas={
            "setWebhook": {"ok": True, "result": True},
            "getWebhookInfo": {"ok": True, "result": {"url": _URL_ESPERADA}},
        }
    )

    main(_args_registro(), http_post_fn=espia)

    llamada_set = espia.llamadas[0]
    assert llamada_set["metodo"] == "setWebhook"
    assert llamada_set["payload"] == {
        "url": _URL_ESPERADA,
        "secret_token": "secreto-super-largo-1234",
        "allowed_updates": ["message"],
    }


# ---------------------------------------------------------------------------
# --summary-out escribe el fichero y no ensucia stdout
# ---------------------------------------------------------------------------


def test_summary_out_escribe_fichero_y_stdout_queda_vacio(tmp_path, capsys):
    espia = _HttpPostEspia(
        respuestas={
            "setWebhook": {"ok": True, "result": True},
            "getWebhookInfo": {"ok": True, "result": {"url": _URL_ESPERADA}},
        }
    )
    destino = tmp_path / "resumen.json"

    codigo = main(_args_registro(["--summary-out", str(destino)]), http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 0
    assert salida.out == ""
    resumen = json.loads(destino.read_text(encoding="utf-8"))
    assert resumen["accion"] == "set"
    assert resumen["verificacion"]["ok"] is True
    # Los logs de progreso siguen yendo a stderr.
    assert salida.err != ""


# ---------------------------------------------------------------------------
# Fallo de red inesperado -> exit 1 (robustez de "exit codes fiables")
# ---------------------------------------------------------------------------


def test_fallo_de_red_en_setwebhook_exit_1(capsys):
    espia = _HttpPostEspia(respuestas={}, excepcion_en="setWebhook")

    codigo = main(_args_registro(), http_post_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 1
    resumen = json.loads(salida.out)
    assert "fallo de red simulado" in resumen["error_fatal"]
