"""llm/client — cliente genérico de LLM vía API (modelo barato) para Flujos B/C.

Contrato (`src/jokes/SPEC.md` §Silver, `src/utils/SPEC.md`): este módulo es
consumo exclusivo de `src/jokes/` (Silver, task 13; Taxonomías, task 14) — la
regla de `CLAUDE.md` es que `theory/` NUNCA usa LLM (`docs/specs/llm-policy.md`),
así que este cliente no tiene ni tendrá caller en `src/theory/`. Vive en
`src/utils/` (no en `src/jokes/`) porque es infraestructura de acceso al LLM
en sí (llamar a la API, pedir salida JSON estructurada, parsear la respuesta),
mientras que el CONTENIDO del contrato (qué prompt, qué schema, qué campos)
es responsabilidad de cada caller (`src/jokes/silver.py` hoy;
`src/jokes/taxonomias.py` mañana, task 14) — mismo reparto que
`llm/embeddings.py` (cliente) vs. `reconciliacion.py` (uso del cliente).

Proveedor: Google Gemini (`generativelanguage.googleapis.com`), vía el SDK
oficial `google-genai` (ya usado para verificar `LLM_API_KEY`/`LLM_MODEL` en
`.env` antes de esta task). Se prefiere el SDK sobre `requests` a mano porque
la salida estructurada de Gemini (`responseMimeType`/`responseSchema`) tiene
un contrato más fino (tipos anidados, enums) que el SDK valida y serializa
por nosotros; reimplementarlo sobre HTTP crudo sería duplicar lógica que el
SDK ya resuelve, sin ganar nada en fiabilidad.

Diseño para TDD sin red frágil (mismo patrón que `supabase_store.py`): la
resolución de credenciales y el parseo de la respuesta viven en funciones
puras (`_resolver_credenciales`, `_parsear_json_respuesta`) testeables sin
red en `tests/unit/utils/test_llm_client.py`. La llamada real a
`génerate_content` vive en `generar_json`, con import perezoso del SDK (no
requerido para los tests de lógica pura), y se testea de verdad (sin mocks
frágiles del SDK) en los tests de integración de cada caller
(`tests/integration/test_silver_live.py`, task 13).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from dotenv import load_dotenv

# Carga `.env` (si existe) para poblar `LLM_API_KEY`/`LLM_MODEL` antes de que
# `generar_json` las lea. No falla si `.env` no existe (mismo patrón que
# `supabase_store.py`/`language_normalizer.py`).
load_dotenv()


class LLMClientError(RuntimeError):
    """Error del cliente LLM (config ausente, respuesta no parseable como JSON)."""


# ---------------------------------------------------------------------------
# Resolución de credenciales — pura, sin red, testeable directamente.
# ---------------------------------------------------------------------------

def _resolver_credenciales(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    *,
    entorno: Optional[dict] = None,
) -> tuple[str, str]:
    """Resuelve `(api_key, model)` a usar en la llamada real.

    Prioridad: argumento explícito > variable de entorno (`LLM_API_KEY` /
    `LLM_MODEL`). `entorno` es inyectable (por defecto `os.environ`) para
    testear sin depender del entorno real del proceso. Lanza `LLMClientError`
    con mensaje explícito si falta cualquiera de las dos — nunca intenta
    llamar a la API con valores vacíos.
    """
    entorno = entorno if entorno is not None else os.environ
    api_key = api_key or entorno.get("LLM_API_KEY")
    model = model or entorno.get("LLM_MODEL")
    if not api_key or not model:
        raise LLMClientError(
            "LLM_API_KEY / LLM_MODEL no configuradas en el entorno (.env). "
            "Revisa .env.example — son necesarias para llamar al LLM."
        )
    return api_key, model


# ---------------------------------------------------------------------------
# Parseo de la respuesta — pura, sin red, testeable con un string fijo.
# ---------------------------------------------------------------------------

def _parsear_json_respuesta(texto: str) -> dict:
    """Parsea el texto de respuesta del LLM (ya pedido como JSON) a `dict`.

    Con `responseMimeType: application/json` + `responseSchema`, Gemini
    devuelve el JSON como texto plano en `response.text` — este parseo es la
    frontera explícita entre "texto libre" y "dato validable", en vez de
    intentar extraer campos de texto libre a mano (regex, heurísticas).
    Lanza `LLMClientError` (no `json.JSONDecodeError` crudo) si el texto no
    es JSON válido, para que el caller no tenga que conocer la excepción
    interna de `json`.
    """
    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise LLMClientError(
            f"La respuesta del LLM no es JSON válido: {exc}. Texto recibido: {texto!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Llamada real — capa fina sobre `google-genai`. Import perezoso: no
# requerido para la lógica pura de arriba ni para sus tests unit.
# ---------------------------------------------------------------------------

def generar_json(
    prompt: str,
    schema: dict,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Llama una vez a Gemini pidiendo salida JSON estructurada y la parsea.

    Sin loop de reintento (política P16, `docs/specs/llm-policy.md`): una
    sola llamada, el resultado sale tal cual hacia el caller — quien decide
    si eso va a revisión humana (Silver) o a un loop acotado externo
    (Taxonomías, task 14, que ya tiene su propio criterio de parada
    verificable y no vive aquí).

    `schema` es un diccionario de `responseSchema` en el formato de Gemini
    (`{"type": "OBJECT", "properties": {...}, "required": [...]}`, con tipos
    en mayúsculas: `STRING`, `OBJECT`, etc.) — el caller es quien define el
    contrato de campos, este cliente es agnóstico a qué schema se le pasa.
    """
    from google import genai
    from google.genai import types

    resolved_key, resolved_model = _resolver_credenciales(api_key, model)

    client = genai.Client(api_key=resolved_key)
    respuesta = client.models.generate_content(
        model=resolved_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    return _parsear_json_respuesta(respuesta.text)
