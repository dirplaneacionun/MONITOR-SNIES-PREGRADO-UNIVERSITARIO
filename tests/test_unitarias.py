"""
Pruebas unitarias de la lógica crítica del monitor (sin red ni archivos del proyecto).

Ejecución:
    python -m unittest discover -s tests -v
"""

import datetime as dt
import json
import logging
import os
import re
import smtplib
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Scripts"))

import catalogos  # noqa: E402
import generar_dashboard  # noqa: E402
import historico  # noqa: E402
import reclasificar  # noqa: E402
import run_snies  # noqa: E402
import send_report  # noqa: E402

S = run_snies.COL_SNIES
F = run_snies.COL_FECHA


def programa(snies, **valores):
    """Registro de programa académico de prueba; `valores` reemplaza campos del registro base."""
    base = {
        S: snies, "NOMBRE_DEL_PROGRAMA": f"PROGRAMA {snies}", "NOMBRE_INSTITUCION": "UNIVERSIDAD X",
        "CODIGO_INSTITUCION": 9999, "NUMERO_CREDITOS": 160, "MUNICIPIO_OFERTA_PROGRAMA": "BARRANQUILLA",
        "NUMERO_PERIODOS_DE_DURACION": 10, "MODALIDAD": "PRESENCIAL", "DIVISION_UNINORTE": "INGENIERIAS",
    }
    base.update(valores)
    return base


class TestCatalogos(unittest.TestCase):
    """Agrupaciones de universidades y de modalidades, y tipos de cambio."""
    def test_grupos_universidad(self):
        self.assertEqual(catalogos.grupo_universidad(1713), catalogos.GRUPO_UNINORTE)
        for codigo in catalogos.MAYOR_INTERES:
            self.assertEqual(catalogos.grupo_universidad(codigo), catalogos.GRUPO_MAYOR_INTERES)
        self.assertEqual(catalogos.grupo_universidad("1701"), catalogos.GRUPO_MAYOR_INTERES)
        self.assertEqual(catalogos.grupo_universidad(9999), catalogos.GRUPO_OTRAS)
        self.assertEqual(catalogos.grupo_universidad(None), catalogos.GRUPO_OTRAS)

    def test_grupos_por_institucion_padre(self):
        """La clasificación usa CODIGO_INSTITUCION_PADRE: una seccional toma el grupo de su institución principal."""
        df = pd.DataFrame({"CODIGO_INSTITUCION": [1702, 1807, 1713, 9999, 1701],
                           "CODIGO_INSTITUCION_PADRE": [1701, 1806, 1713, 9999, 1701]})
        self.assertEqual(catalogos.grupos_de_registros(df).tolist(),
                         [catalogos.GRUPO_MAYOR_INTERES, catalogos.GRUPO_MAYOR_INTERES, catalogos.GRUPO_UNINORTE,
                          catalogos.GRUPO_OTRAS, catalogos.GRUPO_MAYOR_INTERES])
        # Sin la columna de padre (tablas antiguas) se usa el código de la institución
        self.assertEqual(catalogos.grupos_de_registros(df[["CODIGO_INSTITUCION"]]).tolist()[:2], [catalogos.GRUPO_OTRAS] * 2)

    def test_modalidades_agrupadas(self):
        for grupo, valores in catalogos.MODALIDADES_GRUPO.items():
            for valor in valores:
                self.assertEqual(catalogos.modalidad_grupo(valor), grupo, valor)
        self.assertEqual(catalogos.modalidad_grupo(None), catalogos.SIN_INFORMACION)
        self.assertEqual(catalogos.modalidad_grupo("NO EXISTE"), catalogos.SIN_INFORMACION)
        self.assertEqual(catalogos.modalidad_grupo(" presencial "), "PRESENCIAL")


