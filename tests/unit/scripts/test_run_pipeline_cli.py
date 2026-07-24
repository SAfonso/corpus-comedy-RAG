"""Tests para `scripts/run_pipeline.py` (CLI de Flujo A, task 23).

Contrato (`scripts/run_pipeline.py` docstring, task 23): este script es
wiring puro sobre `src/theory/pipeline.run_pipeline` (task 22) y
`src/theory/ingest_teoria.ingestar_version` (task 21), ya aprobados y
testeados en sus propios módulos — aquí NO se re-testea su lógica interna
(cadena de parsers, idempotencia, embeddings...), solo el CLI: parseo de
argv, construcción del resumen JSON, y el mapeo resultado -> exit code.

Todas las dependencias externas (`run_pipeline_fn`, `ingestar_version_fn`,
`crear_store_fn`) se inyectan como dobles de prueba — nunca se llama a
Supabase ni a red real (eso vive en `tests/integration/`, fuera de scope de
esta task).
"""
import json
from pathlib import Path

import pytest

from scripts.run_pipeline import construir_resumen, main
from src.theory.pipeline import ResultadoFichero, ResultadoPipeline


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------


class _ResultadoIngestaFake:
    def __init__(self, version_corpus="v3", num_nuevos=2, num_duplicados=1):
        self.version_corpus = version_corpus
        self.num_nuevos = num_nuevos
        self.num_duplicados = num_duplicados


class _RunPipelineEspia:
    """Doble de `run_pipeline` que registra con qué kwargs se le llamó y
    devuelve un `ResultadoPipeline` fijo (o lanza, si `excepcion` se pasa)."""

    def __init__(self, resultado=None, excepcion=None):
        self.resultado = resultado if resultado is not None else ResultadoPipeline()
        self.excepcion = excepcion
        self.llamadas = []

    def __call__(self, carpetas, *, directorio_procesado=None, ruta_estado=None, version=None):
        self.llamadas.append(
            {
                "carpetas": carpetas,
                "directorio_procesado": directorio_procesado,
                "ruta_estado": ruta_estado,
                "version": version,
            }
        )
        if self.excepcion is not None:
            raise self.excepcion
        return self.resultado


class _IngestarVersionEspia:
    def __init__(self, resultado=None, excepcion=None):
        self.resultado = resultado if resultado is not None else _ResultadoIngestaFake()
        self.excepcion = excepcion
        self.llamadas = []

    def __call__(self, directorio_base, store, *, version=None, generar_embedding_fn=None):
        self.llamadas.append({"directorio_base": directorio_base, "store": store, "version": version})
        if self.excepcion is not None:
            raise self.excepcion
        return self.resultado


class _StoreFake:
    pass


def _crear_store_espia(registro):
    def _crear():
        registro.append(True)
        return _StoreFake()

    return _crear


# ---------------------------------------------------------------------------
# construir_resumen — forma pura del JSON
# ---------------------------------------------------------------------------


def test_construir_resumen_caso_feliz_sin_ingest():
    resultado = ResultadoPipeline(
        version_dir=Path("data/processed/v3"),
        procesados=[Path("a.txt"), Path("b.pdf")],
        fallidos=[],
        ignorados=[Path(".gitkeep")],
    )
    resumen = construir_resumen(resultado, None)

    assert resumen == {
        "procesados": ["a.txt", "b.pdf"],
        "fallidos": [],
        "ignorados": [".gitkeep"],
        "version_dir": "data/processed/v3",
        "ingesta": None,
    }


def test_construir_resumen_con_fallidos():
    resultado = ResultadoPipeline(
        version_dir=None,
        procesados=[],
        fallidos=[ResultadoFichero(path=Path("roto.pdf"), error="boom")],
        ignorados=[],
    )
    resumen = construir_resumen(resultado, None)

    assert resumen["fallidos"] == [{"path": "roto.pdf", "error": "boom"}]
    assert resumen["version_dir"] is None


