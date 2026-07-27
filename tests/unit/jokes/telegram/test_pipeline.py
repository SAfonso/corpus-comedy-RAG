"""Tests unitarios de `src/jokes/telegram/pipeline.py` (task 35) — sin red.

Cubre las DOS entradas públicas del orquestador (§Orquestación end-to-end de
`telegram/SPEC.md`, ver docstring del módulo):

- `procesar_update_sincrono` (pasos 3-5, tramo síncrono): extracción del
  `Update` -> allowlist de `chat_id` -> Bronze + pre-limpieza. Dobles: `store`
  (fake mínimo con `guardar_mensaje_telegram_bronze`); `telegram_bot` real
  (task 16, congelado) para la extracción/pre-limpieza.
- `procesar_evento_background` (pasos 7-11, tramo background): Silver ->
  taxonomías -> reconciliación -> routing -> marcado de `procesado_at`.
  Dobles deterministas para Silver/taxonomías/reconciliación/routing y un
  `store` fake que registra escrituras, mismo patrón que
  `tests/unit/jokes/historico/test_pipeline.py`.

Se verifica en particular (checklist de la task):
- la allowlist rechaza ANTES de tocar Bronze (`guardar_mensaje_telegram_bronze`
  nunca se llama si el `chat_id` no está autorizado);
- un duplicado no agenda/ejecuta el tramo background (a nivel de esta capa,
  el caller de `procesar_update_sincrono` decide si llama al tramo background
  según el `estado` devuelto — aquí se verifica que el estado es `duplicado`
  y que `fila_bronze_id` no apunta a nada nuevo que agendar);
- un `update` sin texto -> `ignorado_no_texto` sin tocar nada;
- la cadena feliz completa: `aceptado` -> Silver -> taxonomías ->
  reconciliación -> routing con `tipo_fuente='propio'` -> `procesado_at`
  marcado;
- los candidatos de reconciliación se piden con `"propio_historico"`, NO
  `"propio"` (§"Obtención de candidatos" de `src/jokes/SPEC.md`);
- un fallo del tramo background no deja el sistema a medias: si cualquier
  paso 7-10 lanza, `procesado_at` NO se marca (el store fake lo demuestra).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.jokes.reconciliacion import ResultadoReconciliacion
from src.jokes.routing import ResultadoRuteo
from src.jokes.silver import ChisteEstructurado
from src.jokes.taxonomias import ResultadoTaxonomia
from src.jokes.telegram.pipeline import (
    TIPO_FUENTE,
    TIPO_FUENTE_CANDIDATOS,
    ResultadoBackground,
    ResultadoSincrono,
    procesar_evento_background,
    procesar_update_sincrono,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "Freskito-Informático.md"
)

CHISTE_REAL = (
    "Me llamo Sergio Afonso, y si, efectivamente no soy de aquí, lo habrán "
    "notado por mi acento y porque aspiro las S, eso es porque soy canario, vale?"
)


def _update_mensaje(
    *, update_id=100, chat_id=555, text=CHISTE_REAL, date=1690000000
) -> dict:
    """Update real de la Bot API con un mensaje de texto (misma forma que
    `test_telegram_bot.py`, reutilizado tal cual)."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "date": date,
            "text": text,
        },
    }


def _update_sin_texto(*, update_id=101) -> dict:
    """Update que no es un mensaje de texto (ej. callback_query) — mismo
    caso que `_extraer_datos_mensaje` ya ignora en `telegram_bot.py`."""
    return {"update_id": update_id, "callback_query": {"id": "abc"}}


# ---------------------------------------------------------------------------
# Doble de SupabaseStore — registra Bronze y las escrituras del tramo
# background, mismo patrón que `FakeStore` en
# `tests/unit/jokes/historico/test_pipeline.py`.
# ---------------------------------------------------------------------------

