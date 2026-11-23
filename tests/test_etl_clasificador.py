"""
Pruebas unitarias del ETL (Scripts/ETL-PROGRAMAS-SNIES) y del clasificador de divisiones
(Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES). Sin red y sin tocar los archivos del proyecto: los archivos de prueba se
crean en carpetas temporales.

Ejecución:
    python -m unittest discover -s tests -v
"""

import datetime as dt
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "Scripts" / "ETL-PROGRAMAS-SNIES" / "Scripts"))
sys.path.insert(0, str(RAIZ / "Scripts" / "CLASIFICAR-DIVISIONES-UNINORTE-SNIES" / "Scripts"))

import clasificar_divisiones as clasificador  # noqa: E402
import etl_programas as etl  # noqa: E402
import normalizar  # noqa: E402
import validaciones  # noqa: E402

LOG_SILENCIOSO = logging.getLogger("pruebas.silencio")
LOG_SILENCIOSO.addHandler(logging.NullHandler())
LOG_SILENCIOSO.propagate = False


# ── Archivos de prueba del ETL ────────────────────────────────────────────────

# Valor base de cada columna del reporte; las pruebas reemplazan las que necesitan. Todo llega como texto, igual que en
# las celdas que el SNIES entrega con formato de texto.
_BASE = {c: "x" for c in etl.EXPECTED_COLUMNS}
_BASE.update({
    "CODIGO_INSTITUCION_PADRE": "1101", "CODIGO_INSTITUCION": "1101", "NOMBRE_INSTITUCION": "Universidad Ñandú",
    "ESTADO_INSTITUCION": "Activa ", "SECTOR": "Privado", "REGISTRO_UNICO": "1", "CODIGO_SNIES_DEL_PROGRAMA": "101",
    "NOMBRE_DEL_PROGRAMA": "Ingeniería de Diseño", "ESTADO_PROGRAMA": "Activo", "RESOLUCION_DE_APROBACION": "123",
    "FECHA_DE_RESOLUCION": "31/12/2025", "FECHA_EJECUTORIA": "31/12/2025", "FECHA_DE_REGISTRO_EN_SNIES": "31/12/2025",
    "VIGENCIA_AÑOS": "7", "NUMERO_CREDITOS": "160", "NUMERO_PERIODOS_DE_DURACION": "10", "COSTO_MATRICULA_ESTUD_NUEVOS": "1,5",
    "VIGENCIA_TRANSITORIA": None,
})


def _encabezado_crudo(columna: str) -> str:
    """Encabezado como lo escribe el SNIES (con tildes); el ETL lo normaliza al nombre esperado."""
    return columna.replace("CODIGO", "CÓDIGO").replace("INSTITUCION", "INSTITUCIÓN")


def _fila(**valores) -> list:
    base = dict(_BASE)
    base.update(valores)
    return [base[c] for c in etl.EXPECTED_COLUMNS]


def _excel(carpeta: str, filas: list[list], columnas=None, nombre="Programas.xlsx") -> Path:
    import openpyxl
    libro = openpyxl.Workbook()
    hoja = libro.active
    columnas = columnas if columnas is not None else [_encabezado_crudo(c) for c in etl.EXPECTED_COLUMNS]
    hoja.append(columnas)
    for fila in filas:
        hoja.append(fila)
    ruta = Path(carpeta) / nombre
    libro.save(ruta)
    return ruta


class _SinRegistro(unittest.TestCase):
    """Silencia el registro del ETL (consola de rich) durante las pruebas."""
    def setUp(self):
        for nombre in ("_log", "_detail"):
            parche = mock.patch.object(etl, nombre)
            parche.start()
            self.addCleanup(parche.stop)


