"""Tests unitarios de `src/jokes/taxonomias.py` — lógica pura, sin red.

Cubre (§Taxonomías, task 14): el criterio de parada binario
(`_encontrar_match`), la construcción de prompt/schema de cada intento, y la
orquestación del loop acotado (`resolver_taxonomia`) con `llamar_llm` y
`store` inyectados (dobles fijos, sin red — mismo patrón que
`tests/unit/jokes/test_silver.py`). El caso de éxito real contra Supabase (con
seed y limpieza) vive en `tests/integration/test_taxonomias_live.py`.

El string real reusado como input (`estructura_detectada="setup/punchline"`)
viene literal de la respuesta real de Silver (task 13) sobre el chiste real
de `tests/fixtures/Freskito-Informático.md` — no se inventa, ya está citado
en el paquete de contexto de esta task.
"""
import pytest

from src.jokes.taxonomias import (
    MAX_INTENTOS,
    PROPUESTO_POR_DEFAULT,
    ResultadoTaxonomia,
    TaxonomiasError,
    _build_prompt_con_contexto,
    _build_prompt_intento1,
    _build_schema,
    _encontrar_match,
    _normalizar_nombre,
    _validar_tipo_taxonomia,
    resolver_taxonomia,
)

# String real producido por Silver (task 13) sobre el chiste real de
# `Freskito-Informático.md` — ver `estructura_detectada` en el paquete de
# contexto de la task 14 (regla del proyecto: nunca inventar fixtures).
ESTRUCTURA_DETECTADA_REAL = "setup/punchline"
TEMA_REAL = "Idiomas y Tinder"


# ---------------------------------------------------------------------------
# _validar_tipo_taxonomia
# ---------------------------------------------------------------------------

class TestValidarTipoTaxonomia:
    @pytest.mark.parametrize("tipo", ["tema", "tecnica"])
    def test_acepta_valores_del_enum(self, tipo):
        assert _validar_tipo_taxonomia(tipo) == tipo

    def test_rechaza_valor_fuera_de_enum(self):
        with pytest.raises(TaxonomiasError, match="tipo inválido"):
            _validar_tipo_taxonomia("fuente")


# ---------------------------------------------------------------------------
# _normalizar_nombre
# ---------------------------------------------------------------------------

class TestNormalizarNombre:
    def test_colapsa_mayusculas_y_espacios(self):
        assert _normalizar_nombre("  Setup/Punchline  ") == "setup/punchline"

    def test_colapsa_espacios_internos_multiples(self):
        assert _normalizar_nombre("misdirection   giro") == "misdirection giro"


# ---------------------------------------------------------------------------
# _encontrar_match — el criterio de parada binario de P16
# ---------------------------------------------------------------------------

class TestEncontrarMatch:
    TABLA = [
        {"id": 1, "nombre": "setup/punchline"},
        {"id": 2, "nombre": "callback"},
    ]

    def test_match_por_nombre_exacto(self):
        fila = _encontrar_match("setup/punchline", self.TABLA)
        assert fila == {"id": 1, "nombre": "setup/punchline"}

    def test_match_por_nombre_normalizado_mayusculas_y_espacios(self):
        fila = _encontrar_match("  Setup/Punchline ", self.TABLA)
        assert fila["id"] == 1

    def test_match_por_id_numerico_como_string(self):
        fila = _encontrar_match("2", self.TABLA)
        assert fila == {"id": 2, "nombre": "callback"}

    def test_sin_match_devuelve_none(self):
        assert _encontrar_match("misdirection", self.TABLA) is None

    def test_marcador_sin_match_devuelve_none(self):
        assert _encontrar_match("SIN_MATCH", self.TABLA) is None

    def test_propuesto_none_devuelve_none(self):
        assert _encontrar_match(None, self.TABLA) is None

    def test_propuesto_vacio_devuelve_none(self):
        assert _encontrar_match("   ", self.TABLA) is None

    def test_tabla_vacia_nunca_matchea(self):
        assert _encontrar_match("setup/punchline", []) is None

    def test_id_numerico_inexistente_no_matchea_por_nombre(self):
        # "99" parece ID, no debe hacer fallback a comparación por nombre.
        assert _encontrar_match("99", self.TABLA) is None


