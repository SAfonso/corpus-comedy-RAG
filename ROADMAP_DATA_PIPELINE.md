# 📦 Comedy Corpus — Data Pipeline
> Proyecto previo e independiente del RAG.
> **Objetivo único:** transformar fuentes heterogéneas y sucias en un corpus limpio,
> normalizado y versionado que el RAG pueda consumir sin fricción.
> **Versión:** 2.0 (multi-fuente) | **Estado:** Fase 0 completada · spec aprobada
> **Spec:** `/docs/specs/comedy-corpus-pipeline.md` (fuente de verdad)

---

## 🔀 Alcance v2 — Multi-fuente (actualización post-Fase 0)

Este roadmap nació para un único caso de uso (ingesta de teoría de comedia desde
Drive). Tras Fase 0 surgieron dos fuentes nuevas, ya incorporadas a la spec:

- **Flujo A — Teoría** (original): libros/cursos desde Drive → ficheros `/data/processed/v{N}/`.
- **Flujo B — Chistes propios (Telegram)**: tiempo real, Bronze→Silver (LLM) → Supabase.
- **Flujo C — Chistes históricos**: batch retroactivo de textos propios → Supabase.

Los une el contrato `tipo_fuente` (`teoria | transcripcion_curso | propio |
propio_historico`) y un índice pgvector compartido. Detalle completo y decisiones
(P1–P14) en **`/docs/specs/comedy-corpus-pipeline.md`**. La sección de interrogatorio
de abajo se conserva como registro histórico de Fase 0.

---

## ⚠️ Por qué este proyecto existe antes del RAG

El RAG asume que los datos de entrada son texto limpio y semánticamente coherente.
No limpia, no normaliza, no unifica idiomas, no filtra ruido.

Si entra esto:
```
[0.19s] SPEAKER_00: Hola, bienvenidas y bienvenidos a este curso.
[7.42s] SPEAKER_00: Bueno, yo soy Denis o Deni, como queráis, y soy cómico.
```

El RAG lo indexa tal cual. El embedding de `[7.42s] SPEAKER_00: Bueno` no
representa nada útil semánticamente. El retrieval fallará en silencio — sin
errores, pero con resultados malos.

**Este proyecto entrega un único artefacto:** `/corpus/v1/` — directorio con
ficheros `.txt` limpios, normalizados, en un idioma consistente, con metadatos
estructurados, listos para ser ingeridos por el RAG.

---

## 🧠 FASE 0 — INTERROGATORIO (Obligatorio antes de todo)

> **Instrucción para Claude Code al iniciar:**
> ```
> Lee este documento completo.
> Tu rol inicial es el de un profesor estricto que necesita entender
> exactamente con qué datos trabajamos antes de proponer ninguna solución.
> Ejecuta el interrogatorio bloque a bloque. Una pregunta cada vez.
> Espera respuesta antes de continuar. No asumas nada.
> Si una respuesta es vaga o contradictoria, presiona.
> Documenta cada respuesta en /docs/CORPUS_INVENTORY.md a medida que avanzas.
> Solo cuando el inventario esté completo, propones la arquitectura del pipeline.
> ```

---

### BLOQUE 0 — Inventario Real de Fuentes
*¿Con qué datos contamos exactamente hoy?*

> ⚠️ La mayoría de proyectos de datos fracasan porque el inventario inicial
> era optimista. "Tengo varios cursos" no es un inventario. Sé implacable aquí.

**1.** Haz una lista exhaustiva de TODO el material que tienes ahora mismo,
físicamente en tu ordenador o accesible. Para cada fuente:
- ¿Qué es? (transcripción, PDF, libro, apuntes, ejemplos sueltos...)
- ¿En qué formato está el fichero? (.txt, .pdf, .docx, .srt, .vtt, .md...)
- ¿En qué idioma?
- ¿Cuántas páginas / minutos / líneas tiene aproximadamente?
- ¿Ya lo tienes descargado o hay que ir a buscarlo?

*(No sigas hasta tener esta lista. Sin inventario, no hay pipeline.)*

**2.** De todo ese material, ¿cuánto está ya en formato texto plano y cuánto
hay que extraer todavía? (PDFs escaneados, vídeos sin transcribir, audios...)

