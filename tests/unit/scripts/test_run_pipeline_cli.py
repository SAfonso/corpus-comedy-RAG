"""Tests para `scripts/run_pipeline.py` (CLI de Flujo A, task 23).

Contrato (`scripts/run_pipeline.py` docstring, task 23; wiring actualizado en
la task 61 para `ingestar_documentos_pendientes`): este script es wiring
puro sobre `src/theory/pipeline.run_pipeline` (task 22) y
`src/theory/ingest_teoria.ingestar_documentos_pendientes` (task 21, leyendo
de Silver desde la task 61), ya aprobados y testeados en sus propios
módulos — aquí NO se re-testea su lógica interna (cadena de parsers,
idempotencia, embeddings...), solo el CLI: parseo de argv, construcción del
resumen JSON, y el mapeo resultado -> exit code.

Todas las dependencias externas (`run_pipeline_fn`, `ingestar_fn`,
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
    def __init__(self, num_documentos=3, num_nuevos=2, num_duplicados=1):
        self.num_documentos = num_documentos
        self.num_nuevos = num_nuevos
        self.num_duplicados = num_duplicados


class _RunPipelineEspia:
    """Doble de `run_pipeline` que registra con qué kwargs se le llamó y
    devuelve un `ResultadoPipeline` fijo (o lanza, si `excepcion` se pasa)."""

    def __init__(self, resultado=None, excepcion=None):
        self.resultado = resultado if resultado is not None else ResultadoPipeline()
        self.excepcion = excepcion
        self.llamadas = []

    def __call__(
        self,
        carpetas,
        *,
        directorio_procesado=None,
        ruta_estado=None,
        version=None,
        drive_sync=None,
        document_store=None,
    ):
        self.llamadas.append(
            {
                "carpetas": carpetas,
                "directorio_procesado": directorio_procesado,
                "ruta_estado": ruta_estado,
                "version": version,
                "drive_sync": drive_sync,
                "document_store": document_store,
            }
        )
        if self.excepcion is not None:
            raise self.excepcion
        return self.resultado


class _IngestarEspia:
    """Doble de `ingestar_documentos_pendientes` (task 61): ya no recibe
    `directorio_base`/`version` — solo `store` (más `documentos`/
    `generar_embedding_fn` opcionales, que este CLI no usa)."""

    def __init__(self, resultado=None, excepcion=None):
        self.resultado = resultado if resultado is not None else _ResultadoIngestaFake()
        self.excepcion = excepcion
        self.llamadas = []

    def __call__(self, store, *, documentos=None, generar_embedding_fn=None):
        self.llamadas.append({"store": store, "documentos": documentos})
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


class _DriveSyncInstanciaFake:
    """Objeto devuelto por `crear_drive_sync_fn` en los tests — no necesita
    comportamiento propio, solo ser un objeto identificable (`is`) para
    verificar que `main()` lo reenvía tal cual a `run_pipeline_fn`."""


class _CrearDriveSyncEspia:
    """Doble de `desde_entorno` (o de cualquier `crear_drive_sync_fn`
    inyectado): registra con qué `staging_dir`/`state_path` se le llamó y
    devuelve una instancia fija, o lanza `excepcion` (simula el `RuntimeError`
    de `desde_entorno` cuando falta `DRIVE_FOLDER_ID`)."""

    def __init__(self, resultado=None, excepcion=None):
        self.resultado = resultado if resultado is not None else _DriveSyncInstanciaFake()
        self.excepcion = excepcion
        self.llamadas = []

    def __call__(self, *, staging_dir=None, state_path=None):
        self.llamadas.append({"staging_dir": staging_dir, "state_path": state_path})
        if self.excepcion is not None:
            raise self.excepcion
        return self.resultado


class _DocumentStoreInstanciaFake:
    """Objeto devuelto por `crear_document_store_fn` en los tests — solo
    necesita ser identificable (`is`) para verificar que `main()` lo reenvía
    tal cual a `run_pipeline_fn`."""


class _CrearDocumentStoreEspia:
    """Doble de `DocumentStore` (o de cualquier `crear_document_store_fn`
    inyectado): registra cuántas veces se llamó (sin argumentos, mismo patrón
    que `_crear_store_por_defecto`) y devuelve una instancia fija, o lanza
    `excepcion` (simula `DocumentStoreError` por falta de
    `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`)."""

    def __init__(self, resultado=None, excepcion=None):
        self.resultado = resultado if resultado is not None else _DocumentStoreInstanciaFake()
        self.excepcion = excepcion
        self.llamadas = 0

    def __call__(self):
        self.llamadas += 1
        if self.excepcion is not None:
            raise self.excepcion
        return self.resultado


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
    ingesta = {"intentada": True, "ejecutada": True, "num_documentos": 1}

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
    ingest_espia = _IngestarEspia(excepcion=RuntimeError("supabase caido"))

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=run_espia,
        ingestar_fn=ingest_espia,
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
    ingest_espia = _IngestarEspia(excepcion=RuntimeError("tambien falla"))

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=run_espia,
        ingestar_fn=ingest_espia,
        crear_store_fn=_StoreFake,
    )

    assert codigo == 1


# ---------------------------------------------------------------------------
# main() — --ingest: wiring, no-op sin nada pendiente, no se llama sin la flag
# ---------------------------------------------------------------------------


def test_ingest_no_se_llama_si_la_flag_no_se_pasa(capsys):
    resultado = ResultadoPipeline(version_dir=Path("data/processed/v1"), procesados=[Path("a.txt")])
    run_espia = _RunPipelineEspia(resultado=resultado)
    ingest_espia = _IngestarEspia()
    registro_store = []

    main(
        [],
        run_pipeline_fn=run_espia,
        ingestar_fn=ingest_espia,
        crear_store_fn=_crear_store_espia(registro_store),
    )

    assert ingest_espia.llamadas == []
    assert registro_store == []


def test_ingest_no_op_sin_error_si_no_habia_nada_pendiente(capsys):
    espia_run = _RunPipelineEspia(resultado=ResultadoPipeline())  # version_dir=None
    ingest_espia = _IngestarEspia()
    registro_store = []

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=espia_run,
        ingestar_fn=ingest_espia,
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


def test_ingest_llama_a_ingestar_fn_solo_con_el_store(tmp_path, capsys):
    """Task 61: `ingestar_documentos_pendientes` ya no recibe `directorio_base`/
    `version` — la selección de qué ingestar vive en `store` (Silver sin
    chunks en Gold), no en el `version_dir` de este run (ver docstring del
    módulo, §Task 61)."""
    version_dir = tmp_path / "processed" / "v3"
    resultado = ResultadoPipeline(version_dir=version_dir, procesados=[Path("a.txt")])
    run_espia = _RunPipelineEspia(resultado=resultado)
    ingest_espia = _IngestarEspia(
        resultado=_ResultadoIngestaFake(num_documentos=1, num_nuevos=5, num_duplicados=0)
    )
    registro_store = []

    codigo = main(
        ["--ingest"],
        run_pipeline_fn=run_espia,
        ingestar_fn=ingest_espia,
        crear_store_fn=_crear_store_espia(registro_store),
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert registro_store == [True]
    assert len(ingest_espia.llamadas) == 1
    llamada = ingest_espia.llamadas[0]
    assert isinstance(llamada["store"], _StoreFake)
    assert llamada["documentos"] is None

    resumen = json.loads(salida.out)
    assert resumen["ingesta"] == {
        "intentada": True,
        "ejecutada": True,
        "num_documentos": 1,
        "num_nuevos": 5,
        "num_duplicados": 0,
    }


# ---------------------------------------------------------------------------
# main() — --sync-drive (task 45, wiring de Drive real, P23)
#
# Contrato: `--sync-drive` es `store_true`, off por defecto (modo solo-local
# intacto: `crear_drive_sync_fn` nunca se llama). Con la flag, construye el
# sync vía `crear_drive_sync_fn(staging_dir=..., state_path=...)` (inyectable,
# por defecto `src.theory.drive_sync.desde_entorno`) y lo pasa a
# `run_pipeline_fn(..., drive_sync=<instancia>)`. Un fallo de construcción
# (p.ej. `RuntimeError` por falta de `DRIVE_FOLDER_ID`) reutiliza el exit code
# 3 ya existente de "fallo fatal inesperado" — no se inventa uno nuevo.
# ---------------------------------------------------------------------------


def test_sync_drive_ausente_no_llama_a_crear_drive_sync_fn(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    drive_espia = _CrearDriveSyncEspia()

    codigo = main([], run_pipeline_fn=run_espia, crear_drive_sync_fn=drive_espia)

    assert codigo == 0
    assert drive_espia.llamadas == []
    assert len(run_espia.llamadas) == 1
    assert run_espia.llamadas[0]["drive_sync"] is None


def test_sync_drive_presente_llama_a_crear_drive_sync_fn_y_lo_reenvia_a_run_pipeline(
    tmp_path, capsys
):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    instancia = _DriveSyncInstanciaFake()
    drive_espia = _CrearDriveSyncEspia(resultado=instancia)

    codigo = main(
        [
            "--sync-drive",
            "--drive-staging-dir",
            str(tmp_path / "staging"),
            "--drive-state-path",
            str(tmp_path / "state.json"),
        ],
        run_pipeline_fn=run_espia,
        crear_drive_sync_fn=drive_espia,
    )

    assert codigo == 0
    assert len(drive_espia.llamadas) == 1
    llamada = drive_espia.llamadas[0]
    assert llamada["staging_dir"] == tmp_path / "staging"
    assert llamada["state_path"] == tmp_path / "state.json"

    assert len(run_espia.llamadas) == 1
    assert run_espia.llamadas[0]["drive_sync"] is instancia


def test_sync_drive_sin_rutas_explicitas_pasa_none_para_usar_los_defaults(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    drive_espia = _CrearDriveSyncEspia()

    codigo = main(
        ["--sync-drive"], run_pipeline_fn=run_espia, crear_drive_sync_fn=drive_espia
    )

    assert codigo == 0
    llamada = drive_espia.llamadas[0]
    assert llamada["staging_dir"] is None
    assert llamada["state_path"] is None


def test_sync_drive_fallo_de_configuracion_devuelve_exit_code_3(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    drive_espia = _CrearDriveSyncEspia(
        excepcion=RuntimeError(
            "theory.drive_sync.desde_entorno sin folder_id: pásalo o define DRIVE_FOLDER_ID."
        )
    )

    codigo = main(
        ["--sync-drive"], run_pipeline_fn=run_espia, crear_drive_sync_fn=drive_espia
    )
    salida = capsys.readouterr()

    assert codigo == 3
    # El fallo ocurre construyendo el drive_sync, ANTES de invocar run_pipeline_fn.
    assert run_espia.llamadas == []
    resumen = json.loads(salida.out)
    assert "DRIVE_FOLDER_ID" in resumen["error_fatal"]
    assert "DRIVE_FOLDER_ID" in salida.err


# ---------------------------------------------------------------------------
# main() — --capturar-bronze / --sin-captura-bronze (task 59, P25)
#
# Contrato: activación asimétrica (ver docstring del módulo, §--capturar-bronze
# / --sin-captura-bronze). Sin --sync-drive: la captura va apagada por
# defecto, --capturar-bronze la enciende. Con --sync-drive: la captura va
# encendida por defecto, --sin-captura-bronze la apaga (y deja
# bronze.omitida=true en el resumen). Un fallo de crear_document_store_fn
# (p.ej. DocumentStoreError por falta de SUPABASE_URL/SUPABASE_SERVICE_KEY)
# reutiliza el exit code 3 ya existente, igual que --sync-drive.
# ---------------------------------------------------------------------------


def test_sin_flags_no_llama_a_crear_document_store_fn_y_bronze_no_activada(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    doc_store_espia = _CrearDocumentStoreEspia()

    codigo = main([], run_pipeline_fn=run_espia, crear_document_store_fn=doc_store_espia)
    salida = capsys.readouterr()

    assert codigo == 0
    assert doc_store_espia.llamadas == 0
    assert run_espia.llamadas[0]["document_store"] is None
    resumen = json.loads(salida.out)
    assert resumen["bronze"] == {"activada": False}


def test_capturar_bronze_sin_sync_drive_activa_la_captura(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    instancia = _DocumentStoreInstanciaFake()
    doc_store_espia = _CrearDocumentStoreEspia(resultado=instancia)

    codigo = main(
        ["--capturar-bronze"],
        run_pipeline_fn=run_espia,
        crear_document_store_fn=doc_store_espia,
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert doc_store_espia.llamadas == 1
    assert run_espia.llamadas[0]["document_store"] is instancia
    resumen = json.loads(salida.out)
    assert resumen["bronze"] == {"activada": True}


def test_sync_drive_activa_la_captura_por_defecto_sin_pedirla_explicitamente(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    drive_espia = _CrearDriveSyncEspia()
    instancia = _DocumentStoreInstanciaFake()
    doc_store_espia = _CrearDocumentStoreEspia(resultado=instancia)

    codigo = main(
        ["--sync-drive"],
        run_pipeline_fn=run_espia,
        crear_drive_sync_fn=drive_espia,
        crear_document_store_fn=doc_store_espia,
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert doc_store_espia.llamadas == 1
    assert run_espia.llamadas[0]["document_store"] is instancia
    resumen = json.loads(salida.out)
    assert resumen["bronze"] == {"activada": True}


def test_sync_drive_con_sin_captura_bronze_desactiva_la_captura_y_lo_marca_omitida(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    drive_espia = _CrearDriveSyncEspia()
    doc_store_espia = _CrearDocumentStoreEspia()

    codigo = main(
        ["--sync-drive", "--sin-captura-bronze"],
        run_pipeline_fn=run_espia,
        crear_drive_sync_fn=drive_espia,
        crear_document_store_fn=doc_store_espia,
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert doc_store_espia.llamadas == 0
    assert run_espia.llamadas[0]["document_store"] is None
    resumen = json.loads(salida.out)
    assert resumen["bronze"] == {"activada": False, "omitida": True}


def test_sin_captura_bronze_sin_sync_drive_no_tiene_efecto(capsys):
    """`--sin-captura-bronze` sin `--sync-drive` no marca `omitida` — ese
    flag solo aplica cuando la captura iba a estar activa por defecto (ver
    docstring del módulo)."""
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    doc_store_espia = _CrearDocumentStoreEspia()

    codigo = main(
        ["--sin-captura-bronze"],
        run_pipeline_fn=run_espia,
        crear_document_store_fn=doc_store_espia,
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert doc_store_espia.llamadas == 0
    resumen = json.loads(salida.out)
    assert resumen["bronze"] == {"activada": False}


def test_capturar_bronze_fallo_de_configuracion_devuelve_exit_code_3(capsys):
    run_espia = _RunPipelineEspia(resultado=ResultadoPipeline())
    doc_store_espia = _CrearDocumentStoreEspia(
        excepcion=RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas en el entorno (.env)."
        )
    )

    codigo = main(
        ["--capturar-bronze"],
        run_pipeline_fn=run_espia,
        crear_document_store_fn=doc_store_espia,
    )
    salida = capsys.readouterr()

    assert codigo == 3
    # El fallo ocurre construyendo el document_store, ANTES de invocar run_pipeline_fn.
    assert run_espia.llamadas == []
    resumen = json.loads(salida.out)
    assert "SUPABASE_URL" in resumen["error_fatal"]
    assert "SUPABASE_URL" in salida.err
