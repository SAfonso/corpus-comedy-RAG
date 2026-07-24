"""coste — dry-run de estimación de tokens + gate de presupuesto (task 26).

Contrato (`docs/specs/llm-policy.md` §Control de coste, `src/jokes/historico/SPEC.md`
§Coste): dado un conjunto de documentos ya cargados (mismo shape que produce
`Loader.load()`: `list[{"name": str, "content": str}]`), estima cuántos tokens
consumiría un run completo del Flujo C (Segmentador + Silver) **sin llamar a
la API de generación real**, y aplica un gate configurable para que el futuro
orquestador (`historico/pipeline.py`, task 27, invocado desde
`scripts/run_historico.py`, task 28) pueda abortar ANTES de gastar dinero de
verdad. Este módulo NO ejecuta el run — solo estima y decide; NO llama a
`segmentar_documento`/`estructurar_chiste` ni a Supabase.

## Qué prompts se cuentan (y qué proxy se usa)

- **Segmentador:** un prompt por CADA ventana candidata de cada documento
  (`segmentador._construir_ventanas`, determinista — el nº de ventanas es
  exactamente el nº de `[REMATE]`, sin aproximación: eso ya lo resuelve
  `_construir_ventanas`, no hay que estimarlo).
- **Silver:** el nº de llamadas reales depende de cuántos chistes resultan de
  la segmentación, y eso solo se sabe llamando de verdad al LLM del
  Segmentador (dónde corta el setup es juicio semántico, P16). Como este
  módulo es un dry-run 100% offline, usamos el mismo proxy documentado en la
  task: **nº de `[REMATE]` de un documento ≈ nº de llamadas a Silver** (una
  ventana candidata produce, en el caso normal, un chiste). Además, para el
  TEXTO de cada prompt de Silver no conocemos el offset real donde el LLM del
  Segmentador cortaría el setup (eso también requiere la llamada real), así
  que se usa la ventana candidata COMPLETA (limpia de etiquetas) en vez del
  recorte real — una sobreestimación deliberada y documentada: mejor
  sobreestimar tokens en un gate de presupuesto que subestimar y dejar pasar
  un run que se dispara de coste.

## Por qué se IMPORTAN las funciones "privadas" de segmentador.py/silver.py
en vez de duplicar la construcción del prompt

`segmentador._build_prompt`/`_construir_ventanas` y `silver._build_prompt`/
`_limpiar_marcado_historico` llevan guion bajo (convención de Python, no
privacidad real del lenguaje). Se decidió IMPORTARLAS directamente en vez de
reescribir una versión simplificada aquí, por precisión: el objetivo de este
módulo es estimar coste real, y una copia duplicada del prompt divergiría en
silencio en cuanto alguien edite el prompt de producción sin acordarse de
actualizar también `coste.py` — el propio dry-run mentiría sobre el coste sin
que nada lo señalara. Importar la función real hace que cualquier cambio de
firma rompa la importación (o los tests de este módulo) de inmediato, en vez
de divergir calladamente. El coste de esta decisión es acoplamiento a un
detalle interno de otro módulo; se acepta porque la alternativa (duplicar)
es estrictamente peor para el propósito de esta task (estimar bien el coste).
Este módulo SOLO invoca esas funciones, nunca las modifica (fuera de scope,
ver `feature_list.json` task 26).

## Cómo se cuentan los tokens (oficial vs. heurística)

- **Vía oficial (por defecto):** el SDK `google-genai` expone
  `client.models.count_tokens(model=..., contents=...)` — confirmado
  inspeccionando el paquete instalado (`google.genai.models.Models.count_tokens`,
  `google-genai==1.67.0`). Llama al endpoint `countTokens` de la API de
  Gemini, que es un endpoint de CONTEO, no de generación: no produce
  contenido, no consume la cuota/coste de una llamada de `generate_content`.
  Sigue requiriendo red + `LLM_API_KEY`/`LLM_MODEL` válidos (no es 100%
  offline), pero es la fuente MÁS PRECISA porque usa el tokenizador real del
  modelo, no una aproximación. Se expone como `contar_tokens_oficial`, con
  import perezoso de `google.genai` (mismo patrón que `generar_json` en
  `src/utils/llm/client.py`) para no exigir el SDK en los tests puros.
- **Heurística offline (`estimar_tokens_heuristica`):** alternativa 100%
  offline, sin red ni credenciales, para dry-runs en entornos sin `.env`
  configurado (p.ej. CI sin secretos, o una primera estimación rápida antes
  de tener acceso a la API). Aproximación de ~4 caracteres por token, la
  regla de bolsillo estándar para modelos tipo GPT/Gemini en textos
  latinos/ingleses (documentada por los propios proveedores). Margen de error
  conocido: **±20-30%** frente al tokenizador real (varía con idioma,
  puntuación y densidad de palabras cortas — el español, con más sílabas por
  palabra que el inglés, tiende al extremo alto de ese margen). Solo debe
  usarse para una primera criba; para decidir un abort real de coste se
  prefiere la vía oficial si hay red disponible.
- **Inyección:** tanto `estimar_coste` como `estimar_y_evaluar` aceptan
  `contar_tokens: Callable[[str], int]` inyectable — igual que `llamar_llm`
  en `segmentador.py`/`silver.py`. Los tests unitarios (`tests/unit/jokes/
  historico/test_coste.py`) SIEMPRE inyectan un contador fake determinista:
  nunca tocan la red real. Un test de integración
  (`tests/integration/test_coste_live.py`) ejercita `contar_tokens_oficial`
  contra la API real, con el mismo patrón de `pytest.skip` que
  `test_segmentador_live.py`/`test_silver_live.py` si faltan credenciales.

## El precio en €

El SDK no expone un endpoint de precios (los precios de la API de Gemini
cambian por fuera del SDK y dependen del modelo/tier elegido en `LLM_MODEL`).
`precio_eur_por_millon_tokens` es, por tanto, un parámetro/env var **best
effort**, no una fuente de verdad: se documenta un valor por defecto
orientativo (rango bajo de un modelo "flash"/barato) para que el desglose en
€ sea legible, pero el caller debe poder ajustarlo cuando cambie el modelo o
el precio del proveedor. La decisión de abortar por defecto se basa en
TOKENS (magnitud estable, no depende del precio vigente); el umbral en € es
opcional y solo se aplica si el caller lo configura explícitamente.

## El gate

`evaluar_gate` compara una `EstimacionCoste` ya calculada contra un umbral de
tokens (obligatorio, con default documentado) y, opcionalmente, un umbral de
€. Prioridad de resolución del umbral: parámetro explícito > variable de
entorno > default. Devuelve una `DecisionGate` con `permitir: bool` y un
desglose textual (`razon`) para que el caller (`scripts/run_historico.py`,
task 28) decida el exit code — este módulo nunca hace `sys.exit` ni levanta
excepción por superar el umbral: es una decisión de datos, no un control de
flujo impuesto.

`UMBRAL_TOKENS_DEFAULT = 1_000_000` (1M tokens): valor conservador pensado
para frenar automáticamente un run que se dispare por error (p.ej. reprocesar
todo el histórico por un bug de idempotencia en `Loader`/`DriveSource`) sin
bloquear un batch semanal normal de tamaño moderado. A precio orientativo de
`PRECIO_EUR_POR_MILLON_TOKENS_DEFAULT`, 1M tokens son unos pocos céntimos/
un par de euros — el umbral es una salvaguarda de volumen, no un límite de
gasto ajustado al céntimo (para eso está el umbral en € opcional, explícito).
Configurable vía parámetro `umbral_tokens` o variable de entorno
`HISTORICO_COSTE_MAX_TOKENS`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

from src.jokes.historico.segmentador import _build_prompt as _build_prompt_segmentador
from src.jokes.historico.segmentador import _construir_ventanas
from src.jokes.silver import _build_prompt as _build_prompt_silver
from src.jokes.silver import _limpiar_marcado_historico
from src.utils.llm.client import _resolver_credenciales

# ---------------------------------------------------------------------------
# Defaults documentados (ver docstring del módulo).
# ---------------------------------------------------------------------------

UMBRAL_TOKENS_DEFAULT = 1_000_000
ENV_UMBRAL_TOKENS = "HISTORICO_COSTE_MAX_TOKENS"
ENV_UMBRAL_EUR = "HISTORICO_COSTE_MAX_EUR"

# Orientativo (modelo "flash"/barato tipo el usado en LLM_MODEL); no es una
# fuente de precios en vivo — ajustar si el modelo/proveedor cambia.
PRECIO_EUR_POR_MILLON_TOKENS_DEFAULT = 0.10
ENV_PRECIO_EUR_POR_MILLON_TOKENS = "HISTORICO_COSTE_EUR_POR_MILLON_TOKENS"


# ---------------------------------------------------------------------------
# Resultado de la estimación y de la decisión del gate.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimacionCoste:
    """Desglose numérico del dry-run — sin decisión (eso es `DecisionGate`)."""

    documentos: int
    llamadas_segmentador: int
    llamadas_silver: int
    tokens_segmentador: int
    tokens_silver: int
    tokens_totales: int
    coste_eur_estimado: float


@dataclass(frozen=True)
class DecisionGate:
    """Decisión del gate de presupuesto, con el desglose que la motiva."""

    permitir: bool
    razon: str
    estimacion: EstimacionCoste
    umbral_tokens: int
    umbral_eur: Optional[float]


# ---------------------------------------------------------------------------
# Reconstrucción de prompts — pura, sin red. Reusa las funciones reales de
# segmentador.py/silver.py (ver docstring del módulo, §por qué importarlas).
# ---------------------------------------------------------------------------


def _prompts_segmentador_de_documento(contenido: str) -> list[str]:
    """Un prompt de Segmentador por cada ventana candidata del documento.

    Idéntico al que construiría `segmentar_documento` de verdad — mismas
    ventanas (`_construir_ventanas`), mismo `_build_prompt`.
    """
    return [
        _build_prompt_segmentador(ventana.texto)
        for ventana in _construir_ventanas(contenido)
    ]


def _prompts_silver_de_documento(contenido: str) -> list[str]:
    """Un prompt de Silver por cada `[REMATE]` del documento (proxy, ver docstring).

    Usa la ventana candidata COMPLETA (limpia de etiquetas de marcado, mismo
    limpiador que usa `estructurar_chiste` de verdad) como proxy conservador
    del texto real del chiste — no conocemos el offset exacto sin llamar al
    LLM del Segmentador.
    """
    return [
        _build_prompt_silver(_limpiar_marcado_historico(ventana.texto))
        for ventana in _construir_ventanas(contenido)
    ]


# ---------------------------------------------------------------------------
# Conteo de tokens — heurística offline y vía oficial (ambas ver docstring).
# ---------------------------------------------------------------------------


def estimar_tokens_heuristica(texto: str) -> int:
    """Estimación 100% offline: ~4 caracteres por token (±20-30% de margen).

    Sin red, sin credenciales. `math.ceil` para no subestimar (redondear a la
    baja podría reportar 0 tokens para un texto no vacío). Ver docstring del
    módulo para el margen de error documentado frente al tokenizador real.
    """
    if not texto:
        return 0
    return max(1, -(-len(texto) // 4))  # ceil division sin importar math


def contar_tokens_oficial(
    texto: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> int:
    """Cuenta tokens con el endpoint dedicado `count_tokens` del SDK real.

    Import perezoso de `google.genai` (no requerido para los tests puros de
    este módulo, mismo patrón que `generar_json` en
    `src/utils/llm/client.py`). Llama a `client.models.count_tokens(...)`,
    que golpea `countTokens` (conteo, NO generación) — requiere red y
    credenciales válidas, pero no ejecuta una llamada de generación cara.
    Reusa `_resolver_credenciales` de `src/utils/llm/client.py` (misma
    prioridad argumento > entorno, mismo error si faltan).
    """
    from google import genai

    resolved_key, resolved_model = _resolver_credenciales(api_key, model)
    client = genai.Client(api_key=resolved_key)
    respuesta = client.models.count_tokens(model=resolved_model, contents=texto)
    return respuesta.total_tokens


# ---------------------------------------------------------------------------
# Estimación agregada — orquestación pura salvo por `contar_tokens` inyectado.
# ---------------------------------------------------------------------------


def estimar_coste(
    documentos: list[dict],
    *,
    contar_tokens: Optional[Callable[[str], int]] = None,
    precio_eur_por_millon_tokens: Optional[float] = None,
    entorno: Optional[dict] = None,
) -> EstimacionCoste:
    """Estima el coste en tokens/€ de un run completo sobre `documentos`.

    `documentos` es la misma forma que devuelve `Loader.load()`:
    `list[{"name": str, "content": str}]`. `contar_tokens` es inyectable
    (`str -> int`); por defecto usa `contar_tokens_oficial` (vía oficial del
    SDK, ver docstring del módulo) — en tests unitarios SIEMPRE se inyecta un
    fake (nunca se llama a la red real desde `tests/unit/`).

    `precio_eur_por_millon_tokens`: prioridad parámetro explícito > variable
    de entorno `HISTORICO_COSTE_EUR_POR_MILLON_TOKENS` > default documentado
    (`PRECIO_EUR_POR_MILLON_TOKENS_DEFAULT`).
    """
    contar = contar_tokens if contar_tokens is not None else contar_tokens_oficial
    entorno = entorno if entorno is not None else os.environ

    if precio_eur_por_millon_tokens is None:
        precio_env = entorno.get(ENV_PRECIO_EUR_POR_MILLON_TOKENS)
        precio_eur_por_millon_tokens = (
            float(precio_env) if precio_env else PRECIO_EUR_POR_MILLON_TOKENS_DEFAULT
        )

    llamadas_segmentador = 0
    llamadas_silver = 0
    tokens_segmentador = 0
    tokens_silver = 0

    for documento in documentos:
        contenido = documento["content"]

        prompts_segmentador = _prompts_segmentador_de_documento(contenido)
        llamadas_segmentador += len(prompts_segmentador)
        tokens_segmentador += sum(contar(prompt) for prompt in prompts_segmentador)

        prompts_silver = _prompts_silver_de_documento(contenido)
        llamadas_silver += len(prompts_silver)
        tokens_silver += sum(contar(prompt) for prompt in prompts_silver)

    tokens_totales = tokens_segmentador + tokens_silver
    coste_eur_estimado = (tokens_totales / 1_000_000) * precio_eur_por_millon_tokens

    return EstimacionCoste(
        documentos=len(documentos),
        llamadas_segmentador=llamadas_segmentador,
        llamadas_silver=llamadas_silver,
        tokens_segmentador=tokens_segmentador,
        tokens_silver=tokens_silver,
        tokens_totales=tokens_totales,
        coste_eur_estimado=coste_eur_estimado,
    )


# ---------------------------------------------------------------------------
# El gate — pura, sin red. Decide, no ejecuta (nunca sys.exit/raise por coste).
# ---------------------------------------------------------------------------


def evaluar_gate(
    estimacion: EstimacionCoste,
    *,
    umbral_tokens: Optional[int] = None,
    umbral_eur: Optional[float] = None,
    entorno: Optional[dict] = None,
) -> DecisionGate:
    """Compara `estimacion` contra un umbral y devuelve una `DecisionGate`.

    Resolución del umbral de tokens: parámetro explícito > variable de
    entorno `HISTORICO_COSTE_MAX_TOKENS` > `UMBRAL_TOKENS_DEFAULT`. El umbral
    en € (`umbral_eur` / `HISTORICO_COSTE_MAX_EUR`) es opcional: si no se
    configura por ninguna vía, no participa en la decisión (el gate por
    defecto es de volumen en tokens, ver docstring del módulo — el precio en
    € es best-effort). Frontera INCLUSIVA: igual al umbral se permite, solo
    superarlo aborta.
    """
    entorno = entorno if entorno is not None else os.environ

    if umbral_tokens is None:
        umbral_env = entorno.get(ENV_UMBRAL_TOKENS)
        umbral_tokens = int(umbral_env) if umbral_env else UMBRAL_TOKENS_DEFAULT

    if umbral_eur is None:
        umbral_eur_env = entorno.get(ENV_UMBRAL_EUR)
        umbral_eur = float(umbral_eur_env) if umbral_eur_env else None

    supera_tokens = estimacion.tokens_totales > umbral_tokens
    supera_eur = umbral_eur is not None and estimacion.coste_eur_estimado > umbral_eur

    permitir = not (supera_tokens or supera_eur)

    partes_razon = [
        f"tokens_totales={estimacion.tokens_totales} vs. umbral_tokens={umbral_tokens}",
        f"coste_eur_estimado={estimacion.coste_eur_estimado:.4f}"
        + (f" vs. umbral_eur={umbral_eur}" if umbral_eur is not None else " (sin umbral_eur configurado)"),
    ]
    if permitir:
        razon = "Dentro de presupuesto: " + "; ".join(partes_razon)
    else:
        motivo = []
        if supera_tokens:
            motivo.append("supera el umbral de tokens")
        if supera_eur:
            motivo.append("supera el umbral de €")
        razon = (
            "Abortar ("
            + " y ".join(motivo)
            + "): "
            + "; ".join(partes_razon)
        )

    return DecisionGate(
        permitir=permitir,
        razon=razon,
        estimacion=estimacion,
        umbral_tokens=umbral_tokens,
        umbral_eur=umbral_eur,
    )


# ---------------------------------------------------------------------------
# Conveniencia end-to-end para el futuro caller (`scripts/run_historico.py`,
# task 28): estima y evalúa en un solo paso.
# ---------------------------------------------------------------------------


def estimar_y_evaluar(
    documentos: list[dict],
    *,
    contar_tokens: Optional[Callable[[str], int]] = None,
    precio_eur_por_millon_tokens: Optional[float] = None,
    umbral_tokens: Optional[int] = None,
    umbral_eur: Optional[float] = None,
    entorno: Optional[dict] = None,
) -> DecisionGate:
    """`estimar_coste` + `evaluar_gate` en un solo paso, mismo `entorno` para ambos."""
    estimacion = estimar_coste(
        documentos,
        contar_tokens=contar_tokens,
        precio_eur_por_millon_tokens=precio_eur_por_millon_tokens,
        entorno=entorno,
    )
    return evaluar_gate(
        estimacion,
        umbral_tokens=umbral_tokens,
        umbral_eur=umbral_eur,
        entorno=entorno,
    )
