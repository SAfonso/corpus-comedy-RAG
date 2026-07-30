"""Tests para `scripts/backfill_teoria_bronze.py` (backfill de Bronze legacy
para el material de teoría ya existente en local, task 66, P25).

Contrato (`src/theory/SPEC.md` §"Captura Bronze" / §"Modo solo-local: la
captura es opt-in"; `src/utils/document_store.py::DocumentStore.capturar`, ya
aprobado en la task 58): este script recorre `data/raw/books/`,
`data/raw/notes/`, `data/raw/transcriptions/` (recursivo, subcarpetas por
ponente) y `data/staging/theory/` (si existe), y captura cada fichero
elegible vía `document_store.capturar(capa="bronze", flujo="teoria",
drive_file_id=None, modified_time=None, ...)`.

Nunca se toca el `data/raw/` real del repo: todas las raíces de estos tests
son estructuras sintéticas en `tmp_path`, inyectadas vía el parámetro
`raices` de `main()`. `crear_document_store_fn` inyecta un doble
(`_DocumentStoreEspia`) — ningún test toca red real, mismo patrón que
`tests/unit/scripts/test_reprocesar_bronze_pendiente.py`.
"""
from __future__ import annotations

import json

import pytest

from scripts.backfill_teoria_bronze import (
    _listar_elegibles,
    construir_resumen,
    main,
)
from src.utils.document_store import DESTINOS, ResultadoCaptura


# ---------------------------------------------------------------------------
# Doble de `DocumentStore` — solo necesita `.capturar(...)`.
# ---------------------------------------------------------------------------


class _DocumentStoreEspia:
    """`ya_existentes`/`fallos` se indexan por NOMBRE de fichero (`path.name`)
    para no acoplar el doble a rutas absolutas de `tmp_path`. Registra cada
    llamada completa para poder verificar qué se pasó a `capturar()`
    (`extra`, `drive_file_id`, `modified_time`, ...)."""

    def __init__(self, ya_existentes: set | None = None, fallos: dict | None = None):
        self.ya_existentes = ya_existentes or set()
        self.fallos = fallos or {}
        self.llamadas: list[dict] = []

    def capturar(
        self,
        ruta,
        capa,
        flujo,
        drive_file_id=None,
        modified_time=None,
        nombre=None,
        mime_type=None,
        extra=None,
    ):
        self.llamadas.append(
            {
                "ruta": ruta,
                "capa": capa,
                "flujo": flujo,
                "drive_file_id": drive_file_id,
                "modified_time": modified_time,
                "nombre": nombre,
                "mime_type": mime_type,
                "extra": extra,
            }
        )
        if ruta.name in self.fallos:
            raise self.fallos[ruta.name]

        ya_existia = ruta.name in self.ya_existentes
        return ResultadoCaptura(
            destino=DESTINOS[("bronze", "teoria")],
            object_path=f"local_legacy/fakehash-{ruta.name}/{ruta.name}",
            hash_md5=f"fakehash-{ruta.name}",
            origen="local_legacy",
            ya_existia=ya_existia,
            fila_id=None if ya_existia else "1",
        )


def _crear_document_store_que_falla(exc: Exception):
    def _crear():
        raise exc

    return _crear


# ---------------------------------------------------------------------------
# Fixtures de estructura de directorios — nunca `data/raw/` real.
# ---------------------------------------------------------------------------


def _construir_raices(tmp_path):
    """Simula la estructura real (`src/theory/SPEC.md`): books/ (planos),
    notes/ (vacío salvo `.gitkeep`), transcriptions/ con subcarpetas por
    ponente, y staging_theory/ que por defecto NO se crea (simula que
    `data/staging/theory/` no existe en el checkout)."""
    books = tmp_path / "books"
    notes = tmp_path / "notes"
    transcriptions = tmp_path / "transcriptions"
    staging_theory = tmp_path / "staging_theory"  # deliberadamente no creada

    books.mkdir()
    notes.mkdir()
    (notes / ".gitkeep").write_text("")
    transcriptions.mkdir()
    (transcriptions / "Demy").mkdir()
    (transcriptions / "Pinol").mkdir()

    (books / "libro1.pdf").write_bytes(b"%PDF-1.4 contenido falso")
    (books / "libro2.epub").write_bytes(b"contenido epub falso")
    (books / "notas_sueltas.txt").write_text("no es un libro pero tiene extension elegible")
    (transcriptions / "Demy" / "charla1.txt").write_text("transcripcion demy 1")
    (transcriptions / "Pinol" / "charla2.txt").write_text("transcripcion pinol 2")

    return books, notes, transcriptions, staging_theory


