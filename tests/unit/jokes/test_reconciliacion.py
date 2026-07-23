"""Tests unitarios de `src/jokes/reconciliacion.py` — lógica pura, sin red.

Cubre (§Reconciliación, task 15): hash de dedup exacto, similitud coseno, la
decisión IGUAL/CAMBIADO/NUEVO contra una lista de `candidatos` fija, y la
orquestación de `reconciliar_chiste` con `generar_embedding_fn` inyectado
(nunca se llama a la red aquí). Los textos de chiste usados son reales,
copiados de `tests/fixtures/Freskito-Informático.md` (task 17) — los
embeddings, al ser vectores numéricos, no existen "reales" sin llamar al
proveedor, así que son sintéticos pero fijos y deterministas.
"""
from pathlib import Path

import pytest

from src.jokes.reconciliacion import (
    UMBRAL_SIMILITUD_CAMBIADO,
    ReconciliacionError,
    ResultadoReconciliacion,
    calcular_hash_normalizado,
    decidir_reconciliacion,
    reconciliar_chiste,
    similitud_coseno,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "Freskito-Informático.md"
)

CHISTE_A = (
    "Me llamo Sergio Afonso, y si, efectivamente no soy de aquí, lo habrán "
    "notado por mi acento y porque aspiro las S, eso es porque soy canario, "
    "vale? No un venenzolano con asma."
)
CHISTE_B = (
    "Soy malísimo con los idiomas, incluso con el mio, tengo faltas de "
    "ortografía hasta en los exámenes orales."
)


def test_fixture_real_contiene_los_chistes_usados_en_los_tests():
    """Confirma que los fragmentos no se inventaron: están literales en el .md real."""
    contenido = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "Me llamo Sergio Afonso" in contenido
    assert "Soy malísimo con los idiomas" in contenido


# ---------------------------------------------------------------------------
# calcular_hash_normalizado
# ---------------------------------------------------------------------------

class TestCalcularHashNormalizado:
    def test_es_determinista(self):
        assert calcular_hash_normalizado(CHISTE_A) == calcular_hash_normalizado(CHISTE_A)

    def test_textos_distintos_dan_hashes_distintos(self):
        assert calcular_hash_normalizado(CHISTE_A) != calcular_hash_normalizado(CHISTE_B)

    def test_ignora_espacios_sobrantes_en_los_bordes(self):
        assert calcular_hash_normalizado(CHISTE_A) == calcular_hash_normalizado(f"  {CHISTE_A}\n")

    def test_es_sensible_a_may_minusculas(self):
        """El hash NO normaliza más allá de strip() — conserva timing/muletillas (§Silver)."""
        assert calcular_hash_normalizado(CHISTE_A) != calcular_hash_normalizado(CHISTE_A.upper())

    def test_texto_vacio_lanza_reconciliacion_error(self):
        with pytest.raises(ReconciliacionError):
            calcular_hash_normalizado("   ")


# ---------------------------------------------------------------------------
# similitud_coseno
# ---------------------------------------------------------------------------

