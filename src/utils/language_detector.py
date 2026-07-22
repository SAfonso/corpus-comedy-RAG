"""language_detector — utils, detección de idioma genérica (código compartido).

Contrato (`src/utils/SPEC.md`): detección de idioma de un texto plano,
consumida hoy por Flujo A / `LanguageNormalizer`
(`src/theory/normalizers/language_normalizer.py`, ver `src/theory/SPEC.md`
§Idioma) para decidir qué fragmentos de teoría traducir a español. No importa
nada de `theory/` ni `jokes/` (regla de dependencias, ver CLAUDE.md raíz) —
es código genérico reutilizable, sin conocimiento de `subtipo` ni de ningún
concepto propio de un flujo.

Determinista, SIN LLM: usa `langdetect` (puerto Python de la librería
`language-detection` de Google, basada en n-gramas de caracteres), no ningún
modelo generativo.

Nota de determinismo: `langdetect` usa internamente una proyección aleatoria
de n-gramas para desambiguar entre idiomas parecidos; sin fijar semilla, el
mismo texto puede clasificarse de forma distinta entre ejecuciones (issue
conocido de la librería, documentado en su propio README). Se fija
`DetectorFactory.seed` a un valor constante al importar este módulo para que
la detección sea reproducible en tests y en producción.
"""
from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0


def detect_language(texto: str) -> str:
    """Devuelve el código de idioma ISO 639-1 detectado en `texto` (p.ej. "es", "en").

    Entrada: texto plano de cualquier longitud. Salida: código de idioma tal
    como lo devuelve `langdetect` (ISO 639-1 en minúsculas).

    No hay valor de idioma "por defecto" silencioso: si `texto` está
    vacío/en blanco, o si `langdetect` no encuentra señal lingüística
    suficiente para decidir (texto demasiado corto o ambiguo), se lanza
    `ValueError` — quien llama decide cómo tratar la ausencia de idioma
    detectable (p.ej. `language_normalizer.py` trata ese caso como
    "idioma indeterminado", nunca como "es" por defecto, para no dar por
    hecho que un fragmento ya está en destino cuando en realidad no se pudo
    determinar).
    """
    if not texto or not texto.strip():
        raise ValueError("no se puede detectar el idioma de un texto vacío")

    try:
        return detect(texto)
    except LangDetectException as exc:
        raise ValueError(f"no se pudo detectar el idioma del texto: {exc}") from exc