class TestDeteccion(unittest.TestCase):
    """Detección de nuevos, modificados e inactivos entre dos ejecuciones."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.parches = [
            mock.patch.object(historico, "HISTORICO_DIR", base),
            mock.patch.object(run_snies, "INACTIVOS_FILE", base / "inactivos.parquet"),
        ]
        for p in self.parches:
            p.start()
        self.fecha = dt.date(2026, 11, 17)

    def tearDown(self):
        for p in self.parches:
            p.stop()
        self.tmp.cleanup()

    def _historico(self, filas):
        df = pd.DataFrame(filas)
        df[F] = pd.to_datetime(df[F])
        for fecha, parte in df.groupby(F):
            historico.guardar_ejecucion(parte.drop(columns=[F]), fecha.date())

    def test_nuevos_inactivos_y_modificados(self):
        anterior = pd.DataFrame([programa(1), programa(2), programa(3, NUMERO_CREDITOS=160.0, MODALIDAD="VIRTUAL")])
        hoy = pd.DataFrame([programa(1), programa(3, NUMERO_CREDITOS=150, MODALIDAD="HIBRIDA (PRESENCIAL-VIRTUAL)"), programa(4)])
        nuevos, modificados, inactivos = run_snies.detectar_novedades(hoy, anterior, self.fecha)
        self.assertEqual(nuevos[S].tolist(), [4])
        self.assertEqual(nuevos["TIPO_NOVEDAD"].tolist(), ["NUEVO"])
        self.assertEqual(inactivos[S].tolist(), [2])
        cambios = dict(zip(modificados["TIPO_CAMBIO_DETECTADO"], modificados["CAMBIO_DETECTADO"]))
        self.assertEqual(cambios, {"NUMERO DE CREDITOS": "160 → 150", "MODALIDAD": "VIRTUAL → HIBRIDA (PRESENCIAL-VIRTUAL)"})
        self.assertTrue((modificados[F] == pd.Timestamp(self.fecha)).all())

    def test_mismo_valor_con_otro_tipo_no_es_cambio(self):
        anterior = pd.DataFrame([programa(1, NUMERO_CREDITOS=160.0, NUMERO_PERIODOS_DE_DURACION=10.0)])
        hoy = pd.DataFrame([programa(1, NUMERO_CREDITOS=160, NUMERO_PERIODOS_DE_DURACION="10")])
        self.assertTrue(run_snies.detectar_novedades(hoy, anterior, self.fecha)[1].empty)

    def test_dato_que_desaparece_no_es_cambio(self):
        anterior = pd.DataFrame([programa(1)])
        hoy = pd.DataFrame([programa(1, MUNICIPIO_OFERTA_PROGRAMA=None)])
        self.assertTrue(run_snies.detectar_novedades(hoy, anterior, self.fecha)[1].empty)

    def test_lectura_filtrada_por_codigo_snies(self):
        self._historico([programa(1, **{F: "2026-11-03"}), programa(2, **{F: "2026-11-03"}), programa(3, **{F: "2026-11-10"})])
        h = historico.leer([S, F], codigos_snies=[1, 3])
        self.assertEqual(sorted(h[S].tolist()), [1, 3])

    def test_dato_que_reaparece_se_compara_con_el_ultimo_conocido(self):
        self._historico([programa(1, **{F: "2026-11-03"}), programa(2, **{F: "2026-11-03"})])
        anterior = pd.DataFrame([programa(1, MUNICIPIO_OFERTA_PROGRAMA=None), programa(2, MUNICIPIO_OFERTA_PROGRAMA=None)])
        hoy = pd.DataFrame([programa(1), programa(2, MUNICIPIO_OFERTA_PROGRAMA="SOLEDAD")])
        modificados = run_snies.detectar_novedades(hoy, anterior, self.fecha)[1]
        self.assertEqual(modificados[S].tolist(), [2])
        self.assertEqual(modificados["CAMBIO_DETECTADO"].tolist(), ["BARRANQUILLA → SOLEDAD"])

    def test_reactivado(self):
        self._historico([programa(5, **{F: "2026-10-01"})])
        nuevos = run_snies.detectar_novedades(pd.DataFrame([programa(5)]), pd.DataFrame([programa(1)]), self.fecha)[0]
        self.assertEqual(nuevos["TIPO_NOVEDAD"].tolist(), ["REACTIVADO"])

    def test_proteccion_contra_descarga_incompleta(self):
        self._historico([programa(i, **{F: "2026-11-10"}) for i in range(100)])
        run_snies.validar_continuidad(91, self.fecha)    # caída del 9 %: dentro del umbral
        run_snies.validar_continuidad(150, self.fecha)   # aumento: siempre válido
        with self.assertRaises(run_snies.EtapaError) as error:
            run_snies.validar_continuidad(89, self.fecha)  # caída del 11 %: posible descarga truncada
        self.assertEqual(error.exception.etapa, "VALIDACION")
        self.assertIn("no se guardó ningún dato", str(error.exception))
        run_snies.validar_continuidad(89, self.fecha, forzar=True)   # caída real confirmada
        run_snies.validar_continuidad(10, dt.date(2026, 11, 10))      # sin ejecución anterior: línea base

    def test_validacion_de_los_filtros_del_portal(self):
        correcto = pd.DataFrame([programa(1, **run_snies.FILTROS_PORTAL), programa(2, **run_snies.FILTROS_PORTAL)])
        run_snies.validar_filtros(correcto)   # todas las filas cumplen los filtros: no lanza
        run_snies.validar_filtros(correcto.assign(NIVEL_ACADEMICO=" pregrado "))   # espacios y mayúsculas no cuentan
        casos = {
            "nivel de formación ampliado": correcto.assign(NIVEL_DE_FORMACION=["UNIVERSITARIO", "TECNOLOGICO"]),
            "programa inactivo": correcto.assign(ESTADO_PROGRAMA=["ACTIVO", "INACTIVO"]),
            "valor vacío": correcto.assign(NIVEL_ACADEMICO=["PREGRADO", None]),
            "columna ausente": correcto.drop(columns="ESTADO_INSTITUCION"),
        }
        for nombre, df in casos.items():
            with self.subTest(nombre), self.assertRaises(run_snies.EtapaError) as error:
                run_snies.validar_filtros(df)
            self.assertEqual(error.exception.etapa, "VALIDACION")
        with self.assertRaises(run_snies.EtapaError) as error:
            run_snies.validar_filtros(casos["nivel de formación ampliado"])
        self.assertIn("NIVEL_DE_FORMACION: 1 fila(s)", str(error.exception))
        self.assertIn("no se guardó ningún dato", str(error.exception))

    def test_reejecucion_del_mismo_dia_no_marca_reactivados(self):
        # El histórico ya contiene la fecha de hoy (re-ejecución): no debe contar como existencia previa
        self._historico([programa(6, **{F: str(self.fecha)})])
        nuevos = run_snies.detectar_novedades(pd.DataFrame([programa(6)]), pd.DataFrame([programa(1)]), self.fecha)[0]
        self.assertEqual(nuevos["TIPO_NOVEDAD"].tolist(), ["NUEVO"])


class TestAcumulacion(unittest.TestCase):
    """Acumulación de novedades en Parquet: reemplazo de una fecha repetida y retiro de columnas obsoletas."""
    def test_reemplaza_la_fecha_y_retira_columnas_obsoletas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.parquet"
            orden = [S, "NOMBRE_DEL_PROGRAMA", F]
            a = pd.DataFrame([programa(1), programa(2)])[[S, "NOMBRE_DEL_PROGRAMA"]].assign(**{F: pd.Timestamp("2026-11-03"), "REQUIERE_REVISION": "FALSO"})
            b = pd.DataFrame([programa(3)])[[S, "NOMBRE_DEL_PROGRAMA"]].assign(**{F: pd.Timestamp("2026-11-10")})
            self.assertEqual(run_snies.acumular_parquet(path, a, dt.date(2026, 11, 3), orden), 2)
            self.assertEqual(run_snies.acumular_parquet(path, b, dt.date(2026, 11, 10), orden), 3)
            self.assertEqual(run_snies.acumular_parquet(path, b, dt.date(2026, 11, 10), orden), 3)  # sin duplicar
            final = pd.read_parquet(path)
            self.assertNotIn("REQUIERE_REVISION", final.columns)
            self.assertEqual(sorted(final[S]), [1, 2, 3])

class TestHistorico(unittest.TestCase):
    """Histórico en un solo archivo: un grupo de filas por ejecución, tipos estables y escritura incremental."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parche = mock.patch.object(historico, "HISTORICO_DIR", Path(self.tmp.name))
        self.parche.start()

    def tearDown(self):
        self.parche.stop()
        self.tmp.cleanup()

    def _guardar(self, fecha, filas):
        return historico.guardar_ejecucion(pd.DataFrame(filas), fecha)

    def _grupos(self):
        import pyarrow.parquet as pq
        total = 0
        for _, path in historico.archivos():
            with open(path, "rb") as fh:
                total += pq.ParquetFile(fh).num_row_groups
        return total

    def test_lectura_por_fecha_y_ejecucion_anterior(self):
        self._guardar(dt.date(2026, 11, 10), [programa(1), programa(3)])
        self._guardar(dt.date(2026, 11, 3), [programa(1), programa(2)])   # fecha anterior: se ubica en orden
        self.assertEqual(historico.fechas(), [dt.date(2026, 11, 3), dt.date(2026, 11, 10)])
        self.assertEqual(self._grupos(), 2)
        todo = historico.leer()
        self.assertEqual(todo[S].tolist(), [1, 2, 1, 3])
        self.assertTrue(str(todo[F].dtype).startswith("datetime64"))
        self.assertEqual(historico.leer([S, F], antes_de=dt.date(2026, 11, 10))[S].tolist(), [1, 2])
        self.assertTrue(historico.leer([S], antes_de=dt.date(2026, 11, 3)).empty)
        fecha, anterior = historico.ejecucion_anterior(dt.date(2026, 11, 17))
        self.assertEqual(fecha, dt.date(2026, 11, 10))
        self.assertEqual(anterior[S].tolist(), [1, 3])
        self.assertNotIn(F, anterior.columns)
        self.assertIsNone(historico.ejecucion_anterior(dt.date(2026, 11, 3)))

    def test_reejecucion_reemplaza_la_fecha_sin_duplicar(self):
        self._guardar(dt.date(2026, 11, 3), [programa(1)])
        self._guardar(dt.date(2026, 11, 10), [programa(2)])
        antes = historico.ruta(2026).read_bytes()
        self.assertEqual(self._guardar(dt.date(2026, 11, 10), [programa(2)]), 2)
        self.assertEqual(historico.ruta(2026).read_bytes(), antes)   # mismos datos: archivo idéntico
        self._guardar(dt.date(2026, 11, 10), [programa(2), programa(4)])
        self.assertEqual(historico.leer()[S].tolist(), [1, 2, 4])

    def test_columna_nueva_se_agrega_sin_perder_datos(self):
        self._guardar(dt.date(2026, 11, 3), [programa(1)])
        self._guardar(dt.date(2026, 11, 10), [programa(2, COLUMNA_NUEVA="X")])
        df = historico.leer([S, "COLUMNA_NUEVA"])
        self.assertEqual(df[S].tolist(), [1, 2])
        self.assertTrue(pd.isna(df["COLUMNA_NUEVA"].iloc[0]))
        self.assertEqual(df["COLUMNA_NUEVA"].iloc[1], "X")

    def test_fecha_vacia_en_una_ejecucion_conserva_el_tipo(self):
        self._guardar(dt.date(2026, 11, 3), [programa(1, FECHA_EJECUTORIA=dt.datetime(2019, 1, 1))])
        self._guardar(dt.date(2026, 11, 10), [programa(2, FECHA_EJECUTORIA=None)])
        df = historico.leer()
        self.assertTrue(str(df["FECHA_EJECUTORIA"].dtype).startswith("datetime64"))
        self.assertTrue(pd.isna(df["FECHA_EJECUTORIA"].iloc[1]))

    def test_numeros_como_texto_con_representacion_unica(self):
        # Regresión: el histórico mezclaba "10.0" y "10" según la ejecución, y las comparaciones fallaban
        self._guardar(dt.date(2026, 11, 3), [programa(1, NUMERO_CREDITOS="180", NUMERO_PERIODOS_DE_DURACION="10")])
        self._guardar(dt.date(2026, 11, 10), [programa(2, NUMERO_CREDITOS=180.0, NUMERO_PERIODOS_DE_DURACION="10.0"),
                                              programa(3, NUMERO_CREDITOS=None, NUMERO_PERIODOS_DE_DURACION=12)])
        df = historico.leer()
        self.assertEqual(df["NUMERO_CREDITOS"].tolist()[:2], ["180", "180"])
        self.assertTrue(pd.isna(df["NUMERO_CREDITOS"].iloc[2]))
        self.assertEqual(df["NUMERO_PERIODOS_DE_DURACION"].tolist(), ["10", "10", "12"])
        self.assertTrue(pd.api.types.is_string_dtype(df["NUMERO_CREDITOS"]))

    def test_alinear_no_pierde_datos_no_convertibles(self):
        import pyarrow as pa
        esquema = pa.schema([("N", pa.float64()), ("D", pa.timestamp("us"))])
        df = pd.DataFrame({"N": ["10", "X"], "D": ["2026-01-01", "no es fecha"]})
        out = historico.alinear(df, esquema)
        self.assertEqual(out["N"].tolist(), ["10", "X"])
        self.assertEqual(out["D"].tolist(), ["2026-01-01", "no es fecha"])
        self.assertEqual(historico.alinear(pd.DataFrame({"N": ["10", None]}), esquema)["N"].tolist()[0], 10.0)

    def test_un_archivo_por_anio_leido_en_conjunto(self):
        # Diciembre y enero quedan en archivos distintos; el año nuevo adopta el esquema del anterior
        self._guardar(dt.date(2025, 12, 29), [programa(1, NUMERO_CREDITOS="180"), programa(2)])
        self._guardar(dt.date(2026, 1, 5), [programa(1, NUMERO_CREDITOS=180.0), programa(3)])
        self.assertEqual([a for a, _ in historico.archivos()], [2025, 2026])
        self.assertEqual(historico.ruta(2026).name, "Historico Programas Activos 2026.parquet")
        import pyarrow.parquet as pq
        self.assertEqual(pq.read_schema(historico.ruta(2025)).field("NUMERO_CREDITOS").type,
                         pq.read_schema(historico.ruta(2026)).field("NUMERO_CREDITOS").type)
        todo = historico.leer()
        self.assertEqual(todo[S].tolist(), [1, 2, 1, 3])
        self.assertEqual(todo["NUMERO_CREDITOS"].tolist()[0::2], ["180", "180"])
        fecha, anterior = historico.ejecucion_anterior(dt.date(2026, 1, 5))   # cruza el cambio de año
        self.assertEqual((fecha, anterior[S].tolist()), (dt.date(2025, 12, 29), [1, 2]))
        self.assertEqual(historico.leer([S], antes_de=dt.date(2026, 1, 5))[S].tolist(), [1, 2])
        self.assertEqual(historico.leer([S], fecha=dt.date(2026, 1, 5))[S].tolist(), [1, 3])
        # Una reescritura sin cambios deja ambos archivos idénticos
        antes = {a: p.read_bytes() for a, p in historico.archivos()}
        historico.reescribir(historico.leer())
        self.assertEqual({a: p.read_bytes() for a, p in historico.archivos()}, antes)

    def test_reescritura_completa_conserva_un_grupo_por_ejecucion(self):
        self._guardar(dt.date(2026, 11, 3), [programa(1)])
        self._guardar(dt.date(2026, 11, 10), [programa(2)])
        df = historico.leer()
        historico.reescribir(df.assign(DIVISION_UNINORTE="CIENCIAS BASICAS"))
        self.assertEqual(self._grupos(), 2)
        self.assertEqual(historico.leer()["DIVISION_UNINORTE"].unique().tolist(), ["CIENCIAS BASICAS"])


