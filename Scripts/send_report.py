"""
send_report.py
--------------
Correo institucional del Monitor SNIES Pregrado · Universitario (SMTP de Gmail).

- Sin emojis ni archivos adjuntos (solo el logotipo institucional, incrustado): el detalle completo está en el Dashboard.
- Dos versiones del mismo contenido (multipart/alternative): texto plano y HTML.
- Mismo lenguaje visual del Dashboard. Orden: encabezado con el acceso al Dashboard, indicadores, resumen
  descriptivo de la ejecución (variación frente a la ejecución anterior y novedades por grupo de
  universidades) y las secciones NUEVOS, MODIFICADOS e INACTIVOS, con todos los registros de la ejecución.
- Orden de los registros: primero la Universidad del Norte, luego las Universidades de Mayor Interés y
  después las demás instituciones, cada grupo por institución y programa. Bajo cada tabla, una nota indica
  las novedades de la Universidad del Norte y de las Universidades de Mayor Interés.
- Tamaño: Gmail recorta los mensajes de más de ~102 KB de HTML y oculta su final. Si el HTML completo supera
  LIMITE_HTML, cada tabla muestra solo sus primeras filas (las de mayor prioridad, por el orden anterior) hasta
  que el mensaje cabe, y una nota indica cuántos registros se muestran y enlaza al detalle en el Dashboard. Por
  debajo del límite el correo es completo. La versión en texto plano nunca se recorta.
- La conexión SMTP usa STARTTLS con verificación de certificado y nombre del servidor. Una falla transitoria al conectar
  o autenticar (corte de la conexión, tiempo de espera, resolución de nombres) se reintenta hasta INTENTOS_SMTP veces con
  espera creciente; un rechazo de credenciales o de certificado no se reintenta, ni tampoco un error durante el envío del
  mensaje, para no duplicarlo. Si el servidor rechaza solo a algunos destinatarios, el correo llega a los demás y el
  rechazo se registra como advertencia (sin la dirección).
- enviar_alerta_falla() notifica fallas técnicas solo a la cuenta remitente.

Variables de entorno:
    SMTP_USER      cuenta remitente del correo institucional
    SMTP_PASS      contraseña de aplicación de la cuenta
    DESTINATARIOS  lista separada por comas
"""

from __future__ import annotations

import contextlib
import logging
import os
import smtplib
import socket
import ssl
import sys
import textwrap
import time
from collections import Counter
from datetime import date
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalogos import (  # noqa: E402
    GRUPO_MAYOR_INTERES, GRUPO_UNINORTE, GRUPOS_UNIVERSIDAD, grupos_de_registros, separar_cambios,
)

# Hijo del logger del orquestador: los mensajes del correo quedan en el log consolidado.
log = logging.getLogger("snies_monitor.correo")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
# Reintentos de la conexión y la autenticación ante fallas transitorias: número de intentos y segundos de espera antes
# del segundo y del tercero.
INTENTOS_SMTP = 3
ESPERA_SMTP = (5, 15)
_ERRORES_TRANSITORIOS = (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionError, TimeoutError,
                         ssl.SSLError, socket.gaierror)
DASHBOARD_URL = "https://dirplaneacionun.github.io/MONITOR-SNIES-PREGRADO-UNIVERSITARIO/"
# Ancho máximo del mensaje (px). Gmail y Outlook web lo ajustan al panel de lectura; Outlook de escritorio lo respeta tal cual.
ANCHO_CORREO = 900
# Tamaño máximo del HTML del correo (bytes): Gmail recorta desde ~102 KB; el margen cubre variaciones de medición
LIMITE_HTML = 95 * 1024

# Identidad visual
# Paleta institucional Universidad del Norte
# Paleta institucional (BRANDBOOK_UNINORTE, códigos HEX impresos), idéntica a la del Dashboard (estilos.css)
NEGRO, ROJO, AMARILLO, GRIS, BORDE = "#151515", "#960A11", "#FFCC00", "#F5F5F5", "#E5E5E5"
TEXTO2, TEXTO3 = "#3E4751", "#6B6B6B"   # textos secundarios del Dashboard (--texto-2 y --texto-3)
# Neutros auxiliares del Dashboard (estilos.css): zebra de las tablas y colores sobre el encabezado negro
ZEBRA, TEXTO_SOBRE_NEGRO, LINEA_SOBRE_NEGRO, FONDO_SOBRE_NEGRO = "#FAFAFA", "#D9D9D9", "#444444", "#232323"
ESTADO = {"nuevos": "#1E8E5A", "modificados": "#FFCC00", "inactivos": "#960A11"}  # el correo no tiene líneas: solo rellenos
FUENTE = "Montserrat, 'Segoe UI', Arial, sans-serif"

# Logotipo institucional (versión blanca para el encabezado negro). Va incrustado en el mensaje (Content-ID) y no
# enlazado: se ve sin descargar imágenes remotas y no requiere que el Dashboard esté publicado.
LOGO = Path(__file__).resolve().parent.parent / "docs" / "assets" / "img" / "logo-uninorte-direccion-blanco.png"
LOGO_CID = "logo-uninorte"

# Columnas de las tablas: las comunes (institución, programa, departamento y modalidad) y la división de Uninorte
# al final; nuevos y modificados agregan entre ambas el tipo de novedad o los cambios detectados del programa.
_INICIO = [("NOMBRE_INSTITUCION", "INSTITUCIÓN"), ("NOMBRE_DEL_PROGRAMA", "PROGRAMA"),
           ("DEPARTAMENTO_OFERTA_PROGRAMA", "DEPARTAMENTO"), ("MODALIDAD", "MODALIDAD")]
_FINAL = [("DIVISION_UNINORTE", "DIVISIÓN DE UNINORTE")]

