"""reconciliacion — dedup híbrido hash + embedding del contrato compartido B/C (task 15).

Contrato (`src/jokes/SPEC.md` §Reconciliación): dado el `texto_normalizado`
de un chiste entrante (salida de Silver, `chiste_normalizado` — ver
`silver.py`), decide si es IGUAL, CAMBIADO o NUEVO frente a los chistes
`propio*` ya existentes:

```
hash(texto_normalizado) coincide       → IGUAL    → dedup (no inserta)
similitud embedding ∈ [~0.85, 1)        → CAMBIADO → nueva revisión del existente
similitud embedding < ~0.85             → NUEVO    → inserta chiste nuevo
```

Aplica igual a histórico↔histórico y Telegram↔histórico (misma unidad
`propio*`, mismo código — regla del contrato compartido, ver docstring de
`silver.py`/`supabase_store.py`).

**Scope de este módulo**: decide, no persiste. Igual que Silver "solo
produce la estructura, no la persiste" (docstring de `silver.py`), este
módulo solo produce la DECISIÓN (`ResultadoReconciliacion`) — el INSERT/UPDATE
resultante en `chistes`/`chistes_revisiones` es responsabilidad del caller
(Flujo B `telegram_bot.py`, task 16; Flujo C `segmentador.py`/loader, task 19),
vía `supabase_store.py`. Por el mismo motivo, este módulo tampoco decide DE
DÓNDE salen los `candidatos` (los chistes existentes contra los que
comparar): los recibe como argumento — mantiene `reconciliacion.py` sin
ninguna dependencia de Supabase, testeable 100% sin red salvo la propia
llamada de embeddings (igual que `silver.py` es agnóstico a de dónde sale el
texto de entrada, ver su docstring).

**Orden hash → embedding, no al revés** (§Reconciliación): el hash es
barato y determinista, cubre el caso frecuente (mismo texto exacto) sin
gastar ni una llamada de embeddings; solo si el hash no coincide con ningún
candidato se calcula/compara el embedding, que es lo que cuesta dinero y red
(`docs/specs/llm-policy.md` §Control de coste).

**Umbral tunable** (§Reconciliación: "los umbrales son indicativos y
afinables con datos reales"): `UMBRAL_SIMILITUD_CAMBIADO` vive como constante
de módulo, no hardcodeado en la lógica, para poder ajustarlo con datos reales
sin tocar la función de decisión.

**Sin loop P16**: la decisión IGUAL/CAMBIADO/NUEVO ya es, en sí misma, un
criterio de parada externo y verificable (hash coincide sí/no; similitud por
encima del umbral sí/no) — no hay nada que reintentar aquí, es la mecánica
que P16 pone como EJEMPLO de "ya cumple el patrón sin cambios"
(`docs/specs/llm-policy.md`).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Callable, Optional

from src.utils.llm.embeddings import generar_embedding

# ---------------------------------------------------------------------------
# Contrato de decisión (§Reconciliación).
# ---------------------------------------------------------------------------

DECISIONES = ("IGUAL", "CAMBIADO", "NUEVO")

# Indicativo, afinable con datos reales (§Reconciliación) — no hardcodeado
# dentro de `decidir_reconciliacion`.
UMBRAL_SIMILITUD_CAMBIADO = 0.85


class ReconciliacionError(ValueError):
    """Error de reconciliación (entrada inválida: texto vacío, embedding mal formado).

    Hereda de `ValueError`, mismo patrón que `SilverError`
    (`src/jokes/silver.py`) — permite capturarla como `ValueError` en el
    resto del contrato B/C sin perder el tipo específico del módulo.
    """


@dataclass(frozen=True)
class ResultadoReconciliacion:
    """Resultado de `decidir_reconciliacion`/`reconciliar_chiste`.

    `hash_normalizado` y `embedding` viajan en el resultado aunque la
    decisión sea NUEVO/CAMBIADO para que el caller no tenga que
    recalcularlos al persistir (`supabase_store.crear_chiste`/`crear_revision`
    esperan justo esos dos valores, §Storage). `chiste_id`/`similitud` solo
    se rellenan cuando la decisión referencia a un chiste existente (IGUAL o
    CAMBIADO) — quedan `None` en NUEVO.
    """

    decision: str
    hash_normalizado: str
    embedding: list[float]
    chiste_id: Optional[str] = None
    similitud: Optional[float] = None


# ---------------------------------------------------------------------------
# Hash de dedup exacto — pura, sin red, testeable directamente.
# ---------------------------------------------------------------------------

def calcular_hash_normalizado(texto_normalizado: str) -> str:
    """Hash determinista de `texto_normalizado` para dedup exacto (§Reconciliación).

    Solo hace `.strip()` antes de hashear (quita espacios/saltos de línea
    sobrantes en los bordes) — nada más: `texto_normalizado` YA es la salida
    normalizada de Silver, que conserva timing y muletillas a propósito
    (§Silver), así que este hash NO debe aplicar ninguna normalización
    adicional (minúsculas, quitar puntuación...) que borraría diferencias
    reales entre dos chistes parecidos. sha256 sobre UTF-8, hexdigest para
    que el resultado sea un `text` simple compatible con la columna
    `hash_normalizado` (§Storage).
    """
    if not texto_normalizado or not texto_normalizado.strip():
        raise ReconciliacionError("texto_normalizado vacío: no hay nada que hashear")
    return hashlib.sha256(texto_normalizado.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Similitud coseno — pura, sin red, testeable con vectores fijos.
# ---------------------------------------------------------------------------

def similitud_coseno(a: list[float], b: list[float]) -> float:
    """Similitud coseno entre dos embeddings (§Reconciliación).

    Lanza `ReconciliacionError` si los vectores no tienen la misma dimensión
    (comparar embeddings de dimensiones distintas es un error de datos, no
    un caso a resolver en silencio devolviendo 0) o si alguno es el vector
    cero (coseno indefinido — división por cero).
    """
    if len(a) != len(b):
        raise ReconciliacionError(
            f"Embeddings de dimensiones distintas: {len(a)} vs {len(b)}"
        )
    producto_punto = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if norma_a == 0 or norma_b == 0:
        raise ReconciliacionError("Similitud coseno indefinida para un vector cero")
    return producto_punto / (norma_a * norma_b)


# ---------------------------------------------------------------------------
# Decisión IGUAL/CAMBIADO/NUEVO — pura, sin red. Recibe `candidatos` ya
# obtenidos por el caller (lista de dicts con "id"/"hash_normalizado"/
# "embedding" de los chistes `propio*` existentes contra los que comparar).
# ---------------------------------------------------------------------------

def decidir_reconciliacion(
    texto_normalizado: str,
    embedding: list[float],
    candidatos: list[dict],
) -> ResultadoReconciliacion:
    """Decide IGUAL/CAMBIADO/NUEVO para un chiste entrante (§Reconciliación).

    `candidatos`: chistes existentes ya obtenidos por el caller, cada uno un
    dict con al menos `"id"` y `"hash_normalizado"`; `"embedding"` es
    opcional por entrada (una fila sin embedding aún calculado simplemente no
    entra en la comparación de similitud, no rompe la decisión).

    Orden hash → embedding (ver docstring del módulo): si algún candidato
    tiene el mismo `hash_normalizado`, decide IGUAL sin mirar embeddings. Si
    no, compara `embedding` contra el de cada candidato que sí tenga uno y se
    queda con la máxima similitud; por encima de `UMBRAL_SIMILITUD_CAMBIADO`
    decide CAMBIADO (referenciando ese candidato), si no, NUEVO.
    """
    hash_normalizado = calcular_hash_normalizado(texto_normalizado)

    for candidato in candidatos:
        if candidato.get("hash_normalizado") == hash_normalizado:
            return ResultadoReconciliacion(
                decision="IGUAL",
                hash_normalizado=hash_normalizado,
                embedding=embedding,
                chiste_id=candidato["id"],
                similitud=1.0,
            )

    mejor_candidato: Optional[dict] = None
    mejor_similitud = -1.0
    for candidato in candidatos:
        embedding_candidato = candidato.get("embedding")
        if not embedding_candidato:
            continue
        similitud = similitud_coseno(embedding, embedding_candidato)
        if similitud > mejor_similitud:
            mejor_similitud = similitud
            mejor_candidato = candidato

    if mejor_candidato is not None and mejor_similitud >= UMBRAL_SIMILITUD_CAMBIADO:
        return ResultadoReconciliacion(
            decision="CAMBIADO",
            hash_normalizado=hash_normalizado,
            embedding=embedding,
            chiste_id=mejor_candidato["id"],
            similitud=mejor_similitud,
        )

    return ResultadoReconciliacion(
        decision="NUEVO",
        hash_normalizado=hash_normalizado,
        embedding=embedding,
    )


# ---------------------------------------------------------------------------
# Orquestación — única función que hace la llamada real (embeddings,
# inyectable para test sin red). Sin loop de reintento.
# ---------------------------------------------------------------------------

def reconciliar_chiste(
    texto_normalizado: str,
    candidatos: list[dict],
    *,
    generar_embedding_fn: Optional[Callable[[str], list[float]]] = None,
) -> ResultadoReconciliacion:
    """Reconcilia UN chiste entrante contra `candidatos` (§Reconciliación).

    Calcula el embedding de `texto_normalizado` (única llamada de red de
    este módulo) y delega el resto en `decidir_reconciliacion`.
    `generar_embedding_fn` es inyectable (`texto -> vector`) para testear la
    orquestación sin red (`tests/unit/jokes/test_reconciliacion.py`); por
    defecto usa `generar_embedding` real de `src/utils/llm/embeddings.py`
    (`tests/integration/test_reconciliacion_live.py`).
    """
    if not texto_normalizado or not texto_normalizado.strip():
        raise ReconciliacionError("texto_normalizado vacío: no hay nada que reconciliar")

    generar = generar_embedding_fn if generar_embedding_fn is not None else generar_embedding
    embedding = generar(texto_normalizado)

    return decidir_reconciliacion(texto_normalizado, embedding, candidatos)