**3.** Las transcripciones que ya tienes con WhisperX — ¿cuántas son?
¿Son todas del mismo formato que el ejemplo visto
(`[timestamp] SPEAKER_XX: texto`)? ¿O hay variaciones de formato entre ellas?

**4.** Los libros en distintos idiomas — ¿en qué idiomas exactamente?
¿Son traducciones del mismo libro o contenidos completamente diferentes?
¿Quieres usar todos los idiomas en el corpus final o normalizar a uno solo?

**5.** ¿Qué son exactamente los "apuntes y ejemplos"?
- ¿Son tuyos (escritos por ti) o de terceros?
- ¿Están en un único fichero o dispersos?
- ¿Tienen una estructura recognoscible o son notas sueltas?

---

### BLOQUE 1 — Calidad y Problemas Conocidos
*¿Qué sabemos ya que está mal en los datos?*

**6.** Mirando la transcripción de ejemplo que ya tienes:
- Los timestamps (`[0.19s]`) y speaker tags (`SPEAKER_00:`) — ¿son útiles
  para algo o es todo ruido que hay que eliminar?
- ¿Hay casos donde el cambio de speaker en mitad de una frase corta el
  sentido de lo que se está explicando?
- ¿Hay transcripciones donde el audio era malo y el texto es incomprensible?

**7.** ¿Tienes material duplicado? Por ejemplo:
- El mismo curso en vídeo Y en PDF
- Apuntes tuyos que resumen un libro que también tienes
- Traducción de un libro que ya tienes en el original
¿Cómo quieres manejar la redundancia — eliminar duplicados o mantenerlos todos?

**8.** ¿Cuánto del material es "ruido" que no aporta conocimiento sobre
escritura de comedia? Por ejemplo: introducciones de curso, agradecimientos,
publicidad, partes off-topic. ¿Tienes una idea aproximada del porcentaje?

**9.** *(Pregunta difícil)* Si ahora mismo abres tres de tus ficheros al azar
y los lees durante 2 minutos cada uno — ¿dirías que el contenido es denso en
conocimiento técnico de comedia, o hay mucho relleno? Sé honesto, porque
eso determina si el corpus resultante va a ser útil.

---

### BLOQUE 2 — El Corpus de Salida
*¿Qué queremos exactamente al final de este proyecto?*

**10.** ¿Cuál es el idioma del corpus final?
- ¿Todo en español, aunque el original sea en inglés?
- ¿Mantener cada documento en su idioma original?
- ¿Corpus bilingüe con documentos en ambos idiomas?
*(Esto afecta enormemente al pipeline: traducción automática añade errores
y coste. Mantener multiidioma complica el retrieval del RAG.)*

**11.** ¿Qué metadatos quieres que tenga cada documento limpio?
Ejemplos posibles:
- `fuente`: nombre del curso / libro de origen
- `autor`: quién lo escribió/dijo
- `idioma_original`: antes de normalizar
- `tipo`: transcripcion | libro | apuntes | ejemplo
- `tecnica_comica`: si es posible etiquetarlo (rule_of_three, misdirection...)
- `calidad`: alta | media | baja (evaluación manual)

¿Cuáles de estos son imprescindibles para v1? ¿Cuáles son nice-to-have?

**12.** ¿Cómo de limpio tiene que estar el texto?
Define qué significa "limpio" para ti:
- ¿Eliminar todas las muletillas verbales ("o sea", "pues", "¿vale?")
  o mantenerlas porque son parte del estilo natural del cómico?
- ¿Corregir errores de transcripción obvios o dejar el texto tal cual?
- ¿Separar en párrafos coherentes o dejar el texto continuo?

*(Ojo: limpiar muletillas puede eliminar ejemplos de lenguaje natural que
son relevantes para aprender a comunicar comedia en directo.)*

**13.** ¿Qué formato tiene el fichero de salida para cada documento?
Opciones:
```
# Opción A — .txt plano
Texto limpio sin más.

# Opción B — .txt con cabecera de metadatos
---
fuente: Curso Denis Grau - Stand-up
tipo: transcripcion
idioma: es
calidad: alta
---
Texto limpio aquí.

# Opción C — .json estructurado
{"metadata": {...}, "content": "texto limpio", "chunks_sugeridos": [...]}
```

---

### BLOQUE 3 — Derechos y Ética
*Lo que nadie pregunta hasta que ya es tarde.*

