"""Tests para `scripts/validate_corpus.py` (task 20 -> task 62, gate de
validación de CONTENIDO de Flujo A).

Contrato (`src/theory/SPEC.md` §"`validate_corpus.py` sin `manifest.json`
(task 62)", `CHECKPOINTS.md`): desde P25 el gate por defecto valida filas
VIGENTES de `silver.teoria_documentos` + su objeto en `silver-teoria`
(Supabase), no `v{N}/documents/*.txt` + `manifest.json` en disco.

Dos familias de tests:

1. **Los 7 checks de contenido** (`check_sin_timestamps` ... `check_idiomas_permitidos`):
   siguen siendo funciones puras sobre `list[DocumentoLeido]`, sin cambio de
   cuerpo. Se testean con fixtures reales generadas con la cadena real ya
   aprobada -- `parse_whisperx_transcript` (task 3) -> `detect_subtypes`
   (task 7) -> `clean_fragments` (task 8) -> `normalize_language` (task 9,
   traductor espía no-op) -> `render_document` (task 11) -- sobre
   `tests/fixtures/sample_transcript.txt`, escritas como `.md` sueltos en un
   `tmp_path` (modo ruta local: ya NO `v{N}/documents/` + `manifest.json`,
   ver docstring del módulo).
2. **Modo Supabase** (`validar_supabase`, `check_fila_objeto_coherente`,
   `TeoriaStore.listar_documentos_vigentes`): con un doble de `TeoriaStore`
   inyectado, sin red -- mismo patrón que
   `tests/unit/scripts/test_reprocesar_bronze_pendiente.py`.

El fixture real (9 fragmentos, 88 palabras totales) es más corto que
`MIN_PALABRAS`, así que cada documento del caso feliz repite la lista de
fragmentos reales dos veces (mismo texto real, ningún contenido inventado)
para superar el umbral de 100 palabras sin necesitar un fixture más largo.
Los casos de FALLO de cada check se construyen mutando una COPIA de ese
contenido real ya generado (borra un campo, trunca/repite texto, cambia un
valor de un campo real) en vez de inventar contenido nuevo desde cero.
"""
import hashlib
from pathlib import Path

from scripts.validate_corpus import (
    CHECKS,
    DocumentoLeido,
    FilaSilverLeida,
    TopologiaStore,
    cargar_documentos,
    check_cabecera_completa,
    check_claves_idempotencia_unicas,
    check_filas_tienen_objeto,
    check_fila_objeto_coherente,
    check_idiomas_permitidos,
    check_max_palabras,
    check_min_palabras,
    check_silver_tiene_bronze_historico,
    check_silver_tiene_bronze_teoria,
    check_sin_duplicados,
    check_sin_speaker_tags,
    check_sin_timestamps,
    leer_documento,
    main,
    validar_ruta_local,
    validar_supabase,
    validar_topologia,
)
from src.theory.cleaners.transcript_cleaner import clean_fragments
from src.theory.detectors.subtype_detector import detect_subtypes
from src.theory.normalizers.format_normalizer import render_document
from src.theory.normalizers.language_normalizer import FragmentoNormalizado, normalize_language
from src.theory.parsers.whisperx_parser import parse_whisperx_transcript
from src.theory.teoria_store import TeoriaStoreError
from src.utils.document_store import DocumentStoreError

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_TXT = FIXTURES_DIR / "sample_transcript.txt"


def _traductor_no_op(texto: str, idioma_origen: str) -> str:
    raise AssertionError(
        "no debería traducirse: el fixture sample_transcript.txt ya está en español"
    )


def _fragmentos_reales() -> list[FragmentoNormalizado]:
    """Cadena real completa (mismo patrón que test_format_normalizer.py):
    9 fragmentos reales, 88 palabras totales."""
    texto = parse_whisperx_transcript(SAMPLE_TXT).texto
    fragmentos_subtipo = detect_subtypes(texto)
    fragmentos_limpios = clean_fragments(fragmentos_subtipo)
    return normalize_language(fragmentos_limpios, traductor=_traductor_no_op)


def _contenido_md_real(fuente: str, repeticiones: int = 2) -> str:
    """El `.md` real (cabecera YAML + cuerpo) que produce `render_document`
    para un documento cuyo cuerpo repite los fragmentos reales
    `repeticiones` veces (176 palabras con `repeticiones=2`, > MIN_PALABRAS)
    -- mismo texto real, sin inventar contenido nuevo."""
    fragmentos = _fragmentos_reales() * repeticiones
    return render_document(
        fragmentos,
        fuente=fuente,
        tipo_fuente="transcripcion_curso",
        autor=None,
        licencia="personal_only",
    )


def _escribir_md(directorio: Path, nombre: str, contenido: str) -> Path:
    ruta = directorio / nombre
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


# --- cargar_documentos / leer_documento (modo ruta local) --------------------


