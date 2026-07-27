"""pipeline — Flujo C (Histórico), módulo orquestador importable (task 27).

Contrato (`src/jokes/historico/SPEC.md` §"Entrada y etapas" 0-5,
`src/jokes/SPEC.md` §Silver/§Taxonomías/§Reconciliación, `CHECKPOINTS.md`):
encadena los componentes YA implementados y testeados del Flujo C, cada uno
con su propia responsabilidad y sus propios tests unitarios — este módulo NO
reimplementa ninguna etapa, solo las conecta y decide el routing final a
Supabase:

    DriveSource.sync() -> [.docx staged]
      -> marcar_remates.procesar_docx() -> [.md marcado]
      -> Loader.load() -> [documento .md]
      -> segmentar_documento() -> [ChisteSegmentado, ...]  (1 por [REMATE])
      -> estructurar_chiste() (Silver)
      -> resolver_taxonomia() (tema + técnica, loop P16)
      -> reconciliar_chiste()  (IGUAL/CAMBIADO/NUEVO)
      -> routing a Supabase (dedup / nueva revisión / inserta)

Análogo estructural a `src/theory/pipeline.py` (Flujo A, task 22): dataclasses
de resultado, reanudación por ESTADO PERSISTIDO y manejo de fallos parciales
sin abortar el batch entero — adaptado a las etapas de Histórico y a la
granularidad de reanudación **por DOCUMENTO** (un documento `.md` contiene
varios chistes, a diferencia de Flujo A donde la unidad es el fichero).

## Dónde encaja el gate de coste (`coste.py`, task 26) — NO va aquí

`historico/coste.py` es un **dry-run previo** (estimación de tokens + gate de
presupuesto) que su propio docstring sitúa explícitamente *antes* del run
completo, invocado por `scripts/run_historico.py` (task 28): *"para que el
futuro orquestador (`historico/pipeline.py`, task 27, invocado desde
`scripts/run_historico.py`, task 28) pueda abortar ANTES de gastar dinero de
verdad"*. El título de la task 27 enumera las etapas 0-5 (drive→marcar→Loader→
Segmentador→Silver→taxonomía→reconciliación→routing) y NO incluye el gate; la
lista de etapas del SPEC (§"Entrada y etapas") tampoco. Por eso el gate vive
en el **caller** (task 28, `--dry-run` + exit code 2 "abort-por-coste"), no
dentro de `run_historico_pipeline`: este módulo asume que la decisión de
gastar ya se tomó aguas arriba. Mantenerlos separados respeta además el
reparto del propio `coste.py` ("NO ejecuta el run — solo estima y decide").

## Reanudación por documento — se apoya en la idempotencia en capas ya existente

`src/jokes/historico/SPEC.md` §"Idempotencia en capas" define tres capas
independientes, cada una con su propio estado; este orquestador **no inventa
una cuarta**, solo las encadena y resuelve el único hueco que ninguna capa
cubre sola: qué pasa cuando una etapa falla a MITAD de un documento.

| Capa | ¿Qué salta? | Clave | Estado |
|------|-------------|-------|--------|
| DriveSource | ¿qué `.docx` descargar? | `fileId`+`modifiedTime` | `drive_state_path` |
| marcar_remates | ¿qué `.md` regenerar? | existencia del `.md` | el `.md` en disco |
| Loader | ¿qué `.md` procesar? | MD5 del `.md` | `loader_state_path` |

El Loader es la capa que decide qué documentos entran a la cadena cara
(Segmentador→Silver→Reconciliación→Supabase), así que es ahí donde importa la
reanudación. Fricción idéntica a la de Flujo A (`src/theory/pipeline.py`
§"DriveMonitor marca visto en scan()"): `Loader.load()` persiste el MD5 de
cada documento nuevo/modificado en `loader_state_path` ANTES de que las etapas
aguas abajo terminen. Si un documento falla en Silver/Reconciliación, quedaría
marcado como "hecho" sin haberlo completado. Solución (sin tocar `loader.py`,
solo su fichero de estado — contrato externo, mismo formato `{name: {"md5":…}}`):

1. Se carga el estado ya comprometido (`estado_comprometido`) antes de nada.
2. `Loader.load()` devuelve los documentos pendientes y escribe su MD5 en
   disco (escritura prematura desde el punto de vista de este módulo).
3. Se REVIERTE el fichero a `estado_comprometido` (deshace la escritura).
4. Cada documento se procesa de forma INDEPENDIENTE (try/except): si falla en
   cualquier etapa (segmentación, Silver, taxonomía, reconciliación o el
   propio INSERT), su MD5 NO se compromete — vuelve a estar pendiente en el
   siguiente run, y el documento entero se reintenta desde el principio de su
   cadena. Si TODOS sus chistes se rutean con éxito, solo ENTONCES se añade su
   MD5 (ya calculado por el Loader, sin recalcular) a `estado_comprometido`.
5. Se escribe `estado_comprometido` a disco — el commit final.

**"Ningún chiste a medias en Supabase":** la unidad atómica es el CHISTE
(segmentar→Silver→taxonomía→reconciliación→routing). Si un documento con 3
chistes falla en el 2º, el documento no se marca hecho y se reintenta entero;
el chiste 1, ya insertado en este run, se reprocesa en el siguiente y su hash
coincide con su propia fila → decisión IGUAL → dedup, NO se reinserta (ver
`src/jokes/SPEC.md` §Reconciliación, "Sin auto-exclusión" — es el resultado
idempotente deseado, no un duplicado). Por eso no hace falta una transacción
multi-chiste: la propia reconciliación por hash hace el replay seguro.

**DriveSource conserva su semántica propia:** su capa commitea el
`modifiedTime` al descargar (igual que `DriveMonitor.scan()` de Flujo A), así
que un `.docx` cuyo marcado (`marcar_remates`) falle por round-trip NO se
re-descarga automáticamente hasta que cambie en Drive — correcto: un fallo de
round-trip es determinista (reintentar los mismos bytes falla igual) y señala
un `.docx` fuente que hay que corregir en Drive, lo que cambiaría su
`modifiedTime` y forzaría la re-descarga (ver `SPEC.md` §Riesgos). Se reporta
en `ResultadoHistorico.marcado_fallidos` para que el operador lo vea, no se
oculta.

Inyección de dependencias (mismo patrón que `scripts/run_pipeline.py` de Flujo
A con `run_pipeline_fn` inyectable): `drive_source`, `store` y las funciones de
etapa (`procesar_docx_fn`, `segmentar_fn`, `estructurar_fn`,
`resolver_taxonomia_fn`, `reconciliar_fn`) se inyectan para testear sin red
(sin Gemini, sin Drive, sin Supabase). `Loader` y `marcar_remates` son puros
(sin red) y se usan reales tanto en producción como en tests. No se toca
ninguno de los módulos encadenados (scope de esta tarea, ver `CLAUDE.md`).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.marcar_remates import procesar_docx
from src.jokes.historico.loader import Loader
from src.jokes.reconciliacion import ResultadoReconciliacion, reconciliar_chiste
from src.jokes.routing import ResultadoRuteo, rutear_chiste
from src.jokes.silver import ChisteEstructurado, estructurar_chiste
from src.jokes.historico.segmentador import ChisteSegmentado, segmentar_documento
from src.jokes.taxonomias import ResultadoTaxonomia, resolver_taxonomia

# `tipo_fuente` de este flujo (enum cerrado en `supabase_store.TIPOS_FUENTE_CHISTE`,
# ver `src/jokes/historico/SPEC.md` §Etapas 5 y `src/jokes/SPEC.md` §Storage).
TIPO_FUENTE = "propio_historico"

# Rutas por defecto, relativas al directorio de trabajo (mismo criterio que
# `src/theory/pipeline.py` y `scripts/validate_corpus.py`: se asume invocación
# desde la raíz del repo). El staging y los `.md` son caché reconstruible (NO
# material sagrado — lo sagrado es el original en Drive y la capa Bronze de
# Supabase, ver `SPEC.md` §"Fuente de entrada").
STAGING_DIR_POR_DEFECTO = Path("data/staging/historico/docx")
CARPETA_MD_POR_DEFECTO = Path("data/staging/historico/markdown")
DRIVE_STATE_POR_DEFECTO = Path("data/state/historico_drive.json")
LOADER_STATE_POR_DEFECTO = Path("data/state/historico_loader.json")

ENV_FOLDER_ID = "DRIVE_FOLDER_ID_HISTORICO"


@dataclass(frozen=True)
class ChisteRuteado(ResultadoRuteo):
    """Resultado de rutear UN chiste a Supabase (etapa 5).

    Subclase de `src.jokes.routing.ResultadoRuteo` (§"routing.py" en
    `src/jokes/SPEC.md`, task 34) — los 4 campos genéricos (`decision`,
    `chiste_id`, `tema_id`, `tecnica_id`) viven en la clase base y no pueden
    derivar entre Flujo B y C; `inicio_localizado` es exclusivo de Histórico
    (bandera del Segmentador — fallback conservador si el LLM alucinó el
    arranque del setup, ver `segmentador.py`) y se propaga para la revisión
    humana muestral. Telegram no tiene Segmentador, así que este 5º campo no
    viaja al módulo compartido (ver §"inicio_localizado NO viaja al módulo
    compartido").
    """

    inicio_localizado: bool


@dataclass
class ResultadoDocumento:
    """Resultado de procesar UN documento `.md` (0..N chistes).

    `error` es `None` si el documento se completó entero (todos sus chistes
    ruteados) y quedó comprometido en `loader_state_path`. Si no es `None`, el
    documento falló en alguna etapa y NO se comprometió — se reintenta entero
    en el siguiente run.
    """

    name: str
    chistes: list[ChisteRuteado] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class FalloMarcado:
    """Un `.docx` staged que falló en `marcar_remates` (round-trip, ver módulo)."""

    docx: str
    error: str


@dataclass
class ResultadoHistorico:
    """Resultado agregado de una llamada a `run_historico_pipeline`.

    - `descargados`: nombres de los `.docx` nuevos/modificados que
      `DriveSource.sync()` bajó a staging en este run.
    - `documentos`: un `ResultadoDocumento` por cada `.md` que el Loader dio
      como pendiente (nuevo/modificado). Los que `ok` completaron; los que no,
      llevan `error` y se reintentan.
    - `marcado_fallidos`: `.docx` que fallaron el marcado por color (no
      llegaron a producir `.md`).
    """

    descargados: list[str] = field(default_factory=list)
    documentos: list[ResultadoDocumento] = field(default_factory=list)
    marcado_fallidos: list[FalloMarcado] = field(default_factory=list)

    @property
    def procesados(self) -> list[str]:
        """Nombres de los `.md` completados y comprometidos en este run."""
        return [d.name for d in self.documentos if d.ok]

    @property
    def fallidos(self) -> list[ResultadoDocumento]:
        """Documentos `.md` que fallaron a mitad (no comprometidos)."""
        return [d for d in self.documentos if not d.ok]

    @property
    def chistes_ruteados(self) -> list[ChisteRuteado]:
        """Todos los chistes ruteados con éxito, en todos los documentos ok."""
        return [c for d in self.documentos if d.ok for c in d.chistes]

    @property
    def ok(self) -> bool:
        """True si no hubo ningún fallo (documento ni marcado)."""
        return not self.fallidos and not self.marcado_fallidos


# ---------------------------------------------------------------------------
# Estado de idempotencia del Loader — mismo formato/contrato que `loader.py`
# (`{name: {"md5": …}}`), reimplementado aquí (no importado) porque son
# métodos privados de otra clase, no su interfaz pública. Mismo criterio que
# `src/theory/pipeline.py` con `processed_files.json`.
# ---------------------------------------------------------------------------

def _cargar_estado(ruta_estado: Path) -> dict:
    if not ruta_estado.exists():
        return {}
    return json.loads(ruta_estado.read_text(encoding="utf-8"))


def _guardar_estado(ruta_estado: Path, estado: dict) -> None:
    ruta_estado.parent.mkdir(parents=True, exist_ok=True)
    ruta_estado.write_text(
        json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Etapa 5 — routing de UN chiste a Supabase según la decisión de reconciliación.
# `reconciliacion.py` "decide, no persiste" (su docstring): el INSERT/UPDATE es
# responsabilidad de este orquestador (caller), vía `supabase_store.py`.
#
# El cuerpo (3 ramas + `_estructura_revision`) vive ahora en el módulo
# compartido `src/jokes/routing.py` (task 34, `rutear_chiste`) — este helper
# solo delega y reañade `inicio_localizado`, que no viaja al contrato
# compartido (ver `src/jokes/SPEC.md` §"inicio_localizado NO viaja al módulo
# compartido").
# ---------------------------------------------------------------------------

def _rutear_a_supabase(
    store: Any,
    estructurado: ChisteEstructurado,
    recon: ResultadoReconciliacion,
    tema_id: Optional[int],
    tecnica_id: Optional[int],
    inicio_localizado: bool,
) -> ChisteRuteado:
    """Aplica la decisión IGUAL/CAMBIADO/NUEVO sobre Supabase (etapa 5).

    Delega en `routing.rutear_chiste` (comportamiento idéntico, ver su
    docstring) con `tipo_fuente=TIPO_FUENTE` ("propio_historico") y envuelve
    el `ResultadoRuteo` devuelto en `ChisteRuteado`, añadiendo
    `inicio_localizado` (exclusivo de Histórico).
    """
    resultado = rutear_chiste(
        store, estructurado, recon,
        tipo_fuente=TIPO_FUENTE, tema_id=tema_id, tecnica_id=tecnica_id,
    )
    return ChisteRuteado(
        decision=resultado.decision,
        chiste_id=resultado.chiste_id,
        tema_id=resultado.tema_id,
        tecnica_id=resultado.tecnica_id,
        inicio_localizado=inicio_localizado,
    )


# ---------------------------------------------------------------------------
# Etapas 3-5 sobre UN chiste segmentado — Silver -> taxonomía -> reconciliación
# -> routing. Deja propagar cualquier excepción: el llamador decide (no marca
# el documento como procesado, lo reintenta entero).
# ---------------------------------------------------------------------------

def _procesar_chiste(
    chiste: ChisteSegmentado,
    store: Any,
    *,
    estructurar_fn: Callable[..., ChisteEstructurado],
    resolver_taxonomia_fn: Callable[..., ResultadoTaxonomia],
    reconciliar_fn: Callable[..., ResultadoReconciliacion],
    llamar_llm: Optional[Callable[[str, dict], dict]],
    generar_embedding_fn: Optional[Callable[[str], list]],
) -> ChisteRuteado:
    # Etapa 4 — Silver: estructura el chiste (5 campos, §Silver).
    estructurado = estructurar_fn(chiste.texto, llamar_llm=llamar_llm)

    # Etapa 4b — Taxonomías: mapea tema/técnica a IDs reales (loop P16, ≤3).
    res_tema = resolver_taxonomia_fn(
        estructurado.tema, "tema", store, llamar_llm=llamar_llm
    )
    res_tecnica = resolver_taxonomia_fn(
        estructurado.estructura_detectada, "tecnica", store, llamar_llm=llamar_llm
    )
    tema_id = res_tema.fila["id"] if res_tema.match and res_tema.fila else None
    tecnica_id = res_tecnica.fila["id"] if res_tecnica.match and res_tecnica.fila else None

    # Etapa 5a — Reconciliación: IGUAL/CAMBIADO/NUEVO contra `propio_historico`.
    candidatos = store.listar_candidatos_reconciliacion(TIPO_FUENTE)
    recon = reconciliar_fn(
        estructurado.chiste_normalizado,
        candidatos,
        generar_embedding_fn=generar_embedding_fn,
    )

    # Etapa 5b — routing a Supabase según la decisión.
    return _rutear_a_supabase(
        store, estructurado, recon, tema_id, tecnica_id, chiste.inicio_localizado
    )


# ---------------------------------------------------------------------------
# Orquestación — única función pública. Entrada del Flujo C end-to-end
# (`scripts/run_historico.py`, task 28, la invocará tras el gate de coste).
# ---------------------------------------------------------------------------

def run_historico_pipeline(
    *,
    folder_id: Optional[str] = None,
    staging_dir: Optional[Path] = None,
    carpeta_md: Optional[Path] = None,
    drive_state_path: Optional[Path] = None,
    loader_state_path: Optional[Path] = None,
    store: Optional[Any] = None,
    drive_source: Optional[Any] = None,
    procesar_docx_fn: Callable[..., tuple] = procesar_docx,
    segmentar_fn: Callable[..., list] = segmentar_documento,
    estructurar_fn: Callable[..., ChisteEstructurado] = estructurar_chiste,
    resolver_taxonomia_fn: Callable[..., ResultadoTaxonomia] = resolver_taxonomia,
    reconciliar_fn: Callable[..., ResultadoReconciliacion] = reconciliar_chiste,
    llamar_llm: Optional[Callable[[str, dict], dict]] = None,
    generar_embedding_fn: Optional[Callable[[str], list]] = None,
) -> ResultadoHistorico:
    """Ejecuta un run completo del Flujo C (Histórico) sobre los documentos
    nuevos/modificados (§"Entrada y etapas" 0-5).

    Etapas encadenadas (ver docstring del módulo): DriveSource -> marcar_remates
    -> Loader -> Segmentador -> Silver -> taxonomías -> reconciliación -> routing
    a Supabase. Reanudación por DOCUMENTO: un `.md` solo se marca hecho (MD5 en
    `loader_state_path`) tras rutear TODOS sus chistes con éxito; si falla a
    mitad, se reintenta entero en el siguiente run (los chistes ya insertados
    deduplican por hash, decisión IGUAL — nunca se reinsertan).

    Inyección (mismo patrón que `scripts/run_pipeline.py` de Flujo A):
    - `drive_source`: objeto con `.sync() -> list[Path]`. Si es `None`, se
      construye un `DriveSource` real desde `folder_id`
      (`DRIVE_FOLDER_ID_HISTORICO` por defecto), `staging_dir` y
      `drive_state_path`. En tests se inyecta un doble sin red.
    - `store`: objeto con la interfaz de `SupabaseStore`. Si es `None`, se
      construye uno real (requiere `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`).
    - `procesar_docx_fn`/`segmentar_fn`/`estructurar_fn`/`resolver_taxonomia_fn`/
      `reconciliar_fn`: las etapas, inyectables (por defecto las reales). `Loader`
      y `marcar_remates` son puros (sin red) y se usan reales.
    - `llamar_llm`/`generar_embedding_fn`: se pasan tal cual a las etapas LLM/
      embeddings (por defecto `None` = clientes reales de `src/utils/llm/`).

    NO incluye el gate de coste (`coste.py`, task 26): es un dry-run previo del
    caller (task 28), ver docstring del módulo, §"Dónde encaja el gate de coste".
    """
    staging_dir = Path(staging_dir) if staging_dir is not None else STAGING_DIR_POR_DEFECTO
    carpeta_md = Path(carpeta_md) if carpeta_md is not None else CARPETA_MD_POR_DEFECTO
    drive_state_path = (
        Path(drive_state_path) if drive_state_path is not None else DRIVE_STATE_POR_DEFECTO
    )
    loader_state_path = (
        Path(loader_state_path) if loader_state_path is not None else LOADER_STATE_POR_DEFECTO
    )
    carpeta_md.mkdir(parents=True, exist_ok=True)

    if drive_source is None:
        from src.jokes.historico.drive_source import DriveSource

        folder_id = folder_id or os.environ.get(ENV_FOLDER_ID)
        if not folder_id:
            raise RuntimeError(
                "run_historico_pipeline sin folder_id: pásalo o define "
                f"{ENV_FOLDER_ID} (carpeta de Drive del histórico)."
            )
        drive_source = DriveSource(
            folder_id=folder_id, staging_dir=staging_dir, state_path=drive_state_path
        )

    if store is None:
        from src.jokes.supabase_store import SupabaseStore

        store = SupabaseStore()

    resultado = ResultadoHistorico()

    # --- Etapa 0: DriveSource -> .docx staged (idempotencia por metadata) ---
    docx_staged = drive_source.sync()
    resultado.descargados = [Path(p).name for p in docx_staged]

    # --- Etapa 1: marcar_remates -> .md marcado (por cada .docx staged) ---
    for docx in docx_staged:
        try:
            procesar_docx_fn(docx, carpeta_md)
        except Exception as exc:  # noqa: BLE001 — round-trip fallido u otro
            resultado.marcado_fallidos.append(FalloMarcado(docx=Path(docx).name, error=str(exc)))

    # --- Etapa 2: Loader -> documentos .md nuevos/modificados (MD5) ---
    estado_comprometido = _cargar_estado(loader_state_path)
    loader = Loader(folder=carpeta_md, state_path=loader_state_path)
    documentos = loader.load()  # escribe MD5 en loader_state_path (prematuro)
    estado_fresco = _cargar_estado(loader_state_path)
    # Revierte la escritura prematura del Loader: el fichero en disco vuelve a
    # reflejar solo lo realmente comprometido hasta este punto (ver módulo).
    _guardar_estado(loader_state_path, estado_comprometido)

    # --- Etapas 3-5: por documento, de forma independiente (try/except) ---
    for documento in documentos:
        nombre = documento["name"]
        res_doc = ResultadoDocumento(name=nombre)
        try:
            chistes = segmentar_fn(documento["content"], llamar_llm=llamar_llm)
            for chiste in chistes:
                res_doc.chistes.append(
                    _procesar_chiste(
                        chiste,
                        store,
                        estructurar_fn=estructurar_fn,
                        resolver_taxonomia_fn=resolver_taxonomia_fn,
                        reconciliar_fn=reconciliar_fn,
                        llamar_llm=llamar_llm,
                        generar_embedding_fn=generar_embedding_fn,
                    )
                )
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de la cadena
            res_doc.error = str(exc)
            resultado.documentos.append(res_doc)
            continue

        # Documento completo: compromete su MD5 (ya calculado por el Loader).
        estado_comprometido[nombre] = estado_fresco[nombre]
        resultado.documentos.append(res_doc)

    _guardar_estado(loader_state_path, estado_comprometido)

    return resultado