# ---------------------------------------------------------------------------
# _build_prompt_intento1 / _build_prompt_con_contexto / _build_schema
# ---------------------------------------------------------------------------

class TestPromptsYSchema:
    TABLA = [
        {"id": 1, "nombre": "setup/punchline"},
        {"id": 2, "nombre": "callback"},
    ]

    def test_prompt_intento1_incluye_el_texto(self):
        prompt = _build_prompt_intento1(ESTRUCTURA_DETECTADA_REAL, "tecnica")
        assert ESTRUCTURA_DETECTADA_REAL in prompt

    def test_prompt_intento1_no_inyecta_nombres_de_la_tabla(self):
        # Intento 1 es deliberadamente sin contexto (§Esquema, punto 1): no
        # tiene ni idea de la tabla, así que "callback" no debe aparecer.
        prompt = _build_prompt_intento1(ESTRUCTURA_DETECTADA_REAL, "tecnica")
        assert "callback" not in prompt

    def test_prompt_intento1_distingue_tema_de_tecnica(self):
        prompt_tema = _build_prompt_intento1(TEMA_REAL, "tema")
        prompt_tecnica = _build_prompt_intento1(ESTRUCTURA_DETECTADA_REAL, "tecnica")
        assert "tema" in prompt_tema
        assert "técnica" in prompt_tecnica

    def test_prompt_con_contexto_inyecta_la_taxonomia_real_completa(self):
        prompt = _build_prompt_con_contexto(ESTRUCTURA_DETECTADA_REAL, "tecnica", self.TABLA)
        assert "id=1" in prompt
        assert "setup/punchline" in prompt
        assert "id=2" in prompt
        assert "callback" in prompt

    def test_prompt_con_contexto_incluye_instruccion_de_sin_match(self):
        prompt = _build_prompt_con_contexto(ESTRUCTURA_DETECTADA_REAL, "tecnica", self.TABLA)
        assert "SIN_MATCH" in prompt

    def test_prompt_con_contexto_tabla_vacia_no_rompe(self):
        prompt = _build_prompt_con_contexto(ESTRUCTURA_DETECTADA_REAL, "tecnica", [])
        assert "vacía" in prompt

    def test_schema_tiene_el_campo_esperado(self):
        schema = _build_schema()
        assert schema["type"] == "OBJECT"
        assert schema["required"] == ["nombre_propuesto"]
        assert schema["properties"]["nombre_propuesto"]["type"] == "STRING"


# ---------------------------------------------------------------------------
# resolver_taxonomia — orquestación con `store`/`llamar_llm` inyectados
# ---------------------------------------------------------------------------

class _FakeStore:
    """Doble de `SupabaseStore` — solo implementa lo que usa `taxonomias.py`."""

    def __init__(self, tabla: list[dict]):
        self._tabla = tabla
        self.candidatos_creados: list[dict] = []

    def listar_temas(self) -> list[dict]:
        return self._tabla

    def listar_tecnicas(self) -> list[dict]:
        return self._tabla

    def crear_candidato_taxonomia(self, **kwargs) -> dict:
        fila = {"id": len(self.candidatos_creados) + 1, "estado": "pendiente", **kwargs}
        self.candidatos_creados.append(fila)
        return fila