class FakeStore:
    def __init__(self, candidatos=None):
        self._candidatos = list(candidatos or [])
        self.bronze = {}  # telegram_update_id -> fila
        self.bronze_calls = []
        self.procesado_calls = []
        self._next_bronze_id = 1
        self.creados = []
        self.revisiones = []
        self.actualizados = []
        self._chistes = {}
        self._next_id = 1000

    # -- Bronze (paso 5) --------------------------------------------------

    def guardar_mensaje_telegram_bronze(self, **kwargs):
        self.bronze_calls.append(kwargs)
        update_id = kwargs["telegram_update_id"]
        if update_id in self.bronze:
            return None  # duplicado, idéntico a supabase_store real
        fila = {
            "id": self._next_bronze_id,
            "telegram_update_id": update_id,
            "texto_raw": kwargs["texto_raw"],
            "procesado_at": None,
        }
        self._next_bronze_id += 1
        self.bronze[update_id] = fila
        return fila

    # -- marcado de procesado_at (paso 11) --------------------------------

    def marcar_telegram_bronze_procesado(self, fila_id):
        self.procesado_calls.append(fila_id)
        for fila in self.bronze.values():
            if fila["id"] == fila_id:
                fila["procesado_at"] = "2026-07-27T00:00:00+00:00"
                return fila
        raise AssertionError(f"fila_bronze_id desconocido: {fila_id}")

    # -- reconciliación (paso 9) -------------------------------------------

    def listar_candidatos_reconciliacion(self, tipo_fuente):
        assert tipo_fuente == "propio_historico", (
            "un 'propio' entrante debe comparar contra 'propio_historico' "
            "(src/jokes/SPEC.md §Obtención de candidatos), nunca 'propio'"
        )
        return list(self._candidatos)

    # -- routing (paso 10, interfaz de SupabaseStore real) -----------------

    def crear_chiste(self, **kwargs):
        nuevo_id = f"chiste-{self._next_id}"
        self._next_id += 1
        fila = {"id": nuevo_id, **kwargs}
        self.creados.append(fila)
        self._chistes[nuevo_id] = fila
        return fila

    def crear_revision(self, **kwargs):
        self.revisiones.append(kwargs)
        return {"id": len(self.revisiones), **kwargs}

    def obtener_chiste(self, chiste_id):
        return self._chistes.get(chiste_id)

    def actualizar_chiste(self, chiste_id, **campos):
        self.actualizados.append({"id": chiste_id, **campos})
        self._chistes.setdefault(chiste_id, {"id": chiste_id}).update(campos)
        return self._chistes[chiste_id]


# ---------------------------------------------------------------------------
# Dobles de las etapas del tramo background — mismo estilo que
# `historico/test_pipeline.py`.
# ---------------------------------------------------------------------------

def _estructurado(chiste_normalizado=CHISTE_REAL):
    return ChisteEstructurado(
        tema="identidad",
        estructura_detectada="misdirection",
        estado="rematado",
        sugerencias_mejora="ninguna",
        chiste_normalizado=chiste_normalizado,
    )


def _estructurar_fn(texto, *, llamar_llm=None):
    return _estructurado(texto)


def _resolver_taxonomia_match(texto, tipo, store, *, llamar_llm=None):
    fila = {"id": 7 if tipo == "tema" else 42, "nombre": texto}
    return ResultadoTaxonomia(match=True, fila=fila, candidato=None, intentos=1)


def _resolver_taxonomia_sin_match(texto, tipo, store, *, llamar_llm=None):
    return ResultadoTaxonomia(
        match=False, fila=None, candidato={"id": 1, "texto": texto}, intentos=3
    )


def _reconciliar_fn_factory(decision, chiste_id=None):
    def reconciliar_fn(texto, candidatos, *, generar_embedding_fn=None):
        return ResultadoReconciliacion(
            decision=decision,
            hash_normalizado=f"hash-{texto}",
            embedding=[0.1, 0.2, 0.3],
            chiste_id=chiste_id,
            similitud=1.0 if decision != "NUEVO" else None,
        )

    return reconciliar_fn


# ---------------------------------------------------------------------------
# procesar_update_sincrono — pasos 3-5
# ---------------------------------------------------------------------------