class TestTipos(unittest.TestCase):
    """Tipos de datos estables al guardar (números como texto, fechas, columnas vacías)."""
    def test_tipos_estables(self):
        df = pd.DataFrame({"MIXTA": [31, "0031", None], "FECHAS": [dt.date(2026, 1, 1), dt.datetime(2026, 1, 2, 5), None]})
        out = run_snies._tipos_estables(df)
        self.assertEqual(out["MIXTA"].dropna().tolist(), ["31", "0031"])
        self.assertTrue(str(out["FECHAS"].dtype).startswith("datetime64"))
        with tempfile.TemporaryDirectory() as tmp:
            out.to_parquet(Path(tmp) / "t.parquet")  # no debe fallar


class TestRegresiones(unittest.TestCase):
    """Regresiones: lectura de códigos largos y registro de una falla del correo."""
    def test_lectura_conserva_codigos_largos(self):
        # Regresión: pandas convertía a número los textos numéricos y truncaba CODIGO_ANTERIOR_ICFES (21 dígitos)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.xlsx"
            pd.DataFrame({S: [101], "CODIGO_ANTERIOR_ICFES": ["110146580001100111100"], "NUMERO_CREDITOS": ["164"]}).to_excel(path, index=False)
            df = run_snies.leer_programas(path)
        self.assertEqual(df["CODIGO_ANTERIOR_ICFES"].iloc[0], "110146580001100111100")
        self.assertEqual(str(df["NUMERO_CREDITOS"].iloc[0]), "164")

    def test_falla_del_correo_marca_la_ejecucion_como_fallida(self):
        # Regresión: una falla al enviar el reporte quedaba solo en el log y la ejecución terminaba en verde
        resultados = {"nuevos": pd.DataFrame(), "modificados": pd.DataFrame(), "inactivos": pd.DataFrame(),
                      "total_activos": 1, "linea_base": False}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(run_snies, "LOGS_DIR", Path(tmp)), \
                mock.patch.object(run_snies, "ejecutar", return_value=resultados), \
                mock.patch("generar_dashboard.main", return_value=0), \
                mock.patch.object(send_report, "enviar_reporte", side_effect=RuntimeError("SMTP no disponible")), \
                mock.patch.object(send_report, "enviar_alerta_falla") as alerta:
            try:
                codigo = run_snies.main([])
            finally:
                for h in list(run_snies.log.handlers):
                    h.close()
                    run_snies.log.removeHandler(h)
        self.assertEqual(codigo, 1)
        alerta.assert_called_once()
        self.assertEqual(alerta.call_args.args[1], "CORREO")