# Ancho sugerido de algunas columnas de las tablas (las demás se reparten el espacio restante): el programa y los cambios
# detectados, que van uno por línea, llevan los textos más largos.
ANCHO_COLUMNA = {"NOMBRE_DEL_PROGRAMA": "22%", "CAMBIO_DETECTADO": "27%"}

SECCIONES = {
    "nuevos": {
        "titulo": "PROGRAMAS ACADÉMICOS NUEVOS",
        "descripcion": "Programas que aparecen en el SNIES y no estaban en la ejecución anterior (incluye los reactivados).",
        "vacio": "No se detectaron programas nuevos en esta ejecución.",
        "unidad": ("registro", "registros"),
        "pagina": "nuevos.html",
        "columnas": _INICIO + [("TIPO_NOVEDAD", "TIPO DE NOVEDAD")] + _FINAL,
    },
    "modificados": {
        "titulo": "PROGRAMAS ACADÉMICOS MODIFICADOS",
        "descripcion": "Cambios en créditos, departamento de oferta, nombre, número de periodos, modalidad o costo de matrícula. Cada registro reúne todos los cambios de un programa en la ejecución.",
        "vacio": "No se detectaron cambios en los programas activos en esta ejecución.",
        "unidad": ("registro", "registros"),
        "pagina": "modificados.html",
        "columnas": _INICIO + [("CAMBIO_DETECTADO", "CAMBIO DETECTADO")] + _FINAL,
    },
    "inactivos": {
        "titulo": "PROGRAMAS ACADÉMICOS INACTIVOS",
        "descripcion": "Programas que estaban activos en la ejecución anterior y ya no figuran en el SNIES.",
        "vacio": "No se detectaron programas inactivos en esta ejecución.",
        "unidad": ("registro", "registros"),
        "pagina": "inactivos.html",
        "columnas": _INICIO + _FINAL,
    },
}


# ── Selección con prioridad ───────────────────────────────────────────────────

def seleccionar(df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Registros de la tabla de una sección, todos ellos: primero la Universidad del Norte, luego las Universidades
    de Mayor Interés y después las demás instituciones, cada grupo en orden alfabético por institución y programa.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    orden = ["_P", "NOMBRE_INSTITUCION", "NOMBRE_DEL_PROGRAMA"]
    return df.assign(_P=_prioridad(df).values).sort_values(orden, kind="stable").drop(columns="_P")


def _prioridad(df: pd.DataFrame) -> pd.Series:
    """Prioridad de cada registro: 0 la Universidad del Norte, 1 las Universidades de Mayor Interés y 2 las demás."""
    return grupos_de_registros(df).map({GRUPO_UNINORTE: 0, GRUPO_MAYOR_INTERES: 1}).fillna(2)


# ── HTML ──────────────────────────────────────────────────────────────────────
# Mismo lenguaje visual del Dashboard: encabezado negro con línea roja, fondo gris claro, tarjetas blancas con
# borde gris de separación, títulos en mayúsculas y tablas con encabezado negro. Los estilos van en línea
# porque la mayoría de los clientes de correo ignoran las hojas de estilo. Colores, tipo de letra, pesos, interletrado,
# radios, bordes y márgenes son los del Dashboard (docs/assets/estilos.css); solo los tamaños de letra de los
# indicadores y del encabezado se reducen un escalón (p. ej. valor del indicador 26 px frente a 32 px) para el ancho de ANCHO_CORREO px.

def _num(v) -> str:
    """Número con separador de miles institucional (punto)."""
    return f"{v:,}".replace(",", ".")


def _fecha(d) -> str:
    """Fecha en formato DD/MM/AAAA."""
    return d.strftime("%d/%m/%Y")




def _cambios_de(df) -> int:
    """Número de cambios detectados en los registros de Modificados `df` (cada registro reúne los de un programa)."""
    return 0 if df is None or df.empty else sum(len(separar_cambios(c)) for c in df["CAMBIO_DETECTADO"])


def _solo_aumento_de_costo(texto) -> bool:
    """¿El único cambio del registro es un aumento del costo de matrícula? (montos anterior y nuevo numéricos, el nuevo mayor)."""
    cambios = separar_cambios(texto)
    if len(cambios) != 1 or cambios[0][0] != "COSTO DE MATRICULA":
        return False
    try:
        return float(cambios[0][2]) > float(cambios[0][1])
    except ValueError:
        return False


def _para_la_tabla(tipo: str, df: pd.DataFrame | None) -> pd.DataFrame | None:
    """
    Registros que muestra la tabla del correo. En Modificados no se muestran los programas cuyo único cambio es un aumento del
    costo de matrícula; siguen contando en el título, los indicadores y los resúmenes, y se conservan en Modificados.parquet,
    en el Dashboard y en los archivos generados. Una reducción del costo, o el costo junto con otro cambio, se muestra.
    """
    if tipo != "modificados" or df is None or df.empty:
        return df
    return df[~df["CAMBIO_DETECTADO"].map(_solo_aumento_de_costo)]


def _nota_costo(df: pd.DataFrame | None) -> list[str]:
    """Nota bajo la descripción de Modificados: qué registros no muestra la tabla del correo y dónde se conservan."""
    ocultos = 0 if df is None else len(df) - len(_para_la_tabla("modificados", df))
    cuenta = f" ({_plural(ocultos, 'registro', 'registros')} en esta ejecución)" if ocultos else ""
    return [f"Nota: la tabla del correo excluye los programas cuyo único cambio corresponde a un aumento en el costo de matrícula{cuenta}.",
            "El Dashboard mantiene disponibles todos los registros.",
            "Los archivos generados también conservan dichos registros."]


def _plural(n: int, singular: str, plural: str) -> str:
    """Cantidad con su unidad en singular o plural: 1 → "1 programa", 3 → "3 programas"."""
    return f"{_num(n)} {singular if n == 1 else plural}"


def _texto(col: str, v) -> str:
    """
    Texto de una celda de las tablas: vacío para nulos y enteros sin decimales. Los cambios detectados de un programa
    van uno por línea ("TIPO: anterior → nuevo").
    """
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if col == "CAMBIO_DETECTADO" and (cambios := separar_cambios(v)):
        return "\n".join(f"{tipo}: {anterior} → {nuevo}" for tipo, anterior, nuevo in cambios)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _tarjeta(contenido: str, color: str | None = None) -> str:
    """Tarjeta blanca del Dashboard; con `color`, borde izquierdo de estado (como las tarjetas de indicadores)."""
    borde = f"border-left:5px solid {color};" if color else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;table-layout:fixed;">'
            f'<tr><td style="background:#FFFFFF;border:1px solid {BORDE};{borde}border-radius:10px;padding:16px 18px;">'
            f"{contenido}</td></tr></table>")