**14.** El material de los cursos de internet — ¿tienes permiso para usarlo?
Considera:
- Cursos de Domestika, Udemy, YouTube: su contenido es propiedad del autor.
  Usarlo en un RAG privado personal tiene una zona gris legal.
  Distribuirlo o usarlo comercialmente es claramente problemático.
- ¿Has comprado los cursos? ¿Tienes permiso explícito del autor?
- ¿Cuál es tu uso previsto — personal/aprendizaje, TFM académico, producto comercial?

**15.** Los libros — ¿son de dominio público, comprados legalmente, o PDFs
descargados de internet sin licencia clara?

*(No te estoy acusando de nada. Te estoy preguntando para que el ADR
correspondiente deje constancia de la decisión tomada con información completa.)*

---

### BLOQUE 4 — Recursos y Restricciones
*¿Cuánto tiempo y dinero tiene este proyecto previo?*

**16.** ¿Cuánto tiempo quieres invertir en este proyecto de pipeline antes
de empezar el RAG? ¿Es algo que quieres resolver en una semana, o le das
un mes de desarrollo cuidadoso?

**17.** ¿Este pipeline va a ser un script que ejecutas una vez y listo,
o quieres que sea un sistema reutilizable que puedas alimentar con nuevos
cursos en el futuro?
*(La respuesta cambia radicalmente la arquitectura: script desechable vs.
pipeline mantenible con tests, CLI, versionado...)*

**18.** ¿Tienes acceso a GPU para correr modelos locales (traducción,
clasificación) o todo tiene que funcionar con CPU o APIs externas?

**19.** ¿Cuánto estás dispuesto a gastar en APIs para este pipeline?
Por ejemplo, usar GPT-4o o Claude para limpiar y etiquetar texto cuesta dinero.
¿Tienes un presupuesto aproximado para la fase de procesado?

---

### BLOQUE 5 — El Detector de Suposiciones
*Las ideas que suenan bien pero pueden no serlo.*

**20.** ¿Has calculado cuánto texto limpio vas a tener al final?
Un curso de stand-up de 4 horas transcrito son aproximadamente 40.000-50.000
palabras brutas. Después de limpiar timestamps, muletillas y off-topic,
quizás queden 25.000 palabras útiles.
¿Es suficiente para un RAG? *(Spoiler: depende del caso de uso. Para consultas
conceptuales sobre técnicas, puede ser suficiente. Para generar ejemplos
variados, probablemente no.)*

**21.** ¿Tienes claro que WhisperX, aunque es excelente, comete errores?
Especialmente con:
- Nombres propios y términos específicos de comedia
- Cambios rápidos de hablante
- Momentos con risas de fondo o efectos de sonido
¿Tienes un plan para revisar manualmente al menos una muestra del output?

**22.** La idea de "limpiar muletillas" — ¿has pensado que en comedia las
muletillas y el ritmo del habla son a veces parte de la técnica?
Un cómico que dice "o sea..." antes del remate lo hace a propósito.
¿Quieres un corpus que capture el estilo oral o solo el contenido conceptual?

**23.** *(La más importante)* Imagina que terminas este pipeline y tienes
un corpus limpio de 100.000 palabras sobre escritura de comedia.
¿Sabes ya qué pregunta le harías al RAG que no podrías responder simplemente
buscando en Google o preguntándole a Claude directamente con un copy-paste
de 2 páginas del libro? Si no tienes esa pregunta clara, el proyecto
completo puede no tener el valor que crees.

---

### Resultado → CORPUS_INVENTORY.md

Al terminar el interrogatorio, Claude Code genera `/docs/CORPUS_INVENTORY.md`:

```markdown
# Inventario del Corpus
**Fecha:** YYYY-MM-DD

## Fuentes confirmadas
| ID | Nombre | Tipo | Formato | Idioma | Tamaño | Estado | Derechos |
|----|--------|------|---------|--------|--------|--------|----------|
| F01 | ... | transcripcion | .txt | es | ~40k palabras | listo | comprado |
| F02 | ... | libro | .pdf | en | ~80k palabras | por extraer | comprado |
| ... |

## Problemas identificados por fuente
[Lista de problemas concretos por cada fuente]

## Corpus de salida esperado
- Idioma final: [es / en / bilingüe]
- Formato: [opción A/B/C]
- Metadatos mínimos: [lista]
- Tamaño estimado post-limpieza: [X palabras]

## Decisiones tomadas
[Cada decisión no obvia con su justificación]

## Riesgos
[Lista de riesgos identificados durante el interrogatorio]
```

