"""Tests unitarios de `src/jokes/telegram/telegram_bot.py` — lógica pura, sin red.

Cubre (§Bronze, §Pre-limpieza mínima, task 16): pre-limpieza no destructiva
(trim, unicode, comandos de bot, menciones), extracción de datos de un
`update` con la forma real del JSON de la Bot API de Telegram, y la
orquestación de `procesar_mensaje_telegram` contra un `store` doble (nunca
se llama a Supabase real aquí). El texto de chiste usado en los updates de
prueba viene literal de `tests/fixtures/Freskito-Informático.md` (task 17),
igual que en `test_silver.py`/`test_reconciliacion.py`.
"""
from pathlib import Path

import pytest

from src.jokes.telegram.telegram_bot import (
    ResultadoProcesamiento,
    _extraer_datos_mensaje,
    limpiar_texto_telegram,
    procesar_mensaje_telegram,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "Freskito-Informático.md"
)

CHISTE_REAL = (
    "Me llamo Sergio Afonso, y si, efectivamente no soy de aquí, lo habrán "
    "notado por mi acento y porque aspiro las S, eso es porque soy canario, vale?"
)


def test_fixture_real_contiene_el_chiste_usado_en_los_tests():
    contenido = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "Me llamo Sergio Afonso" in contenido


def _update_mensaje(
    *, update_id=100, chat_id=555, text=CHISTE_REAL, date=1690000000
) -> dict:
    """Construye un `update` real de la Bot API con un mensaje de texto."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "date": date,
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# limpiar_texto_telegram
# ---------------------------------------------------------------------------

class TestLimpiarTextoTelegram:
    def test_conserva_el_chiste_real_tal_cual(self):
        assert limpiar_texto_telegram(CHISTE_REAL) == CHISTE_REAL

    def test_quita_espacios_sobrantes_en_los_bordes(self):
        assert limpiar_texto_telegram(f"  {CHISTE_REAL}  \n") == CHISTE_REAL

    def test_quita_comando_de_bot_inicial(self):
        assert limpiar_texto_telegram(f"/chiste {CHISTE_REAL}") == CHISTE_REAL

    def test_quita_comando_de_bot_con_nombre_de_bot(self):
        assert limpiar_texto_telegram(f"/chiste@MiComedyBot {CHISTE_REAL}") == CHISTE_REAL

    def test_quita_mencion_de_usuario(self):
        texto = f"@sergio_afonso {CHISTE_REAL}"
        limpio = limpiar_texto_telegram(texto)
        assert "@sergio_afonso" not in limpio
        assert "Me llamo Sergio Afonso" in limpio

    def test_no_toca_muletillas_ni_contenido(self):
        """Regla dura §Silver/§Pre-limpieza: nunca se aplica el Cleaner agresivo aquí."""
        texto_con_muletilla = "Eh pues nada, es que, o sea, un chiste muy bueno."
        assert limpiar_texto_telegram(texto_con_muletilla) == texto_con_muletilla

    def test_texto_vacio_devuelve_vacio(self):
        assert limpiar_texto_telegram("") == ""

    def test_none_devuelve_vacio(self):
        assert limpiar_texto_telegram(None) == ""


# ---------------------------------------------------------------------------
# _extraer_datos_mensaje
# ---------------------------------------------------------------------------

class TestExtraerDatosMensaje:
    def test_extrae_los_cuatro_campos_de_un_mensaje_de_texto(self):
        datos = _extraer_datos_mensaje(_update_mensaje())
        assert datos["telegram_update_id"] == 100
        assert datos["chat_id"] == 555
        assert datos["texto_raw"] == CHISTE_REAL
        assert datos["timestamp_telegram"] == "2023-07-22T04:26:40+00:00"

    def test_texto_raw_es_literal_no_limpio(self):
        """§Bronze: lo que se extrae para persistir es el texto CRUDO, sin pre-limpieza."""
        update = _update_mensaje(text=f"/chiste {CHISTE_REAL}")
        datos = _extraer_datos_mensaje(update)
        assert datos["texto_raw"] == f"/chiste {CHISTE_REAL}"

    def test_update_sin_message_devuelve_none(self):
        assert _extraer_datos_mensaje({"update_id": 1, "callback_query": {}}) is None

    def test_mensaje_sin_texto_devuelve_none(self):
        update = {"update_id": 1, "message": {"chat": {"id": 1}, "sticker": {}}}
        assert _extraer_datos_mensaje(update) is None

    def test_mensaje_con_texto_vacio_devuelve_none(self):
        update = _update_mensaje(text="")
        assert _extraer_datos_mensaje(update) is None

    def test_mensaje_sin_chat_no_rompe(self):
        update = {"update_id": 1, "message": {"date": 1690000000, "text": "hola"}}
        datos = _extraer_datos_mensaje(update)
        assert datos["chat_id"] is None

    def test_mensaje_sin_date_no_rompe(self):
        update = {"update_id": 1, "message": {"chat": {"id": 1}, "text": "hola"}}
        datos = _extraer_datos_mensaje(update)
        assert datos["timestamp_telegram"] is None


# ---------------------------------------------------------------------------
# procesar_mensaje_telegram — orquestación con `store` doble, sin red.
# ---------------------------------------------------------------------------

class _FakeStore:
    """Doble de `SupabaseStore` que simula `ON CONFLICT DO NOTHING` en memoria."""

    def __init__(self):
        self.guardados: dict[int, dict] = {}
        self.llamadas = 0

    def guardar_mensaje_telegram_bronze(self, *, telegram_update_id, texto_raw, chat_id, timestamp_telegram):
        self.llamadas += 1
        if telegram_update_id in self.guardados:
            return None
        fila = {
            "id": f"fila-{telegram_update_id}",
            "telegram_update_id": telegram_update_id,
            "texto_raw": texto_raw,
            "chat_id": chat_id,
            "timestamp_telegram": timestamp_telegram,
        }
        self.guardados[telegram_update_id] = fila
        return fila


class TestProcesarMensajeTelegram:
    def test_mensaje_nuevo_persiste_y_no_es_duplicado(self):
        store = _FakeStore()
        resultado = procesar_mensaje_telegram(_update_mensaje(), store)

        assert isinstance(resultado, ResultadoProcesamiento)
        assert resultado.es_duplicado is False
        assert resultado.fila_bronze["texto_raw"] == CHISTE_REAL
        assert resultado.texto_limpio == CHISTE_REAL

    def test_reenvio_del_mismo_update_id_es_idempotente(self):
        store = _FakeStore()
        procesar_mensaje_telegram(_update_mensaje(update_id=42), store)
        resultado = procesar_mensaje_telegram(_update_mensaje(update_id=42), store)

        assert resultado.es_duplicado is True
        assert resultado.fila_bronze is None
        assert store.llamadas == 2
        assert len(store.guardados) == 1

    def test_texto_raw_persistido_es_literal_sin_pre_limpiar(self):
        """§Bronze es sagrado: se guarda el texto crudo, la pre-limpieza es aparte."""
        store = _FakeStore()
        update = _update_mensaje(text=f"/chiste@MiBot {CHISTE_REAL}")
        resultado = procesar_mensaje_telegram(update, store)

        assert resultado.fila_bronze["texto_raw"] == f"/chiste@MiBot {CHISTE_REAL}"
        assert resultado.texto_limpio == CHISTE_REAL

    def test_update_sin_mensaje_de_texto_devuelve_none_y_no_llama_al_store(self):
        store = _FakeStore()
        resultado = procesar_mensaje_telegram({"update_id": 1, "callback_query": {}}, store)

        assert resultado is None
        assert store.llamadas == 0