def test_cargar_documentos_lee_los_md_del_directorio_directamente(tmp_path):
    _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    _escribir_md(tmp_path, "doc2.md", _contenido_md_real("Sesión 2"))

    documentos = cargar_documentos(tmp_path)

    assert len(documentos) == 2
    assert all(isinstance(d, DocumentoLeido) for d in documentos)
    assert {d.path_relativo for d in documentos} == {"doc1.md", "doc2.md"}


def test_cargar_documentos_ignora_ficheros_que_no_son_md(tmp_path):
    _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    (tmp_path / "notas.txt").write_text("esto no es un .md", encoding="utf-8")

    documentos = cargar_documentos(tmp_path)

    assert len(documentos) == 1
    assert documentos[0].path_relativo == "doc1.md"


def test_cargar_documentos_directorio_inexistente_devuelve_vacio(tmp_path):
    assert cargar_documentos(tmp_path / "no-existe") == []


def test_leer_documento_parsea_cabecera_y_cuerpo_reales(tmp_path):
    ruta = _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))

    doc = leer_documento(ruta, "doc1.md")

    assert doc.metadatos["tipo_fuente"] == "transcripcion_curso"
    assert doc.metadatos["licencia"] == "personal_only"
    assert doc.metadatos["autor"] is None
    assert len(doc.fragmentos_meta) == 18  # 9 fragmentos reales x 2 repeticiones
    assert doc.fragmentos_meta[0]["subtipo"] in ("explicacion", "ejemplo")
    assert "Hola, bienvenidas y bienvenidos a este curso." in doc.cuerpo


# --- Check 1: timestamps ----------------------------------------------------


def test_check_sin_timestamps_falla_si_queda_un_timestamp(tmp_path):
    contenido = _contenido_md_real("Sesión 1") + "\n[7.42s] texto colado sin limpiar\n"
    ruta = _escribir_md(tmp_path, "doc1.md", contenido)

    documentos = [leer_documento(ruta, "doc1.md")]
    resultado = check_sin_timestamps(documentos)

    assert not resultado.ok
    assert any("timestamp" in d for d in resultado.detalles)


def test_check_sin_timestamps_ok_con_contenido_real(tmp_path):
    ruta = _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    resultado = check_sin_timestamps([leer_documento(ruta, "doc1.md")])
    assert resultado.ok


# --- Check 2: speaker tags ---------------------------------------------------


def test_check_sin_speaker_tags_falla_si_queda_un_speaker_tag(tmp_path):
    contenido = _contenido_md_real("Sesión 1") + "\nSPEAKER_00: texto colado sin limpiar\n"
    ruta = _escribir_md(tmp_path, "doc1.md", contenido)

    resultado = check_sin_speaker_tags([leer_documento(ruta, "doc1.md")])

    assert not resultado.ok
    assert any("SPEAKER" in d for d in resultado.detalles)


# --- Check 3: cabecera completa ----------------------------------------------


def test_check_cabecera_completa_falla_si_falta_licencia(tmp_path):
    contenido = _contenido_md_real("Sesión 1")
    lineas = [l for l in contenido.splitlines(keepends=True) if not l.startswith("licencia:")]
    ruta = _escribir_md(tmp_path, "doc1.md", "".join(lineas))

    resultado = check_cabecera_completa([leer_documento(ruta, "doc1.md")])

    assert not resultado.ok
    assert any("licencia" in d for d in resultado.detalles)


def test_check_cabecera_completa_ok_con_cabecera_real_intacta(tmp_path):
    ruta = _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    resultado = check_cabecera_completa([leer_documento(ruta, "doc1.md")])
    assert resultado.ok


# --- Check 4: mínimo de palabras ---------------------------------------------


def test_check_min_palabras_falla_si_el_cuerpo_se_trunca(tmp_path):
    contenido = _contenido_md_real("Sesión 1")
    cabecera, _, cuerpo = contenido.partition("\n---\n")
    cuerpo_truncado = " ".join(cuerpo.split()[:10])
    ruta = _escribir_md(tmp_path, "doc1.md", cabecera + "\n---\n" + cuerpo_truncado)

    resultado = check_min_palabras([leer_documento(ruta, "doc1.md")])

    assert not resultado.ok
    assert any("palabras" in d for d in resultado.detalles)


def test_check_min_palabras_ok_con_documento_real_de_176_palabras(tmp_path):
    ruta = _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    resultado = check_min_palabras([leer_documento(ruta, "doc1.md")])
    assert resultado.ok


# --- Check 5: máximo de palabras ---------------------------------------------


def test_check_max_palabras_falla_si_el_cuerpo_se_concatena_de_mas(tmp_path):
    # 9 fragmentos reales repetidos suficientes veces para superar
    # MAX_PALABRAS=50_000 (88 palabras/pasada * 600 > 50_000) -- mismo texto
    # real, sin inventar contenido, simulando un error de concatenación real.
    contenido = _contenido_md_real("Curso con error de concatenación", repeticiones=600)
    ruta = _escribir_md(tmp_path, "doc1.md", contenido)

    resultado = check_max_palabras([leer_documento(ruta, "doc1.md")])

    assert not resultado.ok
    assert any("palabras" in d for d in resultado.detalles)