> **Solo cuando este documento esté completo se propone la arquitectura del pipeline.**

---

## 🗂️ Estructura del Repositorio

```
comedy-corpus-pipeline/
├── CLAUDE.md                    # Guía operativa (resumen de la spec)
├── ROADMAP_DATA_PIPELINE.md     # Este documento
│
├── docs/
│   ├── specs/
│   │   └── comedy-corpus-pipeline.md   # ⭐ Spec v2 (fuente de verdad)
│   ├── CORPUS_INVENTORY.md      # Generado en Fase 0
│   └── decisions/               # ADRs
│
├── data/                        # Solo Flujo A (teoría). Chistes → Supabase.
│   ├── raw/                     # ⚠️ INMUTABLE. Fuentes originales tal cual.
│   │   ├── transcriptions/      # .txt de WhisperX sin tocar
│   │   ├── books/               # PDFs/EPUB/DOCX originales
│   │   └── notes/               # Apuntes y ejemplos originales
│   ├── state/                   # processed_files.json (idempotencia)
│   └── processed/               # Output del pipeline de teoría
│       └── v1/                  # Versión del corpus (inmutable)
│           ├── documents/       # Un .txt por documento limpio
│           ├── manifest.json    # Índice inmutable
│           └── stats.json       # Estadísticas del corpus
│
├── src/
│   ├── utils/                   # COMPARTIDO entre flujos
│   │   ├── language_detector.py
│   │   ├── quality_scorer.py
│   │   └── llm/                 # cliente LLM (Silver) + embeddings
│   │
│   ├── theory/                  # Flujo A — teoría (batch, determinista)
│   │   ├── drive_monitor.py
│   │   ├── parsers/             # whisperx, pdf, epub, docx
│   │   ├── cleaners/            # transcript_cleaner
│   │   ├── detectors/           # subtype_detector
│   │   ├── normalizers/         # language_normalizer, format_normalizer
│   │   ├── enrichers/           # metadata_tagger (opcional v1)
│   │   └── pipeline.py
│   │
│   └── jokes/                   # Flujos B y C — chistes (LLM → Supabase)
│       ├── telegram_bot.py      # Flujo B (realtime)
│       ├── historico/           # Flujo C (batch): loader, segmentador
│       ├── silver.py            # Silver LLM (compartido B/C)
│       ├── reconciliacion.py    # hash + embedding (compartido B/C)
│       └── supabase_store.py
│
├── tests/
│   ├── unit/
│   │   ├── theory/              # tests del Flujo A
│   │   └── jokes/               # tests de los Flujos B/C
│   ├── integration/
│   └── fixtures/                # Muestras reales de cada tipo de fuente
│       └── sample_transcript.txt
│
└── scripts/
    ├── run_pipeline.py          # Flujo A (teoría)
    ├── run_historico.py         # Flujo C (batch histórico)
    ├── marcar_remates.py        # Preprocesado automático por color docx→.md ([REMATE]+[CHISTOIDE])
    ├── validate_corpus.py       # Valida el output antes de entregar al RAG
    └── stats_report.py          # Informe de estadísticas del corpus
```

---

## 📋 CLAUDE.md del Proyecto

> ⚠️ **Obsoleto (v1).** El `CLAUDE.md` vigente está en la raíz del repo y refleja
> el alcance v2 multi-fuente; la spec completa está en `/docs/specs/comedy-corpus-pipeline.md`.
> Este bloque se conserva como registro histórico de Fase 0.

