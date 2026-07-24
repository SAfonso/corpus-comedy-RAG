"""Tests unitarios de `src/jokes/historico/coste.py` — dry-run de coste (task 26).

Cubre (`docs/specs/llm-policy.md` §Control de coste, `src/jokes/historico/SPEC.md`
§Coste): reconstrucción de los prompts de Segmentador/Silver sin llamar al LLM,
la heurística de conteo de tokens 100% offline, la estimación agregada por
documento/lote, y el gate de presupuesto — todo con un contador de tokens
inyectado (fake, determinista), sin red real (mismo patrón que `llamar_llm`
inyectado en `test_segmentador.py`/`test_silver.py`).

El texto real usado como fixture viene literal de
`tests/fixtures/Freskito-Informático.md` (task 17) — mismo fixture que ya usan
`test_segmentador.py` y `test_loader.py`, no se inventa uno nuevo (scope de la
task 26: reutilizar fixtures existentes).
"""
from pathlib import Path

import pytest

from src.jokes.historico import coste
from src.jokes.historico.coste import (
    PRECIO_EUR_POR_MILLON_TOKENS_DEFAULT,
    UMBRAL_TOKENS_DEFAULT,
    DecisionGate,
    EstimacionCoste,
    _prompts_segmentador_de_documento,
    _prompts_silver_de_documento,
    estimar_coste,
    estimar_tokens_heuristica,
    estimar_y_evaluar,
    evaluar_gate,
)
from src.jokes.historico.segmentador import _build_prompt as _build_prompt_segmentador
from src.jokes.historico.segmentador import _construir_ventanas
from src.jokes.silver import _build_prompt as _build_prompt_silver
from src.jokes.silver import _limpiar_marcado_historico

FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "fixtures"
    / "Freskito-Informático.md"
)
FIXTURE_CONTENT = FIXTURE_PATH.read_text(encoding="utf-8")

_PARRAFOS = [p.strip() for p in FIXTURE_CONTENT.split("\n\n") if p.strip()]


def _parrafo_con(marcador: str) -> str:
    coincidencias = [p for p in _PARRAFOS if marcador in p]
    assert coincidencias, f"marcador {marcador!r} no está en ningún párrafo del fixture"
    return coincidencias[0]


# - un chiste de un solo remate (línea 1),
CHISTE_UN_REMATE = _parrafo_con("Me llamo Sergio Afonso")
# - un tramo con DOS remates seguidos (línea 13),
CHISTE_DOS_REMATES = _parrafo_con("¿Sabéis a cuanto se cotizan")


def test_fixture_real_contiene_los_fragmentos_usados():
    assert "[REMATE]No un venenzolano con asma[/REMATE]" in FIXTURE_CONTENT
    assert FIXTURE_CONTENT.count("[REMATE]") == 20


# ---------------------------------------------------------------------------
# Reconstrucción de prompts — sin red, byte-idéntica a la de producción
# (importa las funciones "privadas" de segmentador.py/silver.py a propósito,
# ver docstring del módulo `coste.py`: la alternativa de duplicar la
# construcción del prompt arriesga divergencia silenciosa).
# ---------------------------------------------------------------------------


class TestPromptsSegmentador:
    def test_un_prompt_por_ventana(self):
        prompts = _prompts_segmentador_de_documento(CHISTE_DOS_REMATES)
        assert len(prompts) == 2

    def test_prompt_identico_al_de_produccion(self):
        ventanas = _construir_ventanas(CHISTE_UN_REMATE)
        prompts = _prompts_segmentador_de_documento(CHISTE_UN_REMATE)
        assert prompts == [_build_prompt_segmentador(ventanas[0].texto)]

    def test_documento_sin_remates_no_produce_prompts(self):
        assert _prompts_segmentador_de_documento("Texto sin marcado.") == []


