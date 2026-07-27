"""routing — paso 5b compartido B/C: aplica IGUAL/CAMBIADO/NUEVO sobre Supabase.

Contrato (`src/jokes/SPEC.md` §"Routing (IGUAL/CAMBIADO/NUEVO a Supabase)",
task 33). Extraído de forma **mecánica** de
`historico/pipeline.py::_rutear_a_supabase` (task 27) — mismo cuerpo, mismas
llamadas al `store`, mismo orden de escrituras. Lo único que cambia respecto al
original es que el `tipo_fuente` llega como parámetro (antes constante de
módulo de Flujo C) y que `inicio_localizado` ya no viaja aquí (se queda en
Flujo C, ver `historico/pipeline.py::ChisteRuteado`).

`reconciliacion.py` decide, no persiste: entrega un `ResultadoReconciliacion`
ya resuelto y este módulo se limita a aplicarlo. **No** obtiene los candidatos
ni llama a la reconciliación — eso es el paso 5a, responsabilidad de cada
caller (`store.listar_candidatos_reconciliacion(...)` + `reconciliar_chiste(...)`),
ver §"Alcance — routing.py es el paso 5b, no el 5a".

`routing.py` **no** revalida `tipo_fuente` contra el enum cerrado
(`supabase_store.TIPOS_FUENTE_CHISTE`) — ese gatekeeper sigue siendo
`supabase_store._validar_tipo_fuente_chiste` (vía `_build_chiste_payload`).
Consecuencia aceptada: un `tipo_fuente` inválido solo falla en la rama NUEVO.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.jokes.reconciliacion import ResultadoReconciliacion
from src.jokes.silver import ChisteEstructurado


@dataclass(frozen=True)
class ResultadoRuteo:
    """Resultado de rutear UN chiste a Supabase (paso 5b).

    - `decision`: `IGUAL` (dedup, no escribe) | `CAMBIADO` (nueva revisión del
      chiste existente) | `NUEVO` (inserta chiste nuevo).
    - `chiste_id`: id del chiste afectado (el existente en IGUAL/CAMBIADO, el
      recién creado en NUEVO). `None` solo si la decisión IGUAL vino de un
      candidato sin `id`.
    - `tema_id`/`tecnica_id`: IDs resueltos por `resolver_taxonomia` (loop
      P16), o `None` si se encoló un candidato para revisión humana (sin match).

    NO incluye `inicio_localizado` (bandera propia del Segmentador de
    Histórico, sin significado en Telegram) — ver §"inicio_localizado NO viaja
    al módulo compartido". Flujo C lo añade en su propia subclase
    (`historico.pipeline.ChisteRuteado`).
    """

    decision: str
    chiste_id: Optional[str]
    tema_id: Optional[int]
    tecnica_id: Optional[int]


def _estructura_revision(estructurado: ChisteEstructurado, tecnica_id: Optional[int]) -> dict:
    """`estructura_detectada` (jsonb de `chistes_revisiones`, §Storage) a partir
    de la salida de Silver (string libre) + el `tecnica_id` ya resuelto.

    Movido tal cual desde `historico/pipeline.py` (task 27) — helper privado,
    solo lo usan las ramas NUEVO/CAMBIADO de `rutear_chiste`.
    """
    return {
        "descripcion": estructurado.estructura_detectada,
        "tecnica_id": tecnica_id,
    }


def rutear_chiste(
    store: Any,
    estructurado: ChisteEstructurado,
    recon: ResultadoReconciliacion,
    *,
    tipo_fuente: str,
    tema_id: Optional[int] = None,
    tecnica_id: Optional[int] = None,
) -> ResultadoRuteo:
    """Aplica la decisión IGUAL/CAMBIADO/NUEVO sobre Supabase (paso 5b).

    - IGUAL: dedup — no escribe nada (idempotencia, §Reconciliación).
    - NUEVO: `crear_chiste` (`version_actual=1`) + `crear_revision` (v1).
    - CAMBIADO: `crear_revision` (siguiente versión, append-only) +
      `actualizar_chiste` (apunta `version_actual` a la nueva y refresca
      texto/hash/embedding/estado). Las taxonomías (`tema_id`/`tecnica_id`)
      solo se escriben si hubo match — nunca se sobrescribe un ID existente
      con `None` cuando la resolución encoló un candidato para revisión humana.

    `tema_id`/`tecnica_id` son keyword-only: evita un swap posicional
    silencioso entre dos `Optional[int]` contiguos.
    """
    if recon.decision == "IGUAL":
        return ResultadoRuteo(
            decision="IGUAL",
            chiste_id=recon.chiste_id,
            tema_id=tema_id,
            tecnica_id=tecnica_id,
        )

    if recon.decision == "NUEVO":
        fila = store.crear_chiste(
            texto_normalizado=estructurado.chiste_normalizado,
            hash_normalizado=recon.hash_normalizado,
            tipo_fuente=tipo_fuente,
            embedding=recon.embedding,
            tema_id=tema_id,
            tecnica_id=tecnica_id,
            estado=estructurado.estado,
            version_actual=1,
        )
        store.crear_revision(
            chiste_id=fila["id"],
            version=1,
            contenido=estructurado.chiste_normalizado,
            estructura_detectada=_estructura_revision(estructurado, tecnica_id),
            estado=estructurado.estado,
            sugerencias_mejora=estructurado.sugerencias_mejora,
        )
        return ResultadoRuteo(
            decision="NUEVO",
            chiste_id=fila["id"],
            tema_id=tema_id,
            tecnica_id=tecnica_id,
        )

    # CAMBIADO — nueva revisión del chiste existente que referenció la
    # reconciliación (recon.chiste_id).
    chiste_id = recon.chiste_id
    existente = store.obtener_chiste(chiste_id)
    version_previa = (existente or {}).get("version_actual") or 1
    nueva_version = version_previa + 1

    store.crear_revision(
        chiste_id=chiste_id,
        version=nueva_version,
        contenido=estructurado.chiste_normalizado,
        estructura_detectada=_estructura_revision(estructurado, tecnica_id),
        estado=estructurado.estado,
        sugerencias_mejora=estructurado.sugerencias_mejora,
    )

    campos: dict[str, Any] = {
        "texto_normalizado": estructurado.chiste_normalizado,
        "hash_normalizado": recon.hash_normalizado,
        "embedding": recon.embedding,
        "estado": estructurado.estado,
        "version_actual": nueva_version,
    }
    if tema_id is not None:
        campos["tema_id"] = tema_id
    if tecnica_id is not None:
        campos["tecnica_id"] = tecnica_id
    store.actualizar_chiste(chiste_id, **campos)

    return ResultadoRuteo(
        decision="CAMBIADO",
        chiste_id=chiste_id,
        tema_id=tema_id,
        tecnica_id=tecnica_id,
    )
