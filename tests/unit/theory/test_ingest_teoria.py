"""Tests unitarios de `src/theory/ingest_teoria.py` — sin red (`store`/embeddings dobles).

Contrato (task 21, `src/theory/SPEC.md` §Storage): reusa la cadena real
`parse_whisperx_transcript -> detect_subtypes -> clean_fragments ->
normalize_language -> generar_version` (mismo patrón que
`test_format_normalizer.py`, sobre `tests/fixtures/sample_transcript.txt`)
para producir una `v{N}/` REAL en `tmp_path` — nunca se inventa a mano un
`manifest.json`/`.txt`, se genera con el código de producción real de la
task 11, así la ingesta se testea contra el formato de verdad.

Ni `TeoriaStore` ni `generar_embedding` se llaman de verdad aquí: `store` es
un doble en memoria y `generar_embedding_fn` es una función fake — la
llamada real de embeddings se cubre en
`tests/integration/test_ingest_teoria_live.py`.
"""
from pathlib import Path

import pytest

from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.ingest_teoria import (
    IngestaTeoriaError,
    ResultadoIngesta,
    _descubrir_ultima_version,
    _separar_cuerpo,
    ingestar_version,
)
from src.theory.normalizers.format_normalizer import DocumentoEntrada, generar_version
from src.theory.normalizers.language_normalizer import normalize_language
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"

FUENTE_FIXTURE = "Curso de stand-up (fixture WhisperX)"
TIPO_FUENTE_FIXTURE = "transcripcion_curso"
LICENCIA_FIXTURE = "personal_only"


def _traductor_no_op(texto: str, idioma_origen: str) -> str:
    raise AssertionError("no debería traducirse: el fixture ya está en español")


def _generar_v1_real(tmp_path: Path) -> Path:
    """Genera una `v1/` real (código de producción, no inventado) en `tmp_path`."""
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos_subtipo = detect_subtypes(texto)
    fragmentos_limpios = clean_fragments(fragmentos_subtipo)
    fragmentos = normalize_language(fragmentos_limpios, traductor=_traductor_no_op)

    documento = DocumentoEntrada(
        fragmentos=fragmentos,
        fuente=FUENTE_FIXTURE,
        tipo_fuente=TIPO_FUENTE_FIXTURE,
        autor=None,
        licencia=LICENCIA_FIXTURE,
    )
    directorio_base = tmp_path / "processed"
    generar_version([documento], directorio_base)
    return directorio_base


# ---------------------------------------------------------------------------
# _descubrir_ultima_version
# ---------------------------------------------------------------------------