def _titulo(texto: str, margen: int = 0) -> str:
    """Título de sección en mayúsculas, con `margen` píxeles de separación superior."""
    return (f'<div style="margin-top:{margen}px;font-family:{FUENTE};font-size:13px;font-weight:800;'
            f'letter-spacing:.05em;color:{NEGRO};">{texto}</div>')


def _subtitulo(texto: str) -> str:
    """Párrafo descriptivo bajo un título."""
    return f'<p style="margin:4px 0 10px;font-family:{FUENTE};font-size:12px;line-height:1.5;color:{TEXTO2};">{texto}</p>'


def _tabla(df: pd.DataFrame, columnas, numericas=(), negrita_ultima=False) -> str:
    """
    Tabla HTML con encabezado negro; `columnas` = [(campo, encabezado)] y `numericas` las columnas alineadas a la
    derecha. Con `negrita_ultima`, la última fila (total) se muestra en negrita.
    """
    # Tablas de novedades (5 o más columnas): letra y márgenes algo menores para que quepan en el ancho del correo;
    # las tres usan el mismo tamaño aunque difieran en número de columnas
    compacta = len(columnas) >= 5
    letra_th, margen = ("10px", "6px") if compacta else ("11px", "10px")
    th = (f"background:{NEGRO};color:#FFFFFF;font-family:{FUENTE};font-size:{letra_th};font-weight:700;"
          f"letter-spacing:.04em;padding:9px {margen};")
    alinear = lambda c: "right" if c in numericas else "left"  # noqa: E731
    cab = "".join(f'<th style="{th}text-align:{alinear(c)};'
                  f'{f"width:{ANCHO_COLUMNA[c]};" if compacta and c in ANCHO_COLUMNA else ""}">{escape(t)}</th>' for c, t in columnas)
    # Los estilos van en línea, no en el bloque <style>: Outlook no aplica sus reglas a las celdas, que se mostraban
    # con la letra por defecto, sin márgenes ni líneas y con las cifras sin alinear. Tipo de letra, tamaño y color van
    # en la fila (las celdas los heredan), y márgenes y borde en cada celda, con lo que el mensaje pesa lo mínimo para
    # el límite de Gmail (LIMITE_HTML). Fondo, alineación vertical y alineación de las cifras van como atributos
    # (bgcolor, valign, align), que todos los clientes respetan.
    letra, relleno = ("11px", "6px") if compacta else ("12px", "10px")
    estilo_fila = f"font-family:{FUENTE};font-size:{letra};color:{NEGRO};"
    estilo_celda = f"padding:7px {relleno};border-bottom:1px solid {BORDE};"
    filas = []
    for i, (_, r) in enumerate(df.iterrows()):
        fondo = "#FFFFFF" if i % 2 == 0 else ZEBRA
        negrita = "font-weight:800;" if negrita_ultima and i == len(df) - 1 else ""
        celdas = "".join(
            (f'<td align="right" style="{estilo_celda}white-space:nowrap;">' if c in numericas else f'<td style="{estilo_celda}">')
            + f"{escape(_texto(c, r.get(c))).replace(chr(10), '<br>')}</td>" for c, _ in columnas)
        filas.append(f'<tr valign="top" bgcolor="{fondo}" style="{estilo_fila}{negrita}">{celdas}</tr>')
    # En pantallas angostas la tabla se desplaza dentro de su tarjeta, sin ensanchar el resto del correo
    return (f'<div style="overflow-x:auto;"><table class="tabla" role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="border-collapse:collapse;border:1px solid {BORDE};margin:4px 0;">'
            f"<thead><tr>{cab}</tr></thead><tbody>{''.join(filas)}</tbody></table></div>")


def _variacion(hoy: int, antes: int | None) -> str:
    """Variación frente a la ejecución anterior con signo (+3, −2), "Sin variación" si es igual y "–" sin ejecución anterior."""
    if antes is None:
        return "–"
    d = hoy - antes
    return "Sin variación" if d == 0 else f"{'+' if d > 0 else '−'}{_num(abs(d))}"