# ---------------------------------------------------------------------------
# _listar_elegibles — filtro de extensión + recursión.
# ---------------------------------------------------------------------------


def test_listar_elegibles_recorre_subcarpetas_de_ponente(tmp_path):
    _, _, transcriptions, _ = _construir_raices(tmp_path)

    elegibles = _listar_elegibles(transcriptions)

    nombres_relativos = sorted(str(p.relative_to(transcriptions)) for p in elegibles)
    assert nombres_relativos == ["Demy/charla1.txt", "Pinol/charla2.txt"]


def test_listar_elegibles_ignora_extension_no_elegible(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / ".gitkeep").write_text("")
    (notes / "notas.md").write_text("markdown no es parseable por el pipeline de teoria")

    elegibles = _listar_elegibles(notes)

    assert elegibles == []


def test_listar_elegibles_raiz_inexistente_devuelve_vacio(tmp_path):
    raiz_inexistente = tmp_path / "no_existe"

    assert _listar_elegibles(raiz_inexistente) == []


# ---------------------------------------------------------------------------
# construir_resumen — forma pura del JSON.
# ---------------------------------------------------------------------------


def test_construir_resumen_forma_basica():
    resumen = construir_resumen(
        raices=["a", "b"],
        vistos=5,
        nuevos=3,
        existentes=2,
        fallidos=[],
    )
    assert resumen == {
        "raices": ["a", "b"],
        "vistos": 5,
        "nuevos": 3,
        "existentes": 2,
        "fallidos": [],
        "error_fatal": None,
    }


def test_construir_resumen_incluye_error_fatal_si_se_indica():
    resumen = construir_resumen(
        raices=["a"], vistos=0, nuevos=0, existentes=0, fallidos=[], error_fatal="sin credenciales"
    )
    assert resumen["error_fatal"] == "sin credenciales"


# ---------------------------------------------------------------------------
# main — barrido completo, todo nuevo: exit 0.
# ---------------------------------------------------------------------------


