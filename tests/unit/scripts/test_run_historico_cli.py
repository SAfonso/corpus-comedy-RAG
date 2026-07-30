"""Tests para `scripts/run_historico.py` (CLI de Flujo C — Histórico, task 28).

Contrato (`scripts/run_historico.py` docstring, task 28): este script es
wiring puro sobre `historico.coste.estimar_y_evaluar` (task 26) y
`historico.pipeline.run_historico_pipeline` (task 27), ya aprobados y
testeados en sus propios módulos — aquí NO se re-testea su lógica interna
(reconstrucción de prompts, cadena de etapas 0-5...), solo el CLI: parseo de
argv, construcción de `documentos` para el gate (etapas 0-2 ejecutadas por
este propio script, ver docstring §"El reto de diseño"), el resumen JSON y
el mapeo resultado -> exit code.

Todas las dependencias externas (`run_historico_pipeline_fn`,
`estimar_y_evaluar_fn`, `crear_drive_source_fn`) se inyectan como dobles de
prueba — nunca se llama a Drive, Supabase ni Gemini reales (eso vive en
`tests/integration/`, fuera de scope de esta task). `procesar_docx` (marcado
por color) es puro/sin red y se usa REAL en el test que ejercita el fixture
`tests/fixtures/Freskito-Informático.docx`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_historico import construir_resumen, main
from src.jokes.historico.coste import DecisionGate, EstimacionCoste
from src.jokes.historico.drive_source import MIME_DOCX
from src.jokes.historico.pipeline import (
    ChisteRuteado,
    FalloMarcado,
    ResultadoDocumento,
    ResultadoHistorico,
)
from src.utils.drive_sync import ArchivoSincronizado

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
FIXTURE_DOCX = FIXTURES / "Freskito-Informático.docx"


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------


class FakeDriveSource:
    """`.sync_con_metadata() -> list[ArchivoSincronizado]`. Devuelve metadata
    sintética fija, una sola vez (llamadas siguientes devuelven vacío —
    simula la idempotencia real de `DriveSource` una vez el estado ya quedó
    comprometido). `.sync()` sigue disponible como envoltorio de compatibilidad."""

    def __init__(self, paths):
        self._paths = list(paths)
        self.sync_calls = 0

    def sync_con_metadata(self):
        self.sync_calls += 1
        if self.sync_calls == 1:
            return [
                ArchivoSincronizado(
                    path=Path(p),
                    file_id=f"file-{i}",
                    name=Path(p).name,
                    modified_time=f"2026-07-01T00:00:0{i}.000Z",
                    mime_type=MIME_DOCX,
                    mime_salida=MIME_DOCX,
                )
                for i, p in enumerate(self._paths)
            ]
        return []

    def sync(self):
        return [a.path for a in self.sync_con_metadata()]


class FakeDocumentStore:
    """Doble mínimo de `DocumentStore.capturar()` (task 64) — no requiere
    credenciales de Supabase, nunca toca red."""

    def __init__(self):
        self.capturas = []

    def capturar(
        self, *, ruta, capa, flujo, drive_file_id, modified_time, nombre, mime_type, extra=None
    ):
        self.capturas.append({"nombre": nombre, "capa": capa, "flujo": flujo})
        return _ResultadoCapturaFake(ya_existia=False)


class _ResultadoCapturaFake:
    def __init__(self, *, ya_existia):
        self.ya_existia = ya_existia


def _crear_document_store_fn_fake(document_store=None):
    store = document_store if document_store is not None else FakeDocumentStore()

    def _crear():
        return store

    _crear.store = store
    return _crear


def _crear_drive_source_fn_fake(drive_source):
    llamadas = []

    def _crear(*, folder_id, staging_dir, state_path, credentials_path):
        llamadas.append(
            {
                "folder_id": folder_id,
                "staging_dir": staging_dir,
                "state_path": state_path,
                "credentials_path": credentials_path,
            }
        )
        return drive_source

    _crear.llamadas = llamadas
    return _crear


class _EstimarYEvaluarEspia:
    """Doble de `estimar_y_evaluar` que registra los kwargs recibidos y
    devuelve una `DecisionGate` fija."""

    def __init__(self, decision=None):
        self.decision = decision or _decision(permitir=True)
        self.llamadas = []

    def __call__(self, documentos, **kwargs):
        self.llamadas.append({"documentos": documentos, **kwargs})
        return self.decision


class _RunHistoricoPipelineEspia:
    """Doble de `run_historico_pipeline` que registra los kwargs recibidos y
    devuelve un `ResultadoHistorico` fijo (o lanza, si `excepcion` se pasa)."""

    def __init__(self, resultado=None, excepcion=None):
        self.resultado = resultado if resultado is not None else ResultadoHistorico()
        self.excepcion = excepcion
        self.llamadas = []

    def __call__(self, **kwargs):
        self.llamadas.append(kwargs)
        if self.excepcion is not None:
            raise self.excepcion
        return self.resultado


def _decision(*, permitir, tokens_totales=100, umbral_tokens=1_000_000, umbral_eur=None):
    estimacion = EstimacionCoste(
        documentos=1,
        llamadas_segmentador=2,
        llamadas_silver=2,
        tokens_segmentador=50,
        tokens_silver=50,
        tokens_totales=tokens_totales,
        coste_eur_estimado=0.01,
    )
    return DecisionGate(
        permitir=permitir,
        razon="dentro de presupuesto" if permitir else "supera el umbral",
        estimacion=estimacion,
        umbral_tokens=umbral_tokens,
        umbral_eur=umbral_eur,
    )


def _procesar_docx_noop(docx, carpeta_salida):
    """Doble de `procesar_docx` que no escribe nada (no hace falta un `.md`
    real para los tests que no inspeccionan `documentos`)."""
    return Path(carpeta_salida) / (Path(docx).stem + ".md"), "OK"


def _kwargs_base(drive_source, *, estimar_y_evaluar_fn=None, run_historico_pipeline_fn=None):
    return dict(
        crear_drive_source_fn=_crear_drive_source_fn_fake(drive_source),
        crear_document_store_fn=_crear_document_store_fn_fake(),
        procesar_docx_fn=_procesar_docx_noop,
        estimar_y_evaluar_fn=estimar_y_evaluar_fn or _EstimarYEvaluarEspia(),
        run_historico_pipeline_fn=run_historico_pipeline_fn or _RunHistoricoPipelineEspia(),
    )


# ---------------------------------------------------------------------------
# construir_resumen — forma pura del JSON
# ---------------------------------------------------------------------------


def test_construir_resumen_solo_gate_sin_pipeline():
    decision = _decision(permitir=True)
    resumen = construir_resumen(decision, None, [], dry_run=True)

    assert resumen["gate"]["permitir"] is True
    assert resumen["gate"]["estimacion"]["tokens_totales"] == 100
    assert resumen["dry_run"] is True
    assert resumen["marcado_fallidos_gate"] == []
    assert resumen["pipeline"] is None


def test_construir_resumen_con_marcado_fallidos_gate():
    decision = _decision(permitir=True)
    fallidos = [FalloMarcado(docx="roto.docx", error="round-trip falló")]
    resumen = construir_resumen(decision, None, fallidos, dry_run=True)

    assert resumen["marcado_fallidos_gate"] == [{"docx": "roto.docx", "error": "round-trip falló"}]


def test_construir_resumen_con_pipeline_completo():
    decision = _decision(permitir=True)
    resultado = ResultadoHistorico(
        descargados=["a.docx"],
        documentos=[
            ResultadoDocumento(
                name="a.md",
                chistes=[
                    ChisteRuteado(
                        decision="NUEVO",
                        chiste_id="c1",
                        tema_id=7,
                        tecnica_id=42,
                        inicio_localizado=True,
                    )
                ],
            )
        ],
    )
    resumen = construir_resumen(decision, resultado, [], dry_run=False)

    assert resumen["pipeline"]["descargados"] == ["a.docx"]
    assert resumen["pipeline"]["procesados"] == ["a.md"]
    assert resumen["pipeline"]["fallidos"] == []
    assert resumen["pipeline"]["chistes_ruteados"] == [
        {
            "chiste_id": "c1",
            "decision": "NUEVO",
            "tema_id": 7,
            "tecnica_id": 42,
            "inicio_localizado": True,
        }
    ]


def test_construir_resumen_gate_none_solo_con_error_fatal_externo():
    # gate=None representa el caso de excepción antes de poder evaluar el gate
    # (ver main(), construir_resumen se llama con decision=None ahí).
    resumen = construir_resumen(None, None, [], dry_run=False)
    assert resumen["gate"] is None
    assert resumen["pipeline"] is None


# ---------------------------------------------------------------------------
# main() — exit codes: gate bloquea (2), dry-run (0/2), fallo (1), éxito (0)
# ---------------------------------------------------------------------------


def test_exit_code_2_si_el_gate_bloquea(tmp_path, capsys):
    drive_source = FakeDriveSource([])
    pipeline_espia = _RunHistoricoPipelineEspia()
    kwargs = _kwargs_base(
        drive_source,
        estimar_y_evaluar_fn=_EstimarYEvaluarEspia(decision=_decision(permitir=False)),
        run_historico_pipeline_fn=pipeline_espia,
    )

    codigo = main(
        ["--loader-state", str(tmp_path / "loader.json"), "--carpeta-md", str(tmp_path / "md")],
        **kwargs,
    )
    salida = capsys.readouterr()

    assert codigo == 2
    assert pipeline_espia.llamadas == []  # nunca se invoca el run real
    resumen = json.loads(salida.out)
    assert resumen["gate"]["permitir"] is False
    assert resumen["pipeline"] is None


def test_exit_code_2_si_el_gate_bloquea_incluso_con_dry_run(tmp_path):
    drive_source = FakeDriveSource([])
    kwargs = _kwargs_base(
        drive_source,
        estimar_y_evaluar_fn=_EstimarYEvaluarEspia(decision=_decision(permitir=False)),
    )
    codigo = main(
        [
            "--dry-run",
            "--loader-state",
            str(tmp_path / "loader.json"),
            "--carpeta-md",
            str(tmp_path / "md"),
        ],
        **kwargs,
    )
    assert codigo == 2


def test_exit_code_0_dry_run_con_gate_en_verde_no_invoca_el_pipeline(tmp_path, capsys):
    drive_source = FakeDriveSource([])
    pipeline_espia = _RunHistoricoPipelineEspia()
    kwargs = _kwargs_base(drive_source, run_historico_pipeline_fn=pipeline_espia)

    codigo = main(
        [
            "--dry-run",
            "--loader-state",
            str(tmp_path / "loader.json"),
            "--carpeta-md",
            str(tmp_path / "md"),
        ],
        **kwargs,
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert pipeline_espia.llamadas == []
    resumen = json.loads(salida.out)
    assert resumen["dry_run"] is True
    assert resumen["gate"]["permitir"] is True
    assert resumen["pipeline"] is None


def test_dry_run_con_marcado_fallidos_gate_sigue_dando_exit_0(tmp_path):
    """Decisión documentada: --dry-run no pliega marcado_fallidos_gate en el
    exit code (ver docstring del módulo, §"Por qué --dry-run no pliega
    marcado_fallidos_gate en el exit code") — solo refleja la decisión del gate."""
    drive_source = FakeDriveSource([tmp_path / "roto.docx"])
    (tmp_path / "roto.docx").write_bytes(b"no es un docx valido")

    def procesar_docx_explota(docx, carpeta_salida):
        raise ValueError("round-trip falló")

    codigo = main(
        [
            "--dry-run",
            "--loader-state",
            str(tmp_path / "state" / "loader.json"),
            "--carpeta-md",
            str(tmp_path / "md"),
        ],
        crear_drive_source_fn=_crear_drive_source_fn_fake(drive_source),
        crear_document_store_fn=_crear_document_store_fn_fake(),
        procesar_docx_fn=procesar_docx_explota,
        estimar_y_evaluar_fn=_EstimarYEvaluarEspia(),
        run_historico_pipeline_fn=_RunHistoricoPipelineEspia(),
    )
    assert codigo == 0


def test_exit_code_0_run_real_sin_fallos(tmp_path, capsys):
    drive_source = FakeDriveSource([])
    resultado = ResultadoHistorico(documentos=[ResultadoDocumento(name="a.md")])
    pipeline_espia = _RunHistoricoPipelineEspia(resultado=resultado)
    kwargs = _kwargs_base(drive_source, run_historico_pipeline_fn=pipeline_espia)

    codigo = main(
        ["--loader-state", str(tmp_path / "loader.json"), "--carpeta-md", str(tmp_path / "md")],
        **kwargs,
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert len(pipeline_espia.llamadas) == 1
    resumen = json.loads(salida.out)
    assert resumen["pipeline"]["procesados"] == ["a.md"]


def test_exit_code_1_run_real_con_documento_fallido(tmp_path):
    drive_source = FakeDriveSource([])
    resultado = ResultadoHistorico(
        documentos=[ResultadoDocumento(name="a.md", error="Silver cayó")]
    )
    kwargs = _kwargs_base(
        drive_source, run_historico_pipeline_fn=_RunHistoricoPipelineEspia(resultado=resultado)
    )

    codigo = main(
        ["--loader-state", str(tmp_path / "loader.json"), "--carpeta-md", str(tmp_path / "md")],
        **kwargs,
    )
    assert codigo == 1


def test_exit_code_1_run_real_con_marcado_fallidos_del_paso_de_gate(tmp_path):
    """Un .docx que falla el marcado durante el paso de gate (etapas 0-2 de
    este script) no reaparece en resultado.marcado_fallidos del run real (el
    segundo sync() no lo vuelve a stagear) — este script lo combina para no
    perder la señal de fallo (ver docstring, §El reto de diseño)."""
    drive_source = FakeDriveSource([tmp_path / "roto.docx"])
    (tmp_path / "roto.docx").write_bytes(b"no es un docx valido")

    def procesar_docx_explota(docx, carpeta_salida):
        raise ValueError("round-trip falló")

    resultado_ok = ResultadoHistorico()  # el run real no ve ese .docx (ya no se re-stagea)
    codigo = main(
        [
            "--loader-state",
            str(tmp_path / "state" / "loader.json"),
            "--carpeta-md",
            str(tmp_path / "md"),
        ],
        crear_drive_source_fn=_crear_drive_source_fn_fake(drive_source),
        crear_document_store_fn=_crear_document_store_fn_fake(),
        procesar_docx_fn=procesar_docx_explota,
        estimar_y_evaluar_fn=_EstimarYEvaluarEspia(),
        run_historico_pipeline_fn=_RunHistoricoPipelineEspia(resultado=resultado_ok),
    )
    assert codigo == 1


def test_exit_code_1_si_run_historico_pipeline_lanza_excepcion(tmp_path, capsys):
    drive_source = FakeDriveSource([])
    kwargs = _kwargs_base(
        drive_source,
        run_historico_pipeline_fn=_RunHistoricoPipelineEspia(excepcion=RuntimeError("supabase caído")),
    )

    codigo = main(
        ["--loader-state", str(tmp_path / "loader.json"), "--carpeta-md", str(tmp_path / "md")],
        **kwargs,
    )
    salida = capsys.readouterr()

    assert codigo == 1
    resumen = json.loads(salida.out)
    assert resumen["error_fatal"] == "supabase caído"
    assert "supabase caído" in salida.err


def test_exit_code_1_si_falta_folder_id(tmp_path, capsys):
    """`crear_drive_source_fn` real levanta RuntimeError sin folder_id — se
    traduce a exit 1 (excepción inesperada antes de poder evaluar el gate)."""
    from scripts.run_historico import _crear_drive_source_por_defecto

    codigo = main(
        ["--loader-state", str(tmp_path / "loader.json"), "--carpeta-md", str(tmp_path / "md")],
        crear_drive_source_fn=_crear_drive_source_por_defecto,
        procesar_docx_fn=_procesar_docx_noop,
        estimar_y_evaluar_fn=_EstimarYEvaluarEspia(),
        run_historico_pipeline_fn=_RunHistoricoPipelineEspia(),
    )
    salida = capsys.readouterr()

    assert codigo == 1
    resumen = json.loads(salida.out)
    assert "folder_id" in resumen["error_fatal"] or "DRIVE_FOLDER_ID_HISTORICO" in resumen["error_fatal"]


# ---------------------------------------------------------------------------
# main() — reutilización de la MISMA instancia de drive_source (no doble sync)
# ---------------------------------------------------------------------------


def test_run_historico_pipeline_recibe_la_misma_instancia_de_drive_source(tmp_path):
    drive_source = FakeDriveSource([])
    pipeline_espia = _RunHistoricoPipelineEspia()
    kwargs = _kwargs_base(drive_source, run_historico_pipeline_fn=pipeline_espia)

    main(
        ["--loader-state", str(tmp_path / "loader.json"), "--carpeta-md", str(tmp_path / "md")],
        **kwargs,
    )

    assert pipeline_espia.llamadas[0]["drive_source"] is drive_source
    assert drive_source.sync_calls == 1  # el propio main() solo llama sync() una vez


# ---------------------------------------------------------------------------
# main() — el estado del Loader no se compromete prematuramente por el gate
# ---------------------------------------------------------------------------


def test_loader_state_no_se_compromete_prematuramente_por_el_paso_de_gate(tmp_path):
    """Verifica el punto 2 de §El reto de diseño: tras evaluar el gate (aunque
    haya documentos pendientes reales), `loader_state_path` en disco queda
    EXACTAMENTE como estaba antes (vacío en este caso) — la responsabilidad de
    comprometerlo es de `run_historico_pipeline` (aquí, un doble que no toca
    disco), nunca de este script."""
    carpeta_md = tmp_path / "md"
    carpeta_md.mkdir()
    (carpeta_md / "doc.md").write_text("contenido con [REMATE]x[/REMATE]", encoding="utf-8")
    loader_state = tmp_path / "state" / "loader.json"

    drive_source = FakeDriveSource([])  # nada nuevo en Drive; el .md ya está en carpeta_md
    estimar_espia = _EstimarYEvaluarEspia()
    kwargs = _kwargs_base(drive_source, estimar_y_evaluar_fn=estimar_espia)

    main(
        ["--loader-state", str(loader_state), "--carpeta-md", str(carpeta_md)],
        **kwargs,
    )

    # El gate SÍ vio el documento pendiente (documentos no vacío)...
    assert estimar_espia.llamadas[0]["documentos"] == [
        {"name": "doc.md", "content": "contenido con [REMATE]x[/REMATE]"}
    ]
    # ...pero el estado revertido no contiene el MD5 del documento: sigue
    # vacío, tal como estaba antes de este `Loader.load()` de más (el propio
    # `_guardar_estado_loader` sí puede crear el fichero al "revertir" a
    # `{}` si no existía antes — lo que importa es el CONTENIDO, no la mera
    # existencia del fichero).
    estado_revertido = json.loads(loader_state.read_text(encoding="utf-8")) if loader_state.exists() else {}
    assert estado_revertido == {}


# ---------------------------------------------------------------------------
# main() — wiring de flags -> kwargs de estimar_y_evaluar_fn / crear_drive_source_fn
# ---------------------------------------------------------------------------


def test_flags_de_coste_se_pasan_a_estimar_y_evaluar_fn(tmp_path):
    drive_source = FakeDriveSource([])
    estimar_espia = _EstimarYEvaluarEspia()
    kwargs = _kwargs_base(drive_source, estimar_y_evaluar_fn=estimar_espia)

    main(
        [
            "--loader-state",
            str(tmp_path / "loader.json"),
            "--carpeta-md",
            str(tmp_path / "md"),
            "--umbral-tokens",
            "500",
            "--umbral-eur",
            "1.5",
            "--precio-eur-por-millon-tokens",
            "0.2",
        ],
        **kwargs,
    )

    llamada = estimar_espia.llamadas[0]
    assert llamada["umbral_tokens"] == 500
    assert llamada["umbral_eur"] == 1.5
    assert llamada["precio_eur_por_millon_tokens"] == 0.2


def test_folder_id_y_rutas_se_pasan_a_crear_drive_source_fn(tmp_path):
    drive_source = FakeDriveSource([])
    crear_fn = _crear_drive_source_fn_fake(drive_source)

    main(
        [
            "--folder-id",
            "carpeta-xyz",
            "--staging-dir",
            str(tmp_path / "staging"),
            "--carpeta-md",
            str(tmp_path / "md"),
            "--drive-state",
            str(tmp_path / "drive.json"),
            "--loader-state",
            str(tmp_path / "loader.json"),
            "--credentials-path",
            str(tmp_path / "creds.json"),
        ],
        crear_drive_source_fn=crear_fn,
        crear_document_store_fn=_crear_document_store_fn_fake(),
        procesar_docx_fn=_procesar_docx_noop,
        estimar_y_evaluar_fn=_EstimarYEvaluarEspia(),
        run_historico_pipeline_fn=_RunHistoricoPipelineEspia(),
    )

    llamada = crear_fn.llamadas[0]
    assert llamada["folder_id"] == "carpeta-xyz"
    assert llamada["staging_dir"] == tmp_path / "staging"
    assert llamada["state_path"] == tmp_path / "drive.json"
    assert llamada["credentials_path"] == tmp_path / "creds.json"


# ---------------------------------------------------------------------------
# main() — canal del resumen: stdout JSON por defecto, o --summary-out
# ---------------------------------------------------------------------------


def test_main_con_summary_out_escribe_fichero_y_stdout_queda_vacio(tmp_path, capsys):
    drive_source = FakeDriveSource([])
    kwargs = _kwargs_base(drive_source)
    destino = tmp_path / "resumen.json"

    codigo = main(
        [
            "--loader-state",
            str(tmp_path / "loader.json"),
            "--carpeta-md",
            str(tmp_path / "md"),
            "--summary-out",
            str(destino),
        ],
        **kwargs,
    )
    salida = capsys.readouterr()

    assert codigo == 0
    assert salida.out == ""
    resumen = json.loads(destino.read_text(encoding="utf-8"))
    assert resumen["gate"]["permitir"] is True


# ---------------------------------------------------------------------------
# main() — extremo a extremo con marcado real (fixture real, sin red)
# ---------------------------------------------------------------------------


def test_documentos_del_gate_usan_marcar_remates_real_sobre_fixture_real(tmp_path):
    """Ejercita `_documentos_pendientes_para_gate` con el `procesar_docx` REAL
    (puro, sin red) sobre el fixture `Freskito-Informático.docx` — confirma
    que el gate recibe contenido de verdad marcado con [REMATE], no un doble."""
    from scripts.marcar_remates import procesar_docx as procesar_docx_real

    drive_source = FakeDriveSource([FIXTURE_DOCX])
    estimar_espia = _EstimarYEvaluarEspia()

    codigo = main(
        [
            "--loader-state",
            str(tmp_path / "state" / "loader.json"),
            "--carpeta-md",
            str(tmp_path / "md"),
        ],
        crear_drive_source_fn=_crear_drive_source_fn_fake(drive_source),
        crear_document_store_fn=_crear_document_store_fn_fake(),
        procesar_docx_fn=procesar_docx_real,
        estimar_y_evaluar_fn=estimar_espia,
        run_historico_pipeline_fn=_RunHistoricoPipelineEspia(),
    )

    assert codigo == 0
    documentos = estimar_espia.llamadas[0]["documentos"]
    assert len(documentos) == 1
    assert documentos[0]["name"] == "Freskito-Informático.md"
    assert "[REMATE]" in documentos[0]["content"]