class TestResolverTaxonomiaSinRed:
    def test_texto_vacio_lanza_error_sin_llamar_al_llm(self):
        store = _FakeStore(tabla=[])

        def llamar_llm_fake(prompt, schema):
            raise AssertionError("no debería llamarse al LLM con texto vacío")

        with pytest.raises(TaxonomiasError):
            resolver_taxonomia("   ", "tecnica", store, llamar_llm=llamar_llm_fake)

    def test_tipo_invalido_lanza_error_sin_llamar_al_llm(self):
        store = _FakeStore(tabla=[])

        def llamar_llm_fake(prompt, schema):
            raise AssertionError("no debería llamarse al LLM con tipo inválido")

        with pytest.raises(TaxonomiasError):
            resolver_taxonomia("x", "fuente", store, llamar_llm=llamar_llm_fake)

    def test_match_en_intento_1_no_hace_mas_llamadas(self):
        tabla = [{"id": 1, "nombre": "setup/punchline"}]
        store = _FakeStore(tabla=tabla)
        llamadas = []

        def llamar_llm_fake(prompt, schema):
            llamadas.append(prompt)
            return {"nombre_propuesto": "setup/punchline"}

        resultado = resolver_taxonomia(
            ESTRUCTURA_DETECTADA_REAL, "tecnica", store, llamar_llm=llamar_llm_fake
        )

        assert resultado.match is True
        assert resultado.fila == {"id": 1, "nombre": "setup/punchline"}
        assert resultado.intentos == 1
        assert resultado.candidato is None
        assert len(llamadas) == 1, "match en intento 1 no debe llamar más veces al LLM"
        assert store.candidatos_creados == []

    def test_match_en_intento_2_inyecta_tabla_real(self):
        tabla = [{"id": 5, "nombre": "callback"}]
        store = _FakeStore(tabla=tabla)
        prompts_vistos = []

        def llamar_llm_fake(prompt, schema):
            prompts_vistos.append(prompt)
            if len(prompts_vistos) == 1:
                return {"nombre_propuesto": "misdirection"}  # falla en intento 1
            return {"nombre_propuesto": "callback"}  # acierta con contexto

        resultado = resolver_taxonomia("giro inesperado", "tecnica", store, llamar_llm=llamar_llm_fake)

        assert resultado.match is True
        assert resultado.intentos == 2
        assert resultado.fila == {"id": 5, "nombre": "callback"}
        assert len(prompts_vistos) == 2
        # El prompt del intento 2 sí inyecta la tabla real completa.
        assert "callback" in prompts_vistos[1]
        assert "id=5" in prompts_vistos[1]
        assert store.candidatos_creados == []

    def test_agota_los_3_intentos_y_encola_candidato(self):
        tabla = [{"id": 1, "nombre": "setup/punchline"}]
        store = _FakeStore(tabla=tabla)
        llamadas = []

        def llamar_llm_fake(prompt, schema):
            llamadas.append(prompt)
            return {"nombre_propuesto": "SIN_MATCH"}

        resultado = resolver_taxonomia(
            "un tema absolutamente inventado sin relación alguna", "tema", store,
            llamar_llm=llamar_llm_fake,
        )

        assert resultado.match is False
        assert resultado.fila is None
        assert resultado.intentos == MAX_INTENTOS
        assert len(llamadas) == MAX_INTENTOS, "nunca debe superar MAX_INTENTOS llamadas al LLM"

        assert len(store.candidatos_creados) == 1, "debe encolar exactamente un candidato"
        candidato = store.candidatos_creados[0]
        assert candidato["tipo"] == "tema"
        assert candidato["texto"] == "un tema absolutamente inventado sin relación alguna"
        assert candidato["propuesto_por"] == PROPUESTO_POR_DEFAULT

    def test_propuesto_por_es_inyectable(self):
        store = _FakeStore(tabla=[])

        def llamar_llm_fake(prompt, schema):
            return {"nombre_propuesto": "SIN_MATCH"}

        resolver_taxonomia(
            "otro tema sin match", "tema", store,
            llamar_llm=llamar_llm_fake, propuesto_por="flujo_c.historico",
        )

        assert store.candidatos_creados[0]["propuesto_por"] == "flujo_c.historico"

    def test_nunca_llama_a_crear_tema_o_tecnica_directamente(self):
        # El doble de store no expone ningún método de creación de
        # temas/tecnicas más que via candidatos_taxonomia — si el loop
        # intentara crear la fila directamente, fallaría con AttributeError
        # (no existe `crear_tema`/`crear_tecnica` en este doble deliberadamente).
        store = _FakeStore(tabla=[])
        assert not hasattr(store, "crear_tema")
        assert not hasattr(store, "crear_tecnica")

        def llamar_llm_fake(prompt, schema):
            return {"nombre_propuesto": "SIN_MATCH"}

        resultado = resolver_taxonomia(
            "tema sin match final", "tema", store, llamar_llm=llamar_llm_fake
        )
        assert resultado.match is False


def test_resultado_taxonomia_es_dataclass_inmutable():
    resultado = ResultadoTaxonomia(match=True, fila={"id": 1}, candidato=None, intentos=1)
    with pytest.raises(Exception):
        resultado.match = False
