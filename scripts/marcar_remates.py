"""marcar_remates — Flujo C (Histórico), preprocesado de marcado por color.

Contrato completo en `src/jokes/historico/SPEC.md` §"Preprocesado de marcado
(`scripts/marcar_remates.py`)`". Migración TDD del prototipo YA VALIDADO en
`notebooks/marcar_remates_colab.ipynb` sobre documentos reales del histórico
(P15, 2026-07-06) — la lógica de clasificación/fusión/emisión/validación es
la misma; este fichero solo la empaqueta como script de `scripts/` con una
CLI mínima.

Resumen del contrato:
- Lee el `.docx` **original con estilos** (nunca un `.md` ya convertido:
  Markdown no conserva color). El color de fuente vive a nivel de *run* en
  el XML crudo de `word/document.xml` — se opera sobre ese XML directamente
  (no con la API de alto nivel de `python-docx`) porque el iterador ingenuo
  de esa librería no expone corrida por corrida el contenido de tablas,
  hyperlinks ni listas de la misma forma fiable que iterar el XML.
- Mapa de color por TONO con margen (no hex exacto): rojo puro (`#FF0000`
  y vecinos) → `[REMATE]…[/REMATE]` (cierra el chiste); burdeos (`#980000`
  y vecinos) → `[CHISTOIDE]…[/CHISTOIDE]` (NO cierra, mini-remate interno).
  Cualquier otro color = texto normal sin etiquetar.
- Runs contiguos del mismo color se fusionan en un span; un span que cruza
  párrafos es un solo tramo; las etiquetas no se solapan; espacios y
  puntuación quedan fuera de las etiquetas.
- Validación round-trip OBLIGATORIA: nº de caracteres alfanuméricos de cada
  color en el `.docx` debe coincidir exactamente con los que quedan dentro
  de la etiqueta correspondiente en el `.md` de salida. Si no cuadra
  (típicamente runs perdidos en tablas/hyperlinks), el marcado **falla** y
  NO se escribe el `.md`.
- Automático, determinista, SIN LLM, desacoplado del resto del pipeline.
  Nunca modifica el `.docx` original (material sagrado, ver CLAUDE.md).

Limitación documentada (heredada del notebook): se lee el color directo del
run (`w:rPr/w:color`); colores heredados de estilos de párrafo/tema (sin
`w:color` explícito en el run) no se consideran — no aparecen en los
documentos reales del histórico usados para validar el prototipo.
"""
from __future__ import annotations

import argparse
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# --- Namespace WordprocessingML ---
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# --- Clasificación de color por tono (margen, no igualdad exacta de hex) ---
UMBRAL_GB = 90  # G y B por debajo de esto -> candidato a rojo
UMBRAL_ROJO_MIN = 120  # R minimo para considerarse rojo
UMBRAL_ROJO_PURO = 200  # R >= esto -> REMATE (#FF0000); si no -> CHISTOIDE (#980000)

# Caracteres que quedan FUERA de las etiquetas cuando caen en el borde de un span.
BORDES = " \t\n.,;:!?…·—–-\"'“”‘’«»()[]{}¡¿"

Token = tuple  # (etiqueta: Optional[str], texto: str)
Parrafo = list  # list[Token]

ETIQUETAS_VALIDAS = ("REMATE", "CHISTOIDE")


def clasificar_color(hex_val: Optional[str]) -> Optional[str]:
    """Clasifica un color de fuente por tono: 'REMATE' | 'CHISTOIDE' | None.

    #FF0000 (rojo puro)  -> REMATE     (cierra el chiste)
    #980000 (burdeos)    -> CHISTOIDE  (mini-remate interno, no es frontera)
    Cualquier otro color -> None       (texto normal)
    """
    if not hex_val or hex_val.lower() == "auto":
        return None
    try:
        r = int(hex_val[0:2], 16)
        g = int(hex_val[2:4], 16)
        b = int(hex_val[4:6], 16)
    except ValueError:
        return None
    if g < UMBRAL_GB and b < UMBRAL_GB and r >= UMBRAL_ROJO_MIN:
        return "REMATE" if r >= UMBRAL_ROJO_PURO else "CHISTOIDE"
    return None


