"""
Pruebas unitarias de la lógica crítica del monitor (sin red ni archivos del proyecto).

Ejecución:
    python -m unittest discover -s tests -v
"""

import datetime as dt
import logging
import os
import re
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

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
    base = {
        S: snies, "NOMBRE_DEL_PROGRAMA": f"PROGRAMA {snies}", "NOMBRE_INSTITUCION": "UNIVERSIDAD X",
        "CODIGO_INSTITUCION": 9999, "NUMERO_CREDITOS": 160, "MUNICIPIO_OFERTA_PROGRAMA": "BARRANQUILLA",
        "NUMERO_PERIODOS_DE_DURACION": 10, "MODALIDAD": "PRESENCIAL", "DIVISION_UNINORTE": "INGENIERIAS",
    }
    base.update(valores)
    return base


class TestCatalogos(unittest.TestCase):
    def test_grupos_universidad(self):
        self.assertEqual(catalogos.grupo_universidad(1713), catalogos.GRUPO_UNINORTE)
        for codigo in catalogos.MAYOR_INTERES:
            self.assertEqual(catalogos.grupo_universidad(codigo), catalogos.GRUPO_MAYOR_INTERES)
        self.assertEqual(catalogos.grupo_universidad("1701"), catalogos.GRUPO_MAYOR_INTERES)
        self.assertEqual(catalogos.grupo_universidad(9999), catalogos.GRUPO_OTRAS)
        self.assertEqual(catalogos.grupo_universidad(None), catalogos.GRUPO_OTRAS)

    def test_modalidades_agrupadas(self):
        for grupo, valores in catalogos.MODALIDADES_GRUPO.items():
            for valor in valores:
                self.assertEqual(catalogos.modalidad_grupo(valor), grupo, valor)
        self.assertEqual(catalogos.modalidad_grupo(None), catalogos.SIN_INFORMACION)
        self.assertEqual(catalogos.modalidad_grupo("NO EXISTE"), catalogos.SIN_INFORMACION)
        self.assertEqual(catalogos.modalidad_grupo(" presencial "), "PRESENCIAL")


class TestDeteccion(unittest.TestCase):
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

    def test_reejecucion_del_mismo_dia_no_marca_reactivados(self):
        # El histórico ya contiene la fecha de hoy (re-ejecución): no debe contar como existencia previa
        self._historico([programa(6, **{F: str(self.fecha)})])
        nuevos = run_snies.detectar_novedades(pd.DataFrame([programa(6)]), pd.DataFrame([programa(1)]), self.fecha)[0]
        self.assertEqual(nuevos["TIPO_NOVEDAD"].tolist(), ["NUEVO"])


class TestAcumulacion(unittest.TestCase):
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
    def test_tipos_estables(self):
        df = pd.DataFrame({"MIXTA": [31, "0031", None], "FECHAS": [dt.date(2026, 1, 1), dt.datetime(2026, 1, 2, 5), None]})
        out = run_snies._tipos_estables(df)
        self.assertEqual(out["MIXTA"].dropna().tolist(), ["31", "0031"])
        self.assertTrue(str(out["FECHAS"].dtype).startswith("datetime64"))
        with tempfile.TemporaryDirectory() as tmp:
            out.to_parquet(Path(tmp) / "t.parquet")  # no debe fallar


