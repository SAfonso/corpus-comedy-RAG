"""segmentador — segmentación de chistes del Flujo C (Histórico) (task 19).

Contrato (`src/jokes/historico/SPEC.md` §Segmentador): dado el texto completo
de UN documento `.md` ya marcado por `scripts/marcar_remates.py` (task 17) con
`[REMATE]…[/REMATE]` y `[CHISTOIDE]…[/CHISTOIDE]`, parte el documento en las
unidades de chiste que luego alimentarán a Silver (`estructurar_chiste`, task
13). Este módulo NO llama a Silver ni a Reconciliación — igual que
`telegram_bot.py` (task 16) tampoco los llama: cada módulo hace su parte, el
wiring end-to-end es de un futuro script de orquestación de flujo.

## Reparto determinista vs. juicio semántico (la decisión central)

- `[REMATE]…[/REMATE]` = **fin determinista** de un chiste. El FIN de cada
  chiste NO lo decide el LLM: es el marcador literal (cierre del `[/REMATE]`),
  derivado del color del documento fuente (`marcar_remates.py`). Por tanto el
  nº de chistes de un documento = nº de `[REMATE]`, y eso es pura regex.
- Lo ÚNICO que decide el LLM es **dónde empieza el setup hacia atrás** desde
  cada remate: dentro de la "ventana candidata" (desde el fin del chiste
  anterior hasta el fin de este remate) puede sobrar cola de un párrafo
  previo, una transición tipo "Otra cosa que me jode es…", una intro que no
  es parte del chiste. El LLM descarta eso y señala el arranque real del
  setup. Es juicio semántico, sin ancla externa verificable.
- `[CHISTOIDE]…[/CHISTOIDE]` = mini-remate interno que aligera una premisa
  larga. **NO es frontera de chiste** (tratarlo como fin partiría chistes por
  la mitad): el Segmentador lo **ignora como fin** y lo **conserva como
  metadato de estructura** (`contiene_chistoide`/`chistoides`) del chiste al
  que pertenece — útil para el Silver aguas abajo.

## Por qué NO hay loop de reintento (P16)

"Dónde empieza de verdad el setup" es exactamente el ejemplo que
`docs/specs/llm-policy.md` (§Loops LLM, P16) pone de "subjetivo / sin ancla
externa → NO va en loop, va a revisión humana". No existe un criterio externo
verificable de "el setup empieza justo aquí" (a diferencia de Taxonomías —
¿el ID existe en Supabase? — o Reconciliación — ¿la similitud supera el
umbral?). Iterar el LLM contra su propia opinión no converge a una verdad,
refuerza la del primer intento. Por eso: **una sola llamada al LLM por
remate**, el resultado sale tal cual, y el control de calidad es revisión
humana muestral (no auto-convergencia). Mismo espíritu que `silver.py`.

## Diseño del prompt/schema: fragmento literal, NO offset numérico

Se le pide al LLM el **fragmento de texto literal** con el que arranca el
setup (`texto_inicio_setup`, las primeras palabras), NO un índice/offset de
carácter. Motivo: los LLM cuentan caracteres fatal — un offset numérico sería
poco fiable y, peor, difícil de validar (cualquier entero dentro de rango es
"válido" aunque caiga a mitad de palabra). Un fragmento literal, en cambio, es
**verificable de forma barata y determinista**: o aparece en la ventana
candidata (`str.find`, tolerante a diferencias de espaciado) o no aparece.
Localizado el fragmento, recortamos la ventana desde ahí. Nótese que esta
verificación NO es el "criterio de parada" de un loop P16 (no reintentamos):
es solo la frontera entre "dato usable" y "alucinación", que hay que manejar
explícitamente, no en silencio.

A diferencia de Silver —que OCULTA las etiquetas al LLM para que detecte la
estructura por su cuenta— aquí la ventana se le pasa al LLM **con** sus
etiquetas `[REMATE]`/`[CHISTOIDE]`: la tarea ES la frontera, así que saber
dónde cae el remate ayuda al modelo a razonar hacia atrás. Es una diferencia
deliberada de contrato, no un descuido.

## Qué hacemos con una alucinación del LLM (fragmento que no está literal)

Sin reintento (P16), decidimos el destino del caso en una sola pasada: si el
`texto_inicio_setup` NO se localiza en la ventana candidata (ni exacto ni con
tolerancia de espaciado), **NO se descarta el chiste ni se aborta el
documento**. Se cae a un **fallback conservador**: se toma la ventana
candidata COMPLETA como texto del chiste (mejor incluir de más una cola de
intro que perder contenido del material —que es SAGRADO— por una respuesta
mala) y se marca `inicio_localizado=False`. Ese flag es la señal explícita
para la **revisión humana muestral**: no se corrige solo, pero tampoco se
oculta. Manejo explícito, sin loop.

Las etiquetas de marcado (`[REMATE]`/`[CHISTOIDE]`) se **consumen** aquí: el
`texto` del `ChisteSegmentado` sale ya limpio y listo para Silver (Silver
mantiene `_limpiar_marcado_historico` solo como cinturón de seguridad, ver su
docstring). Se conserva `texto_marcado` (el recorte crudo, con etiquetas) para
que la revisión humana pueda inspeccionar dónde se cortó y dónde cae el remate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from src.utils.llm.client import generar_json

# ---------------------------------------------------------------------------
# Detección determinista del marcado — regex puras, sin red.
# `.*?` no-greedy + DOTALL: el marcado puede cruzar saltos de línea/párrafos
# (un span rojo que cruza párrafos es UN solo tramo, §marcar_remates).
# ---------------------------------------------------------------------------

_RE_REMATE = re.compile(r"\[REMATE\](.*?)\[/REMATE\]", re.DOTALL)
_RE_CHISTOIDE = re.compile(r"\[CHISTOIDE\](.*?)\[/CHISTOIDE\]", re.DOTALL)

# Cualquier etiqueta de marcado (apertura o cierre) — para consumirlas del
# texto final que va a Silver. Mismo patrón que `silver._limpiar_marcado_historico`.
_RE_ETIQUETAS = re.compile(r"\[/?(?:REMATE|CHISTOIDE)\]")


class SegmentadorError(ValueError):
    """Error de segmentación (respuesta del LLM mal formada).

    Hereda de `ValueError`, mismo patrón que `SilverError`
    (`src/jokes/silver.py`) y `ReconciliacionError` — permite capturarla como
    `ValueError` en el resto del contrato B/C sin perder el tipo específico
    del módulo. Ojo: una alucinación del LLM (fragmento no localizado) NO es
    un `SegmentadorError` — se maneja con fallback conservador, no con
    excepción (ver docstring del módulo). Este error se reserva para respuesta
    estructuralmente inválida (falta el campo, tipo incorrecto).
    """


@dataclass(frozen=True)
class VentanaCandidata:
    """Tramo candidato a un chiste, ya detectado de forma determinista.

    Es el resultado del parseo puro (`_construir_ventanas`), ANTES de llamar
    al LLM: `texto` es todo el texto entre el fin del chiste anterior y el fin
    de este `[REMATE]` (con las etiquetas dentro), `remate` es el contenido
    literal del `[REMATE]` que lo cierra, y `chistoides` los contenidos de los
    `[CHISTOIDE]` internos (metadato de estructura, no fronteras).
    """

    texto: str
    remate: str
    chistoides: tuple[str, ...]


@dataclass(frozen=True)
class ChisteSegmentado:
    """Un chiste ya segmentado — unidad que alimenta a Silver (task 13).

    - `texto`: texto del chiste LIMPIO (sin etiquetas de marcado), recortado
      desde donde el LLM situó el arranque del setup. Es lo que se le pasa a
      `estructurar_chiste`.
    - `remate`: contenido literal del `[REMATE]` que cierra el chiste (el fin
      determinista). Metadato de trazabilidad.
    - `texto_marcado`: el mismo recorte pero CRUDO, conservando las etiquetas
      `[REMATE]`/`[CHISTOIDE]` — para que la revisión humana muestral vea
      dónde se cortó el setup y dónde cae el remate.
    - `contiene_chistoide`: si el chiste tiene uno o más `[CHISTOIDE]` internos.
    - `chistoides`: contenidos literales de esos `[CHISTOIDE]` (estructura).
    - `inicio_localizado`: `True` si el fragmento de inicio del LLM se
      localizó en la ventana candidata; `False` si se cayó al fallback
      conservador (ventana completa) por una alucinación — bandera para la
      revisión humana (ver docstring del módulo).
    """

    texto: str
    remate: str
    texto_marcado: str
    contiene_chistoide: bool
    chistoides: tuple[str, ...]
    inicio_localizado: bool


# ---------------------------------------------------------------------------
# Construcción de ventanas candidatas — pura, sin red, testeable directamente.
# ---------------------------------------------------------------------------

def _construir_ventanas(texto_marcado: str) -> list[VentanaCandidata]:
    """Parte el documento marcado en ventanas candidatas, de forma determinista.

    Una ventana por cada `[REMATE]` (fin determinista de un chiste). La ventana
    del remate i va desde el FIN del `[/REMATE]` anterior (o el inicio del
    documento si es el primero) hasta el FIN de este `[/REMATE]`. Los
    `[CHISTOIDE]` que caen dentro de esa ventana se anexan como metadato — NO
    cortan la ventana (no son frontera). El texto que quede DESPUÉS del último
    `[REMATE]` NO genera chiste: sin remate no hay fin determinista, no es una
    unidad completa (§Segmentador).
    """
    remates = list(_RE_REMATE.finditer(texto_marcado))
    chistoides = list(_RE_CHISTOIDE.finditer(texto_marcado))

    ventanas: list[VentanaCandidata] = []
    inicio = 0
    for match_remate in remates:
        fin = match_remate.end()
        texto_ventana = texto_marcado[inicio:fin]
        chs = tuple(
            c.group(1).strip()
            for c in chistoides
            if inicio <= c.start() < fin
        )
        ventanas.append(
            VentanaCandidata(
                texto=texto_ventana,
                remate=match_remate.group(1).strip(),
                chistoides=chs,
            )
        )
        inicio = fin

    return ventanas


# ---------------------------------------------------------------------------
# Limpieza de etiquetas — pura, sin red. Consume el marcado del texto final.
# ---------------------------------------------------------------------------

def _quitar_etiquetas(texto: str) -> str:
    """Quita todas las etiquetas de marcado y normaliza los bordes.

    El texto del chiste que va a Silver no debe llevar `[REMATE]`/`[CHISTOIDE]`
    (son metadato de dónde cortar, no contenido — ver `silver.py`).
    """
    return _RE_ETIQUETAS.sub("", texto).strip()


# ---------------------------------------------------------------------------
# Localización del fragmento de inicio — pura, sin red. Es la verificación
# barata que separa "dato usable" de "alucinación" (NO un criterio de loop).
# ---------------------------------------------------------------------------

def _localizar_inicio(ventana: str, fragmento: str) -> Optional[int]:
    """Devuelve el offset donde arranca `fragmento` en `ventana`, o `None`.

    Primero intenta una coincidencia exacta (`str.find`). Si falla, reintenta
    con tolerancia a diferencias de espaciado/saltos de línea (el LLM puede
    reescribir los espacios internos del fragmento al copiarlo) construyendo un
    patrón que colapsa cualquier run de espacios en `\\s+`. Si aún así no
    aparece, devuelve `None` = alucinación (fragmento inventado que no está en
    la ventana). NO reintenta la llamada al LLM (P16): solo informa al caller,
    que decide el fallback conservador.
    """
    fragmento = fragmento.strip()
    if not fragmento:
        return None

    idx = ventana.find(fragmento)
    if idx != -1:
        return idx

    # Tolerancia a espaciado: "hola   mundo\n" ~ "hola mundo".
    patron = re.compile(r"\s+".join(re.escape(parte) for parte in fragmento.split()))
    encontrado = patron.search(ventana)
    return encontrado.start() if encontrado else None


# ---------------------------------------------------------------------------
# Construcción del prompt y del schema — puras, testeables sin red.
# ---------------------------------------------------------------------------

def _build_prompt(texto_ventana: str) -> str:
    """Prompt para que el LLM localice el ARRANQUE del setup en la ventana.

    Se le pasa la ventana CON sus etiquetas `[REMATE]`/`[CHISTOIDE]` (a
    diferencia de Silver): la tarea es la frontera, así que saber dónde cierra
    el remate ayuda a razonar hacia atrás. Se le pide devolver el fragmento
    LITERAL de arranque (no un índice), copiado tal cual de la ventana, para
    poder verificarlo con `str.find` (ver docstring del módulo).
    """
    return (
        "Eres un editor de comedia (stand-up, en español). Recibes un TRAMO de "
        "un guion que TERMINA con el remate de un chiste, marcado con "
        "[REMATE]…[/REMATE]. El tramo puede empezar con cola de un chiste "
        "anterior, una transición o una intro que NO forman parte de este "
        "chiste.\n\n"
        "Las etiquetas [CHISTOIDE]…[/CHISTOIDE] son mini-remates internos del "
        "MISMO chiste: NO son el principio ni el final, ignóralas para decidir "
        "el arranque.\n\n"
        "Tu tarea: identifica DÓNDE EMPIEZA DE VERDAD el setup de este chiste "
        "(la primera frase que ya forma parte del chiste, descartando "
        "intros/transiciones que sobran). Devuelve un JSON con el campo "
        "'texto_inicio_setup': el fragmento de texto LITERAL con el que arranca "
        "el setup, copiado EXACTAMENTE del tramo (las primeras ~5-10 palabras, "
        "sin las etiquetas). Si todo el tramo es el chiste, copia sus primeras "
        "palabras.\n\n"
        f"Tramo:\n{texto_ventana}"
    )


def _build_schema() -> dict:
    """`responseSchema` de Gemini — un solo campo string obligatorio.

    Mayúsculas por convención de la API (igual que `silver._build_schema`).
    Un único campo mantiene la respuesta simple y verificable: el contrato de
    este paso es "un fragmento literal", nada más.
    """
    return {
        "type": "OBJECT",
        "properties": {
            "texto_inicio_setup": {"type": "STRING"},
        },
        "required": ["texto_inicio_setup"],
    }


# ---------------------------------------------------------------------------
# Parseo de la respuesta ya deserializada (dict) — pura, sin red.
# ---------------------------------------------------------------------------

def _parsear_respuesta(respuesta: dict) -> str:
    """Extrae `texto_inicio_setup` de la respuesta ya deserializada.

    Lanza `SegmentadorError` si el campo falta o no es string (respuesta
    estructuralmente inválida). Un string VACÍO no es error aquí: se propaga
    tal cual y `_localizar_inicio` lo tratará como "no localizado" → fallback
    conservador (una alucinación es de negocio, no de estructura).
    """
    if "texto_inicio_setup" not in respuesta:
        raise SegmentadorError(
            "Respuesta del LLM sin 'texto_inicio_setup' "
            f"(recibido: {sorted(respuesta.keys())})"
        )
    valor = respuesta["texto_inicio_setup"]
    if not isinstance(valor, str):
        raise SegmentadorError(
            f"'texto_inicio_setup' debe ser string, recibido {type(valor).__name__}"
        )
    return valor


# ---------------------------------------------------------------------------
# Orquestación — única función pública que hace la llamada real (inyectable
# para test sin red). Una llamada al LLM por remate, sin loop de reintento.
# ---------------------------------------------------------------------------

def segmentar_documento(
    texto_marcado: str,
    *,
    llamar_llm: Optional[Callable[[str, dict], dict]] = None,
) -> list[ChisteSegmentado]:
    """Segmenta un documento marcado en chistes (§Segmentador), sin loop (P16).

    Detecta los `[REMATE]` de forma determinista (un chiste por remate) y, para
    cada uno, llama UNA vez al LLM para localizar el arranque del setup dentro
    de su ventana candidata. Un documento sin `[REMATE]` devuelve `[]` (no hay
    fin determinista de chiste que segmentar — no es un error: un `.md` puede
    legítimamente no tener marcado).

    `llamar_llm` es inyectable (`(prompt, schema) -> dict`) para testear la
    orquestación sin red (`tests/unit/jokes/historico/test_segmentador.py`);
    por defecto usa `generar_json` de `src/utils/llm/client.py` contra Gemini
    (`tests/integration/test_segmentador_live.py`). No hay reintento: si el LLM
    alucina un fragmento que no está en la ventana, se cae al fallback
    conservador (ventana completa + `inicio_localizado=False`), NO se reintenta
    ni se descarta el chiste (ver docstring del módulo).
    """
    ventanas = _construir_ventanas(texto_marcado)
    if not ventanas:
        return []

    llamar = llamar_llm if llamar_llm is not None else generar_json
    schema = _build_schema()

    resultados: list[ChisteSegmentado] = []
    for ventana in ventanas:
        prompt = _build_prompt(ventana.texto)
        respuesta = llamar(prompt, schema)
        fragmento = _parsear_respuesta(respuesta)

        offset = _localizar_inicio(ventana.texto, fragmento)
        if offset is None:
            recorte = ventana.texto  # fallback conservador: no perder contenido
            inicio_localizado = False
        else:
            recorte = ventana.texto[offset:]
            inicio_localizado = True

        resultados.append(
            ChisteSegmentado(
                texto=_quitar_etiquetas(recorte),
                remate=ventana.remate,
                texto_marcado=recorte.strip(),
                contiene_chistoide=bool(ventana.chistoides),
                chistoides=ventana.chistoides,
                inicio_localizado=inicio_localizado,
            )
        )

    return resultados
