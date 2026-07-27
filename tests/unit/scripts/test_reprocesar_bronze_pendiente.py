"""Tests para `scripts/reprocesar_bronze_pendiente.py` (script de recuperación
de fallos, Flujo B, task 47).

Contrato (`src/jokes/telegram/SPEC.md` §"Recuperación de fallos" — script de
reproceso; `src/jokes/telegram/pipeline.py::procesar_evento_background`, ya
aprobado en task 35): reprocesa filas de `chistes_telegram_bronze` con
`procesado_at IS NULL` a través de la MISMA función que usa el webhook — este
script no reimplementa nada de Silver/taxonomías/reconciliación/routing.

Dobles inyectados (`crear_store_fn`, `procesar_evento_background_fn`,
`limpiar_texto_fn`) — nunca red real, mismo patrón que
`tests/unit/scripts/test_run_pipeline_cli.py`/`test_set_telegram_webhook.py`.
"""
from __future__ import annotations

import json

import pytest

from scripts.reprocesar_bronze_pendiente import construir_resumen, main
from src.jokes.telegram.pipeline import ResultadoBackground


# ---------------------------------------------------------------------------
# Doble de `store` — solo necesita `listar_telegram_bronze_pendientes` (el
# resto lo consume `procesar_evento_background_fn`, que aquí siempre es un
# doble también, así que el store nunca necesita implementar más).
# ---------------------------------------------------------------------------


class _StoreEspia:
    def __init__(self, pendientes=None, excepcion_al_listar: Exception | None = None):
        self._pendientes = pendientes if pendientes is not None else []
        self._excepcion_al_listar = excepcion_al_listar

    def listar_telegram_bronze_pendientes(self):
        if self._excepcion_al_listar is not None:
            raise self._excepcion_al_listar
        return self._pendientes


def _limpiar_texto_espia(texto: str) -> str:
    """Doble determinista y distinguible de `limpiar_texto_telegram`: para
    verificar que el script pasa el texto YA limpio a
    `procesar_evento_background_fn`, nunca el `texto_raw` crudo."""
    return f"LIMPIO({texto})"


class _ProcesarEventoEspia:
    """Doble de `procesar_evento_background_fn`. `resultados_por_texto_limpio`
    mapea el `texto_limpio` recibido a un `ResultadoBackground` o a una
    excepción a lanzar. Registra cada llamada (`texto_limpio`,
    `fila_bronze_id`) para poder verificar qué se le pasó."""

    def __init__(self, resultados_por_texto_limpio: dict):
        self.resultados = resultados_por_texto_limpio
        self.llamadas: list[dict] = []

    def __call__(self, texto_limpio, fila_bronze_id, store):
        self.llamadas.append({"texto_limpio": texto_limpio, "fila_bronze_id": fila_bronze_id})
        resultado = self.resultados[texto_limpio]
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


# ---------------------------------------------------------------------------
# construir_resumen — forma pura del JSON.
# ---------------------------------------------------------------------------


def test_construir_resumen_forma_basica():
    resumen = construir_resumen(
        pendientes_encontrados=2,
        reprocesados_ok=1,
        fallidos=[{"fila_bronze_id": 5, "error": "boom"}],
    )
    assert resumen == {
        "pendientes_encontrados": 2,
        "reprocesados_ok": 1,
        "fallidos": [{"fila_bronze_id": 5, "error": "boom"}],
        "error_fatal": None,
    }


def test_construir_resumen_incluye_error_fatal_si_se_indica():
    resumen = construir_resumen(
        pendientes_encontrados=0, reprocesados_ok=0, fallidos=[], error_fatal="conexion caida"
    )
    assert resumen["error_fatal"] == "conexion caida"


# ---------------------------------------------------------------------------
# main — sin pendientes: exit 0, no es un fallo.
# ---------------------------------------------------------------------------


def test_main_sin_pendientes_exit_0(capsys):
    store = _StoreEspia(pendientes=[])
    procesar_espia = _ProcesarEventoEspia({})

    exit_code = main(
        [],
        crear_store_fn=lambda: store,
        procesar_evento_background_fn=procesar_espia,
        limpiar_texto_fn=_limpiar_texto_espia,
    )

    assert exit_code == 0
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["pendientes_encontrados"] == 0
    assert resumen["reprocesados_ok"] == 0
    assert resumen["fallidos"] == []
    assert resumen["error_fatal"] is None
    assert procesar_espia.llamadas == []


# ---------------------------------------------------------------------------
# main — varias filas pendientes, todas ok: exit 0, texto_limpio recalculado.
# ---------------------------------------------------------------------------


def test_main_varias_pendientes_todas_ok_recalcula_texto_limpio(capsys):
    pendientes = [
        {"id": 10, "texto_raw": "raw uno"},
        {"id": 11, "texto_raw": "raw dos"},
    ]
    store = _StoreEspia(pendientes=pendientes)
    procesar_espia = _ProcesarEventoEspia(
        {
            "LIMPIO(raw uno)": ResultadoBackground(ok=True),
            "LIMPIO(raw dos)": ResultadoBackground(ok=True),
        }
    )

    exit_code = main(
        [],
        crear_store_fn=lambda: store,
        procesar_evento_background_fn=procesar_espia,
        limpiar_texto_fn=_limpiar_texto_espia,
    )

    assert exit_code == 0
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["pendientes_encontrados"] == 2
    assert resumen["reprocesados_ok"] == 2
    assert resumen["fallidos"] == []

    # Verifica que se pasó el texto YA limpio (nunca el texto_raw crudo).
    assert procesar_espia.llamadas == [
        {"texto_limpio": "LIMPIO(raw uno)", "fila_bronze_id": 10},
        {"texto_limpio": "LIMPIO(raw dos)", "fila_bronze_id": 11},
    ]