def test_construir_resumen_incluye_bloque_de_ingesta_si_se_pasa():
    resultado = ResultadoPipeline(version_dir=Path("data/processed/v1"))
    ingesta = {"intentada": True, "ejecutada": True, "version_corpus": "v1"}

    resumen = construir_resumen(resultado, ingesta)

    assert resumen["ingesta"] == ingesta


# ---------------------------------------------------------------------------
# main() — parseo de args y wiring hacia run_pipeline_fn
# ---------------------------------------------------------------------------


def test_main_pasa_las_flags_a_run_pipeline_fn(tmp_path, capsys):
    espia = _RunPipelineEspia(resultado=ResultadoPipeline())

    codigo = main(
        [
            "--carpeta",
            str(tmp_path / "a"),
            "--carpeta",
            str(tmp_path / "b"),
            "--directorio-procesado",
            str(tmp_path / "processed"),
            "--ruta-estado",
            str(tmp_path / "state.json"),
            "--version",
            "7",
        ],
        run_pipeline_fn=espia,
    )

    assert codigo == 0
    assert len(espia.llamadas) == 1
    llamada = espia.llamadas[0]
    assert llamada["carpetas"] == [tmp_path / "a", tmp_path / "b"]
    assert llamada["directorio_procesado"] == tmp_path / "processed"
    assert llamada["ruta_estado"] == tmp_path / "state.json"
    assert llamada["version"] == 7


def test_main_sin_flags_pasa_none_para_usar_los_defaults_de_run_pipeline(capsys):
    espia = _RunPipelineEspia(resultado=ResultadoPipeline())

    main([], run_pipeline_fn=espia)

    llamada = espia.llamadas[0]
    assert llamada["carpetas"] is None
    assert llamada["directorio_procesado"] is None
    assert llamada["ruta_estado"] is None
    assert llamada["version"] is None


# ---------------------------------------------------------------------------
# main() — canal del resumen: stdout JSON por defecto, o --summary-out
# ---------------------------------------------------------------------------


def test_main_imprime_json_por_stdout_por_defecto(capsys):
    resultado = ResultadoPipeline(version_dir=Path("data/processed/v1"), procesados=[Path("a.txt")])
    espia = _RunPipelineEspia(resultado=resultado)

    codigo = main([], run_pipeline_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 0
    resumen = json.loads(salida.out)
    assert resumen["procesados"] == ["a.txt"]
    assert resumen["version_dir"] == "data/processed/v1"
    # Los logs de progreso van a stderr, nunca mezclados con el JSON de stdout.
    assert "procesados" in salida.err or "run_pipeline" in salida.err


def test_main_con_summary_out_escribe_fichero_y_stdout_queda_vacio(tmp_path, capsys):
    resultado = ResultadoPipeline(version_dir=Path("data/processed/v1"), procesados=[Path("a.txt")])
    espia = _RunPipelineEspia(resultado=resultado)
    destino = tmp_path / "resumen.json"

    codigo = main(["--summary-out", str(destino)], run_pipeline_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 0
    assert salida.out == ""
    resumen = json.loads(destino.read_text(encoding="utf-8"))
    assert resumen["procesados"] == ["a.txt"]


# ---------------------------------------------------------------------------
# main() — exit codes
# ---------------------------------------------------------------------------


def test_exit_code_0_caso_feliz_sin_nada_pendiente(capsys):
    espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    assert main([], run_pipeline_fn=espia) == 0


def test_exit_code_1_si_hay_fallidos(capsys):
    resultado = ResultadoPipeline(fallidos=[ResultadoFichero(path=Path("x.pdf"), error="err")])
    espia = _RunPipelineEspia(resultado=resultado)
    assert main([], run_pipeline_fn=espia) == 1


def test_exit_code_3_si_run_pipeline_lanza_fallo_fatal(capsys):
    espia = _RunPipelineEspia(excepcion=RuntimeError("disco lleno"))

    codigo = main([], run_pipeline_fn=espia)
    salida = capsys.readouterr()

    assert codigo == 3
    resumen = json.loads(salida.out)
    assert resumen["error_fatal"] == "disco lleno"
    assert "disco lleno" in salida.err


def test_exit_code_2_si_ingest_falla_con_pipeline_sin_fallidos(capsys):
    resultado = ResultadoPipeline(version_dir=Path("data/processed/v1"), procesados=[Path("a.txt")])
    run_espia = _RunPipelineEspia(resultado=resultado)
    ingest_espia = _IngestarVersionEspia(excepcion=RuntimeError("supabase caido"))

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=run_espia,
        ingestar_version_fn=ingest_espia,
        crear_store_fn=_StoreFake,
    )
    salida = capsys.readouterr()

    assert codigo == 2
    resumen = json.loads(salida.out)
    assert resumen["ingesta"] == {
        "intentada": True,
        "ejecutada": False,
        "error": "supabase caido",
    }


def test_exit_code_1_tiene_prioridad_si_fallidos_y_ingest_fallan_a_la_vez(capsys):
    resultado = ResultadoPipeline(
        version_dir=Path("data/processed/v1"),
        fallidos=[ResultadoFichero(path=Path("x.pdf"), error="err")],
    )
    run_espia = _RunPipelineEspia(resultado=resultado)
    ingest_espia = _IngestarVersionEspia(excepcion=RuntimeError("tambien falla"))

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=run_espia,
        ingestar_version_fn=ingest_espia,
        crear_store_fn=_StoreFake,
    )

    assert codigo == 1