def _frases_resumen(res: dict, fecha: date, negrita=lambda t: t) -> list[str]:
    """Frases del resumen de la ejecución (activos, variación y novedades), comunes al HTML y al texto plano."""
    activos, antes, f_ant = res.get("total_activos"), res.get("total_anterior"), res.get("fecha_anterior")
    n = {t: 0 if res.get(t) is None else len(res[t]) for t in ("nuevos", "modificados", "inactivos")}
    cambios_mod = _cambios_de(res.get("modificados"))

    frases = [f"El {_fecha(fecha)} se descargó del SNIES el reporte de programas académicos universitarios de "
              "pregrado con estado activo en Colombia."]
    if activos is not None:
        frase = f"Se identificaron {negrita(_plural(activos, 'registro activo', 'registros activos'))}"
        if antes is not None and f_ant is not None:
            d = activos - antes
            frase += (f", la misma cifra de la ejecución anterior ({_fecha(f_ant)})" if d == 0 else
                      f", {_num(abs(d))} {'más' if d > 0 else 'menos'} que en la ejecución anterior "
                      f"({_fecha(f_ant)}: {_num(antes)})")
        frases.append(frase + ".")
    if not any(n.values()):
        frases.append("No se detectaron programas nuevos, modificados ni inactivos.")
    else:
        frases.append(
            f"Novedades detectadas: {_plural(n['nuevos'], 'registro nuevo', 'registros nuevos')}, "
            f"{_plural(n['modificados'], 'registro modificado', 'registros modificados')}"
            f"{f' (con {_plural(cambios_mod, 'cambio', 'cambios')})' if n['modificados'] else ''} y "
            f"{_plural(n['inactivos'], 'registro inactivo', 'registros inactivos')}.")
        grupos = pd.concat([grupos_de_registros(res[t]) for t in n if n[t]])
        uni, mi = int((grupos == GRUPO_UNINORTE).sum()), int((grupos == GRUPO_MAYOR_INTERES).sum())
        frases.append(f"De estas novedades, {_num(uni)} corresponde{'' if uni == 1 else 'n'} a la Universidad del Norte "
                      f"y {_num(mi)} a las Universidades de Mayor Interés.")
    return frases


def _grupos(res: dict):
    """
    Novedades de la ejecución por grupo de universidades (nuevos, modificados e inactivos) y su total:
    (columnas, filas). Los modificados cuentan registros (un programa con todos sus cambios), igual que el indicador MODIFICADOS.
    """
    cols = [("GRUPO", "GRUPO DE UNIVERSIDADES"), ("NUEVOS", "NUEVOS"), ("MODIFICADOS", "MODIFICADOS"), ("INACTIVOS", "INACTIVOS")]
    conteos = {}
    for tipo in ("nuevos", "modificados", "inactivos"):
        df = res.get(tipo)
        grupos = pd.Series(dtype=object) if df is None or df.empty else grupos_de_registros(df)
        conteos[tipo.upper()] = {g: int((grupos == g).sum()) for g in GRUPOS_UNIVERSIDAD}
    filas = [{"GRUPO": g, **{t: _num(c[g]) for t, c in conteos.items()}} for g in GRUPOS_UNIVERSIDAD]
    filas.append({"GRUPO": "TOTAL", **{t: _num(sum(c.values())) for t, c in conteos.items()}})
    return cols, pd.DataFrame(filas)


def _resumen(res: dict, fecha: date) -> str:
    """Resumen descriptivo de la ejecución: activos, variación, novedades y novedades por grupo de universidades."""
    columnas, filas = _grupos(res)
    contenido = _titulo("RESUMEN DE LA EJECUCIÓN") + _subtitulo(" ".join(_frases_resumen(res, fecha, lambda t: f"<b>{t}</b>")))
    contenido += (_titulo("NOVEDADES POR GRUPO DE UNIVERSIDADES", 14)
                  + _subtitulo("Registros nuevos, modificados e inactivos de esta ejecución.")
                  + _tabla(filas, columnas, numericas=("NUEVOS", "MODIFICADOS", "INACTIVOS"), negrita_ultima=True))
    return _tarjeta(contenido)


def _descripcion(tipo: str, df: pd.DataFrame | None) -> str:
    """Descripción de una sección; en Modificados agrega el número de registros de la ejecución."""
    descripcion = SECCIONES[tipo]["descripcion"]
    if tipo == "modificados" and df is not None and len(df):
        descripcion += f" En esta ejecución: {_plural(len(df), 'registro', 'registros')} con {_plural(_cambios_de(df), 'cambio', 'cambios')}."
    return descripcion


def _texto_notas(tipo: str, df: pd.DataFrame, negrita=lambda t: t) -> list[str]:
    """Nota bajo la tabla (común al HTML y al texto plano): novedades de la Universidad del Norte y de las Universidades de Mayor Interés."""
    singular, plural = SECCIONES[tipo]["unidad"]
    grupos = grupos_de_registros(df)
    nombres = {GRUPO_UNINORTE: "Universidad del Norte", GRUPO_MAYOR_INTERES: "Universidades de Mayor Interés"}
    return [" · ".join(f"{negrita(nombres[g] + ':')} {_plural(int((grupos == g).sum()), singular, plural)}" for g in nombres) + "."]


def _aviso_recorte(tipo: str, mostrados: int, total: int, fecha: date) -> str:
    """Nota bajo una tabla recortada por tamaño: registros mostrados y enlace al detalle de esa ejecución en el Dashboard."""
    enlace = f'{DASHBOARD_URL}{SECCIONES[tipo]["pagina"]}#fecha={fecha.isoformat()}'
    verbo = "Se muestra" if mostrados == 1 else "Se muestran"
    return (f'<p style="margin:8px 0 0;font-family:{FUENTE};font-size:12px;line-height:1.5;color:{TEXTO2};'
            f'background:{GRIS};padding:10px 12px;border-radius:6px;border-left:3px solid {AMARILLO};">'
            f'<b style="color:{NEGRO};">{verbo} {_num(mostrados)} de {_num(total)} registros;</b> el detalle completo está en el '
            f'<a href="{enlace}" style="color:{ROJO};font-weight:700;">Dashboard</a>.</p>')


