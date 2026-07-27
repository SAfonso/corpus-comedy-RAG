"""Tests unitarios de `src/jokes/routing.py` (task 34) — sin red.

Contrato (`src/jokes/SPEC.md` §"Routing (IGUAL/CAMBIADO/NUEVO a Supabase)",
task 33): componente compartido B/C, extraído de forma mecánica de
`historico/pipeline.py::_rutear_a_supabase` (task 27). Cubre `rutear_chiste`
de forma aislada, con un doble de `store` en memoria (mismo patrón que
`tests/unit/jokes/historico/test_pipeline.py::FakeStore`), las 3 ramas
IGUAL/CAMBIADO/NUEVO, la regla de no-sobrescritura de taxonomías en CAMBIADO,
y `_estructura_revision` por separado.

Este módulo NO cubre `listar_candidatos_reconciliacion` ni `reconciliar_chiste`
(paso 5a, se queda en cada caller — fuera de alcance de `routing.py`, ver
§"Alcance — routing.py es el paso 5b, no el 5a").
"""
from __future__ import annotations

import pytest

from src.jokes.reconciliacion import ResultadoReconciliacion
from src.jokes.routing import ResultadoRuteo, _estructura_revision, rutear_chiste
from src.jokes.silver import ChisteEstructurado

TIPO_FUENTE = "propio_historico"


# ---------------------------------------------------------------------------
# Doble mínimo de `SupabaseStore` — mismo patrón que
# `test_pipeline.py::FakeStore`, sin la parte de `listar_candidatos_reconciliacion`
# (no es responsabilidad de `routing.py`).
# ---------------------------------------------------------------------------

class FakeStore:
    def __init__(self, chistes_existentes=None):
        self._chistes = dict(chistes_existentes or {})  # id -> fila
        self.creados = []
        self.revisiones = []
        self.actualizados = []
        self._next_id = 1000

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


def _estructurado(chiste_normalizado="chiste normalizado"):
    return ChisteEstructurado(
        tema="tecnología",
        estructura_detectada="misdirection",
        estado="rematado",
        sugerencias_mejora="afinar el remate",
        chiste_normalizado=chiste_normalizado,
    )


def _recon(decision, chiste_id=None, hash_normalizado="hash-x", embedding=None):
    return ResultadoReconciliacion(
        decision=decision,
        hash_normalizado=hash_normalizado,
        embedding=embedding if embedding is not None else [0.1, 0.2, 0.3],
        chiste_id=chiste_id,
        similitud=1.0 if decision != "NUEVO" else None,
    )


# ---------------------------------------------------------------------------
# ResultadoRuteo — forma del dataclass compartido (4 campos, sin inicio_localizado)
# ---------------------------------------------------------------------------

def test_resultado_ruteo_tiene_4_campos_sin_inicio_localizado():
    r = ResultadoRuteo(decision="IGUAL", chiste_id="x", tema_id=1, tecnica_id=2)
    assert r.decision == "IGUAL"
    assert r.chiste_id == "x"
    assert r.tema_id == 1
    assert r.tecnica_id == 2
    assert not hasattr(r, "inicio_localizado")


def test_resultado_ruteo_es_frozen():
    r = ResultadoRuteo(decision="IGUAL", chiste_id="x", tema_id=None, tecnica_id=None)
    with pytest.raises(Exception):
        r.decision = "NUEVO"


# ---------------------------------------------------------------------------
# Rama IGUAL — no escribe nada
# ---------------------------------------------------------------------------

class TestRamaIgual:
    def test_igual_no_escribe_y_devuelve_chiste_id_de_recon(self):
        store = FakeStore()
        res = rutear_chiste(
            store, _estructurado(), _recon("IGUAL", chiste_id="existente-1"),
            tipo_fuente=TIPO_FUENTE,
        )
        assert store.creados == []
        assert store.revisiones == []
        assert store.actualizados == []
        assert res.decision == "IGUAL"
        assert res.chiste_id == "existente-1"

    def test_igual_devuelve_chiste_id_none_tal_cual(self):
        store = FakeStore()
        res = rutear_chiste(
            store, _estructurado(), _recon("IGUAL", chiste_id=None),
            tipo_fuente=TIPO_FUENTE,
        )
        assert res.chiste_id is None
        assert store.creados == store.revisiones == store.actualizados == []


# ---------------------------------------------------------------------------
# Rama NUEVO — crear_chiste + crear_revision v1
# ---------------------------------------------------------------------------