# ---------------------------------------------------------------------------
# main() — --ingest: wiring, no-op sin nada pendiente, no se llama sin la flag
# ---------------------------------------------------------------------------


def test_ingest_no_se_llama_si_la_flag_no_se_pasa(capsys):
    resultado = ResultadoPipeline(version_dir=Path("data/processed/v1"), procesados=[Path("a.txt")])
    run_espia = _RunPipelineEspia(resultado=resultado)
    ingest_espia = _IngestarVersionEspia()
    registro_store = []

    main(
        [],
        run_pipeline_fn=run_espia,
        ingestar_version_fn=ingest_espia,
        crear_store_fn=_crear_store_espia(registro_store),
    )

    assert ingest_espia.llamadas == []
    assert registro_store == []


def test_ingest_no_op_sin_error_si_no_habia_nada_pendiente(capsys):
    espia_run = _RunPipelineEspia(resultado=ResultadoPipeline())  # version_dir=None
    ingest_espia = _IngestarVersionEspia()
    registro_store = []

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=espia_run,
        ingestar_version_fn=ingest_espia,
        crear_store_fn=_crear_store_espia(registro_store),
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert ingest_espia.llamadas == []
    assert registro_store == []  # ni siquiera se construye el store si no hay nada que ingestar
    resumen = json.loads(salida.out)
    assert resumen["ingesta"]["intentada"] is True
    assert resumen["ingesta"]["ejecutada"] is False
    assert "motivo" in resumen["ingesta"]


def test_ingest_llama_a_ingestar_version_con_directorio_base_y_version_correctos(tmp_path, capsys):
    version_dir = tmp_path / "processed" / "v3"
    resultado = ResultadoPipeline(version_dir=version_dir, procesados=[Path("a.txt")])
    run_espia = _RunPipelineEspia(resultado=resultado)
    ingest_espia = _IngestarVersionEspia(
        resultado=_ResultadoIngestaFake(version_corpus="v3", num_nuevos=5, num_duplicados=0)
    )
    registro_store = []

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=run_espia,
        ingestar_version_fn=ingest_espia,
        crear_store_fn=_crear_store_espia(registro_store),
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert registro_store == [True]
    assert len(ingest_espia.llamadas) == 1
    llamada = ingest_espia.llamadas[0]
    assert llamada["directorio_base"] == version_dir.parent
    assert llamada["version"] == 3
    assert isinstance(llamada["store"], _StoreFake)

    resumen = json.loads(salida.out)
    assert resumen["ingesta"] == {
        "intentada": True,
        "ejecutada": True,
        "version_corpus": "v3",
        "num_nuevos": 5,
        "num_duplicados": 0,
    }