def test_check_max_palabras_ok_con_documento_real_de_176_palabras(tmp_path):
    ruta = _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    resultado = check_max_palabras([leer_documento(ruta, "doc1.md")])
    assert resultado.ok


# --- Check 6: duplicados ------------------------------------------------------


def test_check_sin_duplicados_falla_si_dos_ficheros_son_identicos(tmp_path):
    contenido = _contenido_md_real("Sesión 1")
    ruta1 = _escribir_md(tmp_path, "doc1.md", contenido)
    ruta2 = _escribir_md(tmp_path, "doc2.md", contenido)

    documentos = [leer_documento(ruta1, "doc1.md"), leer_documento(ruta2, "doc2.md")]
    resultado = check_sin_duplicados(documentos)

    assert not resultado.ok
    assert any("duplicado" in d for d in resultado.detalles)


def test_check_sin_duplicados_ok_con_dos_documentos_reales_distintos(tmp_path):
    ruta1 = _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    ruta2 = _escribir_md(tmp_path, "doc2.md", _contenido_md_real("Sesión 2"))

    documentos = [leer_documento(ruta1, "doc1.md"), leer_documento(ruta2, "doc2.md")]
    resultado = check_sin_duplicados(documentos)

    assert resultado.ok


# --- Check 7: idiomas permitidos ----------------------------------------------


def test_check_idiomas_permitidos_falla_con_idioma_fuera_de_la_lista(tmp_path):
    contenido = _contenido_md_real("Sesión 1")
    mutado = contenido.replace('idioma_fragmento: "es"', 'idioma_fragmento: "fr"', 1)
    assert mutado != contenido  # confirma que la sustitución encontró algo real
    ruta = _escribir_md(tmp_path, "doc1.md", mutado)

    resultado = check_idiomas_permitidos([leer_documento(ruta, "doc1.md")])

    assert not resultado.ok
    assert any("fr" in d for d in resultado.detalles)


def test_check_idiomas_permitidos_ok_con_documento_real_en_es(tmp_path):
    ruta = _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    resultado = check_idiomas_permitidos([leer_documento(ruta, "doc1.md")])
    assert resultado.ok


# --- validar_ruta_local: los 7 checks juntos, sin red ------------------------


def test_validar_ruta_local_caso_feliz_los_7_checks_pasan(tmp_path):
    _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))
    _escribir_md(tmp_path, "doc2.md", _contenido_md_real("Sesión 2"))

    resultados = validar_ruta_local(tmp_path)

    nombres = {r.nombre for r in resultados}
    assert nombres == {
        "sin_timestamps",
        "sin_speaker_tags",
        "cabecera_completa",
        "min_palabras",
        "max_palabras",
        "sin_duplicados",
        "idiomas_permitidos",
    }
    assert len(resultados) == len(CHECKS) == 7
    for resultado in resultados:
        assert resultado.ok, f"{resultado.nombre} falló inesperadamente: {resultado.detalles}"


def test_validar_ruta_local_directorio_vacio_no_falla(tmp_path):
    resultados = validar_ruta_local(tmp_path)
    assert len(resultados) == 7
    for resultado in resultados:
        assert resultado.ok  # ningún check falla sobre una lista vacía de documentos


# --- check_fila_objeto_coherente (check 8, modo Supabase) --------------------


def test_check_fila_objeto_coherente_ok_si_hash_coincide():
    contenido = b"contenido de prueba"
    fila = {"object_path": "local_legacy/h1/doc.md", "hash_md5": hashlib.md5(contenido).hexdigest()}
    filas_leidas = [
        FilaSilverLeida(
            fila=fila,
            contenido_bytes=contenido,
            documento=DocumentoLeido("local_legacy/h1/doc.md", "x", "", "x", {}, []),
        )
    ]

    resultado = check_fila_objeto_coherente(filas_leidas)

    assert resultado.ok


def test_check_fila_objeto_coherente_falla_si_el_hash_no_coincide():
    contenido = b"contenido de prueba"
    fila = {"object_path": "local_legacy/h1/doc.md", "hash_md5": "hash-que-no-es-el-real"}
    filas_leidas = [
        FilaSilverLeida(
            fila=fila,
            contenido_bytes=contenido,
            documento=DocumentoLeido("local_legacy/h1/doc.md", "x", "", "x", {}, []),
        )
    ]

    resultado = check_fila_objeto_coherente(filas_leidas)

    assert not resultado.ok
    assert any("local_legacy/h1/doc.md" in d for d in resultado.detalles)
    assert any("no coincide" in d for d in resultado.detalles)