def _seccion(tipo: str, df: pd.DataFrame | None, fecha: date, maximo: int | None = None) -> str:
    """
    Tarjeta HTML de una sección (nuevos, modificados o inactivos): descripción, tabla y notas. La tabla muestra todos
    los registros o, con `maximo`, solo los primeros (los de mayor prioridad) y una nota que enlaza al detalle.
    """
    cfg, color = SECCIONES[tipo], ESTADO[tipo]
    total = 0 if df is None else len(df)
    en_tabla_df = _para_la_tabla(tipo, df)
    en_tabla = 0 if en_tabla_df is None else len(en_tabla_df)
    contenido = _titulo(f"{cfg['titulo']} · {_num(total)}") + _subtitulo(_descripcion(tipo, df))
    if tipo == "modificados" and total:
        contenido += (f'<p style="margin:0 0 12px;font-family:{FUENTE};font-size:12px;line-height:1.5;color:{TEXTO2};'
                      f'background:{GRIS};padding:10px 12px;border-radius:6px;border-left:3px solid {AMARILLO};">'
                      f'{"<br>".join(escape(x) for x in _nota_costo(df))}</p>')
    if not total:
        contenido += (f'<p style="margin:0;font-family:{FUENTE};font-size:12px;color:{TEXTO2};background:{GRIS};'
                      f'padding:10px 12px;border-radius:6px;">{cfg["vacio"]}</p>')
        return _tarjeta(contenido, color)
    frases = _texto_notas(tipo, df, negrita=lambda t: f'<b style="color:{NEGRO};">{escape(t)}</b>')
    notas = (f'<p style="margin:8px 0 0;font-family:{FUENTE};font-size:12px;line-height:1.5;color:{TEXTO2};'
             f'background:{GRIS};padding:10px 12px;border-radius:6px;">{"<br>".join(frases)}</p>')
    mostrados = seleccionar(en_tabla_df) if maximo is None else seleccionar(en_tabla_df).head(maximo)
    tabla = _tabla(mostrados, cfg["columnas"]) if len(mostrados) else ""
    if not en_tabla:
        tabla = (f'<p style="margin:0;font-family:{FUENTE};font-size:12px;color:{TEXTO2};background:{GRIS};'
                 f'padding:10px 12px;border-radius:6px;">Todos los registros de esta ejecución son aumentos en el costo de matrícula: '
                 f'no se muestran en la tabla.</p>')
    aviso = _aviso_recorte(tipo, len(mostrados), en_tabla, fecha) if len(mostrados) < en_tabla else ""
    return _tarjeta(contenido + tabla + aviso + notas, color)


def _kpi(etiqueta: str, valor: str, detalle: str, color: str) -> str:
    """Celda de indicador del encabezado: etiqueta, valor destacado y detalle, con borde izquierdo de `color`."""
    return (f'<td class="kpi" width="25%" valign="top" style="padding:0 4px;"><div style="background:#FFFFFF;border:1px solid {BORDE};'
            f'border-left:5px solid {color};border-radius:10px;padding:12px 14px;">'
            f'<div style="font-family:{FUENTE};font-size:10px;font-weight:700;letter-spacing:.06em;color:{TEXTO2};">{etiqueta}</div>'
            f'<div style="font-family:{FUENTE};font-size:26px;font-weight:800;color:{NEGRO};margin-top:2px;">{valor}</div>'
            f'<div style="font-family:{FUENTE};font-size:11px;color:{TEXTO3};">{detalle}</div></div></td>')


def _indicadores(res: dict, fecha: date) -> list[tuple[str, str, str, str]]:
    """Indicadores de la ejecución (etiqueta, valor, detalle, color), comunes al HTML y al texto plano."""
    nue, mod, ina = (res.get(k) for k in ("nuevos", "modificados", "inactivos"))
    n = lambda d: 0 if d is None else len(d)  # noqa: E731
    activos, antes, f_ant = res.get("total_activos"), res.get("total_anterior"), res.get("fecha_anterior")
    detalle_activos = (f"{_variacion(activos, antes)} frente al {_fecha(f_ant)}"
                       if activos is not None and antes is not None and f_ant is not None
                       else f"Programas activos al {_fecha(fecha)}")
    return [("PROGRAMAS ACTIVOS", _num(activos) if activos is not None else "–", detalle_activos, NEGRO),
            ("NUEVOS", _num(n(nue)), f"Ejecución del {_fecha(fecha)}", ESTADO["nuevos"]),
            ("MODIFICADOS", _num(n(mod)), f"Con {_plural(_cambios_de(mod), 'cambio', 'cambios')}", ESTADO["modificados"]),
            ("INACTIVOS", _num(n(ina)), f"Ejecución del {_fecha(fecha)}", ESTADO["inactivos"])]


