"""taxonomias — mapeo de `tema`/`estructura_detectada` (Silver) a IDs reales
de `temas`/`tecnicas` en Supabase, con loop acotado P16 (task 14).

Contrato (`src/jokes/SPEC.md` §Taxonomías, `docs/specs/llm-policy.md` §Loops
LLM): Silver (`src/jokes/silver.py`, task 13) produce `tema` y
`estructura_detectada` como STRINGS LIBRES (el LLM de Silver no conoce la
taxonomía real, solo describe lo que ve). Este módulo hace lo que Silver
explícitamente NO hace: mapear ese string libre a un `tema_id`/`tecnica_id`
EXISTENTE de las tablas `temas`/`tecnicas`, vía un loop acotado a ≤3 intentos
cuyo criterio de parada es SIEMPRE una comprobación binaria y externa al LLM
(¿el nombre/ID propuesto está de verdad en la tabla? — un `SELECT` real, no
"el LLM decide que ya está bien").

Por qué fichero nuevo y no ampliar `silver.py`/`supabase_store.py`: el
contrato de esta task es distinto del de ambos — no genera contenido nuevo
(a diferencia de Silver) ni implementa acceso a Supabase (a diferencia de
`supabase_store.py`), sino que ORQUESTA un loop de resolución que CONSUME a
los dos como dependencias (mismo reparto que `reconciliacion.py`, que también
vive aparte de `supabase_store.py` aunque lo use). Nombre `taxonomias.py`
porque es exactamente el sustantivo del §Taxonomías del SPEC que gobierna.

## Esquema de los 3 intentos (a criterio de esta implementación, documentado
para que quede explícito el porqué):

1. **Intento 1 — sin contexto de la tabla real.** Se le pasa al LLM
   ÚNICAMENTE el string libre a mapear (`tema`/`estructura_detectada`) y se le
   pide el nombre canónico que él propondría, sin ver la lista real. Es
   deliberadamente barato (menos tokens, sin inyectar toda la tabla) y cubre
   el caso frecuente de que el string libre YA coincide, literal o casi
   literal, con una fila existente (ej. Silver dijo `"setup/punchline"` y la
   tabla `tecnicas` ya tiene una fila `nombre="setup/punchline"` — no hace
   falta gastar contexto para que el LLM "adivine" lo que ya es exacto).
2. **Intento 2 — inyecta la taxonomía real completa.** Si el intento 1 no
   produjo un nombre que exista de verdad en la tabla (comprobación binaria,
   `_encontrar_match`), este intento le da al LLM la lista completa y real de
   `temas`/`tecnicas` tal cual está en Supabase en ese momento (id + nombre de
   cada fila) y le pide que elija LITERALMENTE uno de esos nombres si alguno
   corresponde semánticamente (aunque el string libre use otra palabra, ej.
   "misdirection" vs "quiebro" vs "giro" — el ejemplo exacto de `SPEC.md`), o
   que responda `SIN_MATCH` si ninguno corresponde. Nunca se le pide que
   invente una fila nueva.
3. **Intento 3 — igual que el intento 2.** Se decide (a criterio de esta
   implementación, el propio SPEC deja el detalle exacto abierto) repetir el
   mismo prompt con contexto completo en vez de variarlo: da una segunda
   oportunidad de sampleo del LLM contra la MISMA evidencia real (la tabla no
   cambia entre el intento 2 y el 3 dentro de la misma resolución), cubriendo
   el caso de que la primera respuesta con contexto fallara por una
   variación aleatoria de la generación (temperatura > 0), sin inventar un
   tercer prompt distinto que complicaría el contrato sin necesidad clara.
   Si el intento 3 tampoco produce un match real, se agotan los intentos.

Agotados los 3 intentos sin match: se encola una fila en
`candidatos_taxonomia` (vía `SupabaseStore.crear_candidato_taxonomia`, sin
reimplementar el acceso a Supabase) con `tipo`, `texto` (el string original,
sin tocar), `propuesto_por` (marca explícita de que la fila viene de este
loop, para trazabilidad) y `estado='pendiente'` (default de
`supabase_store.py`). El LLM NUNCA llega a invocar una creación de fila en
`temas`/`tecnicas` — ese INSERT no existe en este módulo ni se le da al LLM
ninguna forma de dispararlo directa o indirectamente (regla dura de
`SPEC.md` §Taxonomías: "el LLM nunca crea taxonomía autónomamente").

## Criterio de parada — pura, testeable sin red

`_encontrar_match(propuesto, tabla)` es la única función que decide "¿hay
match?" en cualquiera de los 3 intentos: no interpreta semántica, solo
compara el string propuesto por el LLM contra las filas REALES ya traídas de
Supabase (por `id` si el propuesto parece numérico, por `nombre` normalizado
en minúsculas/espacios si no) — es una comparación de forma, determinista y
sin red, exactamente el tipo de función que P16 exige como criterio de
parada (`docs/specs/llm-policy.md`: "¿el ID propuesto existe en la tabla,
sí o no? — pregunta con respuesta binaria en Supabase, no en la cabeza de
nadie"). El LLM decide QUÉ proponer; esta función decide, sin LLM de por
medio, si lo propuesto es real.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from src.jokes.supabase_store import SupabaseStore, TIPOS_CANDIDATO_TAXONOMIA
from src.utils.llm.client import generar_json

# ---------------------------------------------------------------------------
# Constantes del contrato (§Taxonomías, P16)
# ---------------------------------------------------------------------------

MAX_INTENTOS = 3

# Marca de trazabilidad en `candidatos_taxonomia.propuesto_por` — identifica
# que la fila viene de este loop automático (P16), no de una cola manual ni
# de otro caller. Inyectable por el llamador si necesita distinguir Flujo B
# de Flujo C (ver `propuesto_por` en `resolver_taxonomia`).
PROPUESTO_POR_DEFAULT = "taxonomias.resolver_taxonomia (loop P16)"

# Respuesta literal que el LLM debe usar en los intentos 2/3 cuando ninguna
# fila real corresponde — evita que "no sé" se cuele como si fuera un nombre
# propuesto real (que luego, por casualidad, podría o no matchear).
_MARCADOR_SIN_MATCH = "SIN_MATCH"


class TaxonomiasError(ValueError):
    """Error de entrada del loop de taxonomías (tipo inválido, texto vacío).

    Hereda de `ValueError`, mismo patrón que `SilverError` en `silver.py` —
    errores de contrato de entrada, no de infraestructura (esos son
    `SupabaseStoreError`/`LLMClientError`, ya definidos en sus módulos).
    """


@dataclass(frozen=True)
class ResultadoTaxonomia:
    """Resultado de `resolver_taxonomia` — o hubo match, o quedó encolado.

    Nunca ambos ni ninguno: `match=True` implica `fila` no-None y
    `candidato=None`; `match=False` implica `fila=None` y `candidato` es la
    fila creada en `candidatos_taxonomia` (nunca None en ese caso).
    """

    match: bool
    fila: Optional[dict]
    candidato: Optional[dict]
    intentos: int


# ---------------------------------------------------------------------------
# Validación de entrada — pura, sin red.
# ---------------------------------------------------------------------------

def _validar_tipo_taxonomia(tipo: str) -> str:
    """Valida `tipo` contra `TIPOS_CANDIDATO_TAXONOMIA` (reusa el enum de
    `supabase_store.py`, fuente única de verdad — no se duplica la tupla).
    """
    if tipo not in TIPOS_CANDIDATO_TAXONOMIA:
        raise TaxonomiasError(
            f"tipo inválido: {tipo!r} (permitidos: {', '.join(TIPOS_CANDIDATO_TAXONOMIA)})"
        )
    return tipo


# ---------------------------------------------------------------------------
# Criterio de parada — pura, sin red, el corazón testeable de P16 aquí.
# ---------------------------------------------------------------------------

def _normalizar_nombre(texto: str) -> str:
    """Normalización de FORMA (minúsculas, espacios colapsados), no semántica.

    Existe solo para que "Setup/Punchline" y "setup/punchline " se traten
    como el mismo nombre — sigue siendo una comparación exacta tras
    normalizar forma, no una similitud difusa (eso seguiría sin ser un
    criterio "externo y verificable" en el sentido de P16 si se dejara a
    juicio del LLM).
    """
    return " ".join(texto.strip().lower().split())


def _encontrar_match(propuesto: Optional[str], tabla: list[dict]) -> Optional[dict]:
    """¿`propuesto` corresponde a una fila EXISTENTE de `tabla`? Binario, sin red.

    `tabla` es la lista real de filas (`[{"id": ..., "nombre": ...}, ...]`)
    ya traída de Supabase por el caller (`SupabaseStore.listar_temas` /
    `listar_tecnicas`) — esta función no toca la red, solo compara.

    Dos vías de match, ambas deterministas:
    - `propuesto` parece un ID numérico (ej. el LLM devolvió `"3"`): compara
      contra `fila["id"]`.
    - si no, compara `nombre` normalizado (`_normalizar_nombre`) contra el de
      cada fila.

    Devuelve la fila completa si hay match, `None` si no lo hay (incluye
    `propuesto` vacío/None o `tabla` vacía) — nunca lanza excepción, "no hay
    match" es un resultado válido y esperado del loop, no un error.
    """
    if not propuesto:
        return None
    propuesto_str = str(propuesto).strip()
    if not propuesto_str or propuesto_str == _MARCADOR_SIN_MATCH:
        return None

    if propuesto_str.lstrip("-").isdigit():
        propuesto_id = int(propuesto_str)
        for fila in tabla:
            if fila.get("id") == propuesto_id:
                return fila
        return None

    propuesto_norm = _normalizar_nombre(propuesto_str)
    for fila in tabla:
        if _normalizar_nombre(str(fila.get("nombre", ""))) == propuesto_norm:
            return fila
    return None


# ---------------------------------------------------------------------------
# Construcción de prompt/schema por intento — puras, sin red.
# ---------------------------------------------------------------------------

def _etiqueta_tipo(tipo: str) -> str:
    return "tema" if tipo == "tema" else "técnica"


def _build_schema() -> dict:
    """`responseSchema` de Gemini — un único campo string, mismo patrón que
    `silver._build_schema` (mayúsculas por convención de la API).
    """
    return {
        "type": "OBJECT",
        "properties": {"nombre_propuesto": {"type": "STRING"}},
        "required": ["nombre_propuesto"],
    }


def _build_prompt_intento1(texto: str, tipo: str) -> str:
    """Prompt del intento 1 — SIN inyectar la tabla real (ver docstring del
    módulo, §Esquema de los 3 intentos, punto 1).
    """
    etiqueta = _etiqueta_tipo(tipo)
    return (
        f"Eres un clasificador de {etiqueta}s de stand-up comedy en español. "
        f"Te doy una etiqueta libre extraída de un chiste y debes proponer el "
        f"nombre canónico más probable de {etiqueta} al que correspondería, tal "
        "y como se guardaría en una tabla de referencia (nombre breve, sin "
        "explicación adicional). No conoces la tabla real todavía: propón tu "
        "mejor nombre canónico igualmente.\n\n"
        f"Etiqueta libre: {texto!r}"
    )


def _build_prompt_con_contexto(texto: str, tipo: str, tabla: list[dict]) -> str:
    """Prompt de los intentos 2/3 — inyecta la taxonomía real completa
    (§Esquema de los 3 intentos, puntos 2 y 3).
    """
    etiqueta = _etiqueta_tipo(tipo)
    if tabla:
        listado = "\n".join(f"- id={fila['id']}: {fila['nombre']}" for fila in tabla)
    else:
        listado = "(la tabla está vacía todavía, no hay ninguna fila real)"
    return (
        f"Eres un clasificador de {etiqueta}s de stand-up comedy en español. "
        f"Te doy una etiqueta libre extraída de un chiste y la lista REAL y "
        f"COMPLETA de {etiqueta}s que existen ahora mismo en la tabla de "
        "referencia. Si alguna fila corresponde semánticamente a la etiqueta "
        "libre (aunque use otra palabra para lo mismo, ej. \"misdirection\" vs "
        "\"quiebro\" vs \"giro\"), responde EXACTAMENTE con su nombre tal cual "
        f"aparece en la lista. Si NINGUNA fila corresponde, responde "
        f"exactamente \"{_MARCADOR_SIN_MATCH}\" — nunca propongas ni inventes "
        "una fila que no esté en la lista.\n\n"
        f"Etiqueta libre: {texto!r}\n\n"
        f"{etiqueta.capitalize()}s existentes:\n{listado}"
    )


# ---------------------------------------------------------------------------
# Acceso a la tabla real — capa fina sobre `SupabaseStore`, sin reimplementar
# el acceso a Supabase (reusa `listar_temas`/`listar_tecnicas`).
# ---------------------------------------------------------------------------

def _listar_tabla(store: SupabaseStore, tipo: str) -> list[dict]:
    if tipo == "tema":
        return store.listar_temas()
    return store.listar_tecnicas()


# ---------------------------------------------------------------------------
# Orquestación — el loop acotado P16 en sí. Única función pública.
# ---------------------------------------------------------------------------

def resolver_taxonomia(
    texto: str,
    tipo: str,
    store: SupabaseStore,
    *,
    llamar_llm: Optional[Callable[[str, dict], dict]] = None,
    propuesto_por: str = PROPUESTO_POR_DEFAULT,
) -> ResultadoTaxonomia:
    """Mapea `texto` (`tema`/`estructura_detectada` libre de Silver) a un
    `tema_id`/`tecnica_id` real, con loop acotado a `MAX_INTENTOS` (P16).

    `store` es cualquier objeto con la interfaz de `SupabaseStore`
    (`listar_temas`/`listar_tecnicas`/`crear_candidato_taxonomia`) — en
    producción, una instancia real; en tests unitarios, un doble inyectado
    (mismo patrón que `llamar_llm` inyectable de `silver.estructurar_chiste`,
    para no depender de red en la lógica de orquestación).

    `llamar_llm` por defecto es `generar_json` de `src/utils/llm/client.py`
    (NO se duplica la llamada al LLM, se reusa tal cual). La tabla real
    (`temas`/`tecnicas`) se lee UNA vez al principio (mismo estado de verdad
    para los 3 intentos dentro de esta resolución; si cambiara a mitad del
    loop sería una carrera fuera de alcance de esta task) y se usa tanto para
    construir el prompt con contexto (intentos 2/3) como para el criterio de
    parada binario (`_encontrar_match`) en los 3 intentos.

    Nunca hace más de `MAX_INTENTOS` llamadas al LLM. Si ninguna produce un
    match real, encola un candidato en `candidatos_taxonomia` vía
    `store.crear_candidato_taxonomia` (nunca crea la fila de
    `temas`/`tecnicas` directamente) y devuelve `match=False`.
    """
    _validar_tipo_taxonomia(tipo)
    if not texto or not texto.strip():
        raise TaxonomiasError("texto vacío: no hay nada que mapear a taxonomía")

    llamar = llamar_llm if llamar_llm is not None else generar_json
    schema = _build_schema()
    tabla = _listar_tabla(store, tipo)

    # Intento 1: sin contexto de la tabla real (§Esquema, punto 1).
    respuesta = llamar(_build_prompt_intento1(texto, tipo), schema)
    fila = _encontrar_match(respuesta.get("nombre_propuesto"), tabla)
    if fila is not None:
        return ResultadoTaxonomia(match=True, fila=fila, candidato=None, intentos=1)

    # Intentos 2 y 3: inyectan la taxonomía real completa (§Esquema, puntos 2-3).
    prompt_con_contexto = _build_prompt_con_contexto(texto, tipo, tabla)
    for intento in range(2, MAX_INTENTOS + 1):
        respuesta = llamar(prompt_con_contexto, schema)
        fila = _encontrar_match(respuesta.get("nombre_propuesto"), tabla)
        if fila is not None:
            return ResultadoTaxonomia(match=True, fila=fila, candidato=None, intentos=intento)

    # Agotados los MAX_INTENTOS intentos sin match real: encola para revisión
    # humana (§Taxonomías) — el LLM nunca crea la fila de temas/tecnicas.
    candidato = store.crear_candidato_taxonomia(
        tipo=tipo, texto=texto, propuesto_por=propuesto_por
    )
    return ResultadoTaxonomia(match=False, fila=None, candidato=candidato, intentos=MAX_INTENTOS)