def test_check_fila_objeto_coherente_falla_si_el_objeto_esta_ausente():
    fila = {"object_path": "local_legacy/h1/doc.md", "hash_md5": "cualquiera"}
    filas_leidas = [
        FilaSilverLeida(fila=fila, contenido_bytes=None, documento=None, error_descarga="404 Not Found")
    ]

    resultado = check_fila_objeto_coherente(filas_leidas)

    assert not resultado.ok
    assert any("no encontrado" in d for d in resultado.detalles)
    assert any("404" in d for d in resultado.detalles)


def test_check_fila_objeto_coherente_ok_con_lista_vacia():
    assert check_fila_objeto_coherente([]).ok


# --- validar_supabase (orquestación con doble de TeoriaStore) ----------------


class _StoreFake:
    """Doble mínimo de `TeoriaStore`: `filas_vigentes` es lo que devuelve
    `listar_documentos_vigentes`; `objetos` mapea `object_path` -> bytes (o
    una excepción a lanzar, simulando un 404 de Storage)."""

    def __init__(self, filas_vigentes, objetos):
        self._filas_vigentes = filas_vigentes
        self._objetos = objetos

    def listar_documentos_vigentes(self):
        return self._filas_vigentes

    def descargar_documento(self, bucket, object_path):
        valor = self._objetos[object_path]
        if isinstance(valor, Exception):
            raise valor
        return valor


def _fila_silver(object_path: str, contenido_md: str) -> dict:
    return {
        "id": object_path,
        "bucket": "silver-teoria",
        "object_path": object_path,
        "hash_md5": hashlib.md5(contenido_md.encode("utf-8")).hexdigest(),
    }


def test_validar_supabase_caso_feliz_8_checks_incluido_fila_objeto_coherente():
    contenido1 = _contenido_md_real("Sesión 1")
    contenido2 = _contenido_md_real("Sesión 2")
    fila1 = _fila_silver("doc1.md", contenido1)
    fila2 = _fila_silver("doc2.md", contenido2)
    store = _StoreFake(
        filas_vigentes=[fila1, fila2],
        objetos={
            "doc1.md": contenido1.encode("utf-8"),
            "doc2.md": contenido2.encode("utf-8"),
        },
    )

    resultados = validar_supabase(store)

    nombres = {r.nombre for r in resultados}
    assert nombres == {
        "sin_timestamps",
        "sin_speaker_tags",
        "cabecera_completa",
        "min_palabras",
        "max_palabras",
        "sin_duplicados",
        "idiomas_permitidos",
        "fila_objeto_coherente",
    }
    assert len(resultados) == 8
    for resultado in resultados:
        assert resultado.ok, f"{resultado.nombre} falló inesperadamente: {resultado.detalles}"


def test_validar_supabase_detecta_hash_no_coincide_sin_tumbar_los_demas_checks():
    contenido1 = _contenido_md_real("Sesión 1")
    fila1 = _fila_silver("doc1.md", contenido1)
    fila1["hash_md5"] = "hash-corrupto"  # la fila dice un hash que no es el del objeto real
    store = _StoreFake(filas_vigentes=[fila1], objetos={"doc1.md": contenido1.encode("utf-8")})

    resultados = validar_supabase(store)
    por_nombre = {r.nombre: r for r in resultados}

    assert not por_nombre["fila_objeto_coherente"].ok
    assert por_nombre["sin_timestamps"].ok  # el resto de checks no se ve afectado


def test_validar_supabase_objeto_ausente_no_rompe_la_ejecucion():
    contenido1 = _contenido_md_real("Sesión 1")
    fila1 = _fila_silver("doc1.md", contenido1)
    fila_rota = _fila_silver("doc-roto.md", "no importa")
    store = _StoreFake(
        filas_vigentes=[fila1, fila_rota],
        objetos={
            "doc1.md": contenido1.encode("utf-8"),
            "doc-roto.md": Exception("404: objeto no encontrado"),
        },
    )

    resultados = validar_supabase(store)
    por_nombre = {r.nombre: r for r in resultados}

    assert not por_nombre["fila_objeto_coherente"].ok
    assert any("doc-roto.md" in d for d in por_nombre["fila_objeto_coherente"].detalles)
    # el resto de checks corre igual sobre las filas que SÍ se pudieron leer
    assert por_nombre["sin_timestamps"].ok


def test_validar_supabase_sin_filas_vigentes_devuelve_todo_ok():
    store = _StoreFake(filas_vigentes=[], objetos={})
    resultados = validar_supabase(store)
    assert len(resultados) == 8
    for resultado in resultados:
        assert resultado.ok


# --- main() (CLI) --------------------------------------------------------------


def test_main_modo_ruta_local_devuelve_0_con_fixtures_validos(tmp_path, capsys):
    _escribir_md(tmp_path, "doc1.md", _contenido_md_real("Sesión 1"))

    codigo = main([str(tmp_path)])
    salida = capsys.readouterr().out

    assert codigo == 0
    assert "7/7 checks OK (modo ruta local)" in salida
    assert "no aplica en modo ruta local" in salida