def extraer_parrafos(ruta_docx: Path | str) -> list[Parrafo]:
    """Lee `word/document.xml` y devuelve una lista de párrafos.

    Cada párrafo es una lista de tokens `(etiqueta, texto)`, donde `etiqueta`
    es `'REMATE' | 'CHISTOIDE' | None`. Recorre TODOS los párrafos del
    documento (incluidos los de dentro de tablas, `root.iter` no distingue
    contenedor) y TODOS los runs de cada párrafo (incluidos los de dentro de
    hyperlinks e ítems de lista, por el mismo motivo: se itera el XML crudo
    en vez de depender del iterador de alto nivel de `python-docx`, que no
    garantiza cubrir esos casos). `w:tab` -> tabulador, `w:br` -> salto.
    """
    with zipfile.ZipFile(ruta_docx) as z:
        root = ET.fromstring(z.read("word/document.xml"))

    parrafos: list[Parrafo] = []
    for p in root.iter(f"{W}p"):  # incluye párrafos dentro de w:tbl
        tokens: Parrafo = []
        for r in p.iter(f"{W}r"):  # incluye runs dentro de w:hyperlink
            color = None
            rpr = r.find(f"{W}rPr")
            if rpr is not None:
                c = rpr.find(f"{W}color")
                if c is not None:
                    color = c.get(f"{W}val")
            partes = []
            for hijo in r:
                if hijo.tag == f"{W}t":
                    partes.append(hijo.text or "")
                elif hijo.tag == f"{W}tab":
                    partes.append("\t")
                elif hijo.tag == f"{W}br":
                    partes.append("\n")
            texto = "".join(partes)
            if texto:
                tokens.append((clasificar_color(color), texto))
        parrafos.append(tokens)
    return parrafos


def fusionar_tokens(tokens: list[Token]) -> list[list]:
    """Fusiona tokens contiguos con la misma etiqueta en un solo segmento.

    También absorbe separadores de solo-espacios entre dos tramos iguales:
    `[REMATE]('toma') + (' ') + [REMATE]('ya')` -> `[REMATE]('toma ya')`.
    """
    segs: list[list] = []
    for et, tx in tokens:
        if segs and segs[-1][0] == et:
            segs[-1][1] += tx
        else:
            segs.append([et, tx])
    i = 0
    while i + 2 < len(segs):
        a, b, c = segs[i], segs[i + 1], segs[i + 2]
        if a[0] is not None and a[0] == c[0] and b[0] is None and not b[1].strip():
            a[1] += b[1] + c[1]
            del segs[i + 1 : i + 3]
        else:
            i += 1
    return segs


def emitir_span(etiqueta: str, texto: str) -> str:
    """Envuelve texto en `[ETIQUETA]...[/ETIQUETA]` dejando fuera los
    espacios y la puntuación de los bordes. Si el núcleo queda vacío, no
    etiqueta."""
    ini, fin = 0, len(texto)
    while ini < fin and texto[ini] in BORDES:
        ini += 1
    while fin > ini and texto[fin - 1] in BORDES:
        fin -= 1
    nucleo = texto[ini:fin]
    if not nucleo:
        return texto
    return f"{texto[:ini]}[{etiqueta}]{nucleo}[/{etiqueta}]{texto[fin:]}"


def construir_markdown(parrafos: list[Parrafo]) -> str:
    """Serializa los párrafos a Markdown con las etiquetas embebidas.

    Un span del mismo color que termina un párrafo y abre el siguiente se
    mantiene como UN SOLO tramo (el salto de párrafo queda dentro). Un
    párrafo vacío entre medias SI cierra el span (decisión determinista)."""
    plano: list[list] = []
    for tokens in parrafos:
        segs = fusionar_tokens(tokens)
        if not any(tx.strip() for _, tx in segs):
            segs = []  # parrafo vacio
        if plano:
            plano.append([None, "\n\n"])  # separador de parrafo
        plano.extend(segs)

    # span multi-parrafo: [T] + separador + [T] -> un solo segmento
    i = 0
    while i + 2 < len(plano):
        a, b, c = plano[i], plano[i + 1], plano[i + 2]
        if a[0] is not None and a[0] == c[0] and b[0] is None and not b[1].strip():
            a[1] += b[1] + c[1]
            del plano[i + 1 : i + 3]
        else:
            i += 1

    md = "".join(emitir_span(et, tx) if et else tx for et, tx in plano)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md


def contar_alfanum(texto: str) -> int:
    return len(re.findall(r"\w", texto))