class TestCorreo(unittest.TestCase):
    """Contenido, tamaño y envío del correo y de la alerta técnica."""
    def _df(self, codigos):
        return pd.DataFrame([programa(i, CODIGO_INSTITUCION=c, NOMBRE_INSTITUCION=f"INST {c}") for i, c in enumerate(codigos)])

    def test_muestra_todos_los_registros_con_prioridad(self):
        # Sin límite de filas: se muestran todos los registros, con Uninorte y Mayor Interés primero
        mostrar = send_report.seleccionar(self._df([9999] * 11 + [1711, 1711]))
        self.assertEqual(len(mostrar), 13)
        self.assertEqual(mostrar["CODIGO_INSTITUCION"].tolist()[:3], [1711, 1711, 9999])
        mostrar = send_report.seleccionar(self._df([9999] * 10 + [1701, 1713]))
        self.assertEqual(mostrar["CODIGO_INSTITUCION"].tolist()[:2], [1713, 1701])   # Uninorte primero
        self.assertEqual(len(send_report.seleccionar(self._df([9999] * 150))), 150)
        self.assertTrue(send_report.seleccionar(None).empty)

    def test_el_html_con_muchos_registros_no_supera_el_limite_de_gmail(self):
        # Gmail recorta los mensajes cuyo HTML supera ~102 KB: caso grande (35 nuevos y 61 cambios) con margen holgado
        nuevos = self._df(list(range(1, 36))).assign(TIPO_NOVEDAD="NUEVO", MUNICIPIO_OFERTA_PROGRAMA="BARRANQUILLA",
                                                     MODALIDAD="PRESENCIAL", DIVISION_UNINORTE="ESCUELA DE NEGOCIOS")
        mod = self._df(list(range(1, 62))).assign(TIPO_CAMBIO_DETECTADO="NUMERO DE CREDITOS", CAMBIO_DETECTADO="160 → 144",
                                                  MUNICIPIO_OFERTA_PROGRAMA="BARRANQUILLA", MODALIDAD="PRESENCIAL",
                                                  DIVISION_UNINORTE="INGENIERIAS")
        html = send_report.construir_cuerpo({"pregrado": {"nuevos": nuevos, "modificados": mod, "total_activos": 5500}}, dt.date(2026, 7, 28))
        self.assertEqual(html.count("160 → 144"), 61)   # todos los cambios, sin límite
        self.assertLess(len(html.encode("utf-8")), 90_000)

    def test_columnas_de_las_tablas(self):
        titulos = {t: [c[1] for c in send_report.SECCIONES[t]["columnas"]] for t in send_report.SECCIONES}
        self.assertEqual(titulos["nuevos"], ["INSTITUCIÓN", "PROGRAMA", "MUNICIPIO", "MODALIDAD", "TIPO DE NOVEDAD", "DIVISIÓN DE UNINORTE"])
        self.assertEqual(titulos["modificados"], ["INSTITUCIÓN", "PROGRAMA", "MUNICIPIO", "MODALIDAD", "TIPO DE CAMBIO",
                                                  "CAMBIO DETECTADO", "DIVISIÓN DE UNINORTE"])
        self.assertEqual(titulos["inactivos"], ["INSTITUCIÓN", "PROGRAMA", "MUNICIPIO", "MODALIDAD", "DIVISIÓN DE UNINORTE"])

    def test_html_escapa_contenido(self):
        df = self._df([1713]).assign(NOMBRE_DEL_PROGRAMA="<script>alert(1)</script>", TIPO_NOVEDAD="NUEVO")
        html = send_report.construir_cuerpo({"pregrado": {"nuevos": df, "total_activos": 1}}, dt.date(2026, 11, 17))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_envio_sin_adjuntos_y_con_tls_verificado(self):
        capturado = {}

        class SMTPFalso:
            def __init__(self, host, port, timeout=None):
                capturado["destino"] = (host, port, timeout)
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def ehlo(self): pass
            def starttls(self, context=None): capturado["contexto"] = context
            def login(self, u, p): capturado["login"] = u
            def sendmail(self, de, para, cuerpo): capturado["para"], capturado["cuerpo"] = para, cuerpo

        entorno = {"SMTP_USER": "remitente@example.com", "SMTP_PASS": "x", "DESTINATARIOS": "a@example.com, b@example.com"}
        with mock.patch.dict(os.environ, entorno), mock.patch.object(send_report.smtplib, "SMTP", SMTPFalso):
            send_report.enviar_reporte({"pregrado": {"nuevos": self._df([1713]).assign(TIPO_NOVEDAD="NUEVO"), "total_activos": 1}},
                                       dt.date(2026, 11, 17))
        ctx = capturado["contexto"]
        self.assertIsInstance(ctx, ssl.SSLContext)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(capturado["para"], ["a@example.com", "b@example.com"])
        self.assertNotIn(b"Content-Disposition: attachment", capturado["cuerpo"])

    def test_boton_arriba_y_nota_de_novedades_de_interes(self):
        mod = self._df([1713, 9999]).assign(TIPO_CAMBIO_DETECTADO="MODALIDAD", CAMBIO_DETECTADO="A → B")
        html = send_report.construir_cuerpo({"pregrado": {"modificados": mod, "total_activos": 2}}, dt.date(2026, 11, 17))
        self.assertLess(html.index("VER DASHBOARD COMPLETO"), html.index("PROGRAMAS ACTIVOS"))   # antes de los indicadores
        self.assertEqual(html.count("VER DASHBOARD COMPLETO"), 1)
        texto = re.sub(r"<[^>]+>", "", html)
        self.assertIn("Universidad del Norte: 1 registro · Universidades de Mayor Interés: 0 registros.", texto)
        self.assertNotIn("La tabla presenta", texto)   # caben todos los registros: sin remisión al Dashboard

    def test_todos_los_registros_y_nota_de_novedades_de_interes(self):
        # Caso real del 17/11/2026: 13 cambios, 2 de una universidad de mayor interés; la tabla los muestra todos
        mod = self._df([9999] * 11 + [1711, 1711]).assign(TIPO_CAMBIO_DETECTADO="MODALIDAD", CAMBIO_DETECTADO="A → B")
        res = {"pregrado": {"modificados": mod, "total_activos": 13}}
        html = send_report.construir_cuerpo(res, dt.date(2026, 11, 17))
        texto = re.sub(r"<[^>]+>", "", html)
        self.assertIn("Universidad del Norte: 0 registros · Universidades de Mayor Interés: 2 registros.", texto)
        self.assertEqual(html.count("A → B"), 13)
        self.assertNotIn("La tabla presenta", texto)
        plano = send_report.construir_texto(res, dt.date(2026, 11, 17))
        self.assertEqual(" ".join(plano.split()).count("CAMBIO DETECTADO: A → B"), 13)   # el texto plano ajusta las líneas
        self.assertNotIn("La tabla presenta", plano)

    def test_tabla_de_novedades_por_grupo(self):
        res = {"nuevos": self._df([1713, 9999, 9999]).assign(TIPO_NOVEDAD="NUEVO"),
               "modificados": self._df([1711, 9999]).assign(TIPO_CAMBIO_DETECTADO="MODALIDAD", CAMBIO_DETECTADO="A → B"),
               "inactivos": self._df([9999]), "total_activos": 4}
        columnas, filas = send_report._grupos(res)
        self.assertEqual([c[1] for c in columnas], ["GRUPO DE UNIVERSIDADES", "NUEVOS", "MODIFICADOS", "INACTIVOS"])
        self.assertEqual(filas["GRUPO"].tolist(), ["UNIVERSIDAD DEL NORTE", "UNIVERSIDADES DE MAYOR INTERÉS", "OTRAS UNIVERSIDADES", "TOTAL"])
        self.assertEqual(filas["NUEVOS"].tolist(), ["1", "0", "2", "3"])
        self.assertEqual(filas["MODIFICADOS"].tolist(), ["0", "1", "1", "2"])
        self.assertEqual(filas["INACTIVOS"].tolist(), ["0", "0", "1", "1"])
        html = send_report.construir_cuerpo({"pregrado": res}, dt.date(2026, 11, 17))
        self.assertIn("NOVEDADES POR GRUPO DE UNIVERSIDADES", html)
        for retirado in ("EJECUCIÓN ANTERIOR", "ESTA EJECUCIÓN", "VARIACIÓN"):
            self.assertNotIn(retirado, html)
        # Sin novedades: la tabla muestra ceros, no desaparece
        _, vacia = send_report._grupos({"total_activos": 5})
        self.assertEqual(vacia["NUEVOS"].tolist(), ["0", "0", "0", "0"])

    def test_resumen_descriptivo_con_variacion_y_grupos(self):
        res = {"nuevos": self._df([1713, 9999]).assign(TIPO_NOVEDAD="NUEVO"), "modificados": None, "inactivos": None,
               "total_activos": 4, "total_anterior": 3, "fecha_anterior": dt.date(2026, 11, 10)}
        html = send_report.construir_cuerpo({"pregrado": res}, dt.date(2026, 11, 17))
        self.assertIn("1 más que en la ejecución anterior (10/11/2026: 3)", html)
        self.assertIn("Novedades detectadas: 2 registros nuevos, 0 registros modificados y 0 registros inactivos.", html)
        self.assertIn("1 corresponde a la Universidad del Norte y 0 a las Universidades de Mayor Interés", html)
        self.assertIn("+1 frente al 10/11/2026", html)
        self.assertNotIn("—", html)   # estilo institucional: sin raya

    def test_resumen_sin_novedades_ni_ejecucion_anterior(self):
        html = send_report.construir_cuerpo({"pregrado": {"total_activos": 5}}, dt.date(2026, 11, 17))
        self.assertIn("No se detectaron programas nuevos, modificados ni inactivos.", html)
        self.assertIn("Programas activos al 17/11/2026", html)

    def test_version_texto_plano(self):
        res = {"nuevos": self._df([1713, 9999]).assign(TIPO_NOVEDAD="NUEVO"), "total_activos": 4, "total_anterior": 3,
               "fecha_anterior": dt.date(2026, 11, 10)}
        with mock.patch.dict(os.environ, {"SMTP_USER": "r@example.com", "SMTP_PASS": "x", "DESTINATARIOS": "a@example.com"}), \
                mock.patch.object(send_report, "_enviar") as enviar:
            send_report.enviar_reporte({"pregrado": res}, dt.date(2026, 11, 17))
        partes = enviar.call_args.args[0].get_payload()
        self.assertEqual([p.get_content_type() for p in partes], ["text/plain", "multipart/related"])   # HTML preferido
        # El HTML lleva el logotipo institucional incrustado (Content-ID), sin imágenes remotas
        self.assertEqual([p.get_content_type() for p in partes[1].get_payload()], ["text/html", "image/png"])
        html = partes[1].get_payload()[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("cid:logo-uninorte", html)
        self.assertEqual(partes[1].get_payload()[1]["Content-ID"], "<logo-uninorte>")
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        texto = partes[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("1 más que en la ejecución anterior (10/11/2026: 3)", texto)
        self.assertIn("UNIVERSIDAD DEL NORTE: nuevos 1; modificados 0; inactivos 0", texto)
        self.assertIn("TOTAL: nuevos 2; modificados 0; inactivos 0", texto)
        self.assertIn("1. INSTITUCIÓN: INST 1713", texto)    # Uninorte primero
        self.assertIn(f"Dashboard completo: {send_report.DASHBOARD_URL}", texto.splitlines()[3])
        self.assertNotIn("Ver todos en el Dashboard", texto)
        self.assertNotIn("TIPO:", texto)
        self.assertNotIn("<", texto)
        self.assertNotIn("—", texto)

    def test_asunto_sin_cifras(self):
        with mock.patch.dict(os.environ, {"SMTP_USER": "r@example.com", "SMTP_PASS": "x", "DESTINATARIOS": "a@example.com"}), \
                mock.patch.object(send_report, "_enviar") as enviar:
            send_report.enviar_reporte({"pregrado": {"nuevos": self._df([1713]).assign(TIPO_NOVEDAD="NUEVO"),
                                                     "total_activos": 1}}, dt.date(2026, 11, 17))
        asunto = str(enviar.call_args.args[0]["Subject"])
        self.assertEqual(asunto, "MONITOR SNIES PREGRADO · UNIVERSITARIO · Reporte del 17/11/2026")

    def test_paleta_del_correo_coincide_con_la_del_dashboard(self):
        css = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "estilos.css").read_text(encoding="utf-8")
        variables = {k: v.upper() for k, v in re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", css)}
        esperado = {"negro": send_report.NEGRO, "rojo": send_report.ROJO, "amarillo": send_report.AMARILLO,
                    "gris-claro": send_report.GRIS, "gris-sep": send_report.BORDE, "texto-2": send_report.TEXTO2,
                    "texto-3": send_report.TEXTO3, "nuevos": send_report.ESTADO["nuevos"],
                    "modificados": send_report.ESTADO["modificados"], "inactivos": send_report.ESTADO["inactivos"]}
        for nombre, color in esperado.items():
            self.assertEqual(variables.get(nombre), color.upper(), f"--{nombre} difiere entre el Dashboard y el correo")
        # Las gráficas (app.js) usan los mismos colores institucionales y de estado que la hoja de estilos
        js = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "app.js").read_text(encoding="utf-8").upper()
        for nombre in ("rojo", "amarillo", "negro", "texto-2", "gris-sep", "nuevos", "modificados", "inactivos", "modificados-linea"):
            self.assertIn(f'"{variables[nombre]}"', js, f"--{nombre} ({variables[nombre]}) no se usa igual en app.js")

    def test_neutros_declarados_en_una_sola_lista(self):
        """Ningún color del Dashboard, de las gráficas ni del correo queda fuera de las variables de estilos.css."""
        raiz = Path(__file__).resolve().parent.parent
        css = (raiz / "docs" / "assets" / "estilos.css").read_text(encoding="utf-8")
        inicio = css.index(":root {")
        fin = css.index("\n}\n", inicio)
        variables = {k: v.upper() for k, v in re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", css[inicio:fin])}
        declarados = set(variables.values())
        hexes = lambda texto: {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", texto)}  # noqa: E731
        self.assertLessEqual(hexes(css[fin:]), declarados, "estilos.css usa colores sin declarar como variable")
        js = (raiz / "docs" / "assets" / "app.js").read_text(encoding="utf-8")
        paleta = hexes(re.search(r"const CATEGORICA = \[(.*?)\];", js, re.S).group(1))
        self.assertLessEqual(hexes(js) - paleta, declarados, "app.js usa colores que no están en estilos.css")
        correo = (raiz / "Scripts" / "send_report.py").read_text(encoding="utf-8")
        self.assertLessEqual(hexes(correo), declarados, "send_report.py usa colores que no están en estilos.css")
        # Los neutros del correo y de las gráficas son los de las variables
        self.assertEqual((send_report.ZEBRA, send_report.TEXTO_SOBRE_NEGRO), (variables["gris-zebra"], variables["texto-sobre-negro"]))
        self.assertEqual((send_report.LINEA_SOBRE_NEGRO, send_report.FONDO_SOBRE_NEGRO), (variables["linea-sobre-negro"], variables["fondo-sobre-negro"]))
        self.assertIn(f'const GRIS = "{variables["gris-serie-otros"]}"', js.upper().replace("CONST GRIS", "const GRIS"))
        self.assertIn(f'zerolinecolor: "{variables["gris-eje-cero"]}"', js.upper().replace("ZEROLINECOLOR", "zerolinecolor"))

    def test_cuadro_de_ejecucion_del_correo_equivale_al_del_dashboard(self):
        """Borde y fondo opacos del correo = rgba(255,255,255,alfa) del Dashboard mezclado con el negro del encabezado."""
        raiz = Path(__file__).resolve().parent.parent
        css = (raiz / "docs" / "assets" / "estilos.css").read_text(encoding="utf-8")
        regla = re.search(r"\.enc-total \{[^}]*\}", css).group(0)
        fondo = float(re.search(r"background: rgba\(255, 255, 255, ([.\d]+)\)", regla).group(1))
        borde = float(re.search(r"border: 1px solid rgba\(255, 255, 255, ([.\d]+)\)", regla).group(1))
        negro = int(send_report.NEGRO[1:3], 16)
        mezcla = lambda alfa: round(alfa * 255 + (1 - alfa) * negro)  # noqa: E731
        for alfa, color in ((borde, send_report.LINEA_SOBRE_NEGRO), (fondo, send_report.FONDO_SOBRE_NEGRO)):
            self.assertAlmostEqual(int(color[1:3], 16), mezcla(alfa), delta=1)

    def test_colores_del_brandbook_en_dashboard_y_correo(self):
        """Los HEX impresos en BRANDBOOK_UNINORTE: rojo Pantone 7427 C, negro Pantone Black 6 C, amarillo 116 C, ocre 7556 C, gris K70."""
        css = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "estilos.css").read_text(encoding="utf-8")
        variables = {k: v.upper() for k, v in re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", css)}
        marca = {"rojo": "#960A11", "negro": "#151515", "blanco": "#FFFFFF", "amarillo": "#FFCC00",
                 "modificados": "#FFCC00", "modificados-linea": "#B78B1E", "inactivos": "#960A11", "texto": "#151515", "texto-2": "#3E4751"}
        for nombre, hexa in marca.items():
            self.assertEqual(variables[nombre], hexa, f"--{nombre} no coincide con el brandbook")
        self.assertEqual((send_report.ROJO, send_report.NEGRO, send_report.AMARILLO, send_report.TEXTO2), ("#960A11", "#151515", "#FFCC00", "#3E4751"))
        # Los colores anteriores (rojo #BE0000, naranja #F28C00, amarillo #FFB700, negro puro) ya no deben quedar en el código
        raiz = Path(__file__).resolve().parent.parent
        for ruta in ("docs/assets/estilos.css", "docs/assets/app.js", "docs/assets/detalle.js", "docs/assets/inicio.js", "Scripts/send_report.py"):
            texto = (raiz / ruta).read_text(encoding="utf-8").upper()
            for viejo in ("#BE0000", "#F28C00", "#FFB700", "#000000", "#4D4D4D"):
                self.assertNotIn(viejo, texto, f"{ruta} conserva el color anterior {viejo}")

    def test_logotipos_institucionales_disponibles(self):
        """Los logotipos de docs/assets/img salen de 'Recursos Uninorte' (solo recorte de márgenes) y el correo usa el blanco."""
        raiz = Path(__file__).resolve().parent.parent
        self.assertTrue(send_report.LOGO.exists())
        self.assertEqual(send_report.LOGO.name, "logo-uninorte-blanco.png")
        for origen, derivado in (("BLANCO", "blanco"), ("NEGRO", "negro"), ("PRINCIPAL", "principal")):
            original = raiz / "Recursos Uninorte" / f"LOGO-UN-{origen}.png"
            copia = raiz / "docs" / "assets" / "img" / f"logo-uninorte-{derivado}.png"
            self.assertTrue(copia.exists(), f"falta {copia.name}")
            if original.exists():   # la carpeta de recursos no es indispensable para ejecutar el monitor
                from PIL import Image
                a, b = Image.open(original).convert("RGBA"), Image.open(copia).convert("RGBA")
                recorte = a.crop(a.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox())
                self.assertEqual(list(recorte.getdata()), list(b.getdata()), f"{copia.name} difiere del original")
        favicon = raiz / "Recursos Uninorte" / "FAVICON-UN.ico"
        if favicon.exists():
            self.assertEqual((raiz / "docs" / "assets" / "img" / "favicon-uninorte.ico").read_bytes(), favicon.read_bytes())

    def test_alerta_tecnica_solo_al_remitente(self):
        # Alerta del paso "Pruebas unitarias" del workflow: el log está fuera de Logs/ y no debe presentarse como tal
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"SMTP_USER": "remitente@example.com", "SMTP_PASS": "x", "DESTINATARIOS": "a@example.com"}), \
                mock.patch.object(send_report, "_enviar") as enviar:
            log_path = Path(tmp) / "pruebas_unitarias.log"
            log_path.write_text("FAIL: test_x", encoding="utf-8")
            send_report.enviar_alerta_falla(dt.date(2026, 11, 17), "PRUEBAS UNITARIAS", "Fallaron pruebas", log_path)
        msg, _, _, destinatarios = enviar.call_args.args
        self.assertEqual(destinatarios, ["remitente@example.com"])
        cuerpo = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("FAIL: test_x", cuerpo)
        self.assertIn(">pruebas_unitarias.log<", cuerpo)
        self.assertNotIn("Logs/pruebas_unitarias.log", cuerpo)


class TestCategoriasDesconocidas(unittest.TestCase):
    """Aviso de valores del reporte que el Dashboard no contempla (sectores, modalidades y divisiones)."""
    def test_sin_hallazgos_con_valores_conocidos(self):
        df = pd.DataFrame({"SECTOR": ["OFICIAL", "PRIVADO", " privado "], "MODALIDAD": ["PRESENCIAL", "HIBRIDA (DUAL-VIRTUAL)", None],
                           "DIVISION_UNINORTE": ["SALUD", "DERECHO", "SALUD"]})
        self.assertEqual(run_snies.categorias_desconocidas(df), {})

    def test_detecta_sector_y_modalidad_nuevos(self):
        df = pd.DataFrame({"SECTOR": ["OFICIAL", "MIXTO"], "MODALIDAD": ["PRESENCIAL", "Nueva Modalidad"], "DIVISION_UNINORTE": ["SALUD", "SALUD"]})
        self.assertEqual(run_snies.categorias_desconocidas(df), {"SECTOR": ["MIXTO"], "MODALIDAD": ["NUEVA MODALIDAD"]})

    def test_detecta_mas_divisiones_que_colores(self):
        df = pd.DataFrame({"DIVISION_UNINORTE": [f"DIVISION {i}" for i in range(catalogos.MAX_COLORES_SERIES + 1)]})
        self.assertEqual(list(run_snies.categorias_desconocidas(df)), ["DIVISION_UNINORTE"])
        self.assertEqual(run_snies.categorias_desconocidas(df.iloc[:catalogos.MAX_COLORES_SERIES]), {})

    def test_columnas_ausentes_no_fallan_y_el_aviso_no_detiene_el_flujo(self):
        self.assertEqual(run_snies.categorias_desconocidas(pd.DataFrame({"OTRA": [1]})), {})
        with self.assertLogs("snies_monitor", level="WARNING") as capturado:
            hallazgos = run_snies.avisar_categorias(pd.DataFrame({"SECTOR": ["MIXTO"]}))
        self.assertEqual(hallazgos, {"SECTOR": ["MIXTO"]})
        self.assertIn("MIXTO", capturado.output[0])

    def test_catalogos_coinciden_con_los_del_dashboard(self):
        """Los sectores y el número de colores del catálogo son los que usa app.js."""
        js = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "app.js").read_text(encoding="utf-8")
        paleta = re.findall(r"#[0-9A-Fa-f]{6}", re.search(r"const CATEGORICA = \[(.*?)\];", js, re.S).group(1))
        self.assertEqual(len(paleta), catalogos.MAX_COLORES_SERIES)
        self.assertEqual(set(re.findall(r"(PRIVADO|OFICIAL)", re.search(r"const colorSector = .*", js).group(0))), set(catalogos.SECTORES))