class TestPromptsSilver:
    def test_un_prompt_por_ventana_proxy_de_llamadas_a_silver(self):
        # Proxy documentado: nº de [REMATE] ≈ nº de llamadas a Silver (no se
        # puede saber el número real de chistes sin segmentar de verdad).
        prompts = _prompts_silver_de_documento(CHISTE_DOS_REMATES)
        assert len(prompts) == 2

    def test_prompt_usa_la_ventana_completa_limpia_como_proxy_conservador(self):
        # No conocemos el offset real del setup sin llamar al LLM del
        # Segmentador; se usa la ventana COMPLETA (limpia de etiquetas) como
        # proxy — sobreestima ligeramente el texto real (más seguro para un
        # gate de presupuesto que subestimar).
        ventanas = _construir_ventanas(CHISTE_UN_REMATE)
        esperado = _build_prompt_silver(_limpiar_marcado_historico(ventanas[0].texto))
        prompts = _prompts_silver_de_documento(CHISTE_UN_REMATE)
        assert prompts == [esperado]

    def test_documento_sin_remates_no_produce_prompts(self):
        assert _prompts_silver_de_documento("Texto sin marcado.") == []


# ---------------------------------------------------------------------------
# estimar_tokens_heuristica — 100% offline, sin red ni credenciales
# ---------------------------------------------------------------------------


class TestEstimarTokensHeuristica:
    def test_cadena_vacia_es_cero_tokens(self):
        assert estimar_tokens_heuristica("") == 0

    def test_aproximadamente_cuatro_caracteres_por_token(self):
        texto = "a" * 40
        assert estimar_tokens_heuristica(texto) == 10

    def test_redondea_hacia_arriba_para_no_subestimar(self):
        # 1 carácter no puede ser 0 tokens: siempre al menos 1 si hay texto.
        assert estimar_tokens_heuristica("a") == 1


# ---------------------------------------------------------------------------
# estimar_coste — agregación por documento/lote, contador inyectado
# ---------------------------------------------------------------------------


def _contador_por_longitud(llamadas: list):
    """Fake determinista: 1 token por carácter, y registra cada texto pasado."""

    def contar(texto: str) -> int:
        llamadas.append(texto)
        return len(texto)

    return contar


class TestEstimarCoste:
    def test_documento_sin_remates_da_estimacion_vacia(self):
        estimacion = estimar_coste(
            [{"name": "doc.md", "content": "Sin marcado."}],
            contar_tokens=lambda t: len(t),
        )
        assert isinstance(estimacion, EstimacionCoste)
        assert estimacion.documentos == 1
        assert estimacion.llamadas_segmentador == 0
        assert estimacion.llamadas_silver == 0
        assert estimacion.tokens_totales == 0
        assert estimacion.coste_eur_estimado == 0.0

    def test_cuenta_llamadas_y_tokens_agregados(self):
        llamadas = []
        contar = _contador_por_longitud(llamadas)

        estimacion = estimar_coste(
            [{"name": "doc.md", "content": CHISTE_DOS_REMATES}],
            contar_tokens=contar,
        )

        assert estimacion.documentos == 1
        assert estimacion.llamadas_segmentador == 2
        assert estimacion.llamadas_silver == 2
        # El contador se invocó una vez por cada prompt (2 + 2 = 4).
        assert len(llamadas) == 4
        assert estimacion.tokens_totales == estimacion.tokens_segmentador + estimacion.tokens_silver
        assert estimacion.tokens_totales > 0

    def test_no_llama_a_la_red_real_solo_al_contador_inyectado(self):
        # Si `estimar_coste` intentara usar el SDK real sin inyección,
        # `contar_tokens=` obligatorio en este test lo evita: el fake nunca
        # toca `google.genai`.
        llamadas = []
        contar = _contador_por_longitud(llamadas)
        estimar_coste(
            [{"name": "doc.md", "content": FIXTURE_CONTENT}],
            contar_tokens=contar,
        )
        # 20 remates -> 20 prompts de segmentador + 20 de silver = 40 llamadas.
        assert len(llamadas) == 40

    def test_agrega_varios_documentos(self):
        estimacion = estimar_coste(
            [
                {"name": "a.md", "content": CHISTE_UN_REMATE},
                {"name": "b.md", "content": CHISTE_DOS_REMATES},
            ],
            contar_tokens=lambda t: len(t),
        )
        assert estimacion.documentos == 2
        assert estimacion.llamadas_segmentador == 1 + 2
        assert estimacion.llamadas_silver == 1 + 2

    def test_coste_eur_proporcional_a_tokens_y_precio(self):
        estimacion = estimar_coste(
            [{"name": "doc.md", "content": CHISTE_UN_REMATE}],
            contar_tokens=lambda t: 1_000_000,  # exactamente 1M tokens/llamada
            precio_eur_por_millon_tokens=2.0,
        )
        # 1 remate -> 1 prompt segmentador + 1 prompt silver = 2M tokens.
        assert estimacion.tokens_totales == 2_000_000
        assert estimacion.coste_eur_estimado == pytest.approx(4.0)

    def test_precio_por_defecto_es_el_documentado(self):
        estimacion = estimar_coste(
            [{"name": "doc.md", "content": CHISTE_UN_REMATE}],
            contar_tokens=lambda t: 1_000_000,
        )
        assert estimacion.coste_eur_estimado == pytest.approx(
            2 * PRECIO_EUR_POR_MILLON_TOKENS_DEFAULT
        )


