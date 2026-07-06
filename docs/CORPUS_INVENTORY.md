# Inventario del Corpus
**Fecha:** 2026-04-07
**Estado:** Interrogatorio completo — listo para proponer arquitectura

---

## Fuentes confirmadas

| ID  | Nombre          | Tipo           | Formato           | Idioma | Tamaño aprox.    | Estado          | Derechos |
|-----|-----------------|----------------|-------------------|--------|------------------|-----------------|----------|
| F01 | Cursos stand-up | transcripcion  | .txt (WhisperX)   | es     | 27 ficheros, máx 24kB c/u | descargados | pendiente |
| F02 | Libros comedia  | libro          | .pdf/.epub/.docx  | en (3) + es (2) | ~200 págs c/u | descargados (mayoría) | pendiente |

## Detalles por tipo de fuente

### Transcripciones (F01)
- **Cantidad:** 27
- **Origen:** Cursos de stand-up (no especificado cuáles)
- **Formato:** Uniforme — `[timestamp] SPEAKER_XX: texto` (procesadas previamente por el usuario)
- **Idioma:** Español (todas)
- **Tamaño:** máximo 24kB por fichero (~4.000-5.000 palabras aprox.)
- **Estado:** Todas descargadas y en formato .txt

### Libros (F02)
- **Cantidad:** 5 (todos sobre comedia/stand-up, distintos contenidos)
- **Idiomas:** 3 en inglés, 2 en español
- **Formato:** .pdf, .epub, .docx (mix no especificado por libro)
- **Tamaño:** ~200 páginas cada uno
- **Estado:** La mayoría descargados
- **Problema crítico:** Los PDF probablemente escaneados (imágenes) → requieren OCR. **PENDIENTE VERIFICAR.**
- **Extracción:** Todos los libros requieren conversión a texto plano (ninguno está ya en .txt)

### Apuntes y ejemplos (F03 — futuro)
- No existen en v1, pero el pipeline debe diseñarse para incorporarlos en el futuro.

---

## Corpus de salida esperado

- **Idioma final:** Español (los 3 libros en inglés se traducirán al español)
- **Formato:** TBD (decidir en Bloque 2)
- **Metadatos mínimos:** TBD (decidir en Bloque 2)
- **Tamaño estimado post-limpieza:** TBD

---

## Problemas identificados por fuente

- **F02 — Libros PDF:** posiblemente escaneados → OCR necesario. Riesgo de calidad de extracción. **Verificar manualmente antes de diseñar el parser.**
- **F02 — Libros en inglés:** traducción automática al español introduce errores. Evaluar calidad post-traducción.
- **General:** Tamaño total del corpus puede ser menor de lo esperado una vez limpiados timestamps y ruido.

---

## Decisiones tomadas

| Decisión | Valor | Justificación |
|----------|-------|---------------|
| Idioma del corpus final | Español | Decisión explícita del usuario |
| Libros en inglés | Traducir al español | Consecuencia de la decisión anterior |
| Apuntes/ejemplos en v1 | No incluir | No existen aún; pipeline debe soportarlos en el futuro |

---

## Riesgos

| Riesgo | Estado |
|--------|--------|
| PDFs escaneados no extractables sin OCR | **PENDIENTE VERIFICAR** — usuario no está seguro |
| Calidad de traducción automática (libros EN→ES) | Identificado — mitigar con revisión de muestra |
| Corpus más pequeño de lo esperado post-limpieza | Por evaluar en Bloque 5 |
| Pipeline no reutilizable si se diseña como script de un solo uso | Por decidir en Bloque 4 |

---

---

## Bloque 1 — Calidad y Problemas Conocidos