class TestProcesarUpdateSincrono:
    def test_update_sin_texto_ignorado_sin_tocar_nada(self):
        store = FakeStore()
        resultado = procesar_update_sincrono(
            _update_sin_texto(), store, allowlist=frozenset({555})
        )
        assert resultado.estado == "ignorado_no_texto"
        assert store.bronze_calls == []

    def test_chat_id_no_autorizado_no_toca_bronze(self):
        store = FakeStore()
        resultado = procesar_update_sincrono(
            _update_mensaje(chat_id=999), store, allowlist=frozenset({555})
        )
        assert resultado.estado == "ignorado_no_autorizado"
        # Regla dura de la spec: "nada tocó Supabase".
        assert store.bronze_calls == []

    def test_allowlist_se_evalua_antes_que_bronze_incluso_con_chat_id_valido_en_otra_prueba(self):
        """Confirma explícitamente el orden: extracción -> allowlist -> Bronze."""
        store = FakeStore()
        procesar_update_sincrono(
            _update_mensaje(chat_id=1), store, allowlist=frozenset({2, 3})
        )
        assert store.bronze_calls == [], "chat_id=1 no está en la allowlist {2,3}"

    def test_chat_id_autorizado_acepta_y_persiste_en_bronze(self):
        store = FakeStore()
        resultado = procesar_update_sincrono(
            _update_mensaje(chat_id=555), store, allowlist=frozenset({555})
        )
        assert resultado.estado == "aceptado"
        assert resultado.texto_limpio == CHISTE_REAL
        assert resultado.fila_bronze_id == 1
        assert len(store.bronze_calls) == 1

    def test_duplicado_no_agenda_tramo_background(self):
        store = FakeStore()
        allowlist = frozenset({555})
        primero = procesar_update_sincrono(_update_mensaje(update_id=200), store, allowlist)
        segundo = procesar_update_sincrono(_update_mensaje(update_id=200), store, allowlist)

        assert primero.estado == "aceptado"
        assert segundo.estado == "duplicado"
        # Duplicado: no hay fila nueva que agendar para el tramo background.
        assert segundo.fila_bronze_id is None
        # Bronze se llamó dos veces (idempotencia vive en el store/SQL, no
        # aquí), pero solo UNA fila real quedó creada.
        assert len(store.bronze) == 1

    def test_texto_limpio_viaja_incluso_si_texto_trae_comando_bot(self):
        store = FakeStore()
        resultado = procesar_update_sincrono(
            _update_mensaje(text=f"/chiste {CHISTE_REAL}", chat_id=555),
            store,
            allowlist=frozenset({555}),
        )
        assert resultado.estado == "aceptado"
        assert resultado.texto_limpio == CHISTE_REAL


# ---------------------------------------------------------------------------
# procesar_evento_background — pasos 7-11
# ---------------------------------------------------------------------------