class TestDescubrirUltimaVersion:
    def test_encuentra_la_unica_version_finalizada(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        assert _descubrir_ultima_version(directorio_base) == 1

    def test_se_queda_con_la_mayor_de_varias_finalizadas(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        # Segunda versión real (v2), mismo documento, vía el código real.
        texto = parse_whisperx_transcript(SAMPLE_TXT).texto
        fragmentos = normalize_language(
            clean_fragments(detect_subtypes(texto)), traductor=_traductor_no_op
        )
        documento = DocumentoEntrada(
            fragmentos=fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE
        )
        generar_version([documento], directorio_base)

        assert _descubrir_ultima_version(directorio_base) == 2

    def test_directorio_sin_versiones_finalizadas_lanza_error(self, tmp_path):
        with pytest.raises(IngestaTeoriaError):
            _descubrir_ultima_version(tmp_path / "no-existe")


# ---------------------------------------------------------------------------
# _separar_cuerpo
# ---------------------------------------------------------------------------

class TestSepararCuerpo:
    def test_recupera_los_textos_de_fragmento_en_orden(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        ruta_txt = next((directorio_base / "v1" / "documents").glob("*.txt"))
        texto_documento = ruta_txt.read_text(encoding="utf-8")

        fragmentos_texto = _separar_cuerpo(texto_documento)

        assert len(fragmentos_texto) >= 1
        assert all(isinstance(f, str) and f.strip() for f in fragmentos_texto)

    def test_documento_sin_delimitador_de_cierre_lanza_error(self):
        with pytest.raises(IngestaTeoriaError):
            _separar_cuerpo("esto no tiene cabecera YAML en absoluto")


# ---------------------------------------------------------------------------
# ingestar_version — orquestación con store/embeddings dobles, sin red.
# ---------------------------------------------------------------------------

class _FakeTeoriaStore:
    def __init__(self):
        self.chunks_guardados: dict[tuple, dict] = {}
        self.fuentes: dict[str, int] = {}
        self._siguiente_fuente_id = 1

    def buscar_o_crear_fuente(self, nombre, *, tipo_fuente=None, licencia=None):
        if nombre not in self.fuentes:
            self.fuentes[nombre] = self._siguiente_fuente_id
            self._siguiente_fuente_id += 1
        return self.fuentes[nombre]

    def guardar_chunk(self, *, doc_id, version_corpus, chunk_index, contenido, embedding, tipo_fuente, fuente_id, licencia):
        clave = (doc_id, version_corpus, chunk_index)
        if clave in self.chunks_guardados:
            return None
        fila = {
            "doc_id": doc_id,
            "version_corpus": version_corpus,
            "chunk_index": chunk_index,
            "contenido": contenido,
            "embedding": embedding,
            "tipo_fuente": tipo_fuente,
            "fuente_id": fuente_id,
            "licencia": licencia,
        }
        self.chunks_guardados[clave] = fila
        return fila


def _embedding_fake(texto: str) -> list:
    return [float(len(texto)), 0.0, 0.0]


class TestIngestarVersion:
    def test_ingesta_todos_los_fragmentos_del_documento(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        store = _FakeTeoriaStore()

        resultado = ingestar_version(directorio_base, store, generar_embedding_fn=_embedding_fake)

        assert isinstance(resultado, ResultadoIngesta)
        assert resultado.version_corpus == "v1"
        assert resultado.num_duplicados == 0
        assert resultado.num_nuevos == len(store.chunks_guardados)
        assert resultado.num_nuevos > 0

    def test_reingestar_la_misma_version_es_idempotente(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        store = _FakeTeoriaStore()

        ingestar_version(directorio_base, store, generar_embedding_fn=_embedding_fake)
        resultado_repetido = ingestar_version(
            directorio_base, store, generar_embedding_fn=_embedding_fake
        )

        assert resultado_repetido.num_nuevos == 0
        assert resultado_repetido.num_duplicados == len(store.chunks_guardados)

    def test_resuelve_fuente_una_sola_vez_por_documento(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        store = _FakeTeoriaStore()

        ingestar_version(directorio_base, store, generar_embedding_fn=_embedding_fake)

        assert store.fuentes == {FUENTE_FIXTURE: 1}
        assert all(
            fila["fuente_id"] == 1 for fila in store.chunks_guardados.values()
        )

    def test_chunks_llevan_tipo_fuente_y_licencia_del_manifest(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        store = _FakeTeoriaStore()

        ingestar_version(directorio_base, store, generar_embedding_fn=_embedding_fake)

        for fila in store.chunks_guardados.values():
            assert fila["tipo_fuente"] == TIPO_FUENTE_FIXTURE
            assert fila["licencia"] == LICENCIA_FIXTURE

    def test_version_explicita_ignora_la_mas_reciente(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        # v2 real, mismo documento.
        texto = parse_whisperx_transcript(SAMPLE_TXT).texto
        fragmentos = normalize_language(
            clean_fragments(detect_subtypes(texto)), traductor=_traductor_no_op
        )
        documento = DocumentoEntrada(
            fragmentos=fragmentos, fuente=FUENTE_FIXTURE, tipo_fuente=TIPO_FUENTE_FIXTURE
        )
        generar_version([documento], directorio_base)

        store = _FakeTeoriaStore()
        resultado = ingestar_version(
            directorio_base, store, version=1, generar_embedding_fn=_embedding_fake
        )

        assert resultado.version_corpus == "v1"

    def test_no_llama_dos_veces_al_generador_de_embeddings_por_el_mismo_fragmento(self, tmp_path):
        directorio_base = _generar_v1_real(tmp_path)
        store = _FakeTeoriaStore()
        llamadas = []

        def espia(texto):
            llamadas.append(texto)
            return _embedding_fake(texto)

        resultado = ingestar_version(directorio_base, store, generar_embedding_fn=espia)

        assert len(llamadas) == len(resultado.chunks)