class TestSimilitudCoseno:
    def test_vectores_identicos_dan_similitud_1(self):
        assert similitud_coseno([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_vectores_opuestos_dan_similitud_menos_1(self):
        assert similitud_coseno([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_vectores_ortogonales_dan_similitud_0(self):
        assert similitud_coseno([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_dimensiones_distintas_lanza_error(self):
        with pytest.raises(ReconciliacionError):
            similitud_coseno([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_vector_cero_lanza_error(self):
        with pytest.raises(ReconciliacionError):
            similitud_coseno([0.0, 0.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# decidir_reconciliacion
# ---------------------------------------------------------------------------

EMBEDDING_NUEVO = [1.0, 0.0, 0.0]
EMBEDDING_MUY_PARECIDO = [0.99, 0.05, 0.0]  # similitud > UMBRAL con EMBEDDING_NUEVO
EMBEDDING_DISTINTO = [0.0, 1.0, 0.0]  # ortogonal, similitud 0


class TestDecidirReconciliacion:
    def test_hash_coincide_decide_igual_sin_mirar_embedding(self):
        hash_existente = calcular_hash_normalizado(CHISTE_A)
        candidatos = [
            {"id": "existente-1", "hash_normalizado": hash_existente, "embedding": EMBEDDING_DISTINTO}
        ]

        resultado = decidir_reconciliacion(CHISTE_A, EMBEDDING_NUEVO, candidatos)

        assert resultado.decision == "IGUAL"
        assert resultado.chiste_id == "existente-1"
        assert resultado.similitud == 1.0

    def test_similitud_por_encima_del_umbral_decide_cambiado(self):
        candidatos = [
            {"id": "existente-2", "hash_normalizado": "otro-hash", "embedding": EMBEDDING_MUY_PARECIDO}
        ]

        resultado = decidir_reconciliacion(CHISTE_A, EMBEDDING_NUEVO, candidatos)

        assert resultado.decision == "CAMBIADO"
        assert resultado.chiste_id == "existente-2"
        assert resultado.similitud >= UMBRAL_SIMILITUD_CAMBIADO

    def test_similitud_por_debajo_del_umbral_decide_nuevo(self):
        candidatos = [
            {"id": "existente-3", "hash_normalizado": "otro-hash", "embedding": EMBEDDING_DISTINTO}
        ]

        resultado = decidir_reconciliacion(CHISTE_A, EMBEDDING_NUEVO, candidatos)

        assert resultado.decision == "NUEVO"
        assert resultado.chiste_id is None

    def test_sin_candidatos_decide_nuevo(self):
        resultado = decidir_reconciliacion(CHISTE_A, EMBEDDING_NUEVO, [])
        assert resultado.decision == "NUEVO"

    def test_candidato_sin_embedding_no_rompe_la_comparacion(self):
        """Una fila sin embedding aún calculado se ignora en la similitud, no revienta."""
        candidatos = [
            {"id": "sin-embedding", "hash_normalizado": "otro-hash", "embedding": None},
            {"id": "existente-4", "hash_normalizado": "otro-hash-2", "embedding": EMBEDDING_MUY_PARECIDO},
        ]

        resultado = decidir_reconciliacion(CHISTE_A, EMBEDDING_NUEVO, candidatos)

        assert resultado.decision == "CAMBIADO"
        assert resultado.chiste_id == "existente-4"

    def test_se_queda_con_el_candidato_de_mayor_similitud(self):
        candidatos = [
            {"id": "menos-parecido", "hash_normalizado": "h1", "embedding": [0.9, 0.1, 0.0]},
            {"id": "mas-parecido", "hash_normalizado": "h2", "embedding": EMBEDDING_MUY_PARECIDO},
        ]

        resultado = decidir_reconciliacion(CHISTE_A, EMBEDDING_NUEVO, candidatos)

        assert resultado.chiste_id == "mas-parecido"

    def test_resultado_lleva_hash_y_embedding_siempre(self):
        resultado = decidir_reconciliacion(CHISTE_A, EMBEDDING_NUEVO, [])
        assert isinstance(resultado, ResultadoReconciliacion)
        assert resultado.hash_normalizado == calcular_hash_normalizado(CHISTE_A)
        assert resultado.embedding == EMBEDDING_NUEVO

    def test_texto_vacio_lanza_reconciliacion_error(self):
        with pytest.raises(ReconciliacionError):
            decidir_reconciliacion("   ", EMBEDDING_NUEVO, [])


# ---------------------------------------------------------------------------
# reconciliar_chiste — orquestación con `generar_embedding_fn` inyectado
# ---------------------------------------------------------------------------

class TestReconciliarChisteSinRed:
    def test_llama_una_sola_vez_al_generador_de_embeddings(self):
        llamadas = []

        def generar_fake(texto: str) -> list:
            llamadas.append(texto)
            return EMBEDDING_NUEVO

        resultado = reconciliar_chiste(CHISTE_A, [], generar_embedding_fn=generar_fake)

        assert len(llamadas) == 1
        assert llamadas[0] == CHISTE_A
        assert resultado.decision == "NUEVO"

    def test_propaga_la_decision_igual_end_to_end(self):
        hash_existente = calcular_hash_normalizado(CHISTE_A)
        candidatos = [{"id": "existente-1", "hash_normalizado": hash_existente, "embedding": None}]

        resultado = reconciliar_chiste(
            CHISTE_A, candidatos, generar_embedding_fn=lambda texto: EMBEDDING_NUEVO
        )

        assert resultado.decision == "IGUAL"
        assert resultado.chiste_id == "existente-1"

    def test_texto_vacio_lanza_error_sin_llamar_al_generador(self):
        def generar_fake(texto: str) -> list:
            raise AssertionError("no debería llamarse al generador con texto vacío")

        with pytest.raises(ReconciliacionError):
            reconciliar_chiste("   ", [], generar_embedding_fn=generar_fake)