class TestRegistroSeguro(unittest.TestCase):
    """El registro de ejecución no expone rutas personales ni direcciones de correo."""
    def test_oculta_direcciones_de_correo(self):
        formato = run_snies._FormatoSeguro("%(message)s")
        try:
            raise smtplib.SMTPRecipientsRefused({"persona.ejemplo+a@uninorte.edu.co": (550, b"5.1.1 usuario desconocido"), "otra@correo.com.co": (550, b"x")})
        except smtplib.SMTPRecipientsRefused as exc:
            registro = logging.LogRecord("x", 40, "", 0, f"FALLA EN CORREO: {exc}", None, (type(exc), exc, exc.__traceback__))
        texto = formato.format(registro)
        for direccion in ("persona.ejemplo+a@uninorte.edu.co", "otra@correo.com.co", "uninorte.edu.co"):
            self.assertNotIn(direccion, texto)
        self.assertIn("<correo>", texto)
        self.assertIn("5.1.1 usuario desconocido", texto)   # el resto del mensaje de error se conserva
        self.assertEqual(formato.format(logging.LogRecord("x", 20, "", 0, "Alerta técnica enviada a remitente@gmail.com.", None, None)), "Alerta técnica enviada a <correo>.")

    def test_oculta_rutas_personales(self):
        formato = run_snies._FormatoSeguro("%(message)s")
        msg = f"{Path.home()}{os.sep}Desktop y {tempfile.gettempdir()}{os.sep}x.log"
        texto = formato.format(logging.LogRecord("x", 20, "", 0, msg, None, None))
        self.assertNotIn(str(Path.home()), texto)
        self.assertIn("~", texto)
        self.assertIn("<TEMP>", texto)