class TestRegresiones(unittest.TestCase):
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
    def _df(self, codigos):
        return pd.DataFrame([programa(i, CODIGO_INSTITUCION=c, NOMBRE_INSTITUCION=f"INST {c}") for i, c in enumerate(codigos)])

    def test_prioridad_y_completa_hasta_diez(self):
        # Regresión: con 2 registros prioritarios de 13, la tabla mostraba solo esos 2
        mostrar = send_report.seleccionar(self._df([9999] * 11 + [1711, 1711]))
        self.assertEqual(len(mostrar), 10)
        self.assertEqual(mostrar["CODIGO_INSTITUCION"].tolist()[:3], [1711, 1711, 9999])
        mostrar = send_report.seleccionar(self._df([9999] * 10 + [1701, 1713]))
        self.assertEqual(mostrar["CODIGO_INSTITUCION"].tolist()[:2], [1713, 1701])   # Uninorte primero

    def test_sin_prioritarios_muestra_hasta_diez(self):
        self.assertEqual(len(send_report.seleccionar(self._df([9999] * 15))), 10)
        self.assertEqual(len(send_report.seleccionar(self._df([9999] * 3))), 3)

    def test_maximo_diez_prioritarios(self):
        mostrar = send_report.seleccionar(self._df([1701] * 14 + [1713]))
        self.assertEqual(len(mostrar), 10)
        self.assertEqual(mostrar["CODIGO_INSTITUCION"].iloc[0], 1713)

    def test_columnas_de_las_tablas(self):
        base = ["INSTITUCIÓN", "PROGRAMA", "MODALIDAD", "MUNICIPIO", "DIVISIÓN UNINORTE"]
        titulos = {t: [c[1] for c in send_report.SECCIONES[t]["columnas"]] for t in send_report.SECCIONES}
        self.assertEqual(titulos["nuevos"], base)        # sin la columna TIPO
        self.assertEqual(titulos["inactivos"], base)
        self.assertEqual(titulos["modificados"], base + ["TIPO DE CAMBIO", "CAMBIO DETECTADO"])

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
        self.assertIn("Universidad del Norte: 1 cambio · Universidades de Mayor Interés: 0 cambios.", texto)
        self.assertNotIn("La tabla presenta", texto)   # caben todos los registros: sin remisión al Dashboard

    def test_nota_de_limite_remite_al_dashboard(self):
        # Caso real del 17/11/2026: 13 cambios, 2 de una universidad de mayor interés
        mod = self._df([9999] * 11 + [1711, 1711]).assign(TIPO_CAMBIO_DETECTADO="MODALIDAD", CAMBIO_DETECTADO="A → B")
        res = {"pregrado": {"modificados": mod, "total_activos": 13}}
        html = send_report.construir_cuerpo(res, dt.date(2026, 11, 17))
        texto = re.sub(r"<[^>]+>", "", html)
        self.assertIn("Universidad del Norte: 0 cambios · Universidades de Mayor Interés: 2 cambios.", texto)
        self.assertIn("La tabla presenta 10 de 13 registros", texto)
        self.assertIn(f'href="{send_report.DASHBOARD_URL}modificados.html#fecha=2026-11-17"', html)
        plano = send_report.construir_texto(res, dt.date(2026, 11, 17))
        self.assertIn("La tabla presenta 10 de 13 registros", plano)
        self.assertIn(f"{send_report.DASHBOARD_URL}modificados.html#fecha=2026-11-17", plano)

    def test_resumen_descriptivo_con_variacion_y_grupos(self):
        hoy = self._df([1713, 1701, 9999, 9999])
        res = {"nuevos": self._df([1713, 9999]).assign(TIPO_NOVEDAD="NUEVO"), "modificados": None, "inactivos": None,
               "total_activos": 4, "total_anterior": 3, "fecha_anterior": dt.date(2026, 11, 10),
               "activos_grupo": run_snies.activos_por_grupo(hoy),
               "activos_grupo_anterior": run_snies.activos_por_grupo(self._df([1713, 1701, 9999]))}
        html = send_report.construir_cuerpo({"pregrado": res}, dt.date(2026, 11, 17))
        self.assertIn("1 más que en la ejecución anterior (10/11/2026: 3)", html)
        self.assertIn("Novedades detectadas: 2 programas nuevos, 0 cambios en 0 programas modificados y 0 programas inactivos.", html)
        self.assertIn("1 corresponde a la Universidad del Norte y 0 a las Universidades de Mayor Interés", html)
        self.assertIn("EJECUCIÓN ANTERIOR (10/11/2026)", html)
        self.assertIn("+1 frente al 10/11/2026", html)
        self.assertNotIn("—", html)   # estilo institucional: sin raya

    def test_resumen_sin_novedades_ni_ejecucion_anterior(self):
        html = send_report.construir_cuerpo({"pregrado": {"total_activos": 5}}, dt.date(2026, 11, 17))
        self.assertIn("No se detectaron programas nuevos, modificados ni inactivos.", html)
        self.assertIn("Programas activos al 17/11/2026", html)
        self.assertNotIn("EJECUCIÓN ANTERIOR", html)

    def test_version_texto_plano(self):
        res = {"nuevos": self._df([1713, 9999]).assign(TIPO_NOVEDAD="NUEVO"), "total_activos": 4, "total_anterior": 3,
               "fecha_anterior": dt.date(2026, 11, 10), "activos_grupo": {"UNIVERSIDAD DEL NORTE": 2},
               "activos_grupo_anterior": {"UNIVERSIDAD DEL NORTE": 1}}
        with mock.patch.dict(os.environ, {"SMTP_USER": "r@example.com", "SMTP_PASS": "x", "DESTINATARIOS": "a@example.com"}), \
                mock.patch.object(send_report, "_enviar") as enviar:
            send_report.enviar_reporte({"pregrado": res}, dt.date(2026, 11, 17))
        partes = enviar.call_args.args[0].get_payload()
        self.assertEqual([p.get_content_type() for p in partes], ["text/plain", "text/html"])   # HTML preferido
        texto = partes[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("1 más que en la ejecución anterior (10/11/2026: 3)", texto)
        self.assertIn("UNIVERSIDAD DEL NORTE: 2; ejecución anterior: 1; variación: +1", texto)
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


class TestRegistroSeguro(unittest.TestCase):
    def test_oculta_rutas_personales(self):
        formato = run_snies._FormatoSeguro("%(message)s")
        msg = f"{Path.home()}{os.sep}Desktop y {tempfile.gettempdir()}{os.sep}x.log"
        texto = formato.format(logging.LogRecord("x", 20, "", 0, msg, None, None))
        self.assertNotIn(str(Path.home()), texto)
        self.assertIn("~", texto)
        self.assertIn("<TEMP>", texto)


class TestDatosDashboard(unittest.TestCase):
    def test_codificacion_columnar(self):
        df = pd.DataFrame({"A": ["X", "X", "Y", "X"], "B": [1.0, 2.5, None, 4.0], "C": pd.to_datetime(["2026-01-01", None, "2026-01-03", "2026-01-04"])})
        t = generar_dashboard.codificar(df)
        decod = lambda c: t["datos"][c] if isinstance(t["datos"][c], list) else [t["datos"][c]["d"][i] for i in t["datos"][c]["i"]]  # noqa: E731
        self.assertEqual(decod("A"), ["X", "X", "Y", "X"])
        self.assertEqual(decod("B"), [1, 2.5, None, 4])
        self.assertEqual(decod("C"), ["2026-01-01", None, "2026-01-03", "2026-01-04"])

    def test_rangos(self):
        self.assertEqual(generar_dashboard._rangos([0, 1, 2, 5, 7, 8]), [[0, 2], [5, 5], [7, 8]])


class TestReclasificacion(unittest.TestCase):
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
