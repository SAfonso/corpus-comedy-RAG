# Flujo C — Chistes históricos (batch retroactivo)

> Spec de `src/jokes/historico/` (y de `scripts/marcar_remates.py`, que
> alimenta este flujo aunque viva físicamente en `scripts/`). Para Silver,
> Reconciliación, Taxonomías y el esquema de Supabase (compartidos con el
> Flujo B), ver [`src/jokes/SPEC.md`](../SPEC.md) — no se duplican aquí.
> Contexto general en
> [`docs/specs/00-overview.md`](../../../docs/specs/00-overview.md) y política
> LLM/coste en [`docs/specs/llm-policy.md`](../../../docs/specs/llm-policy.md).

Procesado retroactivo de textos propios ya escritos, con varios chistes por
documento. Reutiliza el Silver y la Reconciliación del Flujo B (ver
`src/jokes/SPEC.md`); lo que sigue es **específico de Histórico**: la entrada
marcada por color y la segmentación.

## Preprocesado de marcado (`scripts/marcar_remates.py`)

Script **automático y determinista**, previo y desacoplado del pipeline.
El color ya existe en el documento fuente, así que la marcación se deriva de
él sin intervención humana (P15, 2026-07-06).

- **Motivo:** Markdown plano no conserva el color de texto. Por eso NO se
  parte de un `.md` ya convertido; se lee el documento **original con
  estilos** (`.docx` / Google Docs). El color de fuente vive a nivel de *run*
  en el XML del `.docx`, lo que permite detectarlo de forma determinista.
- **Mapa color → etiqueta** (el rojo **no es 1:1 con remate**; hay dos rojos
  con significado distinto):

  | Color fuente        | Etiqueta                     | Semántica                                                        |
  |----------------------|--------------------------------|---------------------------------------------------------------------|
  | `#FF0000` rojo puro  | `[REMATE]…[/REMATE]`          | Remate principal — **cierra** el chiste (frontera, ver §Segmentador) |
  | `#980000` burdeos    | `[CHISTOIDE]…[/CHISTOIDE]`     | Mini-remate interno, menos fuerza — **NO** cierra el chiste         |

  Cualquier otro color = texto normal, sin etiquetar. La clasificación es
  **por tono con margen**, no por igualdad exacta de hex (el color puede
  variar un par de dígitos entre documentos).
- **Reglas de marcado:**
  - Runs contiguos del mismo color se **fusionan** en un único span.
  - Un span rojo que cruza párrafos = **un solo tramo** (el marcador se
    mantiene abierto entre párrafos).
  - Las dos etiquetas **no se solapan**: al cambiar de color se cierra una y
    se abre la otra.
  - Espacios y puntuación quedan **fuera** de las etiquetas.
- **Cobertura de parseo:** además de párrafos, recorrer **tablas, hyperlinks y
  listas** (runs que el iterador ingenuo de `python-docx` no devuelve).
- **Validación round-trip (obligatoria):** nº de caracteres de cada color en
  el `.docx` == nº de caracteres dentro de la etiqueta correspondiente en el
  `.md`. Un descuadre indica runs perdidos (típicamente en tablas o
  hyperlinks) y **debe fallar** el marcado.
- **Salida:** `.md` con marcadores embebidos, que alimenta este flujo.
- **No integrado** en la arquitectura de teoría ni en la orquestación: se
  mantiene como paso previo desacoplado. Al ser automático, no añade fricción
  manual.

> **Prototipo:** validado primero en Google Colab
> (`notebooks/marcar_remates_colab.ipynb`) sobre documentos reales del
> histórico antes de bajarlo a `scripts/` (SDD: spec → tests con fixtures
> reales → implementación).

## Entrada y etapas

**Entrada:** `.md` ya marcados con `[REMATE]…[/REMATE]` y
`[CHISTOIDE]…[/CHISTOIDE]`, generados por `marcar_remates.py`. El pipeline los
trata como texto plano normal.

**Etapas:**
1. **Loader (`loader.py`):** lee los `.md`. Idempotencia de documento por hash
   MD5 (ver §Idempotencia): un documento idéntico ya procesado se salta.
2. **Segmentador (`segmentador.py`):** `[REMATE]` = fin **determinista** de
   cada chiste; el LLM afina hacia atrás dónde empieza el setup por contenido
   semántico y descarta intros/transiciones que no son del chiste.
   `[CHISTOIDE]` **NO es frontera de chiste** (es un mini-remate interno que
   aligera una premisa larga): el Segmentador lo **ignora como fin** y lo
   **conserva como metadato de estructura** del chiste al que pertenece (útil
   para el Silver). Tratarlo como fin partiría chistes por la mitad.
   **Fuera del alcance de P16** (ver `docs/specs/llm-policy.md`): dónde
   "empieza de verdad" el setup no tiene criterio verificable externo (es
   juicio semántico) — este paso **no** lleva loop de reintento automático;
   su control de calidad es revisión humana muestral, no auto-convergencia.
3. **Silver:** mismo esquema que Telegram — ver `src/jokes/SPEC.md` §Silver.
4. **Reconciliación** → Supabase con `tipo_fuente='propio_historico'` — ver
   `src/jokes/SPEC.md` §Reconciliación.

**Re-ejecutable:** con el tiempo llegarán documentos nuevos que pueden traer
chistes iguales o cambiados. La reconciliación a nivel de chiste enruta cada
uno a IGUAL (dedup) / CAMBIADO (nueva revisión) / NUEVO. El hash de documento
evita reprocesar lo idéntico.

## Idempotencia y versionado

Hash MD5 del **documento** (no evento, a diferencia del Flujo B — ver
`src/jokes/telegram/SPEC.md`) + reconciliación de chiste. Versionado por
chiste, sin `v{N}` (ver `src/jokes/SPEC.md` §Versionado).

## Coste

Volumen relevante → estimación de tokens previa (dry-run), batching y gate de
coste antes del run completo. Detalle en `docs/specs/llm-policy.md`.

## Riesgos propios de este flujo

| Riesgo | Mitigación |
|--------|-----------|
| Marcado por color pierde runs (tablas/hyperlinks) o confunde tonos de rojo | Validación round-trip obligatoria; clasificación por tono con margen; regenerar `.md` desde el `.docx` fuente (el original es la verdad) |
| Coste de tokens del histórico mayor de lo previsto | Dry-run de estimación + gate antes del run completo |