class TestDatosDashboard(unittest.TestCase):
    """Datos que el generador publica para el Dashboard."""
    def test_codificacion_columnar(self):
        df = pd.DataFrame({"A": ["X", "X", "Y", "X"], "B": [1.0, 2.5, None, 4.0], "C": pd.to_datetime(["2026-01-01", None, "2026-01-03", "2026-01-04"])})
        t = generar_dashboard.codificar(df)
        decod = lambda c: t["datos"][c] if isinstance(t["datos"][c], list) else [t["datos"][c]["d"][i] for i in t["datos"][c]["i"]]  # noqa: E731
        self.assertEqual(decod("A"), ["X", "X", "Y", "X"])
        self.assertEqual(decod("B"), [1, 2.5, None, 4])
        self.assertEqual(decod("C"), ["2026-01-01", None, "2026-01-03", "2026-01-04"])

    def test_rangos(self):
        self.assertEqual(generar_dashboard._rangos([0, 1, 2, 5, 7, 8]), [[0, 2], [5, 5], [7, 8]])


class TestSeguridadDelSitio(unittest.TestCase):
    """Política de seguridad de contenido de las páginas del Dashboard y configuración de Dependabot."""

    PAGINAS = ("index", "nuevos", "modificados", "inactivos")

    @staticmethod
    def _scripts_en_linea(html: str) -> list[str]:
        """Contenido de los scripts sin atributo src, analizado con el analizador HTML (no con expresiones regulares)."""
        from html.parser import HTMLParser

        class Recolector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.actual, self.scripts = None, []

            def handle_starttag(self, etiqueta, atributos):
                if etiqueta == "script" and "src" not in dict(atributos):
                    self.actual = []

            def handle_data(self, datos):
                if self.actual is not None:
                    self.actual.append(datos)

            def handle_endtag(self, etiqueta):
                if etiqueta == "script" and self.actual is not None:
                    self.scripts.append("".join(self.actual))
                    self.actual = None

        recolector = Recolector()
        recolector.feed(html)
        return recolector.scripts

    def test_politica_de_contenido_y_huella_del_mapa_de_importacion(self):
        import base64
        import hashlib
        docs = Path(__file__).resolve().parent.parent / "docs"
        for nombre in self.PAGINAS:
            html = (docs / f"{nombre}.html").read_text(encoding="utf-8")
            politica = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html)
            self.assertIsNotNone(politica, f"{nombre}.html sin política de seguridad")
            for directiva in ("default-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'none'", "connect-src 'self'"):
                self.assertIn(directiva, politica.group(1))
            self.assertNotIn("unsafe-eval", politica.group(1))
            self.assertNotIn("script-src 'self' 'unsafe-inline'", politica.group(1))
            # El único script en línea es el mapa de importación, autorizado por su huella SHA-256
            en_linea = self._scripts_en_linea(html)
            self.assertEqual(len(en_linea), 1)
            huella = "sha256-" + base64.b64encode(hashlib.sha256(en_linea[0].encode()).digest()).decode()
            self.assertIn(f"'{huella}'", politica.group(1))
            self.assertNotRegex(html, r'(?:src|href)="https?://')   # ningún recurso externo

    def test_dependabot_cubre_acciones_y_librerias(self):
        texto = (Path(__file__).resolve().parent.parent / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "github-actions"', texto)
        self.assertIn('package-ecosystem: "pip"', texto)
        self.assertIn('interval: "monthly"', texto)
        self.assertIn('package-ecosystem: "npm"', texto)
        self.assertIn('directory: "/Librerias"', texto)

    def test_registro_de_librerias_incluidas_coincide_con_los_archivos(self):
        """El registro de versiones vigilado por Dependabot coincide con las librerías copiadas en docs/assets/vendor."""
        raiz = Path(__file__).resolve().parent.parent
        dependencias = json.loads((raiz / "Librerias" / "package.json").read_text(encoding="utf-8"))["dependencies"]
        self.assertEqual(set(dependencias), {"plotly.js-dist-min", "flatpickr", "hyparquet", "hyparquet-writer"})
        for nombre, version in dependencias.items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", f"{nombre}: la versión debe ser exacta (sin ^ ni ~)")
        vendor = raiz / "docs" / "assets" / "vendor"
        plotly = (vendor / "plotly" / "plotly.min.js").read_text(encoding="utf-8")[:300]
        flatpickr = (vendor / "flatpickr" / "flatpickr.min.js").read_text(encoding="utf-8")[:300]
        self.assertIn(f"plotly.js v{dependencias['plotly.js-dist-min']}", plotly)
        self.assertIn(f"flatpickr v{dependencias['flatpickr']}", flatpickr)
        bloqueo = json.loads((raiz / "Librerias" / "package-lock.json").read_text(encoding="utf-8"))["packages"]
        for nombre, version in dependencias.items():
            self.assertEqual(bloqueo[f"node_modules/{nombre}"]["version"], version, f"package-lock.json no coincide con package.json en {nombre}")
        for carpeta in ("hyparquet", "hyparquet-writer"):   # sin la versión en sus archivos: basta con que existan y tengan licencia
            self.assertTrue((vendor / carpeta / "LICENSE").exists() and (vendor / carpeta / "src" / "index.js").exists())


class TestFlujosDeTrabajo(unittest.TestCase):
    """Configuración de los workflows de GitHub Actions: permisos mínimos, acciones fijadas y flujo de pruebas del Dashboard."""

    RAIZ = Path(__file__).resolve().parent.parent / ".github" / "workflows"

    def test_acciones_fijadas_a_un_commit(self):
        for ruta in self.RAIZ.glob("*.yml"):
            for accion in re.findall(r"uses:\s*(\S+)", ruta.read_text(encoding="utf-8")):
                self.assertRegex(accion, r"@[0-9a-f]{40}$", f"{ruta.name}: {accion} debe fijarse a un commit exacto")

    def test_pruebas_del_dashboard_son_de_solo_lectura_y_corren_las_dos_suites(self):
        texto = (self.RAIZ / "pruebas_dashboard.yml").read_text(encoding="utf-8")
        self.assertRegex(texto, r"permissions:\s*\n\s+contents: read\b")
        self.assertNotIn("contents: write", texto)
        self.assertNotIn("secrets.", texto)                  # no necesita credenciales
        self.assertNotIn("pull_request_target", texto)       # el código de una solicitud de cambios no corre con permisos del repositorio
        self.assertIn("persist-credentials: false", texto)
        self.assertIn("workflow_dispatch:", texto)
        self.assertIn("python -m unittest discover -s tests", texto)
        self.assertIn("python tests/e2e_dashboard.py", texto)
        self.assertRegex(texto, r"timeout-minutes:\s*\d+")
        # Ejecuta el Dashboard completo cuando cambia lo que lo genera o lo prueba
        for ruta in ("docs/assets/**", "Scripts/generar_dashboard.py", "tests/**"):
            self.assertEqual(texto.count(f'"{ruta}"'), 2, f"{ruta} debe disparar la prueba en pull_request y en push")

    def test_el_flujo_semanal_no_depende_de_la_prueba_del_dashboard(self):
        semanal = (self.RAIZ / "snies_daily.yml").read_text(encoding="utf-8")
        self.assertNotIn("e2e_dashboard", semanal)           # la actualización semanal solo ejecuta las pruebas unitarias


class TestFechaSinHora(unittest.TestCase):
    """FECHA_DE_OBTENCION se guarda como fecha (AAAA-MM-DD, sin hora) en el histórico y en las novedades."""

    def test_historico_y_novedades_guardan_fecha_sin_hora(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(historico, "HISTORICO_DIR", Path(tmp)):
            historico.guardar_ejecucion(pd.DataFrame([programa(1), programa(2)]), dt.date(2026, 11, 3))
            historico.guardar_ejecucion(pd.DataFrame([programa(1), programa(2), programa(3)]), dt.date(2026, 11, 10))
            ruta = historico.ruta(2026)
            self.assertEqual(pq.read_schema(ruta).field(F).type, pa.date32())
            anotacion = {c["name"]: c for c in json.loads(pq.read_schema(ruta).metadata[b"pandas"])["columns"]}
            self.assertEqual(anotacion[F]["pandas_type"], "date")          # la anotación de pandas coincide con el tipo real
            self.assertEqual(historico.fechas(), [dt.date(2026, 11, 3), dt.date(2026, 11, 10)])
            self.assertTrue(str(historico.leer()[F].dtype).startswith("datetime64"))          # en memoria sigue siendo datetime64
            self.assertEqual(len(historico.leer(fecha=dt.date(2026, 11, 10))), 3)               # los filtros por fecha funcionan
            self.assertEqual(len(historico.leer(antes_de=dt.date(2026, 11, 10))), 2)
            nov = Path(tmp) / "Nuevos.parquet"
            run_snies._escribir_parquet(pd.DataFrame([programa(3)]).assign(**{F: pd.Timestamp("2026-11-10"), "TIPO_NOVEDAD": "NUEVO"}), nov)
            self.assertEqual(pq.read_schema(nov).field(F).type, pa.date32())
            self.assertEqual(pq.read_table(nov).column(F)[0].as_py(), dt.date(2026, 11, 10))

    def test_migracion_de_marca_de_tiempo_a_fecha_conserva_los_datos(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(historico, "HISTORICO_DIR", Path(tmp)):
            df = pd.DataFrame([programa(1), programa(2)]).assign(**{F: pd.Timestamp("2026-11-03")})
            tabla = pa.Table.from_pandas(df, preserve_index=False)            # archivo anterior: marca de tiempo
            pq.write_table(tabla, historico.ruta(2026))
            self.assertTrue(pa.types.is_timestamp(pq.read_schema(historico.ruta(2026)).field(F).type))
            antes = historico.leer()
            self.assertEqual([p.name for p in historico.migrar_fecha_a_date()], [historico.ruta(2026).name])
            self.assertEqual(pq.read_schema(historico.ruta(2026)).field(F).type, pa.date32())
            anotacion = {c["name"]: c for c in json.loads(pq.read_schema(historico.ruta(2026)).metadata[b"pandas"])["columns"]}
            self.assertEqual(anotacion[F]["pandas_type"], "date")
            despues = historico.leer()
            self.assertEqual(historico.migrar_fecha_a_date(), [])              # idempotente
            self.assertEqual(antes.drop(columns=F).to_dict("records"), despues.drop(columns=F).to_dict("records"))
            self.assertEqual(pd.to_datetime(antes[F]).tolist(), pd.to_datetime(despues[F]).tolist())
            # Una ejecución posterior se agrega sobre el archivo ya migrado
            historico.guardar_ejecucion(pd.DataFrame([programa(1)]), dt.date(2026, 11, 10))
            self.assertEqual(historico.fechas(), [dt.date(2026, 11, 3), dt.date(2026, 11, 10)])


class TestCatalogosDelDashboard(unittest.TestCase):
    """Datos que el Dashboard usa para los filtros jerárquicos de universidades y de campos de CINE."""

    @staticmethod
    def _registros():
        cols = ["CODIGO_INSTITUCION", "CODIGO_INSTITUCION_PADRE", "NOMBRE_INSTITUCION", "CINE_F_2013_AC_CAMPO_AMPLIO",
                "CINE_F_2013_AC_CAMPO_ESPECIFIC", "FECHA_DE_OBTENCION"]
        hist = pd.DataFrame([
            [9001, 9001, "UNIVERSIDAD ZETA -", "SALUD", "MEDICINA", pd.Timestamp("2026-01-01")],
            [9001, 9001, "UNIVERSIDAD ZETA", "SALUD", "ENFERMERIA", pd.Timestamp("2026-02-01")],
            [9002, 9002, "UNIVERSIDAD ALFA", "EDUCACION", "PEDAGOGIA", pd.Timestamp("2026-02-01")],
            [9002, 9002, "UNIVERSIDAD ALFA", "EDUCACION", "PEDAGOGIA", pd.Timestamp("2026-03-01")],
            [9004, 9002, "UNIVERSIDAD ALFA SECCIONAL", "EDUCACION", "PEDAGOGIA", pd.Timestamp("2026-03-15")],
            [9003, 9003, "UNIVERSIDAD SIN CAMPO", None, None, pd.Timestamp("2026-03-01")],
        ], columns=cols)
        return hist, {"nuevos": hist.iloc[:1].copy()}

    def test_instituciones_con_el_nombre_mas_reciente_y_las_universidades_de_interes(self):
        hist, nov = self._registros()
        inst = generar_dashboard.instituciones(hist, nov)
        self.assertEqual(inst["9001"], "UNIVERSIDAD ZETA")        # nombre más reciente, sin el guion final
        self.assertEqual(inst["9002"], "UNIVERSIDAD ALFA")         # por institución padre: la seccional 9004 no figura aparte
        self.assertNotIn("9004", inst)
        self.assertIn(str(catalogos.UNINORTE), inst)             # figuran aunque no estén en los datos
        for codigo in catalogos.MAYOR_INTERES:
            self.assertIn(str(codigo), inst)

    def test_jerarquia_cine_ordenada_y_sin_vacios(self):
        hist, nov = self._registros()
        self.assertEqual(generar_dashboard.jerarquia_cine(hist, nov),
                         [["EDUCACION", ["PEDAGOGIA"]], ["SALUD", ["ENFERMERIA", "MEDICINA"]]])

    def test_divisiones_del_catalogo_unicas_y_ordenadas(self):
        """El catálogo fija el color de cada división: sin repetidas, sin vacíos y en orden alfabético estable."""
        hist = pd.DataFrame({"DIVISION_UNINORTE": ["SALUD", "ARTES", None, "SALUD", ""]})
        nov = {"nuevos": pd.DataFrame({"DIVISION_UNINORTE": ["DERECHO", "ARTES"]}), "inactivos": pd.DataFrame({"X": [1]})}
        self.assertEqual(generar_dashboard.divisiones_uninorte(hist, nov), ["ARTES", "DERECHO", "SALUD"])

    def test_paleta_de_series_contraste_y_separacion(self):
        """Paleta de las gráficas de varias series: 15 colores únicos, contraste ≥ 3:1 sobre blanco y series contiguas distinguibles."""
        import math
        js = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "app.js").read_text(encoding="utf-8")
        paleta = re.findall(r'#[0-9A-Fa-f]{6}', re.search(r"const CATEGORICA = \[(.*?)\];", js, re.S).group(1))
        self.assertEqual(len(paleta), 15)
        self.assertEqual(len({c.upper() for c in paleta}), 15)
        self.assertNotIn("#FFCC00", {c.upper() for c in paleta})   # el amarillo y el ocre quedan para Modificados
        self.assertNotIn("#B78B1E", {c.upper() for c in paleta})
        lineal = lambda c: [((v / 255) / 12.92 if v / 255 <= 0.04045 else (((v / 255) + 0.055) / 1.055) ** 2.4) for v in (int(c[i:i + 2], 16) for i in (1, 3, 5))]  # noqa: E731
        luz = lambda rgb: 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]  # noqa: E731

        def oklab(rgb):
            l, m, s = (x ** (1 / 3) for x in (0.4122214708 * rgb[0] + 0.5363325363 * rgb[1] + 0.0514459929 * rgb[2],
                                              0.2119034982 * rgb[0] + 0.6806995451 * rgb[1] + 0.1073969566 * rgb[2],
                                              0.0883024619 * rgb[0] + 0.2817188376 * rgb[1] + 0.6299787005 * rgb[2]))
            return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)

        sim = {"protan": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
               "deutan": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]]}
        de = lambda a, b: 100 * math.dist(oklab(a), oklab(b))  # noqa: E731
        con = lambda rgb, k: [sum(sim[k][i][j] * rgb[j] for j in range(3)) for i in range(3)]  # noqa: E731
        for c in paleta:
            self.assertGreaterEqual(1.05 / (luz(lineal(c)) + 0.05), 3.0, f"{c} tiene menos de 3:1 de contraste sobre blanco")
        for a, b in zip(paleta, paleta[1:]):
            self.assertGreaterEqual(de(lineal(a), lineal(b)), 15, f"{a} y {b} se parecen con visión normal")
            for k in sim:
                self.assertGreaterEqual(de(con(lineal(a), k), con(lineal(b), k)), 6, f"{a} y {b} se confunden en {k}")

    def test_orden_de_columnas_de_las_tablas(self):
        primeras = generar_dashboard.COLS_PRIMERO
        base = ["NOMBRE_INSTITUCION", "NOMBRE_DEL_PROGRAMA", "DEPARTAMENTO_OFERTA_PROGRAMA", "MUNICIPIO_OFERTA_PROGRAMA",
                "SECTOR", "MODALIDAD"]   # SECTOR inmediatamente después del municipio y antes de los créditos
        cola = ["NUMERO_CREDITOS", "NUMERO_PERIODOS_DE_DURACION", "PERIODICIDAD", "COSTO_MATRICULA_ESTUD_NUEVOS", "DIVISION_UNINORTE"]
        self.assertEqual(primeras["nuevos"], ["FECHA_DE_OBTENCION", "TIPO_NOVEDAD"] + base + cola)
        self.assertEqual(primeras["modificados"], ["FECHA_DE_OBTENCION", "TIPO_CAMBIO_DETECTADO", "CAMBIO_DETECTADO"] + base + cola)
        self.assertEqual(primeras["inactivos"], ["FECHA_DE_OBTENCION"] + base + cola)
        for tipo in ("nuevos", "modificados", "inactivos"):
            cols = primeras[tipo]
            self.assertEqual(cols.index("SECTOR"), cols.index("MUNICIPIO_OFERTA_PROGRAMA") + 1)
            self.assertLess(cols.index("SECTOR"), cols.index("NUMERO_CREDITOS"))
        # Las demás columnas conservan su orden y no se agrega REACTIVADO_POSTERIORMENTE
        df = pd.DataFrame(columns=["X1", *base[::-1], "X2", *cola, "TIPO_NOVEDAD", "FECHA_DE_OBTENCION", "X3"])
        ordenado = generar_dashboard._ordenar_columnas(df, "nuevos")
        self.assertEqual(list(ordenado.columns), ["FECHA_DE_OBTENCION", "TIPO_NOVEDAD"] + base + cola + ["X1", "X2", "X3"])
        self.assertFalse(any("REACTIVADO" in c for tipo in primeras for c in primeras[tipo]))
        # El municipio viaja con las versiones de programas activos (filtro de municipios)
        self.assertIn("MUNICIPIO_OFERTA_PROGRAMA", generar_dashboard.COLS_VERSION)