class TestRamaNuevo:
    def test_nuevo_inserta_chiste_y_crea_revision_v1(self):
        store = FakeStore()
        res = rutear_chiste(
            store, _estructurado("chiste uno"), _recon("NUEVO", hash_normalizado="hash-uno"),
            tipo_fuente=TIPO_FUENTE, tema_id=7, tecnica_id=42,
        )
        assert len(store.creados) == 1
        creado = store.creados[0]
        assert creado["texto_normalizado"] == "chiste uno"
        assert creado["hash_normalizado"] == "hash-uno"
        assert creado["tipo_fuente"] == TIPO_FUENTE
        assert creado["tema_id"] == 7
        assert creado["tecnica_id"] == 42
        assert creado["estado"] == "rematado"
        assert creado["version_actual"] == 1

        assert len(store.revisiones) == 1
        rev = store.revisiones[0]
        assert rev["chiste_id"] == creado["id"]
        assert rev["version"] == 1
        assert rev["contenido"] == "chiste uno"
        assert rev["estructura_detectada"] == {"descripcion": "misdirection", "tecnica_id": 42}
        assert rev["estado"] == "rematado"
        assert rev["sugerencias_mejora"] == "afinar el remate"

        assert res.decision == "NUEVO"
        assert res.chiste_id == creado["id"]
        assert res.tema_id == 7
        assert res.tecnica_id == 42

    def test_nuevo_tipo_fuente_parametrizado_no_hardcodeado(self):
        """El mismo `rutear_chiste` sirve para Flujo B (`propio`) — el
        `tipo_fuente` viene del caller, no de una constante de módulo."""
        store = FakeStore()
        rutear_chiste(
            store, _estructurado(), _recon("NUEVO"),
            tipo_fuente="propio",
        )
        assert store.creados[0]["tipo_fuente"] == "propio"

    def test_nuevo_sin_match_taxonomia_tema_tecnica_none(self):
        store = FakeStore()
        rutear_chiste(
            store, _estructurado(), _recon("NUEVO"),
            tipo_fuente=TIPO_FUENTE, tema_id=None, tecnica_id=None,
        )
        creado = store.creados[0]
        assert creado["tema_id"] is None
        assert creado["tecnica_id"] is None


# ---------------------------------------------------------------------------
# Rama CAMBIADO — crear_revision (N+1) + actualizar_chiste
# ---------------------------------------------------------------------------

class TestRamaCambiado:
    def test_cambiado_crea_revision_siguiente_version_y_actualiza(self):
        store = FakeStore(chistes_existentes={"existente-1": {"id": "existente-1", "version_actual": 2}})
        res = rutear_chiste(
            store, _estructurado("chiste retocado"),
            _recon("CAMBIADO", chiste_id="existente-1", hash_normalizado="hash-retocado"),
            tipo_fuente=TIPO_FUENTE, tema_id=7, tecnica_id=42,
        )
        assert store.creados == []
        assert len(store.revisiones) == 1
        assert store.revisiones[0]["version"] == 3
        assert store.revisiones[0]["chiste_id"] == "existente-1"

        assert len(store.actualizados) == 1
        upd = store.actualizados[0]
        assert upd["id"] == "existente-1"
        assert upd["version_actual"] == 3
        assert upd["texto_normalizado"] == "chiste retocado"
        assert upd["hash_normalizado"] == "hash-retocado"
        assert upd["tema_id"] == 7
        assert upd["tecnica_id"] == 42

        assert res.decision == "CAMBIADO"
        assert res.chiste_id == "existente-1"

    def test_cambiado_version_actual_ausente_asume_1(self):
        store = FakeStore(chistes_existentes={"e1": {"id": "e1"}})  # sin version_actual
        rutear_chiste(
            store, _estructurado(), _recon("CAMBIADO", chiste_id="e1"),
            tipo_fuente=TIPO_FUENTE,
        )
        assert store.revisiones[0]["version"] == 2
        assert store.actualizados[0]["version_actual"] == 2

    def test_cambiado_sin_match_taxonomia_no_sobrescribe_ids_con_none(self):
        store = FakeStore(chistes_existentes={"e1": {"id": "e1", "version_actual": 1}})
        rutear_chiste(
            store, _estructurado("retoque"), _recon("CAMBIADO", chiste_id="e1"),
            tipo_fuente=TIPO_FUENTE, tema_id=None, tecnica_id=None,
        )
        upd = store.actualizados[0]
        assert "tema_id" not in upd
        assert "tecnica_id" not in upd

    def test_cambiado_con_match_parcial_solo_escribe_el_que_matchea(self):
        store = FakeStore(chistes_existentes={"e1": {"id": "e1", "version_actual": 1}})
        rutear_chiste(
            store, _estructurado(), _recon("CAMBIADO", chiste_id="e1"),
            tipo_fuente=TIPO_FUENTE, tema_id=9, tecnica_id=None,
        )
        upd = store.actualizados[0]
        assert upd["tema_id"] == 9
        assert "tecnica_id" not in upd


# ---------------------------------------------------------------------------
# tema_id/tecnica_id keyword-only (evita swap posicional silencioso)
# ---------------------------------------------------------------------------

def test_tema_id_tecnica_id_son_keyword_only():
    with pytest.raises(TypeError):
        rutear_chiste(  # noqa: intencional — posicional debe fallar
            FakeStore(), _estructurado(), _recon("IGUAL", chiste_id="x"),
            TIPO_FUENTE, 7, 42,
        )


# ---------------------------------------------------------------------------
# _estructura_revision — helper privado movido tal cual desde
# `historico/pipeline.py`.
# ---------------------------------------------------------------------------

def test_estructura_revision_empaqueta_descripcion_y_tecnica_id():
    assert _estructura_revision(_estructurado(), 42) == {
        "descripcion": "misdirection", "tecnica_id": 42,
    }


def test_estructura_revision_tecnica_id_none():
    assert _estructura_revision(_estructurado(), None) == {
        "descripcion": "misdirection", "tecnica_id": None,
    }
