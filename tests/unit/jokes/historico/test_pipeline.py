"""Tests unitarios de `src/jokes/historico/pipeline.py` (task 27) — sin red.

Cubre la orquestación del Flujo C end-to-end (§"Entrada y etapas" 0-5) con las
etapas LLM/embeddings/Drive/Supabase inyectadas como dobles: DriveSource,
Segmentador, Silver, taxonomías, reconciliación y `SupabaseStore` son fakes
deterministas; `Loader` y `marcar_remates` (puros, sin red) se usan REALES.

El texto de entrada es el fixture real `tests/fixtures/Freskito-Informático.md`
(y su `.docx` de origen para la etapa de marcado) — nunca se inventan datos
(regla del proyecto, misma que `test_segmentador.py`/`test_drive_source.py`).

Se verifica:
- routing IGUAL/CAMBIADO/NUEVO a Supabase (etapa 5);
- reanudación por documento: un `.md` completado no se reprocesa; uno que
  falla a mitad se reintenta entero (y no queda comprometido);
- cadena completa desde `.docx` real (marcar_remates real) hasta el routing;
- fallo de marcado (round-trip) reportado sin abortar el batch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.jokes.historico.pipeline import (
    ChisteRuteado,
    ResultadoHistorico,
    run_historico_pipeline,
)
from src.jokes.historico.segmentador import ChisteSegmentado
from src.jokes.reconciliacion import ResultadoReconciliacion
from src.jokes.silver import ChisteEstructurado
from src.jokes.taxonomias import ResultadoTaxonomia

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures"
FIXTURE_MD = FIXTURES / "Freskito-Informático.md"
FIXTURE_DOCX = FIXTURES / "Freskito-Informático.docx"
FIXTURE_MD_CONTENT = FIXTURE_MD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Dobles deterministas (sin red).
# ---------------------------------------------------------------------------

class FakeDriveSource:
    """`.sync() -> list[Path]`. Devuelve una lista fija de paths."""

    def __init__(self, paths):
        self._paths = list(paths)
        self.sync_calls = 0

    def sync(self):
        self.sync_calls += 1
        return list(self._paths)


class FakeStore:
    """Doble mínimo de `SupabaseStore` que registra las escrituras.

    `candidatos` alimenta `listar_candidatos_reconciliacion`; simula la tabla
    `chistes` ya existente. `crear_chiste` añade la fila creada (para que un
    reproceso pueda deduplicar por hash, como haría Supabase de verdad).
    """

    def __init__(self, candidatos=None, chistes_existentes=None):
        self._candidatos = list(candidatos or [])
        self._chistes = dict(chistes_existentes or {})  # id -> fila
        self.creados = []
        self.revisiones = []
        self.actualizados = []
        self._next_id = 1000

    def listar_candidatos_reconciliacion(self, tipo_fuente):
        assert tipo_fuente == "propio_historico"
        return list(self._candidatos)

    def crear_chiste(self, **kwargs):
        nuevo_id = f"chiste-{self._next_id}"
        self._next_id += 1
        fila = {"id": nuevo_id, **kwargs}
        self.creados.append(fila)
        self._chistes[nuevo_id] = fila
        # Un chiste recién creado es candidato de reconciliación en reprocesos.
        self._candidatos.append(
            {
                "id": nuevo_id,
                "hash_normalizado": kwargs.get("hash_normalizado"),
                "embedding": kwargs.get("embedding"),
            }
        )
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


def _chiste_segmentado(texto, remate="remate", inicio_localizado=True):
    return ChisteSegmentado(
        texto=texto,
        remate=remate,
        texto_marcado=f"{texto} [REMATE]{remate}[/REMATE]",
        contiene_chistoide=False,
        chistoides=(),
        inicio_localizado=inicio_localizado,
    )


def _estructurado(chiste_normalizado):
    return ChisteEstructurado(
        tema="tecnología",
        estructura_detectada="misdirection",
        estado="rematado",
        sugerencias_mejora="afinar el remate",
        chiste_normalizado=chiste_normalizado,
    )


def _segmentar_fn_factory(textos):
    """Devuelve un `segmentar_fn` que ignora el input y emite `textos` fijos."""

    def segmentar_fn(texto_marcado, *, llamar_llm=None):
        return [_chiste_segmentado(t) for t in textos]

    return segmentar_fn


def _estructurar_fn(texto, *, llamar_llm=None):
    return _estructurado(texto)  # chiste_normalizado == texto de entrada


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


def _reconciliar_por_hash(texto, candidatos, *, generar_embedding_fn=None):
    """Reconciliación realista por hash: IGUAL si el hash ya está en candidatos,
    NUEVO si no (simula el dedup idempotente de un reproceso)."""
    hash_norm = f"hash-{texto}"
    for c in candidatos:
        if c.get("hash_normalizado") == hash_norm:
            return ResultadoReconciliacion(
                decision="IGUAL", hash_normalizado=hash_norm,
                embedding=[0.1], chiste_id=c["id"], similitud=1.0,
            )
    return ResultadoReconciliacion(
        decision="NUEVO", hash_normalizado=hash_norm, embedding=[0.1],
    )


@pytest.fixture
def carpeta_md(tmp_path):
    d = tmp_path / "md"
    d.mkdir()
    return d


@pytest.fixture
def loader_state(tmp_path):
    return tmp_path / "state" / "loader.json"


def _correr(carpeta_md, loader_state, **kwargs):
    """Helper: run con defaults de test (drive vacío salvo override)."""
    kwargs.setdefault("drive_source", FakeDriveSource([]))
    kwargs.setdefault("carpeta_md", carpeta_md)
    kwargs.setdefault("loader_state_path", loader_state)
    kwargs.setdefault("estructurar_fn", _estructurar_fn)
    kwargs.setdefault("resolver_taxonomia_fn", _resolver_taxonomia_match)
    return run_historico_pipeline(**kwargs)


# ---------------------------------------------------------------------------
# Routing IGUAL / CAMBIADO / NUEVO (etapa 5)
# ---------------------------------------------------------------------------

class TestRouting:
    def test_nuevo_inserta_chiste_y_crea_revision_v1(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("cualquier cosa", encoding="utf-8")
        store = FakeStore()
        res = _correr(
            carpeta_md, loader_state, store=store,
            segmentar_fn=_segmentar_fn_factory(["chiste uno"]),
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )

        assert len(store.creados) == 1
        creado = store.creados[0]
        assert creado["tipo_fuente"] == "propio_historico"
        assert creado["hash_normalizado"] == "hash-chiste uno"
        assert creado["tema_id"] == 7 and creado["tecnica_id"] == 42
        assert creado["version_actual"] == 1
        # Revisión inicial v1 (append-only, §Versionado).
        assert len(store.revisiones) == 1
        assert store.revisiones[0]["version"] == 1
        assert store.revisiones[0]["chiste_id"] == creado["id"]
        assert store.revisiones[0]["estructura_detectada"] == {
            "descripcion": "misdirection", "tecnica_id": 42,
        }
        # Resultado agregado.
        assert res.ok
        ruteados = res.chistes_ruteados
        assert [c.decision for c in ruteados] == ["NUEVO"]
        assert ruteados[0].chiste_id == creado["id"]

    def test_igual_deduplica_sin_escribir(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("x", encoding="utf-8")
        store = FakeStore()
        res = _correr(
            carpeta_md, loader_state, store=store,
            segmentar_fn=_segmentar_fn_factory(["chiste repetido"]),
            reconciliar_fn=_reconciliar_fn_factory("IGUAL", chiste_id="existente-1"),
        )

        assert store.creados == []
        assert store.revisiones == []
        assert store.actualizados == []
        assert res.chistes_ruteados[0].decision == "IGUAL"
        assert res.chistes_ruteados[0].chiste_id == "existente-1"

    def test_cambiado_crea_revision_siguiente_version_y_actualiza(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("x", encoding="utf-8")
        store = FakeStore(chistes_existentes={"existente-1": {"id": "existente-1", "version_actual": 2}})
        res = _correr(
            carpeta_md, loader_state, store=store,
            segmentar_fn=_segmentar_fn_factory(["chiste retocado"]),
            reconciliar_fn=_reconciliar_fn_factory("CAMBIADO", chiste_id="existente-1"),
        )

        assert store.creados == []
        # Nueva revisión = version_actual previa (2) + 1 = 3.
        assert len(store.revisiones) == 1
        assert store.revisiones[0]["version"] == 3
        assert store.revisiones[0]["chiste_id"] == "existente-1"
        # Update apunta version_actual a la nueva y refresca contenido/hash.
        assert len(store.actualizados) == 1
        upd = store.actualizados[0]
        assert upd["id"] == "existente-1"
        assert upd["version_actual"] == 3
        assert upd["hash_normalizado"] == "hash-chiste retocado"
        assert upd["tema_id"] == 7 and upd["tecnica_id"] == 42
        assert res.chistes_ruteados[0].decision == "CAMBIADO"

    def test_sin_match_de_taxonomia_no_escribe_ids(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("x", encoding="utf-8")
        store = FakeStore()
        _correr(
            carpeta_md, loader_state, store=store,
            resolver_taxonomia_fn=_resolver_taxonomia_sin_match,
            segmentar_fn=_segmentar_fn_factory(["chiste sin taxonomia"]),
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        creado = store.creados[0]
        # tema_id/tecnica_id ausentes (None) — no se fuerzan a Supabase.
        assert "tema_id" not in creado or creado["tema_id"] is None
        assert "tecnica_id" not in creado or creado["tecnica_id"] is None

    def test_cambiado_sin_match_taxonomia_no_sobrescribe_ids_con_none(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("x", encoding="utf-8")
        store = FakeStore(chistes_existentes={"e1": {"id": "e1", "version_actual": 1}})
        _correr(
            carpeta_md, loader_state, store=store,
            resolver_taxonomia_fn=_resolver_taxonomia_sin_match,
            segmentar_fn=_segmentar_fn_factory(["retoque"]),
            reconciliar_fn=_reconciliar_fn_factory("CAMBIADO", chiste_id="e1"),
        )
        upd = store.actualizados[0]
        # Nunca se mete tema_id=None/tecnica_id=None en el UPDATE.
        assert "tema_id" not in upd
        assert "tecnica_id" not in upd


# ---------------------------------------------------------------------------
# inicio_localizado y multi-chiste
# ---------------------------------------------------------------------------

class TestVariosChistes:
    def test_un_documento_varios_chistes_una_ruta_cada_uno(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("x", encoding="utf-8")
        store = FakeStore()
        res = _correr(
            carpeta_md, loader_state, store=store,
            segmentar_fn=_segmentar_fn_factory(["a", "b", "c"]),
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        assert len(store.creados) == 3
        assert len(res.chistes_ruteados) == 3
        assert res.procesados == ["doc.md"]

    def test_inicio_localizado_se_propaga(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("x", encoding="utf-8")

        def segmentar_fn(texto_marcado, *, llamar_llm=None):
            return [_chiste_segmentado("fallback", inicio_localizado=False)]

        res = _correr(
            carpeta_md, loader_state, store=FakeStore(),
            segmentar_fn=segmentar_fn,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        assert res.chistes_ruteados[0].inicio_localizado is False


# ---------------------------------------------------------------------------
# Reanudación por documento
# ---------------------------------------------------------------------------

class TestReanudacion:
    def test_documento_completado_no_se_reprocesa(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("contenido", encoding="utf-8")
        store = FakeStore()
        kwargs = dict(
            segmentar_fn=_segmentar_fn_factory(["chiste"]),
            reconciliar_fn=_reconciliar_por_hash,
        )
        res1 = _correr(carpeta_md, loader_state, store=store, **kwargs)
        assert res1.procesados == ["doc.md"]
        assert len(store.creados) == 1

        # Segundo run: mismo .md, MD5 sin cambios -> Loader lo salta.
        res2 = _correr(carpeta_md, loader_state, store=store, **kwargs)
        assert res2.documentos == []  # nada pendiente
        assert len(store.creados) == 1  # no se reinsertó nada

    def test_documento_modificado_se_reprocesa(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("v1", encoding="utf-8")
        store = FakeStore()
        kwargs = dict(
            segmentar_fn=_segmentar_fn_factory(["chiste"]),
            reconciliar_fn=_reconciliar_por_hash,
        )
        _correr(carpeta_md, loader_state, store=store, **kwargs)

        (carpeta_md / "doc.md").write_text("v2 distinto", encoding="utf-8")
        res2 = _correr(carpeta_md, loader_state, store=store, **kwargs)
        assert [d.name for d in res2.documentos] == ["doc.md"]

    def test_fallo_a_mitad_no_compromete_y_se_reintenta(self, carpeta_md, loader_state):
        (carpeta_md / "doc.md").write_text("contenido", encoding="utf-8")
        store = FakeStore()

        def estructurar_explota(texto, *, llamar_llm=None):
            raise RuntimeError("Silver cayó (red)")

        res1 = _correr(
            carpeta_md, loader_state, store=store,
            segmentar_fn=_segmentar_fn_factory(["chiste"]),
            estructurar_fn=estructurar_explota,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        assert not res1.ok
        assert len(res1.fallidos) == 1
        assert "Silver" in res1.fallidos[0].error
        # No se comprometió: el estado del Loader NO contiene el doc.
        estado = json.loads(loader_state.read_text()) if loader_state.exists() else {}
        assert "doc.md" not in estado

        # Segundo run (ya sin fallo): el doc se reintenta entero.
        res2 = _correr(
            carpeta_md, loader_state, store=store,
            segmentar_fn=_segmentar_fn_factory(["chiste"]),
            reconciliar_fn=_reconciliar_por_hash,
        )
        assert res2.procesados == ["doc.md"]
        assert len(store.creados) == 1

    def test_fallo_segundo_chiste_reintenta_doc_y_primero_deduplica(self, carpeta_md, loader_state):
        """Ningún chiste a medias: si el 2º chiste falla, el doc se reintenta
        entero; el 1º (ya insertado) deduplica por hash (IGUAL) en el reproceso."""
        (carpeta_md / "doc.md").write_text("contenido", encoding="utf-8")
        store = FakeStore()

        llamadas = {"n": 0}

        def estructurar_falla_en_segundo(texto, *, llamar_llm=None):
            llamadas["n"] += 1
            if texto == "chiste-2" and llamadas["n"] <= 2:
                raise RuntimeError("falla puntual en el 2º chiste")
            return _estructurado(texto)

        kwargs = dict(
            segmentar_fn=_segmentar_fn_factory(["chiste-1", "chiste-2"]),
            reconciliar_fn=_reconciliar_por_hash,
        )
        res1 = _correr(
            carpeta_md, loader_state, store=store,
            estructurar_fn=estructurar_falla_en_segundo, **kwargs,
        )
        assert not res1.ok
        assert len(store.creados) == 1  # solo el 1º alcanzó a insertarse

        # Retry: doc reprocesado entero. El 1º hace hash-match -> IGUAL (dedup),
        # el 2º ya no falla -> NUEVO. Total insertados: 2, sin duplicar el 1º.
        res2 = _correr(
            carpeta_md, loader_state, store=store,
            estructurar_fn=_estructurar_fn, **kwargs,
        )
        assert res2.procesados == ["doc.md"]
        decisiones = [c.decision for c in res2.chistes_ruteados]
        assert decisiones == ["IGUAL", "NUEVO"]
        assert len(store.creados) == 2  # 1 (run1) + 1 (el 2º en run2), sin re-crear el 1º


# ---------------------------------------------------------------------------
# Cadena completa desde .docx real (marcar_remates real, sin red)
# ---------------------------------------------------------------------------

class TestCadenaCompletaDesdeDocx:
    def test_marca_docx_real_carga_y_rutea(self, carpeta_md, loader_state):
        # DriveSource devuelve el .docx real; marcar_remates (real, puro)
        # produce el .md; Loader (real) lo carga; el resto son dobles.
        store = FakeStore()
        res = run_historico_pipeline(
            drive_source=FakeDriveSource([FIXTURE_DOCX]),
            carpeta_md=carpeta_md,
            loader_state_path=loader_state,
            store=store,
            segmentar_fn=_segmentar_fn_factory(["chiste real"]),
            estructurar_fn=_estructurar_fn,
            resolver_taxonomia_fn=_resolver_taxonomia_match,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        # El .md se generó a partir del .docx real.
        md_generado = carpeta_md / "Freskito-Informático.md"
        assert md_generado.exists()
        assert "[REMATE]" in md_generado.read_text(encoding="utf-8")
        # Descarga registrada y documento ruteado.
        assert res.descargados == ["Freskito-Informático.docx"]
        assert res.procesados == ["Freskito-Informático.md"]
        assert len(store.creados) == 1

    def test_fallo_de_marcado_se_reporta_sin_abortar(self, carpeta_md, loader_state):
        store = FakeStore()

        def procesar_docx_explota(docx, carpeta_salida):
            raise ValueError("round-trip FALLIDO")

        res = run_historico_pipeline(
            drive_source=FakeDriveSource([FIXTURE_DOCX]),
            carpeta_md=carpeta_md,
            loader_state_path=loader_state,
            store=store,
            procesar_docx_fn=procesar_docx_explota,
            segmentar_fn=_segmentar_fn_factory(["x"]),
            estructurar_fn=_estructurar_fn,
            resolver_taxonomia_fn=_resolver_taxonomia_match,
            reconciliar_fn=_reconciliar_fn_factory("NUEVO"),
        )
        assert len(res.marcado_fallidos) == 1
        assert res.marcado_fallidos[0].docx == "Freskito-Informático.docx"
        assert not res.ok
        # No se produjo .md, así que no hay documentos procesados.
        assert res.documentos == []


# ---------------------------------------------------------------------------
# Casos borde
# ---------------------------------------------------------------------------

class TestBordes:
    def test_run_sin_nada_pendiente_es_ok(self, carpeta_md, loader_state):
        res = _correr(carpeta_md, loader_state, store=FakeStore())
        assert res.ok
        assert res.documentos == []
        assert res.descargados == []
        assert res.chistes_ruteados == []

    def test_documento_sin_remates_no_rutea_pero_se_completa(self, carpeta_md, loader_state):
        (carpeta_md / "vacio.md").write_text("Texto sin marcado.", encoding="utf-8")
        store = FakeStore()
        # segmentar_fn devuelve [] (documento sin [REMATE]).
        res = _correr(
            carpeta_md, loader_state, store=store,
            segmentar_fn=lambda texto, *, llamar_llm=None: [],
        )
        assert store.creados == []
        assert res.procesados == ["vacio.md"]  # se completa (0 chistes), se compromete
