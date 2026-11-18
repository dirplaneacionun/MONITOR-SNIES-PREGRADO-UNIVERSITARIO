"""
send_report.py
--------------
Correo institucional del Monitor SNIES Pregrado · Universitario (SMTP de Gmail).

- Sin emojis ni archivos adjuntos: el detalle completo está en el Dashboard.
- Dos versiones del mismo contenido (multipart/alternative): texto plano y HTML.
- Mismo lenguaje visual del Dashboard. Orden: encabezado con el acceso al Dashboard, indicadores, resumen
  descriptivo de la ejecución (variación frente a la ejecución anterior y programas activos por grupo de
  universidades) y las secciones NUEVOS, MODIFICADOS e INACTIVOS, con un máximo de 10 registros por tabla.
- Orden de los registros: primero la Universidad del Norte, luego las Universidades de Mayor Interés y
  después las demás instituciones, hasta completar 10. Bajo cada tabla, una nota indica las novedades de la
  Universidad del Norte y de las Universidades de Mayor Interés y, si la tabla no alcanza a mostrar todos los
  registros, remite al Dashboard (sección filtrada por la fecha de la ejecución).
- La conexión SMTP usa STARTTLS con verificación de certificado y nombre del servidor.
- enviar_alerta_falla() notifica fallas técnicas solo a la cuenta remitente.

Variables de entorno:
    SMTP_USER      cuenta remitente (dirplaneacionun@gmail.com)
    SMTP_PASS      contraseña de aplicación de la cuenta
    DESTINATARIOS  lista separada por comas
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
import sys
import textwrap
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalogos import (  # noqa: E402
    GRUPO_MAYOR_INTERES, GRUPO_UNINORTE, TIPOS_CAMBIO_ETIQUETA, grupo_universidad,
)

# Hijo del logger del orquestador: los mensajes del correo quedan en el log consolidado.
log = logging.getLogger("snies_monitor.correo")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
DASHBOARD_URL = "https://dirplaneacionun.github.io/MONITOR-SNIES-PREGRADO-UNIVERSITARIO/"
MAX_FILAS = 10

# Identidad visual
# Paleta institucional Universidad del Norte
NEGRO, ROJO, AMARILLO, GRIS, BORDE = "#000000", "#BE0000", "#FFB700", "#F5F5F5", "#E5E5E5"
TEXTO2, TEXTO3 = "#4D4D4D", "#6B6B6B"   # textos secundarios del Dashboard (--texto-2 y --texto-3)
ESTADO = {"nuevos": "#1E8E5A", "modificados": "#F28C00", "inactivos": "#BE0000"}  # validados para daltonismo
FUENTE = "Montserrat, 'Segoe UI', Arial, sans-serif"

# Columnas comunes de las tres tablas (los modificados agregan el tipo de cambio y el cambio detectado)
COLUMNAS_BASE = [("NOMBRE_INSTITUCION", "INSTITUCIÓN"), ("NOMBRE_DEL_PROGRAMA", "PROGRAMA"),
                     ("MODALIDAD", "MODALIDAD"), ("MUNICIPIO_OFERTA_PROGRAMA", "MUNICIPIO"),
                     ("DIVISION_UNINORTE", "DIVISIÓN UNINORTE")]

SECCIONES = {
    "nuevos": {
        "titulo": "PROGRAMAS ACADÉMICOS NUEVOS",
        "descripcion": "Programas que aparecen en el SNIES y no estaban en la ejecución anterior (incluye los reactivados).",
        "vacio": "No se detectaron programas nuevos en esta ejecución.",
        "unidad": ("programa nuevo", "programas nuevos"),
        "pagina": "nuevos.html",
        "columnas": COLUMNAS_BASE,
    },
    "modificados": {
        "titulo": "PROGRAMAS ACADÉMICOS MODIFICADOS",
        "descripcion": "Cambios en créditos, municipio de oferta, nombre, número de periodos o modalidad.",
        "vacio": "No se detectaron cambios en los programas activos en esta ejecución.",
        "unidad": ("cambio", "cambios"),
        "pagina": "modificados.html",
        "columnas": COLUMNAS_BASE + [("TIPO_CAMBIO_DETECTADO", "TIPO DE CAMBIO"), ("CAMBIO_DETECTADO", "CAMBIO DETECTADO")],
    },
    "inactivos": {
        "titulo": "PROGRAMAS ACADÉMICOS INACTIVOS",
        "descripcion": "Programas que estaban activos en la ejecución anterior y ya no figuran en el SNIES.",
        "vacio": "No se detectaron programas inactivos en esta ejecución.",
        "unidad": ("programa inactivo", "programas inactivos"),
        "pagina": "inactivos.html",
        "columnas": COLUMNAS_BASE,
    },
}


# ── Selección con prioridad ───────────────────────────────────────────────────

def seleccionar(df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Registros a mostrar en la tabla de una sección (máximo MAX_FILAS): primero la Universidad del Norte, luego
    las Universidades de Mayor Interés y después las demás instituciones, cada grupo en orden alfabético por
    institución y programa.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    grupos = df["CODIGO_INSTITUCION"].map(grupo_universidad)
    prioridad = grupos.map({GRUPO_UNINORTE: 0, GRUPO_MAYOR_INTERES: 1}).fillna(2)
    orden = ["_P", "NOMBRE_INSTITUCION", "NOMBRE_DEL_PROGRAMA"]
    return df.assign(_P=prioridad.values).sort_values(orden, kind="stable").head(MAX_FILAS).drop(columns="_P")


# ── HTML ──────────────────────────────────────────────────────────────────────
# Mismo lenguaje visual del Dashboard: encabezado negro con línea roja, fondo gris claro, tarjetas blancas con
# borde gris de separación, títulos en mayúsculas y tablas con encabezado negro. Los estilos van en línea
# porque la mayoría de los clientes de correo ignoran las hojas de estilo.

def _num(v) -> str:
    """Número con separador de miles institucional (punto)."""
    return f"{v:,}".replace(",", ".")


def _fecha(d) -> str:
    return d.strftime("%d/%m/%Y")


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{_num(n)} {singular if n == 1 else plural}"


def _texto(col: str, v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if col == "TIPO_CAMBIO_DETECTADO":
        return TIPOS_CAMBIO_ETIQUETA.get(v, v)
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
    return (f'<div style="margin-top:{margen}px;font-family:{FUENTE};font-size:13px;font-weight:800;'
            f'letter-spacing:.05em;color:{NEGRO};">{texto}</div>')


def _subtitulo(texto: str) -> str:
    return f'<p style="margin:4px 0 10px;font-family:{FUENTE};font-size:12px;line-height:1.5;color:{TEXTO2};">{texto}</p>'


def _tabla(df: pd.DataFrame, columnas, numericas=()) -> str:
    # Tablas de más de 5 columnas (modificados): letra y márgenes algo menores para que quepan en el ancho del correo
    compacta = len(columnas) > 5
    letra_th, letra_td, margen = ("10px", "11px", "6px") if compacta else ("11px", "12px", "10px")
    th = (f"background:{NEGRO};color:#FFFFFF;font-family:{FUENTE};font-size:{letra_th};font-weight:700;"
          f"letter-spacing:.04em;padding:9px {margen};")
    alinear = lambda c: "right" if c in numericas else "left"  # noqa: E731
    cab = "".join(f'<th style="{th}text-align:{alinear(c)};">{escape(t)}</th>' for c, t in columnas)
    filas = []
    for i, (_, r) in enumerate(df.iterrows()):
        fondo = "#FFFFFF" if i % 2 == 0 else "#FAFAFA"
        td = (f"background:{fondo};font-family:{FUENTE};font-size:{letra_td};color:{NEGRO};padding:7px {margen};"
              f"border-bottom:1px solid {BORDE};vertical-align:top;")
        filas.append("<tr>" + "".join(
            f'<td style="{td}text-align:{alinear(c)};{"white-space:nowrap;" if c in numericas else ""}">'
            f'{escape(_texto(c, r.get(c)))}</td>' for c, _ in columnas) + "</tr>")
    # En pantallas angostas la tabla se desplaza dentro de su tarjeta, sin ensanchar el resto del correo
    return (f'<div style="overflow-x:auto;"><table class="tabla" role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="border-collapse:collapse;border:1px solid {BORDE};margin:4px 0;">'
            f"<thead><tr>{cab}</tr></thead><tbody>{''.join(filas)}</tbody></table></div>")


def _variacion(hoy: int, antes: int | None) -> str:
    if antes is None:
        return "–"
    d = hoy - antes
    return "Sin variación" if d == 0 else f"{'+' if d > 0 else '−'}{_num(abs(d))}"


def _frases_resumen(res: dict, fecha: date, negrita=lambda t: t) -> list[str]:
    """Frases del resumen de la ejecución (activos, variación y novedades), comunes al HTML y al texto plano."""
    activos, antes, f_ant = res.get("total_activos"), res.get("total_anterior"), res.get("fecha_anterior")
    n = {t: 0 if res.get(t) is None else len(res[t]) for t in ("nuevos", "modificados", "inactivos")}
    mod = res.get("modificados")
    prog_mod = 0 if mod is None or mod.empty else mod["CODIGO_SNIES_DEL_PROGRAMA"].nunique()

    frases = [f"El {_fecha(fecha)} se descargó del SNIES el reporte de programas académicos universitarios de "
              "pregrado con estado activo en Colombia."]
    if activos is not None:
        frase = f"Se identificaron {negrita(_plural(activos, 'programa activo', 'programas activos'))}"
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
            f"Novedades detectadas: {_plural(n['nuevos'], 'programa nuevo', 'programas nuevos')}, "
            f"{_plural(n['modificados'], 'cambio', 'cambios')} en "
            f"{_plural(prog_mod, 'programa modificado', 'programas modificados')} y "
            f"{_plural(n['inactivos'], 'programa inactivo', 'programas inactivos')}.")
        grupos = pd.concat([res[t]["CODIGO_INSTITUCION"] for t in n if n[t]]).map(grupo_universidad)
        uni, mi = int((grupos == GRUPO_UNINORTE).sum()), int((grupos == GRUPO_MAYOR_INTERES).sum())
        frases.append(f"De estas novedades, {_num(uni)} corresponde{'' if uni == 1 else 'n'} a la Universidad del Norte "
                      f"y {_num(mi)} a las Universidades de Mayor Interés.")
    return frases


def _grupos(res: dict, fecha: date):
    """Programas activos por grupo de universidades: (columnas, filas) o None si no hay datos."""
    hoy_g, antes_g, f_ant = res.get("activos_grupo"), res.get("activos_grupo_anterior"), res.get("fecha_anterior")
    if not hoy_g:
        return None
    con_anterior = bool(antes_g) and f_ant is not None
    cols = [("GRUPO", "GRUPO DE UNIVERSIDADES")]
    if con_anterior:
        cols.append(("ANTES", f"EJECUCIÓN ANTERIOR ({_fecha(f_ant)})"))
    cols += [("HOY", f"ESTA EJECUCIÓN ({_fecha(fecha)})"), ("VAR", "VARIACIÓN")]
    filas = pd.DataFrame([{"GRUPO": g, "ANTES": _num(antes_g.get(g, 0)) if con_anterior else None, "HOY": _num(v),
                           "VAR": _variacion(v, antes_g.get(g, 0) if con_anterior else None)}
                          for g, v in hoy_g.items()])
    return cols, filas


def _resumen(res: dict, fecha: date) -> str:
    """Resumen descriptivo de la ejecución: activos, variación, novedades y programas activos por grupo."""
    contenido = _titulo("RESUMEN DE LA EJECUCIÓN") + _subtitulo(" ".join(_frases_resumen(res, fecha, lambda t: f"<b>{t}</b>")))
    grupos = _grupos(res, fecha)
    if grupos:
        contenido += (_titulo("PROGRAMAS ACTIVOS POR GRUPO DE UNIVERSIDADES", 14)
                      + _tabla(grupos[1], grupos[0], numericas=("ANTES", "HOY", "VAR")))
    return _tarjeta(contenido)


def _descripcion(tipo: str, df: pd.DataFrame | None) -> str:
    descripcion = SECCIONES[tipo]["descripcion"]
    if tipo == "modificados" and df is not None and len(df):
        descripcion += (f" En esta ejecución: {_plural(len(df), 'cambio', 'cambios')} en "
                        f"{_plural(df['CODIGO_SNIES_DEL_PROGRAMA'].nunique(), 'programa', 'programas')}.")
    return descripcion


def _notas(tipo: str, df: pd.DataFrame) -> tuple[list[tuple[str, int]], int]:
    """
    Datos de las notas bajo la tabla de una sección: novedades de la Universidad del Norte y de las Universidades
    de Mayor Interés [(grupo, cantidad)] y número de registros que no caben en la tabla (0 si caben todos).
    """
    grupos = df["CODIGO_INSTITUCION"].map(grupo_universidad)
    interes = [(GRUPO_UNINORTE, int((grupos == GRUPO_UNINORTE).sum())),
               (GRUPO_MAYOR_INTERES, int((grupos == GRUPO_MAYOR_INTERES).sum()))]
    return interes, max(len(df) - MAX_FILAS, 0)


def _texto_notas(tipo: str, df: pd.DataFrame, negrita=lambda t: t, enlace=lambda t: t) -> list[str]:
    """Frases de las notas bajo la tabla (comunes al HTML y al texto plano)."""
    singular, plural = SECCIONES[tipo]["unidad"]
    interes, fuera = _notas(tipo, df)
    nombres = {GRUPO_UNINORTE: "Universidad del Norte", GRUPO_MAYOR_INTERES: "Universidades de Mayor Interés"}
    frases = [" · ".join(f"{negrita(nombres[g] + ':')} {_plural(n, singular, plural)}" for g, n in interes) + "."]
    if fuera:
        frases.append(f"La tabla presenta {_num(MAX_FILAS)} de {_num(len(df))} registros, con prioridad para la Universidad "
                      f"del Norte y las Universidades de Mayor Interés. El detalle completo puede consultarse en el "
                      f"{enlace('Dashboard')}.")
    return frases


def _seccion(tipo: str, df: pd.DataFrame | None, fecha: date) -> str:
    cfg, color = SECCIONES[tipo], ESTADO[tipo]
    total = 0 if df is None else len(df)
    contenido = _titulo(f"{cfg['titulo']} · {_num(total)}") + _subtitulo(_descripcion(tipo, df))
    if not total:
        contenido += (f'<p style="margin:0;font-family:{FUENTE};font-size:12px;color:{TEXTO2};background:{GRIS};'
                      f'padding:10px 12px;border-radius:6px;">{cfg["vacio"]}</p>')
        return _tarjeta(contenido, color)
    url = f'{DASHBOARD_URL}{cfg["pagina"]}#fecha={fecha.isoformat()}'   # sección filtrada por esta ejecución
    frases = _texto_notas(tipo, df, negrita=lambda t: f'<b style="color:{NEGRO};">{escape(t)}</b>',
                          enlace=lambda t: f'<a href="{url}" style="color:{ROJO};font-weight:700;">{t}</a>')
    notas = (f'<p style="margin:8px 0 0;font-family:{FUENTE};font-size:12px;line-height:1.5;color:{TEXTO2};'
             f'background:{GRIS};padding:10px 12px;border-radius:6px;">{"<br>".join(frases)}</p>')
    return _tarjeta(contenido + _tabla(seleccionar(df), cfg["columnas"]) + notas, color)


def _kpi(etiqueta: str, valor: str, detalle: str, color: str) -> str:
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
    prog_mod = 0 if mod is None or mod.empty else mod["CODIGO_SNIES_DEL_PROGRAMA"].nunique()
    return [("PROGRAMAS ACTIVOS", _num(activos) if activos is not None else "–", detalle_activos, NEGRO),
            ("NUEVOS", _num(n(nue)), f"Ejecución del {_fecha(fecha)}", ESTADO["nuevos"]),
            ("MODIFICADOS", _num(n(mod)), f"Cambios en {_plural(prog_mod, 'programa', 'programas')}", ESTADO["modificados"]),
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
    grupos = _grupos(res, fecha)
    if grupos:
        cols, filas = grupos
        lineas += ["", "PROGRAMAS ACTIVOS POR GRUPO DE UNIVERSIDADES"]
        for _, r in filas.iterrows():
            antes = f"; ejecución anterior: {r['ANTES']}" if r["ANTES"] is not None else ""
            lineas.append(ajustar(f"{r['GRUPO']}: {r['HOY']}{antes}; variación: {r['VAR']}", "  ", "    "))
    for tipo in ("nuevos", "modificados", "inactivos"):
        cfg, df = SECCIONES[tipo], res.get(tipo)
        total = 0 if df is None else len(df)
        lineas += ["", f"{cfg['titulo']} · {_num(total)}", ajustar(_descripcion(tipo, df))]
        if not total:
            lineas.append(cfg["vacio"])
            continue
        for i, (_, r) in enumerate(seleccionar(df).iterrows(), 1):
            campos = " | ".join(f"{t}: {_texto(c, r.get(c))}" for c, t in cfg["columnas"] if _texto(c, r.get(c)))
            lineas.append(ajustar(f"{i}. {campos}", "  ", "     "))
        lineas += [ajustar(t) for t in _texto_notas(tipo, df)]
        if len(df) > MAX_FILAS:   # dirección en su propia línea, completa
            lineas.append(f"Detalle en el Dashboard: {DASHBOARD_URL}{cfg['pagina']}#fecha={fecha.isoformat()}")
    lineas += ["",
               ajustar("Fuente: Sistema Nacional de Información de la Educación Superior (SNIES), Ministerio de "
                       "Educación Nacional · Dirección de Planeación, Universidad del Norte."),
               "Reporte generado automáticamente en cada ejecución del monitor."]
    return "\n".join(lineas) + "\n"


def construir_cuerpo(resultados: dict, fecha: date) -> str:
    res = resultados.get("pregrado") or {}
    nue, mod, ina = (res.get(k) for k in ("nuevos", "modificados", "inactivos"))
    kpis = "".join(_kpi(*k) for k in _indicadores(res, fecha))
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
<table role="presentation" width="720" cellpadding="0" cellspacing="0" style="max-width:720px;width:100%;table-layout:fixed;">
  <tr><td style="background:{NEGRO};border-bottom:4px solid {ROJO};border-radius:10px 10px 0 0;padding:20px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td class="enc-titulo" valign="top">
        <div style="font-family:{FUENTE};font-size:20px;font-weight:800;letter-spacing:.04em;color:#FFFFFF;">MONITOR SNIES PREGRADO · UNIVERSITARIO</div>
        <div style="font-family:{FUENTE};font-size:13px;color:#D9D9D9;margin-top:4px;">Programas Académicos Universitarios de Pregrado en Colombia</div>
        <a href="{DASHBOARD_URL}" style="display:inline-block;margin-top:14px;background:{ROJO};color:#FFFFFF;font-family:{FUENTE};font-size:12px;font-weight:700;letter-spacing:.06em;text-decoration:none;padding:10px 22px;border-radius:8px;">VER DASHBOARD COMPLETO</a>
      </td>
      <td class="enc-fecha" valign="top" align="right" width="150">
        <div style="border:1px solid #3A3A3A;border-top:3px solid {AMARILLO};border-radius:10px;padding:8px 14px;text-align:right;">
          <div style="font-family:{FUENTE};font-size:10px;font-weight:700;letter-spacing:.08em;color:#D9D9D9;">EJECUCIÓN</div>
          <div style="font-family:{FUENTE};font-size:18px;font-weight:800;color:#FFFFFF;">{_fecha(fecha)}</div>
        </div>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:16px 0 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;"><tr>{kpis}</tr></table>
    {_resumen(res, fecha)}
    {_seccion("nuevos", nue, fecha)}
    {_seccion("modificados", mod, fecha)}
    {_seccion("inactivos", ina, fecha)}
  </td></tr>
  <tr><td align="center" style="padding:8px 12px 4px;font-family:{FUENTE};font-size:11px;line-height:1.5;color:{TEXTO3};">
    Fuente: Sistema Nacional de Información de la Educación Superior (SNIES), Ministerio de Educación Nacional · Dirección de Planeación, Universidad del Norte.<br>
    Reporte generado automáticamente en cada ejecución del monitor.
  </td></tr>
</table></td></tr></table>
</body></html>"""