class TestReclasificacion(unittest.TestCase):
    """Reclasificación de divisiones del histórico y de las novedades."""
    def test_aplicar_clasificacion(self):
        df = pd.DataFrame([programa(1, NUCLEO_BASICO_DEL_CONOCIMIENTO="N", CINE_F_2013_AC_CAMPO_DETALLADO="C", DIVISION_UNINORTE="VIEJA",
                                    PUNTAJE_TOTAL=0.1, REQUIERE_REVISION="FALSO", **{F: pd.Timestamp("2026-11-03")})])
        clave = reclasificar.claves(df).iloc[0]
        mapa = pd.DataFrame({"DIVISION_UNINORTE": ["NUEVA"], "PUNTAJE_TOTAL": [0.9]}, index=[clave])
        out = reclasificar.aplicar_clasificacion(df, mapa)
        self.assertEqual(out["DIVISION_UNINORTE"].tolist(), ["NUEVA"])
        self.assertEqual(out["PUNTAJE_TOTAL"].tolist(), [0.9])
        self.assertNotIn("REQUIERE_REVISION", out.columns)

    def _escenario(self, tmp):
        """Histórico de 2 ejecuciones y Nuevos.parquet en `tmp`; el clasificador simulado pasa INGENIERIAS → CIENCIAS BASICAS."""
        base = Path(tmp)
        filas = [programa(1, DIVISION_UNINORTE="INGENIERIAS", PUNTAJE_TOTAL="1", NUCLEO_BASICO_DEL_CONOCIMIENTO="N",
                          CINE_F_2013_AC_CAMPO_DETALLADO="C"),
                 programa(2, DIVISION_UNINORTE="ESCUELA DE NEGOCIOS", PUNTAJE_TOTAL="1", NUCLEO_BASICO_DEL_CONOCIMIENTO="N",
                          CINE_F_2013_AC_CAMPO_DETALLADO="C")]
        parches = [mock.patch.object(historico, "HISTORICO_DIR", base / "Historico"),
                   mock.patch.object(reclasificar, "NUEVOS_FILE", base / "Nuevos.parquet"),
                   mock.patch.object(reclasificar, "MODIFICADOS_FILE", base / "Modificados.parquet"),
                   mock.patch.object(reclasificar, "INACTIVOS_FILE", base / "Inactivos.parquet")]
        for p in parches:
            p.start()
        for fecha in (dt.date(2026, 11, 3), dt.date(2026, 11, 10)):
            historico.guardar_ejecucion(pd.DataFrame(filas), fecha)
        run_snies._escribir_parquet(pd.DataFrame(filas[:1]).assign(**{F: pd.Timestamp("2026-11-10"), "TIPO_NOVEDAD": "NUEVO"}),
                                    base / "Nuevos.parquet")

        def clasificar(catalogo, registros):
            # Clasificador simulado: el programa 1 (antes INGENIERIAS) pasa a CIENCIAS BASICAS; el 2 conserva su división
            self.catalogo = catalogo
            division = catalogo[S].map({1: "CIENCIAS BASICAS", 2: "ESCUELA DE NEGOCIOS"}).values
            return pd.DataFrame({"DIVISION_UNINORTE": division, "PUNTAJE_TOTAL": 0.9}, index=catalogo["_K"].values)
        parches.append(mock.patch.object(reclasificar, "clasificar_catalogo", clasificar))
        parches[-1].start()
        return parches

    def test_catalogo_toma_el_registro_mas_reciente_leyendo_por_partes(self):
        # Misma clave en dos años y en una novedad de la misma fecha: gana la fecha mayor y, a igual fecha, la fuente
        # posterior (la novedad); cada fuente se entrega por separado, como al leer un año a la vez
        base = dict(NUCLEO_BASICO_DEL_CONOCIMIENTO="N", CINE_F_2013_AC_CAMPO_DETALLADO="C")
        h2025 = pd.DataFrame([programa(1, MUNICIPIO_OFERTA_PROGRAMA="A", **base, **{F: pd.Timestamp("2025-12-29")})])
        h2026 = pd.DataFrame([programa(1, MUNICIPIO_OFERTA_PROGRAMA="B", **base, **{F: pd.Timestamp("2026-01-05")}),
                              programa(2, **base, **{F: pd.Timestamp("2026-01-05")})])
        nov = pd.DataFrame([programa(1, MUNICIPIO_OFERTA_PROGRAMA="C", **base, **{F: pd.Timestamp("2026-01-05")}, TIPO_NOVEDAD="NUEVO")])
        catalogo, total = reclasificar.armar_catalogo(enumerate([h2025, h2026, nov]))
        self.assertEqual(total, 4)
        self.assertEqual(len(catalogo), 2)
        self.assertEqual(catalogo.set_index(S).loc[1, "MUNICIPIO_OFERTA_PROGRAMA"], "C")
        self.assertNotIn("TIPO_NOVEDAD", catalogo.columns)
        self.assertNotIn(F, catalogo.columns)

    def test_simulacion_no_escribe_y_resume_los_cambios(self):
        with tempfile.TemporaryDirectory() as tmp:
            parches = self._escenario(tmp)
            try:
                antes = {p: p.read_bytes() for p in Path(tmp).rglob("*.parquet")}
                resumen_md = Path(tmp) / "resumen.md"
                with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(resumen_md)}):
                    resumen = reclasificar.reclasificar(simular=True)
                self.assertEqual({p: p.read_bytes() for p in Path(tmp).rglob("*.parquet")}, antes)   # nada se escribe
                r = resumen[historico.HISTORICO_DIR]
                self.assertEqual((r["registros"], r["cambian"], r["programas"]), (4, 2, 1))
                self.assertEqual(r["transiciones"], {"INGENIERIAS → CIENCIAS BASICAS": 1})
                texto = resumen_md.read_text(encoding="utf-8")
                self.assertIn("SIMULACIÓN", texto)
                self.assertIn("| INGENIERIAS → CIENCIAS BASICAS | 1 |", texto)
            finally:
                for p in parches:
                    p.stop()

    def test_reclasificacion_aplica_y_conserva_un_grupo_por_ejecucion(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp:
            parches = self._escenario(tmp)
            try:
                reclasificar.reclasificar()
                h = historico.leer()
                self.assertEqual(h["DIVISION_UNINORTE"].tolist(), ["CIENCIAS BASICAS", "ESCUELA DE NEGOCIOS"] * 2)
                with open(historico.ruta(2026), "rb") as fh:
                    self.assertEqual(pq.ParquetFile(fh).num_row_groups, 2)
                nuevos = pd.read_parquet(Path(tmp) / "Nuevos.parquet")
                self.assertEqual(nuevos["DIVISION_UNINORTE"].tolist(), ["CIENCIAS BASICAS"])
                # Segunda pasada: ya no hay cambios y ningún archivo se reescribe
                antes = {p: p.read_bytes() for p in Path(tmp).rglob("*.parquet")}
                resumen = reclasificar.reclasificar()
                self.assertFalse(any(r["cambian"] for r in resumen.values()))
                self.assertEqual({p: p.read_bytes() for p in Path(tmp).rglob("*.parquet")}, antes)
            finally:
                for p in parches:
                    p.stop()


if __name__ == "__main__":
    unittest.main()