# ---------------------------------------------------------------------------
# main — una fila falla (ResultadoBackground(ok=False)), las demás continúan.
# ---------------------------------------------------------------------------


def test_main_una_fila_falla_las_demas_continuan(capsys):
    pendientes = [
        {"id": 1, "texto_raw": "raw ok 1"},
        {"id": 2, "texto_raw": "raw mal"},
        {"id": 3, "texto_raw": "raw ok 2"},
    ]
    store = _StoreEspia(pendientes=pendientes)
    procesar_espia = _ProcesarEventoEspia(
        {
            "LIMPIO(raw ok 1)": ResultadoBackground(ok=True),
            "LIMPIO(raw mal)": ResultadoBackground(ok=False, error="fallo en reconciliacion"),
            "LIMPIO(raw ok 2)": ResultadoBackground(ok=True),
        }
    )

    exit_code = main(
        [],
        crear_store_fn=lambda: store,
        procesar_evento_background_fn=procesar_espia,
        limpiar_texto_fn=_limpiar_texto_espia,
    )

    assert exit_code == 1
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["pendientes_encontrados"] == 3
    assert resumen["reprocesados_ok"] == 2
    assert resumen["fallidos"] == [{"fila_bronze_id": 2, "error": "fallo en reconciliacion"}]
    # Las tres filas se intentaron — el fallo de la fila 2 no abortó el lote.
    assert len(procesar_espia.llamadas) == 3


def test_main_excepcion_inesperada_en_una_fila_no_aborta_el_lote(capsys):
    """Un fallo aislado también cubre una excepción NO capturada por
    `procesar_evento_background` (p.ej. si `marcar_telegram_bronze_procesado`
    -- paso 11, fuera del try interno de pipeline.py -- lanza por un hipo de
    red): el script debe capturarla por fila, no dejar que tumbe el proceso."""
    pendientes = [
        {"id": 1, "texto_raw": "raw ok"},
        {"id": 2, "texto_raw": "raw explota"},
    ]
    store = _StoreEspia(pendientes=pendientes)
    procesar_espia = _ProcesarEventoEspia(
        {
            "LIMPIO(raw ok)": ResultadoBackground(ok=True),
            "LIMPIO(raw explota)": RuntimeError("conexion perdida a mitad de marcado"),
        }
    )

    exit_code = main(
        [],
        crear_store_fn=lambda: store,
        procesar_evento_background_fn=procesar_espia,
        limpiar_texto_fn=_limpiar_texto_espia,
    )

    assert exit_code == 1
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["reprocesados_ok"] == 1
    assert resumen["fallidos"] == [
        {"fila_bronze_id": 2, "error": "conexion perdida a mitad de marcado"}
    ]
    assert len(procesar_espia.llamadas) == 2


# ---------------------------------------------------------------------------
# main — excepción al listar pendientes (fallo de conexión): exit 2, sin
# traceback crudo.
# ---------------------------------------------------------------------------


def test_main_excepcion_al_listar_pendientes_exit_2(capsys):
    store = _StoreEspia(excepcion_al_listar=RuntimeError("supabase inalcanzable"))
    procesar_espia = _ProcesarEventoEspia({})

    exit_code = main(
        [],
        crear_store_fn=lambda: store,
        procesar_evento_background_fn=procesar_espia,
        limpiar_texto_fn=_limpiar_texto_espia,
    )

    assert exit_code == 2
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["pendientes_encontrados"] == 0
    assert resumen["reprocesados_ok"] == 0
    assert resumen["fallidos"] == []
    assert resumen["error_fatal"] == "supabase inalcanzable"
    # No se intentó procesar ninguna fila.
    assert procesar_espia.llamadas == []


def test_main_excepcion_al_crear_store_exit_2(capsys):
    """La construcción del store (p.ej. credenciales ausentes) es igual de
    fatal que un fallo al listar — mismo exit code 2."""

    def _crear_store_que_falla():
        raise RuntimeError("SUPABASE_URL no configurada")

    exit_code = main(
        [],
        crear_store_fn=_crear_store_que_falla,
        procesar_evento_background_fn=_ProcesarEventoEspia({}),
        limpiar_texto_fn=_limpiar_texto_espia,
    )

    assert exit_code == 2
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["error_fatal"] == "SUPABASE_URL no configurada"


# ---------------------------------------------------------------------------
# --summary-out escribe el fichero (y no imprime por stdout).
# ---------------------------------------------------------------------------


def test_main_summary_out_escribe_fichero(tmp_path, capsys):
    pendientes = [{"id": 1, "texto_raw": "raw uno"}]
    store = _StoreEspia(pendientes=pendientes)
    procesar_espia = _ProcesarEventoEspia({"LIMPIO(raw uno)": ResultadoBackground(ok=True)})
    destino = tmp_path / "resumen.json"

    exit_code = main(
        ["--summary-out", str(destino)],
        crear_store_fn=lambda: store,
        procesar_evento_background_fn=procesar_espia,
        limpiar_texto_fn=_limpiar_texto_espia,
    )

    assert exit_code == 0
    assert destino.exists()
    resumen = json.loads(destino.read_text(encoding="utf-8"))
    assert resumen["pendientes_encontrados"] == 1
    assert resumen["reprocesados_ok"] == 1

    captura = capsys.readouterr()
    assert captura.out == ""  # el JSON no se imprime por stdout si hay --summary-out