# ---------------------------------------------------------------------------
# evaluar_gate — decisión permitir/abortar, umbral configurable
# ---------------------------------------------------------------------------


def _estimacion(tokens_totales: int, coste_eur: float = 0.0) -> EstimacionCoste:
    return EstimacionCoste(
        documentos=1,
        llamadas_segmentador=1,
        llamadas_silver=1,
        tokens_segmentador=tokens_totales // 2,
        tokens_silver=tokens_totales - tokens_totales // 2,
        tokens_totales=tokens_totales,
        coste_eur_estimado=coste_eur,
    )


class TestEvaluarGate:
    def test_por_debajo_del_umbral_permite(self):
        decision = evaluar_gate(_estimacion(100), umbral_tokens=1000)
        assert isinstance(decision, DecisionGate)
        assert decision.permitir is True
        assert decision.umbral_tokens == 1000

    def test_por_encima_del_umbral_aborta(self):
        decision = evaluar_gate(_estimacion(2000), umbral_tokens=1000)
        assert decision.permitir is False
        assert "1000" in decision.razon or "1_000" in decision.razon.replace(",", "_")

    def test_umbral_por_defecto_si_no_se_pasa_nada(self):
        decision = evaluar_gate(_estimacion(100), entorno={})
        assert decision.umbral_tokens == UMBRAL_TOKENS_DEFAULT

    def test_variable_de_entorno_tiene_prioridad_sobre_el_default(self):
        decision = evaluar_gate(
            _estimacion(500), entorno={"HISTORICO_COSTE_MAX_TOKENS": "200"}
        )
        assert decision.umbral_tokens == 200
        assert decision.permitir is False

    def test_parametro_explicito_tiene_prioridad_sobre_la_variable_de_entorno(self):
        decision = evaluar_gate(
            _estimacion(500),
            umbral_tokens=1000,
            entorno={"HISTORICO_COSTE_MAX_TOKENS": "200"},
        )
        assert decision.umbral_tokens == 1000
        assert decision.permitir is True

    def test_umbral_eur_opcional_tambien_aborta(self):
        decision = evaluar_gate(
            _estimacion(100, coste_eur=50.0),
            umbral_tokens=10_000,
            umbral_eur=10.0,
        )
        assert decision.permitir is False
        assert decision.umbral_eur == 10.0

    def test_exactamente_en_el_umbral_permite(self):
        # Frontera inclusiva: igual al umbral no es "superarlo".
        decision = evaluar_gate(_estimacion(1000), umbral_tokens=1000)
        assert decision.permitir is True


# ---------------------------------------------------------------------------
# estimar_y_evaluar — conveniencia end-to-end para el futuro caller (task 28)
# ---------------------------------------------------------------------------


class TestEstimarYEvaluar:
    def test_combina_estimacion_y_gate(self):
        decision = estimar_y_evaluar(
            [{"name": "doc.md", "content": FIXTURE_CONTENT}],
            contar_tokens=lambda t: len(t),
            umbral_tokens=10,
        )
        assert isinstance(decision, DecisionGate)
        assert decision.permitir is False
        assert decision.estimacion.documentos == 1
        assert decision.estimacion.llamadas_segmentador == 20

    def test_permite_con_umbral_alto(self):
        decision = estimar_y_evaluar(
            [{"name": "doc.md", "content": CHISTE_UN_REMATE}],
            contar_tokens=lambda t: len(t),
            umbral_tokens=10_000_000,
        )
        assert decision.permitir is True