class TestProcesarEventoBackground:
    def test_cadena_feliz_completa_marca_procesado_at(self):
        store = FakeStore()
        fila = store.guardar_mensaje_telegram_bronze(
            telegram_update_id=1, texto_raw=CHISTE_REAL
        )

        resultado = procesar_evento_background(
            CHISTE_REAL,
            fila["id"],
            store,
            estructurar_fn=_estructurar_fn,
            resolver_taxonomia_fn=_resolver_taxonomia_match,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )

        assert resultado.ok
        assert resultado.ruteo.decision == "NUEVO"
        assert resultado.ruteo.tema_id == 7
        assert resultado.ruteo.tecnica_id == 42
        # Routing con tipo_fuente='propio' (Flujo B, no 'propio_historico').
        assert len(store.creados) == 1
        assert store.creados[0]["tipo_fuente"] == "propio"
        # Paso 11: procesado_at marcado, mismo id de fila Bronze.
        assert store.procesado_calls == [fila["id"]]
        assert store.bronze[1]["procesado_at"] is not None

    def test_candidatos_de_reconciliacion_piden_propio_historico(self):
        """Test explícito: un 'propio' entrante compara contra
        'propio_historico', NUNCA contra 'propio' (fácil de equivocarse,
        `src/jokes/SPEC.md` §Obtención de candidatos)."""
        store = FakeStore()
        fila = store.guardar_mensaje_telegram_bronze(
            telegram_update_id=2, texto_raw=CHISTE_REAL
        )
        # El propio FakeStore.listar_candidatos_reconciliacion ya hace el
        # assert duro; si pipeline.py pidiera "propio" el test fallaría ahí.
        resultado = procesar_evento_background(
            CHISTE_REAL,
            fila["id"],
            store,
            estructurar_fn=_estructurar_fn,
            resolver_taxonomia_fn=_resolver_taxonomia_match,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        assert resultado.ok
        assert TIPO_FUENTE_CANDIDATOS == "propio_historico"
        assert TIPO_FUENTE == "propio"

    def test_igual_deduplica_y_aun_asi_marca_procesado_at(self):
        store = FakeStore()
        fila = store.guardar_mensaje_telegram_bronze(
            telegram_update_id=3, texto_raw=CHISTE_REAL
        )
        resultado = procesar_evento_background(
            CHISTE_REAL,
            fila["id"],
            store,
            estructurar_fn=_estructurar_fn,
            resolver_taxonomia_fn=_resolver_taxonomia_match,
            reconciliar_fn=_reconciliar_fn_factory("IGUAL", chiste_id="existente-1"),
        )
        assert resultado.ok
        assert resultado.ruteo.decision == "IGUAL"
        assert store.creados == []  # dedup: no escribe
        # La cadena SÍ completó (el mensaje ya se procesó, aunque dedupe) ->
        # procesado_at se marca igualmente, si no quedaría "pendiente" para
        # siempre en el script de reproceso (task 47).
        assert store.procesado_calls == [fila["id"]]

    def test_fallo_en_silver_no_marca_procesado_at(self):
        store = FakeStore()
        fila = store.guardar_mensaje_telegram_bronze(
            telegram_update_id=4, texto_raw=CHISTE_REAL
        )

        def estructurar_explota(texto, *, llamar_llm=None):
            raise RuntimeError("Silver cayó (red)")

        resultado = procesar_evento_background(
            CHISTE_REAL,
            fila["id"],
            store,
            estructurar_fn=estructurar_explota,
            resolver_taxonomia_fn=_resolver_taxonomia_match,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )

        assert not resultado.ok
        assert "Silver" in resultado.error
        # Nada se comprometió: ni chiste creado ni procesado_at marcado.
        assert store.creados == []
        assert store.procesado_calls == []
        assert store.bronze[4]["procesado_at"] is None

    def test_fallo_en_routing_no_marca_procesado_at(self):
        store = FakeStore()
        fila = store.guardar_mensaje_telegram_bronze(
            telegram_update_id=5, texto_raw=CHISTE_REAL
        )

        def rutear_explota(*args, **kwargs):
            raise RuntimeError("Supabase caído a mitad del routing")

        resultado = procesar_evento_background(
            CHISTE_REAL,
            fila["id"],
            store,
            estructurar_fn=_estructurar_fn,
            resolver_taxonomia_fn=_resolver_taxonomia_match,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
            rutear_fn=rutear_explota,
        )

        assert not resultado.ok
        assert store.procesado_calls == []
        assert store.bronze[5]["procesado_at"] is None

    def test_sin_match_de_taxonomia_no_impide_completar_ni_marcar(self):
        store = FakeStore()
        fila = store.guardar_mensaje_telegram_bronze(
            telegram_update_id=6, texto_raw=CHISTE_REAL
        )
        resultado = procesar_evento_background(
            CHISTE_REAL,
            fila["id"],
            store,
            estructurar_fn=_estructurar_fn,
            resolver_taxonomia_fn=_resolver_taxonomia_sin_match,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        assert resultado.ok
        creado = store.creados[0]
        assert "tema_id" not in creado or creado["tema_id"] is None
        assert "tecnica_id" not in creado or creado["tecnica_id"] is None
        assert store.procesado_calls == [fila["id"]]


# ---------------------------------------------------------------------------
# Dataclasses de resultado — forma mínima exigida por la task.
# ---------------------------------------------------------------------------

class TestFormaDeLosResultados:
    def test_resultado_sincrono_tiene_los_campos_minimos(self):
        r = ResultadoSincrono(estado="aceptado", texto_limpio="x", fila_bronze_id=1)
        assert r.estado == "aceptado"
        assert r.texto_limpio == "x"
        assert r.fila_bronze_id == 1

    def test_resultado_background_ok_lleva_ruteo(self):
        ruteo = ResultadoRuteo(decision="NUEVO", chiste_id="x", tema_id=None, tecnica_id=None)
        r = ResultadoBackground(ok=True, ruteo=ruteo)
        assert r.ok
        assert r.ruteo is ruteo
        assert r.error is None

    def test_resultado_background_fallo_lleva_error_sin_ruteo(self):
        r = ResultadoBackground(ok=False, error="boom")
        assert not r.ok
        assert r.ruteo is None
        assert r.error == "boom"