# ── Envío ─────────────────────────────────────────────────────────────────────

def _enviar(msg: MIMEMultipart, remitente: str, clave: str, destinatarios: list[str]) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as server:
        server.ehlo()
        # Contexto TLS que verifica el certificado y el nombre del servidor (el de smtplib por defecto no lo hace)
        server.starttls(context=ssl.create_default_context())
        server.login(remitente, clave)
        server.sendmail(remitente, destinatarios, msg.as_bytes())


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
    msg.attach(MIMEText(construir_cuerpo(resultados, fecha), "html", "utf-8"))

    log.info(f"Conectando a {SMTP_HOST}:{SMTP_PORT}...")
    _enviar(msg, remitente, clave, destinatarios)
    log.info(f"Correo enviado a {len(destinatarios)} destinatario(s).")


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
    enlace = (f'<p style="font-family:{FUENTE};font-size:13px;">Ejecución en GitHub Actions: '
              f'<a href="https://github.com/{repo}/actions/runs/{run_id}" style="color:{ROJO};">ver detalle</a></p>'
              if repo and run_id else "")
    fila = lambda k, v: (f'<tr><td style="padding:6px 12px;background:{GRIS};font-weight:700;">{k}</td>'  # noqa: E731
                         f'<td style="padding:6px 12px;">{escape(v)}</td></tr>')
    cuerpo = f"""<div style="font-family:{FUENTE};max-width:900px;margin:auto;color:{NEGRO};">
      <h2 style="color:{ROJO};border-bottom:3px solid {ROJO};padding-bottom:8px;">ALERTA TÉCNICA · MONITOR SNIES PREGRADO</h2>
      <p style="font-size:13px;">La ejecución del {fecha.strftime('%d/%m/%Y')} no se completó. No se envió el reporte a los destinatarios.</p>
      <table style="border-collapse:collapse;font-size:13px;margin:12px 0;">
        {fila("ETAPA", etapa)}{fila("DETALLE", detalle)}{fila("LOG", ("Logs/" if log_path.parent.name == "Logs" else "") + log_path.name)}
      </table>{enlace}
      <h3 style="font-size:14px;">ÚLTIMAS LÍNEAS DEL LOG</h3>
      <pre style="background:{GRIS};padding:12px;font-size:11px;white-space:pre-wrap;">{escape(extracto)}</pre>
    </div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"ALERTA TÉCNICA · Monitor SNIES Pregrado · {fecha.strftime('%d/%m/%Y')}"
    msg["From"] = msg["To"] = remitente
    msg.attach(MIMEText(cuerpo, "html", "utf-8"))
    _enviar(msg, remitente, clave, [remitente])
    log.info(f"Alerta técnica enviada a {remitente}")
