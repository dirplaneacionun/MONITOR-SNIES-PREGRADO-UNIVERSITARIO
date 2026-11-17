"""
Pruebas unitarias de la lógica crítica del monitor (sin red ni archivos del proyecto).

Ejecución:
    python -m unittest discover -s tests -v
"""

import datetime as dt
import logging
import os
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
            mock.patch.object(run_snies, "HISTORICO_FILE", base / "historico.parquet"),
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
        df.to_parquet(run_snies.HISTORICO_FILE, index=False)

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

    def test_prioridad_exclusiva(self):
        mostrar, prioritarios = send_report.seleccionar(self._df([9999] * 10 + [1701, 1713]))
        self.assertTrue(prioritarios)
        self.assertEqual(mostrar["CODIGO_INSTITUCION"].tolist(), [1713, 1701])  # Uninorte primero

    def test_sin_prioritarios_muestra_hasta_diez(self):
        mostrar, prioritarios = send_report.seleccionar(self._df([9999] * 15))
        self.assertFalse(prioritarios)
        self.assertEqual(len(mostrar), 10)

    def test_maximo_diez_prioritarios(self):
        mostrar, _ = send_report.seleccionar(self._df([1701] * 14 + [1713]))
        self.assertEqual(len(mostrar), 10)
        self.assertEqual(mostrar["CODIGO_INSTITUCION"].iloc[0], 1713)

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


if __name__ == "__main__":
    unittest.main()