def test_main_modo_ruta_local_devuelve_1_si_un_check_falla(tmp_path, capsys):
    contenido = _contenido_md_real("Sesión 1") + "\n[7.42s] texto colado sin limpiar\n"
    _escribir_md(tmp_path, "doc1.md", contenido)

    codigo = main([str(tmp_path)])

    assert codigo == 1


def test_main_modo_ruta_local_directorio_vacio_devuelve_0(tmp_path, capsys):
    codigo = main([str(tmp_path)])
    salida = capsys.readouterr().out

    assert codigo == 0
    assert "7/7 checks OK (modo ruta local)" in salida


def test_main_modo_supabase_con_store_inyectado_devuelve_0(capsys):
    contenido1 = _contenido_md_real("Sesión 1")
    fila1 = _fila_silver("doc1.md", contenido1)
    store = _StoreFake(filas_vigentes=[fila1], objetos={"doc1.md": contenido1.encode("utf-8")})

    codigo = main([], store=store)
    salida = capsys.readouterr().out

    assert codigo == 0
    assert "8/8 checks OK (modo Supabase)" in salida


def test_main_modo_supabase_con_store_inyectado_devuelve_1_si_falla_un_check(capsys):
    contenido1 = _contenido_md_real("Sesión 1")
    fila1 = _fila_silver("doc1.md", contenido1)
    fila1["hash_md5"] = "hash-corrupto"
    store = _StoreFake(filas_vigentes=[fila1], objetos={"doc1.md": contenido1.encode("utf-8")})

    codigo = main([], store=store)

    assert codigo == 1