| Pregunta | Respuesta |
|----------|-----------|
| Timestamps y speaker tags | Ruido — eliminar del texto. Conservar speaker dominante como **metadato del documento** (útil en transcripciones futuras con múltiples voces) |
| Cambios de speaker que cortan el sentido | No ocurre en el material actual |
| Audio malo / texto incomprensible | No reportado |
| Material duplicado | Existirá. Política: eliminar explicaciones repetidas, **conservar ejemplos** aunque vengan de fuentes distintas. Requiere distinguir chunks "explicación" vs "ejemplo" en el pipeline |
| Porcentaje de ruido (intros, publicidad, off-topic) | Desconocido — a medir durante el pipeline |
| Densidad de contenido útil | **Mucho relleno** — el QualityScorer es imprescindible, no nice-to-have |

---

---

## Bloque 2 — El Corpus de Salida

| Pregunta | Respuesta |
|----------|-----------|
| Idioma del corpus final | **Bilingüe explícito** — teoría traducida al español, ejemplos conservados en inglés. El RAG debe configurarse para multiidioma |
| Metadatos v1 | `fuente`, `autor`, `idioma_original`, `idioma_fragmento`, `subtipo` (explicacion / ejemplo) |
| Metadatos v2 (diferidos) | `tecnica_comica` — requiere clasificación automática/manual |
| Nivel de limpieza | Agresivo: eliminar muletillas, repeticiones, timestamps. Corregir errores de transcripción. Separar en párrafos coherentes |
| Formato de salida | **Opción B** — `.txt` con cabecera de metadatos YAML al inicio |

---

---

## Bloque 3 — Derechos y Ética

| Pregunta | Respuesta |
|----------|-----------|
| Permisos sobre los cursos | Sí — comprados legalmente |
| Uso previsto | Aprendizaje personal |
| Permisos sobre los libros | Sí — comprados legalmente |

---

## Bloque 4 — Recursos y Restricciones

| Pregunta | Respuesta |
|----------|-----------|
| Tiempo disponible para el pipeline | ~2 semanas |
| Script único vs sistema reutilizable | **Sistema reutilizable** — el pipeline debe poder incorporar nuevos datos |
| Arquitectura de ejecución | **Agente autónomo** que monitoriza Google Drive periódicamente y ejecuta el pipeline si detecta ficheros nuevos. Requiere: Google Drive API, estado persistente, scheduler, pipeline idempotente |
| GPU disponible | No necesaria — se usan APIs externas |
| APIs: OCR | Tesseract (gratuito/local) como primera opción; API externa solo si la calidad es inaceptable |
| APIs: Traducción | DeepL free tier o LibreTranslate local |
| APIs: Limpieza | Reglas deterministas en código, sin LLM |
| Presupuesto | Minimizar coste al máximo |

---

## Bloque 5 — Detector de Suposiciones

| Pregunta | Respuesta |
|----------|-----------|
| Tamaño estimado del corpus | ~130k palabras brutas en transcripciones + ~250k en libros. Post-limpieza: estimado 150k-250k palabras útiles. Suficiente para v1, crecerá con nuevos datos |
| Revisión manual de transcripciones | No planificada — usuario confía en la calidad de WhisperX. **Riesgo registrado** |
| Muletillas vs estilo oral | Limpieza agresiva por defecto. **Excepción:** fragmentos marcados como ejemplos (detectados por marcadores lingüísticos: "un ejemplo", "por ejemplo", "algo así como"...) conservan el estilo oral |



---

## Arquitectura aprobada

**Flujo:** DriveMonitor → Parser → SubtypeDetector → Cleaner → LanguageDetector → LanguageNormalizer → QualityScorer → FormatNormalizer → `/data/processed/v{N}/`

**Decisiones clave:**
- Pipeline reutilizable con agente que monitoriza Google Drive periódicamente
- Idempotente via hash MD5 en `/data/state/processed_files.json`
- SubtypeDetector ejecuta antes que el Cleaner (ejemplos tienen reglas distintas)
- QualityScorer obligatorio (no nice-to-have)
- Corpus bilingüe: teoría en español, ejemplos en idioma original
- Stack 100% gratuito: Tesseract, langdetect, DeepL free tier, APScheduler

*Fase 0 completada — listo para implementación*