class TestEtlConversiones(unittest.TestCase):
    """Normalización de encabezados y conversión de códigos, números y fechas."""
    def test_encabezados_sin_tildes_conservando_la_enie(self):
        casos = {
            "CÓDIGO_INSTITUCIÓN_PADRE": "CODIGO_INSTITUCION_PADRE",
            "Nombre\xa0del  programa": "NOMBRE_DEL_PROGRAMA",
            " vigencia años ": "VIGENCIA_AÑOS",
            "VIGENCIA_AÑOS": "VIGENCIA_AÑOS",   # Ñ descompuesta (N + tilde combinada): no debe perderse
            "Costo (matrícula) $": "COSTO_MATRICULA_",   # los caracteres no permitidos se eliminan
        }
        for crudo, esperado in casos.items():
            with self.subTest(crudo):
                self.assertEqual(etl.normalize_header(crudo), esperado)

    def test_codigos_enteros_exactos(self):
        for valor, esperado in (("1101", 1101), ("103.0", 103), (103.0, 103), (" 7 ", 7), (0, 0), (102, 102)):
            with self.subTest(valor):
                self.assertEqual(etl._to_code(valor), esperado)
        # Sin ceros a la izquierda, decimales, más de 15 dígitos ni booleanos: perderían información
        for valor in ("0123", "8.5", 8.5, "1.000", "1" * 16, "abc", "", True, None):
            with self.subTest(valor), self.assertRaises((ValueError, TypeError)):
                etl._to_code(valor)

    def test_numeros_con_coma_decimal(self):
        self.assertEqual(etl._to_number("8,5"), 8.5)
        self.assertEqual(etl._to_number("7"), 7)
        self.assertIsInstance(etl._to_number("7"), int)
        self.assertEqual(etl._to_number(3.5), 3.5)
        for valor in ("abc", "1,2,3", "", True):
            with self.subTest(valor), self.assertRaises(ValueError):
                etl._to_number(valor)

    def test_fechas(self):
        self.assertEqual(etl._to_date("31/12/2025"), dt.date(2025, 12, 31))
        self.assertEqual(etl._to_date(dt.datetime(2025, 12, 31)), dt.date(2025, 12, 31))        # medianoche: se reduce a fecha
        con_hora = dt.datetime(2025, 12, 31, 8, 30)
        self.assertEqual(etl._to_date(con_hora), con_hora)                                       # con hora: se conserva
        for valor in ("2025-12-31", "31/13/2025", "abc"):
            with self.subTest(valor), self.assertRaises(ValueError):
                etl._to_date(valor)

    def test_motivo_de_exclusion_de_un_codigo_snies(self):
        validos = (102, "102", 102.0, "103.0")
        for v in validos:
            with self.subTest(v):
                self.assertIsNone(etl._key_reject_reason(v))
        esperados = {None: "vacío", "": "vacío", "   ": "vacío", 0: "cero", "0": "cero", -5: "negativo", "-5": "negativo",
                     8.5: "decimal", "abc": "texto", "0123": "formato", "1.000": "formato", "1" * 16: "formato"}
        for valor, motivo in esperados.items():
            with self.subTest(valor):
                self.assertEqual(etl._key_reject_reason(valor), motivo)