```markdown
# Comedy Corpus Pipeline — Claude Code Instructions

## Propósito
Pipeline de limpieza y normalización de datos para el Comedy RAG.
Input: /data/raw/ (NUNCA modificar)
Output: /data/processed/v{N}/ (versionado)

## Regla más importante
/data/raw/ es SAGRADO. Nunca modificar, nunca eliminar, nunca sobrescribir.
Todo el trabajo ocurre en /data/processed/.

## Metodología: SDD + TDD
1. Lee la spec relevante en /docs/specs/ antes de implementar
2. Escribe tests primero con fixtures reales de /tests/fixtures/
3. Los fixtures DEBEN ser muestras reales de los datos, no inventadas

## Testing
- pytest tests/unit/ -v
- pytest tests/integration/ -v
- Antes de hacer commit: python scripts/validate_corpus.py

## Parsers: una función = un tipo de fuente
Nunca mezcles lógica de parsing de transcripciones con parsing de PDFs.
Si necesitas código compartido, extráelo a src/utils/.

## Sobre las transcripciones WhisperX
Formato de entrada esperado: [timestamp] SPEAKER_XX: texto
El parser DEBE:
- Eliminar timestamps y speaker tags
- Unir líneas del mismo speaker si son consecutivas
- Separar en párrafos cuando cambia el tema (no el speaker)
- Preservar el contenido, nunca interpretarlo

## Versionado del corpus
Cada run del pipeline genera /data/processed/v{N}/
NUNCA sobreescribir una versión existente.
El manifest.json de cada versión es inmutable una vez generado.

## Decisiones pendientes (TBD post-interrogatorio)
- Idioma del corpus final
- Metadatos mínimos por documento
- Política de deduplicación
- Formato de salida (A/B/C)
Ver /docs/CORPUS_INVENTORY.md cuando esté completo.
```

---

## 🎯 Hitos del Pipeline

### Hito 1 — Parsers (Extracción)
**Objetivo:** Leer cada tipo de fuente y sacar texto plano sin pérdida de información.

- [ ] `WhisperXParser`: timestamps + speaker tags → texto continuo por speaker
- [ ] `PDFParser`: extraer texto manteniendo estructura de capítulos si existe
- [ ] `TextParser`: normalizar .txt/.md/.docx a texto plano
- [ ] Tests con fixtures reales de cada tipo
- [ ] **Definition of Done:** cada parser procesa su fixture sin errores y
      el output tiene más del 95% del contenido original (sin metadatos)

### Hito 2 — Cleaners (Limpieza)
**Objetivo:** Texto legible, coherente, sin ruido técnico.

- [ ] `TranscriptCleaner`: eliminar artefactos de transcripción según política
      decidida en Fase 0 (muletillas sí/no, correcciones sí/no)
- [ ] `LanguageDetector`: etiquetar idioma de cada documento automáticamente
- [ ] `QualityScorer`: puntuación 0-1 de densidad de contenido útil
- [ ] Tests: para cada cleaner, fixture "sucio" → output esperado exacto
- [ ] **Definition of Done:** muestra manual de 10 documentos post-limpieza
      validada por el usuario como "lista para RAG"

### Hito 3 — Normalizers (Homogeneización)
**Objetivo:** Corpus coherente entre fuentes heterogéneas.

- [ ] `FormatNormalizer`: output uniforme para todos los documentos
- [ ] `LanguageNormalizer`: aplicar política de idioma decidida en Fase 0
- [ ] Deduplicación: detectar y gestionar contenido repetido entre fuentes
- [ ] **Definition of Done:** todos los documentos tienen el mismo formato,
      mismo esquema de metadatos, y no hay duplicados exactos

### Hito 4 — Pipeline + Validación
**Objetivo:** Proceso reproducible y validado end-to-end.

- [ ] `pipeline.py`: orquesta Parser → Cleaner → Normalizer → Output
- [ ] `manifest.json`: índice completo del corpus con metadatos
- [ ] `validate_corpus.py`: checks automáticos de calidad del corpus
- [ ] `stats_report.py`: informe de palabras, documentos, idiomas, calidad media
- [ ] **Definition of Done:**
  - Pipeline ejecuta sin errores en todas las fuentes
  - Validate pasa todos los checks
  - Stats report revisado y aprobado manualmente
  - Corpus entregado a `/data/processed/v1/` listo para el RAG

> Los Hitos 1–4 son el **Flujo A (teoría)**. Los Hitos 5–6 cubren los flujos de
> chistes (v2). Ver detalle en `/docs/specs/comedy-corpus-pipeline.md` §5–§11.

### Hito 5 — Chistes propios (Telegram, Flujo B)
**Objetivo:** ingesta en tiempo real chiste a chiste, estructurada y deduplicada.