def test_main_captura_todo_nuevo_exit_0(capsys, tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    store = _DocumentStoreEspia()

    exit_code = main(
        [],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=lambda: store,
    )

    assert exit_code == 0
    resumen = json.loads(capsys.readouterr().out)
    # books: libro1.pdf, libro2.epub, notas_sueltas.txt (3) + transcriptions: 2 = 5
    assert resumen["vistos"] == 5
    assert resumen["nuevos"] == 5
    assert resumen["existentes"] == 0
    assert resumen["fallidos"] == []
    assert resumen["error_fatal"] is None
    assert len(store.llamadas) == 5


def test_main_pasa_modo_legacy_y_metadata_correcta(capsys, tmp_path):
    """Verifica el contrato exacto de la llamada a `capturar()`: modo legacy
    (drive_file_id/modified_time None), capa/flujo fijos, y `extra` con
    exactamente tipo_fuente/licencia/ruta_relativa (§Bronze de
    `src/theory/SPEC.md`)."""
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    store = _DocumentStoreEspia()

    main(
        [],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=lambda: store,
    )

    llamada_transcripcion = next(
        c for c in store.llamadas if c["ruta"].name == "charla1.txt"
    )
    assert llamada_transcripcion["capa"] == "bronze"
    assert llamada_transcripcion["flujo"] == "teoria"
    assert llamada_transcripcion["drive_file_id"] is None
    assert llamada_transcripcion["modified_time"] is None
    assert llamada_transcripcion["extra"] == {
        "tipo_fuente": "transcripcion_curso",
        "licencia": "personal_only",
        # relativa a `transcriptions/` (la raíz barrida), preserva Demy/.
        "ruta_relativa": "Demy/charla1.txt",
    }

    llamada_libro = next(c for c in store.llamadas if c["ruta"].name == "libro1.pdf")
    assert llamada_libro["extra"]["tipo_fuente"] == "teoria"
    assert llamada_libro["extra"]["ruta_relativa"] == "libro1.pdf"


# ---------------------------------------------------------------------------
# main — ficheros ya capturados: no cuentan como fallo, no se re-suben.
# ---------------------------------------------------------------------------


def test_main_fichero_ya_existia_no_es_fallo_ni_se_resube(capsys, tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    store = _DocumentStoreEspia(ya_existentes={"libro1.pdf", "charla1.txt"})

    exit_code = main(
        [],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=lambda: store,
    )

    assert exit_code == 0
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["vistos"] == 5
    assert resumen["existentes"] == 2
    assert resumen["nuevos"] == 3
    assert resumen["fallidos"] == []
    # El doble sigue viendo la llamada (capturar() decide ya_existia
    # internamente), pero no es un fallo ni se contabiliza como nuevo.
    assert len(store.llamadas) == 5


# ---------------------------------------------------------------------------
# main — un fallo no aborta el resto del batch.
# ---------------------------------------------------------------------------


def test_main_un_fallo_no_aborta_el_resto_del_batch(capsys, tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    store = _DocumentStoreEspia(fallos={"libro2.epub": RuntimeError("bucket caido")})

    exit_code = main(
        [],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=lambda: store,
    )

    assert exit_code == 1
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["vistos"] == 5
    assert resumen["nuevos"] == 4
    assert resumen["existentes"] == 0
    assert resumen["fallidos"] == [
        {"path": str(books / "libro2.epub"), "error": "bucket caido"}
    ]
    # Los 5 ficheros se intentaron: el fallo de libro2.epub no abortó el resto.
    assert len(store.llamadas) == 5


# ---------------------------------------------------------------------------
# main — extensión no elegible se ignora sin capturar.
# ---------------------------------------------------------------------------


def test_main_extension_no_elegible_se_ignora(capsys, tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    # notes/ solo tiene .gitkeep (no elegible) — no debe generar llamada alguna.
    store = _DocumentStoreEspia()

    main(
        [],
        raices=(notes,),
        crear_document_store_fn=lambda: store,
    )

    assert store.llamadas == []


# ---------------------------------------------------------------------------
# main — data/staging/theory/ (o cualquier raíz) ausente no rompe nada.
# ---------------------------------------------------------------------------


def test_main_raiz_ausente_no_rompe_nada(capsys, tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    assert not staging_theory.exists()
    store = _DocumentStoreEspia()

    exit_code = main(
        [],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=lambda: store,
    )

    assert exit_code == 0
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["vistos"] == 5  # nada añadido por la raíz ausente
    assert resumen["fallidos"] == []


# ---------------------------------------------------------------------------
# main — fallo fatal construyendo DocumentStore (p.ej. sin credenciales).
# ---------------------------------------------------------------------------


def test_main_fallo_fatal_creando_document_store_exit_2(capsys, tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)

    exit_code = main(
        [],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=_crear_document_store_que_falla(
            RuntimeError("SUPABASE_URL no configurada")
        ),
    )

    assert exit_code == 2
    resumen = json.loads(capsys.readouterr().out)
    assert resumen["vistos"] == 0
    assert resumen["nuevos"] == 0
    assert resumen["existentes"] == 0
    assert resumen["fallidos"] == []
    assert resumen["error_fatal"] == "SUPABASE_URL no configurada"


# ---------------------------------------------------------------------------
# main — no toca ni renombra nada en las raíces barridas (lectura pura).
# ---------------------------------------------------------------------------


def test_main_no_modifica_los_ficheros_barridos(tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    contenido_antes = (books / "libro1.pdf").read_bytes()
    mtime_antes = (books / "libro1.pdf").stat().st_mtime
    store = _DocumentStoreEspia()

    main(
        [],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=lambda: store,
    )

    assert (books / "libro1.pdf").read_bytes() == contenido_antes
    assert (books / "libro1.pdf").stat().st_mtime == mtime_antes
    assert (books / "libro1.pdf").exists()


# ---------------------------------------------------------------------------
# main — resumen JSON via --summary-out (fichero en vez de stdout).
# ---------------------------------------------------------------------------


def test_main_summary_out_escribe_fichero_y_stdout_queda_vacio(capsys, tmp_path):
    books, notes, transcriptions, staging_theory = _construir_raices(tmp_path)
    store = _DocumentStoreEspia()
    destino = tmp_path / "resumen" / "salida.json"

    exit_code = main(
        ["--summary-out", str(destino)],
        raices=(books, notes, transcriptions, staging_theory),
        crear_document_store_fn=lambda: store,
    )

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    resumen = json.loads(destino.read_text(encoding="utf-8"))
    assert resumen["vistos"] == 5