def validar_roundtrip(
    parrafos: list[Parrafo], md: str
) -> tuple[bool, dict[str, int], dict[str, int]]:
    """Guardia de integridad: los caracteres alfanuméricos de cada color en
    el `.docx` (derivados de `parrafos`, la extracción cruda) deben ser
    EXACTAMENTE los que quedaron dentro de su etiqueta en el `.md` generado.
    (La puntuación/espacios movidos fuera de la etiqueta no son
    alfanuméricos, no afectan al recuento.)
    """
    esperado = {et: 0 for et in ETIQUETAS_VALIDAS}
    for tokens in parrafos:
        for et, tx in tokens:
            if et in esperado:
                esperado[et] += contar_alfanum(tx)
    obtenido = {}
    for et in esperado:
        dentro = re.findall(rf"\[{et}\](.*?)\[/{et}\]", md, re.DOTALL)
        obtenido[et] = contar_alfanum("".join(dentro))
    return esperado == obtenido, esperado, obtenido


def _marcar_con_recuento(ruta_docx: Path | str) -> tuple[str, dict[str, int]]:
    """Implementación compartida: extrae, construye y valida round-trip.

    Devuelve `(md, esperado)`. Lanza `ValueError` si la validación
    round-trip falla — el llamador no debe escribir ni considerar válido
    ningún resultado en ese caso.
    """
    parrafos = extraer_parrafos(ruta_docx)
    md = construir_markdown(parrafos)
    ok, esperado, obtenido = validar_roundtrip(parrafos, md)
    if not ok:
        raise ValueError(
            f"round-trip FALLIDO (esperado {esperado}, obtenido {obtenido}) "
            f"- posible run perdido; no se escribe el .md"
        )
    return md, esperado


def marcar_remates(ruta_docx: Path | str) -> str:
    """Procesa un `.docx` y devuelve el Markdown con etiquetas embebidas.

    No escribe nada a disco (eso es responsabilidad de `procesar_docx`). El
    `.docx` original solo se LEE, nunca se modifica. Lanza `ValueError` si la
    validación round-trip falla (posible run perdido, p.ej. en tablas o
    hyperlinks) — en ese caso el llamador no debe considerar válido el
    resultado.
    """
    md, _ = _marcar_con_recuento(ruta_docx)
    return md


def procesar_docx(
    ruta_docx: Path | str, carpeta_salida: Path | str, sobrescribir: bool = True
) -> tuple[Path, str]:
    """Procesa un `.docx` -> `.md` con etiquetas. El original NUNCA se toca.

    Si la validación round-trip falla, lanza `ValueError` y NO escribe
    salida (ni siquiera parcial).
    """
    ruta_docx = Path(ruta_docx)
    ruta_md = Path(carpeta_salida) / (ruta_docx.stem + ".md")
    if ruta_md.exists() and not sobrescribir:
        return ruta_md, "omitido (ya existe)"

    md, esperado = _marcar_con_recuento(ruta_docx)  # puede lanzar ValueError
    ruta_md.write_text(md, encoding="utf-8")
    return ruta_md, f"OK - REMATE {esperado['REMATE']} chars, CHISTOIDE {esperado['CHISTOIDE']} chars"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Marca remates/chistoides por color en .docx del histórico -> .md con etiquetas."
    )
    parser.add_argument("carpeta_docs", type=Path, help="Carpeta con los .docx de entrada")
    parser.add_argument(
        "carpeta_salida",
        type=Path,
        nargs="?",
        default=None,
        help="Carpeta de salida para los .md (por defecto: <carpeta_docs>/Markdown)",
    )
    parser.add_argument(
        "--no-sobrescribir",
        action="store_true",
        help="No regenerar .md ya existentes",
    )
    args = parser.parse_args(argv)

    carpeta_salida = args.carpeta_salida or (args.carpeta_docs / "Markdown")
    os.makedirs(carpeta_salida, exist_ok=True)

    for archivo in sorted(os.listdir(args.carpeta_docs)):
        if not archivo.lower().endswith(".docx") or archivo.startswith("~$"):
            continue
        ruta = args.carpeta_docs / archivo
        print(f"\n--- Procesando: {archivo} ---")
        try:
            ruta_md, msg = procesar_docx(
                ruta, carpeta_salida, sobrescribir=not args.no_sobrescribir
            )
            print(f"OK {ruta_md.name}: {msg}")
        except Exception as e:  # noqa: BLE001 - script de línea de comandos
            print(f"ERROR procesando {archivo}: {e}")

    print("\n--- PROCESO FINALIZADO ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
