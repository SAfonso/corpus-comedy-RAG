"""Tests para `.github/workflows/run_historico_semanal.yml` (task 31).

Este workflow es infraestructura de CI/CD, no código Python — no hay una API
que importar. Lo único verificable sin disparar un run real de GitHub Actions
(sin secrets configurados en este entorno de desarrollo, ver `feature_list.json`
task 31 y `CHECKPOINTS.md`) es que el YAML parsea correctamente y que contiene
la forma esperada por el contrato de la tarea: invoca `scripts/run_historico.py`
(task 28, no reimplementado aquí), dispara por cron semanal + `workflow_dispatch`,
referencia los secrets documentados en el P21 de `docs/specs/00-overview.md`, y
no oculta un fallo del CLI con `continue-on-error`.

Nota sobre PyYAML y la clave `on:`: YAML 1.1 (el resuelto por
`yaml.safe_load`) trata `on`/`off`/`yes`/`no` sueltos como booleanos, así que
la clave del workflow que GitHub llama `on` aparece en el dict parseado como
la clave booleana `True`, no como el string `"on"`. Es un efecto puramente de
PyYAML (GitHub interpreta su propio YAML sin ese problema) — `_get_on()` lo
normaliza para que el test no dependa de este detalle.
"""
from pathlib import Path

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "run_historico_semanal.yml"
)


def _cargar_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"falta el workflow en {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_on(workflow: dict) -> dict:
    """Normaliza la clave `on:` (ver docstring del módulo, PyYAML la resuelve
    como la clave booleana `True`, no como el string `"on"`)."""
    if True in workflow:
        return workflow[True]
    return workflow["on"]


def _steps(workflow: dict) -> list[dict]:
    jobs = workflow["jobs"]
    assert len(jobs) == 1, "se esperaba un único job en el workflow"
    job = next(iter(jobs.values()))
    return job["steps"]


def test_workflow_parsea_como_yaml_valido():
    workflow = _cargar_workflow()
    assert isinstance(workflow, dict)
    assert "jobs" in workflow


def test_trigger_schedule_semanal():
    on = _get_on(_cargar_workflow())
    assert "schedule" in on, "falta el trigger `schedule` (cron semanal, título de la task 31)"
    crons = [entry["cron"] for entry in on["schedule"]]
    assert len(crons) == 1
    # Cron de 5 campos (min hora dia mes dia-semana); el día de la semana
    # (último campo) fija la cadencia semanal.
    campos = crons[0].split()
    assert len(campos) == 5, f"cron mal formado: {crons[0]!r}"
    assert campos[-1] != "*", "el campo día-de-la-semana debe fijar una cadencia semanal, no diaria"


def test_trigger_workflow_dispatch_manual():
    on = _get_on(_cargar_workflow())
    assert "workflow_dispatch" in on, (
        "falta workflow_dispatch — trigger manual para probar el workflow sin "
        "esperar al cron (decisión documentada en el YAML y en P21)"
    )


def test_job_instala_dependencias_con_pip_sin_venv():
    steps = _steps(_cargar_workflow())
    run_steps = " ".join(s.get("run", "") for s in steps)
    assert "pip install -r requirements.txt" in run_steps
    # Runner efímero de GitHub Actions: sin necesidad de un .venv (a
    # diferencia de init.sh en local, ver init.sh y el prompt de la task 31).
    assert ".venv" not in run_steps


def test_job_invoca_run_historico_cli_sin_continue_on_error():
    steps = _steps(_cargar_workflow())
    run_steps = [s for s in steps if "run_historico.py" in s.get("run", "")]
    assert run_steps, "el workflow debe invocar scripts/run_historico.py (task 28)"
    for step in run_steps:
        assert step.get("continue-on-error") in (None, False), (
            "el fallo (exit 1) o abort por coste (exit 2) de run_historico.py "
            "debe hacer fallar el job visiblemente — sin continue-on-error"
        )


def test_credenciales_drive_via_secreto_string_materializado_a_fichero():
    """Restricción fijada en src/jokes/historico/SPEC.md §'Auth desatendida':
    cuenta de servicio, nunca OAuth interactivo. Un secreto de GitHub Actions
    es un string, así que el JSON de la cuenta de servicio se escribe a un
    fichero temporal del runner y GOOGLE_APPLICATION_CREDENTIALS apunta ahí."""
    steps = _steps(_cargar_workflow())
    todo_run = " ".join(s.get("run", "") for s in steps)
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in todo_run
    assert "RUNNER_TEMP" in todo_run

    todo_env = {}
    for s in steps:
        todo_env.update(s.get("env", {}) or {})
    assert "GOOGLE_APPLICATION_CREDENTIALS" in todo_env
    assert "runner.temp" in todo_env["GOOGLE_APPLICATION_CREDENTIALS"]


def test_secrets_esperados_referenciados():
    """Nombres 1:1 con las variables de Flujo C en `.env.example` (excepto la
    credencial de Drive, ver test anterior) — convención documentada en P21."""
    steps = _steps(_cargar_workflow())
    envs = {}
    for s in steps:
        envs.update(s.get("env", {}) or {})
    esperados = {
        "DRIVE_FOLDER_ID_HISTORICO",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "LLM_API_KEY",
        "LLM_MODEL",
        "EMBEDDINGS_API_KEY",
        "EMBEDDINGS_MODEL",
    }
    faltantes = esperados - envs.keys()
    assert not faltantes, f"faltan secrets esperados en el step de ejecución: {faltantes}"
    for nombre in esperados:
        assert "secrets." + nombre in envs[nombre]


def test_cache_de_estado_entre_runs():
    """Decisión P21: cachear data/staging/historico + data/state/historico_*
    para que el run semanal no repita todo el trabajo desde cero (runner
    efímero, ver `src/jokes/historico/pipeline.py` para las rutas por defecto)."""
    steps = _steps(_cargar_workflow())
    cache_steps = [s for s in steps if s.get("uses", "").startswith("actions/cache")]
    assert cache_steps, "falta el paso de cache de estado entre runs (decisión P21)"
    paths = cache_steps[0]["with"]["path"]
    assert "data/staging/historico" in paths
    assert "data/state/historico_drive.json" in paths
    assert "data/state/historico_loader.json" in paths


def test_no_toca_material_sagrado():
    """Regla más importante del proyecto (CLAUDE.md): /data/raw/ y la capa
    Bronze nunca se tocan. Este workflow no debe referenciar data/raw en
    ningún paso (ni de checkout, ni de cache, ni de limpieza)."""
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "data/raw" not in workflow_text