class TestEtlProceso(_SinRegistro):
    """process_programas de punta a punta sobre un Excel con las situaciones que el ETL debe resolver."""
    def _procesar(self, filas, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            return etl.process_programas(_excel(tmp, filas, **kw))

    def test_exclusiones_tipos_y_normalizacion(self):
        n = len(etl.EXPECTED_COLUMNS)
        filas = [
            _fila(CODIGO_SNIES_DEL_PROGRAMA="101"),
            _fila(CODIGO_SNIES_DEL_PROGRAMA="101"),                                   # repetido idéntico
            _fila(CODIGO_SNIES_DEL_PROGRAMA="102"),
            _fila(CODIGO_SNIES_DEL_PROGRAMA="102", NUMERO_CREDITOS="120"),            # repetido con diferencias
            _fila(CODIGO_SNIES_DEL_PROGRAMA="abc"),                                   # texto
            _fila(CODIGO_SNIES_DEL_PROGRAMA="0"),                                     # cero
            _fila(CODIGO_SNIES_DEL_PROGRAMA=None),                                    # vacío
            [None] * n,                                                               # fila intermedia vacía: se omite
            _fila(CODIGO_SNIES_DEL_PROGRAMA="103.0"),
        ]
        df, excluidas, stats = self._procesar(filas)
        # Se conserva la primera aparición de cada código y los códigos se tipifican como enteros
        self.assertEqual(df["CODIGO_SNIES_DEL_PROGRAMA"].tolist(), [101, 102, 103])
        self.assertEqual(df["NUMERO_CREDITOS"].tolist(), [160, 160, 160])
        self.assertEqual(df["COSTO_MATRICULA_ESTUD_NUEVOS"].tolist(), [1.5, 1.5, 1.5])
        self.assertEqual(df["FECHA_DE_RESOLUCION"].iloc[0], dt.date(2025, 12, 31))
        # Mayúsculas, sin tildes y conservando la Ñ; sin espacios sobrantes
        self.assertEqual(df["NOMBRE_INSTITUCION"].iloc[0], "UNIVERSIDAD ÑANDU")
        self.assertEqual(df["NOMBRE_DEL_PROGRAMA"].iloc[0], "INGENIERIA DE DISEÑO")
        self.assertEqual(df["ESTADO_INSTITUCION"].iloc[0], "ACTIVA")
        # Las excluidas van al archivo de revisión con su fila de Excel y su motivo
        self.assertEqual(excluidas[etl.REVIEW_ROW_COLUMN].tolist(), [3, 5, 6, 7, 8])
        motivos = excluidas[etl.REVIEW_REASON_COLUMN].tolist()
        self.assertTrue(motivos[0].startswith("Repetido idéntico"))
        self.assertTrue(motivos[1].startswith("Repetido con diferencias"))
        self.assertEqual(excluidas[etl.REVIEW_DIFF_COLUMN].iloc[1], "NUMERO_CREDITOS")
        self.assertEqual(motivos[2:], ["Texto (no numérico)", "Cero", "Vacío"])
        self.assertEqual((stats["rows_blank"], stats["rows_invalid"], stats["rows_repeated"], stats["rows_repeated_diff"],
                          stats["rows_excluded"]), (1, 3, 2, 1, 5))

    def test_la_conversion_de_una_columna_es_todo_o_nada(self):
        filas = [_fila(CODIGO_SNIES_DEL_PROGRAMA="101", NUMERO_CREDITOS="160"),
                 _fila(CODIGO_SNIES_DEL_PROGRAMA="102", NUMERO_CREDITOS="ciento veinte")]
        df, _, stats = self._procesar(filas)
        # Un valor no convertible deja la columna como viene en la fuente (sin pérdida) y emite una alerta
        self.assertEqual(df["NUMERO_CREDITOS"].tolist(), ["160", "ciento veinte"])
        self.assertTrue(any("NUMERO_CREDITOS se conserva como viene en la fuente" in a for a in stats["alerts"]))
        # Las demás columnas del contrato sí se tipifican
        self.assertEqual(df["NUMERO_PERIODOS_DE_DURACION"].tolist(), [10, 10])

    def test_fecha_fuera_del_rango_plausible_alerta_sin_alterar_el_dato(self):
        filas = [_fila(CODIGO_SNIES_DEL_PROGRAMA="101", FECHA_EJECUTORIA="29/12/1899")]
        df, _, stats = self._procesar(filas)
        self.assertEqual(df["FECHA_EJECUTORIA"].iloc[0], dt.date(1899, 12, 29))
        self.assertTrue(any("FECHA_EJECUTORIA" in a and "29/12/1899" in a for a in stats["alerts"]))

    def test_celdas_con_solo_espacios_son_celdas_vacias(self):
        df, _, _ = self._procesar([_fila(CODIGO_SNIES_DEL_PROGRAMA="101", NUMERO_CREDITOS="   ")])
        self.assertTrue(pd.isna(df["NUMERO_CREDITOS"].iloc[0]))
        self.assertEqual(df["NUMERO_PERIODOS_DE_DURACION"].tolist(), [10])

    def test_columna_nueva_se_exporta_con_alerta(self):
        columnas = [_encabezado_crudo(c) for c in etl.EXPECTED_COLUMNS] + ["Columna nueva del SNIES"]
        df, _, stats = self._procesar([_fila() + ["dato"]], columnas=columnas)
        self.assertIn("COLUMNA_NUEVA_DEL_SNIES", df.columns)
        self.assertTrue(any("fuera del esquema esperado" in a for a in stats["alerts"]))

    def test_errores_que_detienen_el_proceso(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Columna esperada ausente
            sin_sector = [c for c in etl.EXPECTED_COLUMNS if c != "SECTOR"]
            ruta = _excel(tmp, [_fila()[:len(sin_sector)]], columnas=[_encabezado_crudo(c) for c in sin_sector], nombre="a.xlsx")
            with self.assertRaisesRegex(etl.FileProcessingError, "no cumple el esquema esperado"):
                etl.process_programas(ruta)
            # Encabezados que quedan iguales al normalizarlos
            columnas = [_encabezado_crudo(c) for c in etl.EXPECTED_COLUMNS] + ["sector"]
            ruta = _excel(tmp, [_fila() + ["x"]], columnas=columnas, nombre="b.xlsx")
            with self.assertRaisesRegex(etl.FileProcessingError, "columnas duplicadas"):
                etl.process_programas(ruta)
            # Solo encabezados, sin filas de datos
            with self.assertRaises(etl.FileProcessingError):
                etl.process_programas(_excel(tmp, [], nombre="c.xlsx"))
            # Ninguna fila con código SNIES válido
            with self.assertRaisesRegex(etl.FileProcessingError, "ninguna fila tiene un"):
                etl.process_programas(_excel(tmp, [_fila(CODIGO_SNIES_DEL_PROGRAMA="abc")], nombre="d.xlsx"))
            # Archivo que no es un Excel
            falso = Path(tmp) / "e.xlsx"
            falso.write_text("no es un excel", encoding="utf-8")
            with self.assertRaisesRegex(etl.FileProcessingError, "No se pudo leer"):
                etl.process_programas(falso)


class TestEtlFuenteTruncada(_SinRegistro):
    """Protección contra una fuente truncada: la salida anterior se conserva."""
    def _salida_anterior(self, carpeta, filas):
        return _excel(carpeta, [_fila(CODIGO_SNIES_DEL_PROGRAMA=str(100 + i)) for i in range(filas)], nombre="Programas.xlsx")

    def test_una_caida_de_mas_del_10_por_ciento_se_rechaza(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida = self._salida_anterior(tmp, 100)
            alertas = []
            with self.assertRaisesRegex(etl.FileProcessingError, "Posible fuente truncada"):
                etl._check_row_drop(80, salida, False, alertas)
            self.assertEqual(alertas, [])

    def test_una_caida_dentro_del_umbral_o_sin_salida_previa_pasa(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida = self._salida_anterior(tmp, 100)
            for filas in (100, 95, 90, 150):   # 90 es exactamente el umbral del 10 %
                with self.subTest(filas):
                    etl._check_row_drop(filas, salida, False, [])
            etl._check_row_drop(5, Path(tmp) / "no_existe.xlsx", False, [])

    def test_force_exporta_y_deja_la_alerta(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida = self._salida_anterior(tmp, 100)
            alertas = []
            etl._check_row_drop(50, salida, True, alertas)
            self.assertEqual(len(alertas), 1)
            self.assertIn("por --force", alertas[0])
            self.assertIn("50 fila(s) frente a 100", alertas[0])


# ── Clasificador ──────────────────────────────────────────────────────────────

class TestClasificadorDecision(unittest.TestCase):
    """Reglas de decisión de la votación ponderada de tres capas."""
    def test_sin_votos_va_a_otro_con_revision(self):
        self.assertEqual(clasificador.decidir_clasificacion({}), ("OTRO", 0.0, 0.0, "BAJA", True))

    def test_confianza_segun_puntaje_y_margen(self):
        decidir = clasificador.decidir_clasificacion
        self.assertEqual(decidir({"A": 0.60})[3:], ("ALTA", False))
        self.assertEqual(decidir({"A": 0.40, "B": 0.10})[3:], ("MEDIA", False))
        self.assertEqual(decidir({"A": 0.40, "B": 0.35})[3:], ("BAJA", True))        # margen 0,05: solo BAJA
        self.assertEqual(decidir({"A": 0.40, "B": 0.35})[0], "A")                    # BAJA conserva la división ganadora
        self.assertEqual(decidir({"A": 0.10}), ("OTRO", 0.10, 0.10, "BAJA", True))   # por debajo de UMBRAL_BAJA
        self.assertEqual(decidir({"A": 0.30, "B": 0.30})[0], "OTRO")                 # empate sin margen

    def test_los_umbrales_exactos_no_se_pierden_por_el_redondeo_de_punto_flotante(self):
        # 0,50 - 0,40 vale 0,0999...: con tolerancia debe contar como el margen de 0,10 de la confianza MEDIA
        division, puntaje, margen, confianza, revision = clasificador.decidir_clasificacion({"A": 0.50, "B": 0.40})
        self.assertEqual((division, confianza, revision), ("A", "MEDIA", False))

    def test_consenso_entre_capas(self):
        consenso = clasificador.evaluar_consenso
        self.assertEqual(consenso(None, None, None, None), "SIN_VOTOS")
        self.assertEqual(consenso("A", "A", None, "A"), "UNANIME")
        self.assertEqual(consenso("A", "B", "A", None), "MAYORIA")
        self.assertEqual(consenso("A", "B", "C", None), "DIVIDIDO")
        self.assertEqual(consenso("A", None, None, None), "UNANIME")

    def test_pesos_y_catalogo_de_divisiones_validos(self):
        self.assertTrue(validaciones.validar_pesos(LOG_SILENCIOSO))
        self.assertTrue(validaciones.validar_catalogo_divisiones(LOG_SILENCIOSO))
        self.assertAlmostEqual(clasificador.PESO_CAPA1 + clasificador.PESO_CAPA2_NOMBRE + clasificador.PESO_CAPA3_CINE
                               + clasificador.PESO_CAPA3_NBC, 1.0)

    def test_los_diccionarios_de_reglas_son_coherentes(self):
        # Divisiones válidas en NBC, overrides y reglas; claves repetidas y reglas inalcanzables
        self.assertTrue(validaciones.validar_divisiones_diccionarios(LOG_SILENCIOSO))

    def test_pesos_invalidos_se_rechazan(self):
        with mock.patch.object(validaciones, "PESO_CAPA1", 0.30):
            self.assertFalse(validaciones.validar_pesos(LOG_SILENCIOSO))


class TestClasificadorNormalizacion(unittest.TestCase):
    """La versión escalar y la de Series deben dar exactamente lo mismo."""
    def test_normalizar_texto(self):
        self.assertEqual(normalizar.normalizar_texto("  Ingeniería,  de-Sistemas. "), "INGENIERÍA DE SISTEMAS")
        for vacio in (None, float("nan"), "", "   ", ".-"):
            with self.subTest(repr(vacio)):
                self.assertIsNone(normalizar.normalizar_texto(vacio))

    def test_normalizar_para_keywords(self):
        self.assertEqual(normalizar.normalizar_para_keywords("INGENIERÍA  de Diseño (Año)"), "ingenieria de diseno ano")
        self.assertEqual(normalizar.normalizar_para_keywords(None), "")
        self.assertEqual(normalizar.normalizar_para_keywords("  "), "")

    def test_serie_y_valor_escalar_coinciden(self):
        textos = ["Ingeniería de Sistemas", "  Diseño,  Gráfico ", "Gestión\xa0Ambiental", "A-B/C", None, "", "..."]
        s = pd.Series(textos, dtype="object")
        for texto, mayus, minus in zip(textos, normalizar.normalizar_serie(s), normalizar.normalizar_serie_keywords(s)):
            with self.subTest(repr(texto)):
                esperado = normalizar.normalizar_texto(texto)
                self.assertTrue((mayus is None and esperado is None) or mayus == esperado, (mayus, esperado))
                self.assertEqual(minus, normalizar.normalizar_para_keywords(texto))

    def test_el_espacio_duro_cuenta_como_espacio(self):
        self.assertEqual(normalizar.normalizar_para_keywords("gestion\xa0ambiental"), "gestion ambiental")

    def test_forma_canonica_ignora_orden_y_conectores(self):
        self.assertEqual(normalizar.forma_canonica("gestion e innovacion de empresas"),
                         normalizar.forma_canonica("innovacion y gestion de empresas"))
        self.assertNotEqual(normalizar.forma_canonica("gestion de empresas"), normalizar.forma_canonica("gestion ambiental"))


class TestClasificadorOverrides(unittest.TestCase):
    """Un override por nombre prevalece sobre la votación y deja su origen registrado."""
    def _df(self, nombres):
        return pd.DataFrame({"NOMBRE": nombres, "DIVISION_UNINORTE": "OTRO", "ORIGEN_DECISION": "VOTACION",
                             "CONFIANZA": "BAJA", "REQUIERE_REVISION": "VERDADERO"})

    def test_aplica_el_override_y_no_toca_los_demas_nombres(self):
        nombre, division = next(iter(clasificador.NOMBRE_OVERRIDE.items()))
        df = self._df([nombre, "programa que no tiene override alguno xyz"])
        clasificador.aplicar_nombre_overrides(df, {"nombre": "NOMBRE"}, LOG_SILENCIOSO)
        self.assertEqual(df["DIVISION_UNINORTE"].tolist(), [division, "OTRO"])
        self.assertEqual(df["ORIGEN_DECISION"].tolist(), ["OVERRIDE", "VOTACION"])
        self.assertEqual(df["CONFIANZA"].tolist(), ["ALTA", "BAJA"])
        self.assertEqual(df["REQUIERE_REVISION"].tolist(), ["FALSO", "VERDADERO"])

    def test_el_override_tolera_mayusculas_tildes_y_puntuacion_del_nombre(self):
        nombre, division = next(iter(clasificador.NOMBRE_OVERRIDE.items()))
        variante = nombre.upper().replace(" ", "  ") + " "
        df = self._df([variante])
        clasificador.aplicar_nombre_overrides(df, {"nombre": "NOMBRE"}, LOG_SILENCIOSO)
        self.assertEqual(df["DIVISION_UNINORTE"].tolist(), [division])

    def test_todo_override_apunta_a_una_division_valida(self):
        validas = {normalizar.normalizar_texto(d) for d in validaciones.DIVISIONES_VALIDAS}
        invalidos = {n: d for n, d in clasificador.NOMBRE_OVERRIDE.items() if normalizar.normalizar_texto(d) not in validas}
        self.assertEqual(invalidos, {})


class TestClasificadorEscrituraAtomica(unittest.TestCase):
    """La salida anterior solo se reemplaza cuando la nueva se escribió y verificó completa."""
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.salida = os.path.join(carpeta.name, "Programas.xlsx")
        Path(self.salida).write_text("anterior", encoding="utf-8")
        self.carpeta = Path(carpeta.name)

    def _escribir(self, contenido):
        return lambda ruta: Path(ruta).write_text(contenido, encoding="utf-8")

    def _restos(self):
        return [p.name for p in self.carpeta.iterdir() if ".tmp" in p.name]

    def test_reemplaza_la_salida_al_terminar(self):
        self.assertTrue(clasificador._escritura_atomica(self.salida, self._escribir("nueva"), LOG_SILENCIOSO))
        self.assertEqual(Path(self.salida).read_text(encoding="utf-8"), "nueva")
        self.assertEqual(self._restos(), [])

    def test_un_error_al_escribir_conserva_la_salida_anterior(self):
        def falla(ruta):
            Path(ruta).write_text("incompleta", encoding="utf-8")
            raise OSError("disco lleno")
        self.assertFalse(clasificador._escritura_atomica(self.salida, falla, LOG_SILENCIOSO))
        self.assertEqual(Path(self.salida).read_text(encoding="utf-8"), "anterior")
        self.assertEqual(self._restos(), [])

    def test_una_verificacion_fallida_conserva_la_salida_anterior(self):
        def verificar(ruta):
            raise validaciones.ErrorIntegridad("celdas alteradas")
        self.assertFalse(clasificador._escritura_atomica(self.salida, self._escribir("alterada"), LOG_SILENCIOSO, verificar))
        self.assertEqual(Path(self.salida).read_text(encoding="utf-8"), "anterior")
        self.assertEqual(self._restos(), [])

    def test_archivo_abierto_en_otra_aplicacion_no_lo_pierde(self):
        with mock.patch.object(clasificador.os, "replace", side_effect=PermissionError):
            self.assertFalse(clasificador._escritura_atomica(self.salida, self._escribir("nueva"), LOG_SILENCIOSO))
        self.assertEqual(Path(self.salida).read_text(encoding="utf-8"), "anterior")
        self.assertEqual(self._restos(), [])


class TestClasificadorIntegridadDeSalida(unittest.TestCase):
    """verificar_integridad: la salida conserva exactamente los datos de la fuente (valores y tipos)."""
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.carpeta = carpeta.name

    def _libro(self, nombre, filas):
        import openpyxl
        libro = openpyxl.Workbook()
        for fila in filas:
            libro.active.append(fila)
        ruta = os.path.join(self.carpeta, nombre)
        libro.save(ruta)
        return ruta

    def test_salida_con_columnas_agregadas_al_final_es_valida(self):
        fuente = self._libro("fuente.xlsx", [["SNIES", "NOMBRE"], [101, "A"], ["102", "B"]])
        salida = self._libro("salida.xlsx", [["SNIES", "NOMBRE", "DIVISION"], [101, "A", "X"], ["102", "B", "Y"]])
        validaciones.verificar_integridad(fuente, salida)

    def test_un_valor_alterado_se_detecta(self):
        fuente = self._libro("fuente.xlsx", [["SNIES", "NOMBRE"], [101, "A"]])
        salida = self._libro("salida.xlsx", [["SNIES", "NOMBRE"], [101, "B"]])
        with self.assertRaisesRegex(validaciones.ErrorIntegridad, "alterada"):
            validaciones.verificar_integridad(fuente, salida)

    def test_un_tipo_alterado_se_detecta(self):
        # '106341' (texto) no es 106341 (número)
        fuente = self._libro("fuente.xlsx", [["SNIES"], ["106341"]])
        salida = self._libro("salida.xlsx", [["SNIES"], [106341]])
        with self.assertRaises(validaciones.ErrorIntegridad):
            validaciones.verificar_integridad(fuente, salida)

    def test_filas_faltantes_o_sobrantes_se_detectan(self):
        fuente = self._libro("fuente.xlsx", [["SNIES"], [1], [2]])
        con_menos = self._libro("menos.xlsx", [["SNIES"], [1]])
        con_mas = self._libro("mas.xlsx", [["SNIES"], [1], [2], [3]])
        with self.assertRaisesRegex(validaciones.ErrorIntegridad, "menos filas"):
            validaciones.verificar_integridad(fuente, con_menos)
        with self.assertRaisesRegex(validaciones.ErrorIntegridad, "mas filas"):
            validaciones.verificar_integridad(fuente, con_mas)

    def test_encabezados_cambiados_se_detectan(self):
        fuente = self._libro("fuente.xlsx", [["SNIES", "NOMBRE"], [1, "A"]])
        salida = self._libro("salida.xlsx", [["NOMBRE", "SNIES"], ["A", 1]])
        with self.assertRaisesRegex(validaciones.ErrorIntegridad, "encabezados"):
            validaciones.verificar_integridad(fuente, salida)

    def test_parquet_valores_y_columnas(self):
        fuente = os.path.join(self.carpeta, "fuente.parquet")
        pd.DataFrame({"SNIES": [1, 2], "NOMBRE": ["A", "B"]}).to_parquet(fuente)
        buena = os.path.join(self.carpeta, "buena.parquet")
        pd.DataFrame({"SNIES": [1, 2], "NOMBRE": ["A", "B"], "DIVISION": ["X", "Y"]}).to_parquet(buena)
        validaciones.verificar_integridad(fuente, buena)
        alterada = os.path.join(self.carpeta, "alterada.parquet")
        pd.DataFrame({"SNIES": [1, 2], "NOMBRE": ["A", "Z"]}).to_parquet(alterada)
        with self.assertRaisesRegex(validaciones.ErrorIntegridad, "NOMBRE"):
            validaciones.verificar_integridad(fuente, alterada)
        con_menos_filas = os.path.join(self.carpeta, "menos.parquet")
        pd.DataFrame({"SNIES": [1], "NOMBRE": ["A"]}).to_parquet(con_menos_filas)
        with self.assertRaisesRegex(validaciones.ErrorIntegridad, "filas"):
            validaciones.verificar_integridad(fuente, con_menos_filas)


if __name__ == "__main__":
    unittest.main()
