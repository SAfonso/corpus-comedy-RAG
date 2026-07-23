"""llm/embeddings — cliente de embeddings para el contrato compartido B/C.

Contrato (`src/jokes/SPEC.md` §Reconciliación, §Stack; `src/utils/SPEC.md`):
consumo exclusivo de `src/jokes/reconciliacion.py` (task 15) para calcular el
embedding del chiste entrante que se compara, por similitud coseno, contra
los embeddings ya almacenados en `pgvector` (columna `chistes.embedding`).
Mismo reparto de responsabilidades que `llm/client.py` (task 13) vs.
`silver.py`: este módulo es infraestructura de acceso al proveedor de
embeddings (llamar a la API, extraer el vector), mientras que el USO del
vector (hash primero, similitud coseno después, umbral IGUAL/CAMBIADO/NUEVO)
es responsabilidad de `reconciliacion.py`.

Proveedor: Google Gemini (mismo SDK `google-genai` que `llm/client.py`,
modelo de embeddings tipo `text-embedding-004`) — evita añadir un segundo
proveedor/SDK solo para embeddings cuando el que ya está integrado los sirve.
Credenciales: `.env.example` reserva `EMBEDDINGS_API_KEY`/`EMBEDDINGS_MODEL`
separadas de `LLM_API_KEY`/`LLM_MODEL` por si el proveedor llegara a diferir
en el futuro, pero por defecto (mismo proveedor hoy) caen a las credenciales
del LLM si las específicas de embeddings no están puestas — así no hace
falta duplicar la clave en `.env` mientras el proveedor sea el mismo.

Diseño para TDD sin red frágil (mismo patrón que `llm/client.py`): la
resolución de credenciales y la selección del vector de la respuesta viven en
funciones puras (`_resolver_credenciales_embeddings`, `_seleccionar_embedding`)
testeables sin red en `tests/unit/utils/test_embeddings.py`. La llamada real
a `embed_content` vive en `generar_embedding`, con import perezoso del SDK, y
se testea de verdad (sin mocks del SDK) en
`tests/integration/test_reconciliacion_live.py` (task 15).
"""
from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

# Carga `.env` (si existe) antes de que `generar_embedding` lea las variables
# de entorno — mismo patrón que `llm/client.py`/`supabase_store.py`.
load_dotenv()


class EmbeddingsClientError(RuntimeError):
    """Error del cliente de embeddings (config ausente, respuesta vacía/mal formada)."""


# ---------------------------------------------------------------------------
# Resolución de credenciales — pura, sin red, testeable directamente.
# ---------------------------------------------------------------------------

def _resolver_credenciales_embeddings(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    *,
    entorno: Optional[dict] = None,
) -> tuple[str, str]:
    """Resuelve `(api_key, model)` para la llamada de embeddings.

    Prioridad: argumento explícito > `EMBEDDINGS_API_KEY`/`EMBEDDINGS_MODEL`
    del entorno > `LLM_API_KEY`/`LLM_MODEL` del entorno (mismo proveedor por
    defecto, ver docstring del módulo). `entorno` es inyectable (por defecto
    `os.environ`) para testear sin depender del entorno real del proceso.
    Lanza `EmbeddingsClientError` si no se puede resolver ninguna de las dos.
    """
    entorno = entorno if entorno is not None else os.environ
    api_key = api_key or entorno.get("EMBEDDINGS_API_KEY") or entorno.get("LLM_API_KEY")
    model = model or entorno.get("EMBEDDINGS_MODEL") or entorno.get("LLM_MODEL")
    if not api_key or not model:
        raise EmbeddingsClientError(
            "EMBEDDINGS_API_KEY/EMBEDDINGS_MODEL (o, en su defecto, "
            "LLM_API_KEY/LLM_MODEL) no configuradas en el entorno (.env). "
            "Revisa .env.example — son necesarias para generar embeddings."
        )
    return api_key, model


# ---------------------------------------------------------------------------
# Selección del vector de la respuesta — pura, sin red, testeable con datos
# fijos (una lista de vectores, como los que devuelve el SDK ya extraídos).
# ---------------------------------------------------------------------------

def _seleccionar_embedding(vectores: list) -> list[float]:
    """Valida y devuelve el único vector esperado de una respuesta de embeddings.

    `vectores` es la lista de vectores ya extraídos de la respuesta del SDK
    (uno por cada texto de `contents`; aquí siempre se pide uno solo). Lanza
    `EmbeddingsClientError` si la respuesta no trae exactamente un vector no
    vacío — nunca deja pasar un embedding vacío o ausente en silencio hacia
    la comparación por similitud coseno de `reconciliacion.py`.
    """
    if not vectores:
        raise EmbeddingsClientError("La respuesta de embeddings no trae ningún vector.")
    if len(vectores) != 1:
        raise EmbeddingsClientError(
            f"Se esperaba un único vector de embedding, se recibieron {len(vectores)}."
        )
    valores = vectores[0]
    if not valores:
        raise EmbeddingsClientError("El vector de embedding recibido está vacío.")
    return list(valores)


# ---------------------------------------------------------------------------
# Llamada real — capa fina sobre `google-genai`. Import perezoso: no
# requerido para la lógica pura de arriba ni para sus tests unit.
# ---------------------------------------------------------------------------

def generar_embedding(
    texto: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> list[float]:
    """Calcula el embedding de `texto` vía Gemini, una única llamada.

    Igual que `generar_json` (`llm/client.py`): sin loop de reintento — el
    caller (`reconciliacion.py`) decide qué hacer si falla. No aplica P16
    aquí porque no hay reintento en absoluto que justificar: es una llamada
    de infraestructura, no una decisión que converja por criterio externo.
    """
    from google import genai

    resolved_key, resolved_model = _resolver_credenciales_embeddings(api_key, model)

    client = genai.Client(api_key=resolved_key)
    respuesta = client.models.embed_content(model=resolved_model, contents=[texto])
    vectores = [emb.values for emb in (respuesta.embeddings or [])]
    return _seleccionar_embedding(vectores)