def construir_texto(resultados: dict, fecha: date) -> str:
    """
    Versión en texto plano del reporte, con el mismo contenido del HTML. Se incluye como alternativa en el
    mensaje: la muestran los clientes configurados para texto, los lectores de pantalla y las vistas previas,
    y su presencia reduce la probabilidad de que el correo se clasifique como no deseado.
    """
    res = resultados.get("pregrado") or {}
    # Sin cortar palabras largas ni en guiones: las direcciones web deben quedar completas y utilizables
    ajustar = lambda t, ini="", sig="": textwrap.fill(t, 78, initial_indent=ini, subsequent_indent=sig,  # noqa: E731
                                                      break_long_words=False, break_on_hyphens=False)
    lineas = ["MONITOR SNIES PREGRADO · UNIVERSITARIO",
              "Programas Académicos Universitarios de Pregrado en Colombia",
              f"Reporte de la ejecución del {_fecha(fecha)}",
              f"Dashboard completo: {DASHBOARD_URL}", "",
              "INDICADORES"]
    lineas += [f"  {e.capitalize()}: {v} ({d})" for e, v, d, _ in _indicadores(res, fecha)]
    lineas += ["", "RESUMEN DE LA EJECUCIÓN", ajustar(" ".join(_frases_resumen(res, fecha)))]
    _, filas = _grupos(res)
    lineas += ["", "NOVEDADES POR GRUPO DE UNIVERSIDADES"]
    for _, r in filas.iterrows():
        lineas.append(ajustar(f"{r['GRUPO']}: nuevos {r['NUEVOS']}; modificados {r['MODIFICADOS']}; inactivos {r['INACTIVOS']}", "  ", "    "))
    for tipo in ("nuevos", "modificados", "inactivos"):
        cfg, df = SECCIONES[tipo], res.get(tipo)
        total = 0 if df is None else len(df)
        lineas += ["", f"{cfg['titulo']} · {_num(total)}", ajustar(_descripcion(tipo, df))]
        if not total:
            lineas.append(cfg["vacio"])
            continue
        if tipo == "modificados":
            lineas += [ajustar(x) for x in _nota_costo(df)]
        en_tabla = _para_la_tabla(tipo, df)
        if not len(en_tabla):
            lineas.append("Todos los registros de esta ejecución son aumentos en el costo de matrícula: no se muestran en la tabla.")
        for i, (_, r) in enumerate(seleccionar(en_tabla).iterrows(), 1):
            campos = " | ".join(f"{t}: {_texto(c, r.get(c)).replace(chr(10), '; ')}" for c, t in cfg["columnas"] if _texto(c, r.get(c)))
            lineas.append(ajustar(f"{i}. {campos}", "  ", "     "))
        lineas += [ajustar(t) for t in _texto_notas(tipo, df)]
    lineas += ["",
               ajustar("Fuente: Sistema Nacional de Información de la Educación Superior (SNIES), Ministerio de "
                       "Educación Nacional · Dirección de Planeación y Estudios Institucionales, Universidad del Norte."),
               "Reporte generado automáticamente en cada ejecución del monitor."]
    return "\n".join(lineas) + "\n"


def _topes(res: dict, filas: int) -> dict[str, int]:
    """
    Reparte `filas` registros entre las tablas, en orden de prioridad: primero los de la Universidad del Norte, luego los
    de las Universidades de Mayor Interés y después los demás; dentro de cada prioridad, por turnos entre las secciones.
    Retorna cuántos registros muestra cada sección (siempre sus primeros, según el orden de `seleccionar`).
    """
    candidatos = []
    for orden, tipo in enumerate(SECCIONES):
        df = _para_la_tabla(tipo, res.get(tipo))
        if df is None or df.empty:
            continue
        contados: dict[int, int] = {}
        for prioridad in _prioridad(seleccionar(df)):
            posicion = contados.get(prioridad, 0)
            contados[prioridad] = posicion + 1
            candidatos.append((prioridad, posicion, orden, tipo))
    elegidos = Counter(tipo for *_, tipo in sorted(candidatos)[:filas])
    return {tipo: elegidos.get(tipo, 0) for tipo in SECCIONES}


def construir_cuerpo(resultados: dict, fecha: date, limite: int = LIMITE_HTML) -> str:
    """
    Versión HTML del reporte (estilos en línea para los clientes de correo). Si el HTML completo supera `limite` bytes,
    recorta las tablas al mayor número de registros con el que el mensaje cabe (ver _topes).
    """
    res = resultados.get("pregrado") or {}
    completo = _cuerpo(res, fecha)
    if len(completo.encode("utf-8")) <= limite:
        return completo
    total = sum(len(_para_la_tabla(t, res[t])) for t in SECCIONES if res.get(t) is not None)
    cabe = lambda filas: len(_cuerpo(res, fecha, _topes(res, filas)).encode("utf-8")) <= limite  # noqa: E731
    bajo, alto = 0, total   # `bajo` cabe (o es el mínimo posible) y `alto` no cabe: búsqueda binaria del máximo que cabe
    while alto - bajo > 1:
        medio = (bajo + alto) // 2
        bajo, alto = (medio, alto) if cabe(medio) else (bajo, medio)
    log.warning(f"El HTML del correo superaba {limite // 1024} KB (Gmail recorta desde ~102 KB): se muestran "
                f"{bajo:,} de {total:,} registros y el detalle completo queda en el Dashboard.")
    return _cuerpo(res, fecha, _topes(res, bajo))