- [ ] `telegram_bot`: recibe mensajes → Bronze (raw, dedup por `telegram_update_id`)
- [ ] Pre-limpieza mínima no destructiva (trim, unicode, strip meta)
- [ ] `silver`: LLM → `tema`, `estructura_detectada`, `estado`, `sugerencias_mejora`, `chiste_normalizado`
- [ ] Mapeo a taxonomías (`tema_id`/`tecnica_id`) + cola `candidatos_taxonomia`
- [ ] `reconciliacion`: hash + embedding → IGUAL/CAMBIADO/NUEVO
- [ ] `supabase_store`: tablas `chistes` + `chistes_revisiones` (versión por chiste)
- [ ] **Definition of Done:** un chiste enviado por Telegram aparece estructurado en
      Supabase; reenviarlo no duplica; una versión mejorada crea una revisión.

### Hito 6 — Chistes históricos (batch retroactivo, Flujo C)
**Objetivo:** segmentar textos largos ya escritos y clasificarlos con el mismo esquema.

- [ ] `scripts/marcar_remates.py`: docx/gdoc con estilos → `.md`, marcado **automático por color**
      (`#FF0000`→`[REMATE]`, `#980000`→`[CHISTOIDE]`); fusión de spans, multi-párrafo = 1 span,
      cobertura tablas/hyperlinks/listas, validación round-trip. (Prototipo previo en Colab)
- [ ] `historico/loader`: idempotencia de documento por hash MD5
- [ ] `historico/segmentador`: `[REMATE]` como fin + LLM afina el inicio del setup;
      `[CHISTOIDE]` NO es frontera (se conserva como metadato de estructura)
- [ ] Reutiliza `silver` + `reconciliacion` del Hito 5 → Supabase (`tipo_fuente=propio_historico`)
- [ ] Gate de coste: estimación de tokens (dry-run) + batching antes del run completo
- [ ] **Definition of Done:** un documento con varios chistes se segmenta correctamente;
      un chiste ya presente (Telegram o histórico) se reconcilia, no se duplica.

---

## 📊 Checks de Validación del Corpus Final

El script `validate_corpus.py` verificará automáticamente:

```python
CHECKS = [
    "ningún fichero en processed/ tiene timestamps [XX.XXs]",
    "ningún fichero tiene SPEAKER_XX:",
    "todos los ficheros tienen cabecera de metadatos completa",
    "ningún fichero tiene menos de 100 palabras (probable error de parsing)",
    "ningún fichero tiene más de 50.000 palabras (probable error de concatenación)",
    "no hay ficheros duplicados (hash MD5)",
    "todos los idiomas detectados están en la lista permitida",
    "manifest.json referencia exactamente los mismos ficheros que existen",
]
```

---

## ⚠️ Riesgos Identificados

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|-----------|
| Corpus real más pequeño de lo esperado post-limpieza | Alta | Alto | Medir antes de comprometerse con el RAG |
| PDFs escaneados no extractables con texto | Media | Medio | OCR como fallback (pytesseract), testar en Fase 0 |
| Derechos de uso del material no claros | Media | Alto | ADR explícito con decisión documentada |
| Traducciones automáticas de baja calidad | Alta | Medio | Solo traducir si es imprescindible, preferir idioma original |
| Muletillas eliminadas que eran parte de ejemplos cómicos | Media | Medio | Política explícita decidida en Fase 0, reversible desde /raw |
| Pipeline diseñado para una sola ejecución, no reutilizable | Alta | Medio | Decidir en Bloque 4 pregunta 17 antes de diseñar |

---

## 🔗 Relación con el Proyecto RAG

```
comedy-corpus-pipeline/
└── data/processed/v1/          →    comedy-rag/
        ├── documents/           →        data/raw/corpus/
        └── manifest.json        →        data/raw/manifest.json

El RAG trata el output de este pipeline como su /data/raw/.
Nunca modifica esos ficheros. Si el corpus mejora, se genera v2
en el pipeline y se actualiza el RAG con la nueva versión.
```

**Regla de oro entre proyectos:**
El pipeline no sabe nada del RAG. El RAG no sabe cómo se generó el corpus.
Son proyectos independientes con una interfaz clara entre ellos: el directorio
`/data/processed/v{N}/`.

---

*Última actualización: Fase 0 completada · spec v2 multi-fuente aprobada*
*Próxima acción: SDD sobre el primer componente (WhisperXParser): spec de detalle → tests con fixture real → implementación*