def test_main_sin_argumento_ni_store_falla_claro_por_falta_de_credenciales(monkeypatch, capsys):
    """Sin `ruta` ni `store` inyectado, `main` construye un `TeoriaStore()`
    real -- en este entorno de test no hay `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`
    configuradas, así que debe fallar con un `TeoriaStoreError` ya capturado
    (mensaje de configuración claro, exit code 1), nunca un traceback."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    codigo = main([])
    salida = capsys.readouterr().out

    assert codigo == 1
    assert "SUPABASE_URL" in salida or "SUPABASE_SERVICE_KEY" in salida


def test_teoria_store_error_es_importable_desde_el_modulo():
    """`TeoriaStoreError` es la excepción que `main` captura explícitamente
    (ver docstring del módulo) -- confirma que sigue siendo la clase real de
    `src/theory/teoria_store.py`, no una redefinición local."""
    assert issubclass(TeoriaStoreError, RuntimeError)


# =============================================================================
# Modo topología (task 68) -- NO valida contenido (eso es la task 62, arriba,
# intacta): valida que cada fila Silver tenga su Bronze de origen, que cada
# fila (Bronze y Silver, teoría e histórico) tenga su objeto en Storage y que
# no haya claves de idempotencia duplicadas dentro de una tabla. Filas
# sintéticas con las columnas reales documentadas en `src/theory/SPEC.md`
# §"bronze/silver.teoria_documentos" y `src/jokes/historico/SPEC.md` §"Cómo
# referencia Silver a su Bronze" -- no hay fixtures de fichero que leer aquí
# porque estos checks son puramente relacionales sobre las filas, igual que
# los tests de `tests/unit/utils/test_document_store.py`.
# =============================================================================


def _fila_bronze_teoria(hash_md5: str, *, object_path: str = None, bucket: str = "bronze-teoria", drive_file_id=None, modified_time=None) -> dict:
    return {
        "id": f"bronze-{hash_md5}",
        "bucket": bucket,
        "object_path": object_path or f"local_legacy/{hash_md5}/doc.pdf",
        "drive_file_id": drive_file_id,
        "modified_time": modified_time,
        "hash_md5": hash_md5,
    }


def _fila_silver_teoria(origen_hash_md5: str, *, hash_md5: str = "silver-hash", object_path: str = None, bucket: str = "silver-teoria") -> dict:
    return {
        "id": f"silver-{origen_hash_md5}",
        "bucket": bucket,
        "object_path": object_path or f"local_legacy/{hash_md5}/doc.md",
        "drive_file_id": None,
        "modified_time": None,
        "hash_md5": hash_md5,
        "origen_hash_md5": origen_hash_md5,
    }


def _fila_historico(*, drive_file_id: str, modified_time: str, capa: str, object_path: str = None) -> dict:
    bucket = "bronze-historico" if capa == "bronze" else "silver-historico"
    return {
        "id": f"{capa}-{drive_file_id}-{modified_time}",
        "bucket": bucket,
        "object_path": object_path or f"drive/{drive_file_id}/{modified_time}/doc",
        "drive_file_id": drive_file_id,
        "modified_time": modified_time,
        "hash_md5": f"hash-{drive_file_id}-{modified_time}",
    }


# --- check_silver_tiene_bronze_teoria (join por origen_hash_md5) ------------


def test_check_silver_tiene_bronze_teoria_ok_si_el_hash_de_origen_existe_en_bronze():
    bronze = [_fila_bronze_teoria("hash-original-1")]
    silver = [_fila_silver_teoria("hash-original-1")]

    resultado = check_silver_tiene_bronze_teoria(bronze, silver)

    assert resultado.ok


def test_check_silver_tiene_bronze_teoria_falla_si_el_hash_de_origen_no_esta_en_bronze():
    bronze = [_fila_bronze_teoria("hash-original-1")]
    silver = [_fila_silver_teoria("hash-original-HUERFANO")]

    resultado = check_silver_tiene_bronze_teoria(bronze, silver)

    assert not resultado.ok
    assert any("hash-original-HUERFANO" in d for d in resultado.detalles)


def test_check_silver_tiene_bronze_teoria_ok_con_listas_vacias():
    assert check_silver_tiene_bronze_teoria([], []).ok


# --- check_silver_tiene_bronze_historico (join por (drive_file_id, modified_time)) --


def test_check_silver_tiene_bronze_historico_ok_si_el_par_existe_en_bronze():
    bronze = [_fila_historico(drive_file_id="f1", modified_time="t1", capa="bronze")]
    silver = [_fila_historico(drive_file_id="f1", modified_time="t1", capa="silver")]

    resultado = check_silver_tiene_bronze_historico(bronze, silver)

    assert resultado.ok


def test_check_silver_tiene_bronze_historico_falla_si_el_par_no_esta_en_bronze():
    bronze = [_fila_historico(drive_file_id="f1", modified_time="t1", capa="bronze")]
    silver = [_fila_historico(drive_file_id="f1", modified_time="t2-DISTINTO", capa="silver")]

    resultado = check_silver_tiene_bronze_historico(bronze, silver)

    assert not resultado.ok
    assert any("f1" in d and "t2-DISTINTO" in d for d in resultado.detalles)


# --- check_filas_tienen_objeto (fila -> objeto en Storage, cualquier tabla) --


def test_check_filas_tienen_objeto_ok_si_todos_los_objetos_existen():
    filas = [_fila_bronze_teoria("h1"), _fila_bronze_teoria("h2")]
    resultado = check_filas_tienen_objeto(filas, lambda bucket, path: True, "bronze_teoria_tiene_objeto")
    assert resultado.ok
    assert resultado.nombre == "bronze_teoria_tiene_objeto"


def test_check_filas_tienen_objeto_falla_si_un_objeto_no_existe():
    filas = [_fila_bronze_teoria("h1"), _fila_bronze_teoria("h2")]
    existentes = {filas[0]["object_path"]}

    resultado = check_filas_tienen_objeto(
        filas, lambda bucket, path: path in existentes, "bronze_teoria_tiene_objeto"
    )

    assert not resultado.ok
    assert any(filas[1]["object_path"] in d for d in resultado.detalles)
    assert not any(filas[0]["object_path"] in d for d in resultado.detalles)


def test_check_filas_tienen_objeto_ok_con_lista_vacia():
    assert check_filas_tienen_objeto([], lambda b, p: False, "x").ok


# --- check_claves_idempotencia_unicas (por tabla: par Drive o hash legacy) --


def test_check_claves_idempotencia_unicas_ok_sin_duplicados():
    filas = [
        _fila_historico(drive_file_id="f1", modified_time="t1", capa="bronze"),
        _fila_historico(drive_file_id="f2", modified_time="t1", capa="bronze"),
    ]
    assert check_claves_idempotencia_unicas(filas, "x").ok


def test_check_claves_idempotencia_unicas_falla_con_par_drive_duplicado():
    fila1 = _fila_historico(drive_file_id="f1", modified_time="t1", capa="bronze", object_path="a")
    fila2 = _fila_historico(drive_file_id="f1", modified_time="t1", capa="bronze", object_path="b")

    resultado = check_claves_idempotencia_unicas([fila1, fila2], "claves_unicas_bronze_historico")

    assert not resultado.ok
    assert any("f1" in d and "t1" in d for d in resultado.detalles)


def test_check_claves_idempotencia_unicas_falla_con_hash_legacy_duplicado():
    fila1 = _fila_bronze_teoria("hash-repetido", object_path="a")
    fila2 = _fila_bronze_teoria("hash-repetido", object_path="b")

    resultado = check_claves_idempotencia_unicas([fila1, fila2], "claves_unicas_bronze_teoria")

    assert not resultado.ok
    assert any("hash-repetido" in d for d in resultado.detalles)


def test_check_claves_idempotencia_unicas_no_mezcla_modo_drive_y_legacy():
    """Una fila Drive y una legacy nunca colisionan entre sí -- son dos
    índices únicos parciales DISTINTOS en Postgres (task 53), nunca el mismo
    espacio de claves."""
    fila_drive = _fila_historico(drive_file_id="f1", modified_time="t1", capa="bronze")
    fila_legacy = _fila_bronze_teoria("hash-cualquiera")

    resultado = check_claves_idempotencia_unicas([fila_drive, fila_legacy], "x")

    assert resultado.ok


def test_check_claves_idempotencia_unicas_ok_con_lista_vacia():
    assert check_claves_idempotencia_unicas([], "x").ok


# --- validar_topologia (orquestación con doble de TopologiaStore) -----------


class _TopologiaStoreFake:
    """Doble mínimo: `tablas` mapea (capa, flujo) -> list[dict]; `objetos`
    es el conjunto de `object_path` que SÍ existen en Storage (cualquier otro
    se reporta como ausente) -- mismo patrón de doble mínimo que `_StoreFake`
    (modo Supabase de contenido, arriba)."""

    def __init__(self, tablas: dict, objetos: set):
        self._tablas = tablas
        self._objetos = objetos

    def filas(self, capa: str, flujo: str) -> list:
        return self._tablas.get((capa, flujo), [])

    def objeto_existe(self, bucket, object_path) -> bool:
        return object_path in self._objetos


def test_validar_topologia_caso_feliz_todos_los_checks_ok():
    bronze_teoria = [_fila_bronze_teoria("h1")]
    silver_teoria = [_fila_silver_teoria("h1")]
    bronze_historico = [_fila_historico(drive_file_id="f1", modified_time="t1", capa="bronze")]
    silver_historico = [_fila_historico(drive_file_id="f1", modified_time="t1", capa="silver")]

    todas_las_filas = bronze_teoria + silver_teoria + bronze_historico + silver_historico
    store = _TopologiaStoreFake(
        tablas={
            ("bronze", "teoria"): bronze_teoria,
            ("silver", "teoria"): silver_teoria,
            ("bronze", "historico"): bronze_historico,
            ("silver", "historico"): silver_historico,
        },
        objetos={f["object_path"] for f in todas_las_filas},
    )

    resultados = validar_topologia(store)

    assert len(resultados) == 10
    for resultado in resultados:
        assert resultado.ok, f"{resultado.nombre} falló inesperadamente: {resultado.detalles}"


def test_validar_topologia_detecta_silver_huerfana_sin_tumbar_los_demas_checks():
    bronze_teoria = [_fila_bronze_teoria("h1")]
    silver_teoria = [_fila_silver_teoria("h1"), _fila_silver_teoria("h-HUERFANO")]

    todas_las_filas = bronze_teoria + silver_teoria
    store = _TopologiaStoreFake(
        tablas={
            ("bronze", "teoria"): bronze_teoria,
            ("silver", "teoria"): silver_teoria,
            ("bronze", "historico"): [],
            ("silver", "historico"): [],
        },
        objetos={f["object_path"] for f in todas_las_filas},
    )

    resultados = validar_topologia(store)
    por_nombre = {r.nombre: r for r in resultados}

    assert not por_nombre["silver_tiene_bronze_teoria"].ok
    assert por_nombre["silver_tiene_bronze_historico"].ok  # no afectado
    assert por_nombre["claves_unicas_bronze_teoria"].ok  # no afectado


def test_validar_topologia_detecta_objeto_ausente():
    bronze_teoria = [_fila_bronze_teoria("h1")]
    store = _TopologiaStoreFake(
        tablas={
            ("bronze", "teoria"): bronze_teoria,
            ("silver", "teoria"): [],
            ("bronze", "historico"): [],
            ("silver", "historico"): [],
        },
        objetos=set(),  # ningún objeto existe
    )

    resultados = validar_topologia(store)
    por_nombre = {r.nombre: r for r in resultados}

    assert not por_nombre["bronze_teoria_tiene_objeto"].ok


def test_validar_topologia_sin_filas_devuelve_todo_ok():
    store = _TopologiaStoreFake(
        tablas={
            ("bronze", "teoria"): [],
            ("silver", "teoria"): [],
            ("bronze", "historico"): [],
            ("silver", "historico"): [],
        },
        objetos=set(),
    )

    resultados = validar_topologia(store)
    assert len(resultados) == 10
    for resultado in resultados:
        assert resultado.ok


# --- TopologiaStore (capa fina sobre DocumentStore, sin red) ----------------


class _FakeResultadoSelect:
    def __init__(self, data):
        self.data = data


class _FakeTablaSoloSelect:
    def __init__(self, filas):
        self._filas = filas

    def select(self, columnas):
        return self

    def execute(self):
        return _FakeResultadoSelect(self._filas)


class _FakeSchemaSoloSelect:
    def __init__(self, filas_por_tabla):
        self._filas_por_tabla = filas_por_tabla

    def table(self, tabla):
        return _FakeTablaSoloSelect(self._filas_por_tabla.get(tabla, []))


class _FakeStorageBucketDescarga:
    def __init__(self, objetos_existentes, bucket):
        self._objetos_existentes = objetos_existentes
        self._bucket = bucket

    def download(self, object_path):
        if (self._bucket, object_path) not in self._objetos_existentes:
            raise Exception(f"404: objeto no encontrado ({self._bucket}/{object_path})")
        return b"contenido"


class _FakeStorageDescarga:
    def __init__(self, objetos_existentes):
        self._objetos_existentes = objetos_existentes

    def from_(self, bucket):
        return _FakeStorageBucketDescarga(self._objetos_existentes, bucket)


class _FakeClienteDocumentStore:
    def __init__(self, filas_por_schema_tabla, objetos_existentes):
        self._filas_por_schema_tabla = filas_por_schema_tabla
        self.storage = _FakeStorageDescarga(objetos_existentes)

    def schema(self, schema):
        return _FakeSchemaSoloSelect(self._filas_por_schema_tabla.get(schema, {}))


class _DocumentStoreFake:
    """Doble mínimo de `DocumentStore`: solo expone `.client`, que es lo
    único que usa `TopologiaStore` (nunca llama a `.capturar()`, es de solo
    lectura -- Bronze es append-only, `CLAUDE.md`)."""

    def __init__(self, client):
        self.client = client


def test_topologia_store_filas_lee_la_tabla_correcta_segun_destino():
    fila = {"id": "1", "bucket": "bronze-teoria", "object_path": "x", "hash_md5": "h1"}
    cliente = _FakeClienteDocumentStore(
        filas_por_schema_tabla={"bronze": {"teoria_documentos": [fila]}},
        objetos_existentes=set(),
    )
    store = TopologiaStore(_DocumentStoreFake(cliente))

    assert store.filas("bronze", "teoria") == [fila]
    assert store.filas("silver", "historico") == []  # tabla vacía en el doble


def test_topologia_store_objeto_existe_true_si_la_descarga_no_lanza():
    cliente = _FakeClienteDocumentStore(
        filas_por_schema_tabla={},
        objetos_existentes={("bronze-teoria", "local_legacy/h1/doc.pdf")},
    )
    store = TopologiaStore(_DocumentStoreFake(cliente))

    assert store.objeto_existe("bronze-teoria", "local_legacy/h1/doc.pdf") is True


def test_topologia_store_objeto_existe_false_si_la_descarga_lanza():
    cliente = _FakeClienteDocumentStore(filas_por_schema_tabla={}, objetos_existentes=set())
    store = TopologiaStore(_DocumentStoreFake(cliente))

    assert store.objeto_existe("bronze-teoria", "no-existe") is False


# --- main() (CLI), modo --topologia -----------------------------------------


def test_main_modo_topologia_con_store_inyectado_devuelve_0(capsys):
    bronze_teoria = [_fila_bronze_teoria("h1")]
    silver_teoria = [_fila_silver_teoria("h1")]
    todas = bronze_teoria + silver_teoria
    store = _TopologiaStoreFake(
        tablas={
            ("bronze", "teoria"): bronze_teoria,
            ("silver", "teoria"): silver_teoria,
            ("bronze", "historico"): [],
            ("silver", "historico"): [],
        },
        objetos={f["object_path"] for f in todas},
    )

    codigo = main(["--topologia"], topologia_store=store)
    salida = capsys.readouterr().out

    assert codigo == 0
    assert "10/10 checks OK (modo topología)" in salida


def test_main_modo_topologia_con_store_inyectado_devuelve_1_si_falla_un_check(capsys):
    bronze_teoria = [_fila_bronze_teoria("h1")]
    silver_teoria = [_fila_silver_teoria("h-HUERFANO")]  # sin Bronze correspondiente
    store = _TopologiaStoreFake(
        tablas={
            ("bronze", "teoria"): bronze_teoria,
            ("silver", "teoria"): silver_teoria,
            ("bronze", "historico"): [],
            ("silver", "historico"): [],
        },
        objetos={f["object_path"] for f in bronze_teoria + silver_teoria},
    )

    codigo = main(["--topologia"], topologia_store=store)

    assert codigo == 1


def test_main_modo_topologia_sin_store_ni_credenciales_falla_claro(monkeypatch, capsys):
    """Sin `--topologia` inyectado un store, `main` construye un
    `DocumentStore()` real -- en test no hay `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`,
    así que debe fallar con `DocumentStoreError` ya capturado (exit 1), nunca
    un traceback -- mismo patrón que el modo Supabase de contenido."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    codigo = main(["--topologia"])
    salida = capsys.readouterr().out

    assert codigo == 1
    assert "SUPABASE_URL" in salida or "SUPABASE_SERVICE_KEY" in salida


def test_main_topologia_y_ruta_local_son_mutuamente_excluyentes(tmp_path):
    """`--topologia` junto con una ruta posicional es un uso ambiguo (¿qué
    modo manda?) -- `argparse` lo rechaza con exit code 2 (uso incorrecto de
    CLI), no lo resuelve en silencio a favor de uno de los dos."""
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        main([str(tmp_path), "--topologia"])
    assert exc_info.value.code == 2


def test_document_store_error_es_importable_desde_el_modulo():
    """`DocumentStoreError` es la excepción que `main` captura explícitamente
    en modo `--topologia` -- confirma que es la clase real de
    `src/utils/document_store.py`, no una redefinición local."""
    assert issubclass(DocumentStoreError, RuntimeError)