def _cuerpo(res: dict, fecha: date, topes: dict[str, int] | None = None) -> str:
    """HTML del reporte; `topes` limita los registros que muestra cada sección (por defecto, todos)."""
    topes = topes or {}
    nue, mod, ina = (res.get(k) for k in ("nuevos", "modificados", "inactivos"))
    kpis = "".join(_kpi(*k) for k in _indicadores(res, fecha))
    logo = (f'<img src="cid:{LOGO_CID}" width="240" height="41" alt="Universidad del Norte" style="display:block;border:0;'
            f'height:auto;margin:0 0 14px;font-family:{FUENTE};font-size:13px;font-weight:700;color:#FFFFFF;">') if LOGO.exists() else ""
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  /* Pantallas angostas (clientes que admiten estilos incrustados): indicadores en dos columnas, fecha bajo el título
     y tablas más compactas. Los demás clientes muestran el diseño de escritorio. */
  @media (max-width: 600px) {{
    .kpi {{ display: inline-block !important; width: 50% !important; box-sizing: border-box; padding: 0 4px 8px !important; }}
    .enc-titulo, .enc-fecha {{ display: block !important; width: auto !important; }}
    .enc-fecha {{ text-align: left !important; padding-top: 14px !important; }}
    .enc-fecha > div {{ text-align: left !important; display: inline-block !important; }}
    .tabla th, .tabla td {{ padding: 6px !important; font-size: 10.5px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{GRIS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{GRIS};"><tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="{ANCHO_CORREO}" cellpadding="0" cellspacing="0" style="max-width:{ANCHO_CORREO}px;width:100%;table-layout:fixed;">
  <tr><td style="background:{NEGRO};border-bottom:4px solid {ROJO};border-radius:10px 10px 0 0;padding:20px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td class="enc-titulo" valign="top">
        {logo}<div style="font-family:{FUENTE};font-size:20px;font-weight:800;letter-spacing:.04em;color:#FFFFFF;">MONITOR SNIES PREGRADO · UNIVERSITARIO</div>
        <div style="font-family:{FUENTE};font-size:13px;color:{TEXTO_SOBRE_NEGRO};margin-top:4px;">Programas Académicos Universitarios de Pregrado en Colombia</div>
        <a href="{DASHBOARD_URL}" style="display:inline-block;margin-top:14px;background:{ROJO};color:#FFFFFF;font-family:{FUENTE};font-size:12px;font-weight:700;letter-spacing:.06em;text-decoration:none;padding:10px 22px;border-radius:8px;">VER DASHBOARD COMPLETO</a>
      </td>
      <td class="enc-fecha" valign="top" align="right" width="150">
        <div style="background:{FONDO_SOBRE_NEGRO};border:1px solid {LINEA_SOBRE_NEGRO};border-top:3px solid {AMARILLO};border-radius:10px;padding:8px 14px;text-align:right;">
          <div style="font-family:{FUENTE};font-size:10px;font-weight:700;letter-spacing:.08em;color:{TEXTO_SOBRE_NEGRO};">EJECUCIÓN</div>
          <div style="font-family:{FUENTE};font-size:18px;font-weight:800;color:#FFFFFF;">{_fecha(fecha)}</div>
        </div>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:16px 0 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;"><tr>{kpis}</tr></table>
    {_resumen(res, fecha)}
    {_seccion("nuevos", nue, fecha, topes.get("nuevos"))}
    {_seccion("modificados", mod, fecha, topes.get("modificados"))}
    {_seccion("inactivos", ina, fecha, topes.get("inactivos"))}
  </td></tr>
  <tr><td align="center" style="padding:8px 12px 4px;font-family:{FUENTE};font-size:11px;line-height:1.5;color:{TEXTO3};">
    Fuente: Sistema Nacional de Información de la Educación Superior (SNIES), Ministerio de Educación Nacional · Dirección de Planeación y Estudios Institucionales, Universidad del Norte.<br>
    Reporte generado automáticamente en cada ejecución del monitor.
  </td></tr>
</table></td></tr></table>
</body></html>"""


# ── Envío ─────────────────────────────────────────────────────────────────────

def _conectar(remitente: str, clave: str) -> smtplib.SMTP:
    """
    Abre la conexión SMTP con STARTTLS y la autentica; retorna el servidor listo para enviar (quien lo recibe lo cierra).
    Una falla transitoria (corte de la conexión, tiempo de espera, resolución de nombres) se reintenta hasta INTENTOS_SMTP
    veces, con la espera de ESPERA_SMTP entre intentos, y deja una advertencia sin datos sensibles. No se reintentan el
    rechazo de las credenciales ni los errores de certificado: repetirlos no los resuelve.
    """
    for intento in range(1, INTENTOS_SMTP + 1):
        servidor = None
        try:
            servidor = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60)
            servidor.ehlo()
            # Contexto TLS que verifica el certificado y el nombre del servidor (el de smtplib por defecto no lo hace)
            servidor.starttls(context=ssl.create_default_context())
            servidor.login(remitente, clave)
            return servidor
        except Exception as exc:
            if servidor is not None:
                # El cierre de una conexión ya rota no debe ocultar el error original
                with contextlib.suppress(Exception):
                    servidor.close()
            if (not isinstance(exc, _ERRORES_TRANSITORIOS) or isinstance(exc, ssl.SSLCertVerificationError)
                    or intento == INTENTOS_SMTP):
                raise
            espera = ESPERA_SMTP[min(intento, len(ESPERA_SMTP)) - 1]
            log.warning(f"Falla transitoria de la conexión SMTP ({type(exc).__name__}: {exc}); intento {intento} de "
                        f"{INTENTOS_SMTP}. Se reintenta en {espera} s.")
            time.sleep(espera)


def _enviar(msg: MIMEMultipart, remitente: str, clave: str, destinatarios: list[str]) -> dict:
    """
    Envía `msg` por SMTP con STARTTLS y autenticación de la cuenta remitente (la conexión se reintenta ante fallas
    transitorias; ver _conectar). Retorna los destinatarios que el servidor rechazó ({dirección: (código, respuesta)};
    vacío si aceptó a todos): smtplib solo lanza una excepción cuando rechaza a todos, de modo que un rechazo parcial debe
    revisarse en el valor de retorno. El envío del mensaje no se reintenta: si la conexión se corta durante él, el servidor
    pudo haberlo aceptado y repetirlo lo duplicaría.
    """
    with _conectar(remitente, clave) as servidor:
        return servidor.sendmail(remitente, destinatarios, msg.as_bytes())


def _registrar_rechazados(destinatarios: list[str], rechazados) -> int:
    """
    Registra una advertencia cuando el servidor SMTP rechazó a algún destinatario y retorna cuántos fueron. Se informa
    su posición en la lista de destinatarios y el código de respuesta, no la dirección: el log del flujo es público.
    """
    if not isinstance(rechazados, dict) or not rechazados:
        return 0
    posiciones = [str(i) for i, d in enumerate(destinatarios, 1) if d in rechazados]
    codigos = sorted({str(r[0]) for r in rechazados.values() if r})
    log.warning(f"El servidor SMTP rechazó a {len(rechazados)} de {len(destinatarios)} destinatario(s) "
                f"(posición en DESTINATARIOS: {', '.join(posiciones) or 'desconocida'}; código SMTP: {', '.join(codigos) or 'desconocido'}). "
                "El correo llegó a los demás: revise esas direcciones en el secreto DESTINATARIOS.")
    return len(rechazados)


def enviar_reporte(resultados: dict, fecha: date) -> None:
    """Construye y envía el reporte semanal a DESTINATARIOS (sin adjuntos)."""
    remitente = os.environ["SMTP_USER"]
    clave = os.environ["SMTP_PASS"]
    destinatarios = [d.strip() for d in os.environ["DESTINATARIOS"].split(",") if d.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"MONITOR SNIES PREGRADO · UNIVERSITARIO · Reporte del {fecha.strftime('%d/%m/%Y')}"
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)
    # Alternativas en orden de preferencia creciente: texto plano y luego HTML (el cliente muestra la última que admite)
    msg.attach(MIMEText(construir_texto(resultados, fecha), "plain", "utf-8"))
    # El HTML y el logotipo incrustado van en un multipart/related, que es la alternativa preferida
    html = MIMEMultipart("related")
    html.attach(MIMEText(construir_cuerpo(resultados, fecha), "html", "utf-8"))
    if LOGO.exists():
        imagen = MIMEImage(LOGO.read_bytes(), "png")
        imagen.add_header("Content-ID", f"<{LOGO_CID}>")
        imagen.add_header("Content-Disposition", "inline", filename=LOGO.name)
        html.attach(imagen)
    msg.attach(html)

    log.info(f"Conectando a {SMTP_HOST}:{SMTP_PORT}...")
    rechazados = _registrar_rechazados(destinatarios, _enviar(msg, remitente, clave, destinatarios))
    log.info(f"Correo enviado a {len(destinatarios) - rechazados} de {len(destinatarios)} destinatario(s).")


def enviar_alerta_falla(fecha: date, etapa: str, detalle: str, log_path: Path) -> None:
    """
    Alerta técnica ante una falla del flujo semanal. Se envía únicamente a la cuenta
    remitente (SMTP_USER), nunca a DESTINATARIOS, e incluye el final del log consolidado.
    """
    remitente = os.environ.get("SMTP_USER")
    clave = os.environ.get("SMTP_PASS")
    if not remitente or not clave:
        log.warning("Alerta técnica no enviada: faltan SMTP_USER o SMTP_PASS.")
        return
    try:
        lineas = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        extracto = "\n".join(lineas[-60:])
    except OSError:
        extracto = "(no se pudo leer el log)"

    repo, run_id = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    enlace = (f'<p style="margin:0 0 10px;font-family:{FUENTE};font-size:12px;color:{TEXTO2};">Ejecución en GitHub Actions: '
              f'<a href="https://github.com/{repo}/actions/runs/{run_id}" style="color:{ROJO};font-weight:700;">ver detalle</a></p>'
              if repo and run_id else "")
    td = f"font-family:{FUENTE};font-size:12px;color:{NEGRO};padding:7px 10px;border-bottom:1px solid {BORDE};vertical-align:top;"
    fila = lambda k, v: (f'<tr><td style="{td}background:{GRIS};font-weight:700;letter-spacing:.04em;">{k}</td>'  # noqa: E731
                         f'<td style="{td}">{escape(v)}</td></tr>')
    # Mismo lenguaje visual que el reporte: encabezado negro con línea roja y tarjeta blanca con borde de estado
    contenido = (
        _titulo("LA EJECUCIÓN NO SE COMPLETÓ")
        + _subtitulo(f"No se envió el reporte a los destinatarios. Ejecución del {_fecha(fecha)}.")
        + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid {BORDE};margin:4px 0 12px;">'
        + fila("ETAPA", etapa) + fila("DETALLE", detalle)
        + fila("LOG", ("Logs/" if log_path.parent.name == "Logs" else "") + log_path.name)
        + "</table>" + enlace + _titulo("ÚLTIMAS LÍNEAS DEL LOG", 6)
        + f'<pre style="margin:8px 0 0;background:{GRIS};border-radius:6px;padding:10px 12px;font-size:11px;color:{NEGRO};white-space:pre-wrap;">{escape(extracto)}</pre>'
    )
    cuerpo = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:{GRIS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{GRIS};"><tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="{ANCHO_CORREO}" cellpadding="0" cellspacing="0" style="max-width:{ANCHO_CORREO}px;width:100%;table-layout:fixed;">
  <tr><td style="background:{NEGRO};border-bottom:4px solid {ROJO};border-radius:10px 10px 0 0;padding:20px 24px;">
    <div style="font-family:{FUENTE};font-size:20px;font-weight:800;letter-spacing:.04em;color:#FFFFFF;">ALERTA TÉCNICA · MONITOR SNIES PREGRADO</div>
    <div style="font-family:{FUENTE};font-size:13px;color:{TEXTO_SOBRE_NEGRO};margin-top:4px;">Aviso para la cuenta remitente</div>
  </td></tr>
  <tr><td style="padding:16px 0 0;">{_tarjeta(contenido, ROJO)}</td></tr>
</table></td></tr></table>
</body></html>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"ALERTA TÉCNICA · Monitor SNIES Pregrado · {fecha.strftime('%d/%m/%Y')}"
    msg["From"] = msg["To"] = remitente
    msg.attach(MIMEText(cuerpo, "html", "utf-8"))
    _enviar(msg, remitente, clave, [remitente])
    log.info(f"Alerta técnica enviada a {remitente}")
