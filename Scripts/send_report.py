"""
send_report.py
--------------
Correo institucional del Monitor SNIES Pregrado · Universitario (SMTP de Gmail).

- Sin emojis ni archivos adjuntos: el detalle completo está en el Dashboard.
- Orden: NUEVOS, MODIFICADOS, INACTIVOS; máximo 10 registros por tabla.
- Prioridad: si hay registros de la Universidad del Norte o de las Universidades de Mayor
  Interés, la tabla muestra exclusivamente esos (hasta 10, primero Uninorte); si no hay,
  muestra los demás registros detectados hasta completar 10.

Variables de entorno:
    SMTP_USER      — cuenta remitente (dirplaneacionun@gmail.com)
    SMTP_PASS      — contraseña de aplicación de la cuenta
    DESTINATARIOS  — lista separada por comas
"""

from __future__ import annotations

import logging
import os
import smtplib
import sys
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
NEGRO, ROJO, GRIS, BORDE, TEXTO2 = "#1A140C", "#BE0000", "#F1F1F1", "#E2DED8", "#57504A"
ESTADO = {"nuevos": "#1E8E5A", "modificados": "#E69F00", "inactivos": "#BE0000"}  # validados para daltonismo
FUENTE = "Montserrat, 'Segoe UI', Arial, sans-serif"

SECCIONES = {
    "nuevos": {
        "titulo": "PROGRAMAS ACADÉMICOS NUEVOS",
        "descripcion": "Programas que aparecen en el SNIES y no estaban en la ejecución anterior.",
        "pagina": "nuevos.html",
        "columnas": [("NOMBRE_INSTITUCION", "INSTITUCIÓN"), ("NOMBRE_DEL_PROGRAMA", "PROGRAMA"),
                     ("MODALIDAD", "MODALIDAD"), ("MUNICIPIO_OFERTA_PROGRAMA", "MUNICIPIO"),
                     ("DIVISION_UNINORTE", "DIVISIÓN UNINORTE"), ("TIPO_NOVEDAD", "TIPO")],
    },
    "modificados": {
        "titulo": "PROGRAMAS ACADÉMICOS MODIFICADOS",
        "descripcion": "Cambios en créditos, municipio de oferta, nombre, número de periodos o modalidad.",
        "pagina": "modificados.html",
        "columnas": [("NOMBRE_INSTITUCION", "INSTITUCIÓN"), ("NOMBRE_DEL_PROGRAMA", "PROGRAMA"),
                     ("TIPO_CAMBIO_DETECTADO", "TIPO DE CAMBIO"), ("CAMBIO_DETECTADO", "CAMBIO DETECTADO")],
    },
    "inactivos": {
        "titulo": "PROGRAMAS ACADÉMICOS INACTIVOS",
        "descripcion": "Programas que estaban activos en la ejecución anterior y ya no figuran en el SNIES.",
        "pagina": "inactivos.html",
        "columnas": [("NOMBRE_INSTITUCION", "INSTITUCIÓN"), ("NOMBRE_DEL_PROGRAMA", "PROGRAMA"),
                     ("MODALIDAD", "MODALIDAD"), ("MUNICIPIO_OFERTA_PROGRAMA", "MUNICIPIO"),
                     ("DIVISION_UNINORTE", "DIVISIÓN UNINORTE")],
    },
}


# ── Selección con prioridad ───────────────────────────────────────────────────

def seleccionar(df: pd.DataFrame | None) -> tuple[pd.DataFrame, bool]:
    """
    Retorna (registros a mostrar, si son prioritarios). Si hay registros de la Universidad del
    Norte o de Universidades de Mayor Interés se muestran solo esos (Uninorte primero).
    """
    if df is None or df.empty:
        return pd.DataFrame(), False
    grupos = df["CODIGO_INSTITUCION"].map(grupo_universidad)
    prioridad = grupos.map({GRUPO_UNINORTE: 0, GRUPO_MAYOR_INTERES: 1}).fillna(2)
    df = df.assign(_P=prioridad.values)
    prioritarios = df[df["_P"] < 2]
    base = prioritarios if not prioritarios.empty else df
    orden = ["_P", "NOMBRE_INSTITUCION", "NOMBRE_DEL_PROGRAMA"]
    return base.sort_values(orden, kind="stable").head(MAX_FILAS).drop(columns="_P"), not prioritarios.empty


# ── HTML ──────────────────────────────────────────────────────────────────────

def _texto(col: str, v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if col == "TIPO_CAMBIO_DETECTADO":
        return TIPOS_CAMBIO_ETIQUETA.get(v, v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _tabla(df: pd.DataFrame, columnas, color: str) -> str:
    th = (f"background:{NEGRO};color:#FFFFFF;font-family:{FUENTE};font-size:11px;font-weight:700;"
          "letter-spacing:.04em;text-align:left;padding:8px 10px;border-bottom:3px solid " + color + ";")
    cab = "".join(f'<th style="{th}">{escape(t)}</th>' for _, t in columnas)
    filas = []
    for i, (_, r) in enumerate(df.iterrows()):
        fondo = "#FFFFFF" if i % 2 == 0 else "#FAF8F6"
        td = (f"background:{fondo};font-family:{FUENTE};font-size:12px;color:{NEGRO};padding:7px 10px;"
              f"border-bottom:1px solid {BORDE};vertical-align:top;")
        filas.append("<tr>" + "".join(f'<td style="{td}">{escape(_texto(c, r.get(c)))}</td>' for c, _ in columnas) + "</tr>")
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;margin:8px 0 4px;">'
            f"<thead><tr>{cab}</tr></thead><tbody>{''.join(filas)}</tbody></table>")


def _seccion(tipo: str, df: pd.DataFrame | None) -> str:
    cfg, color = SECCIONES[tipo], ESTADO[tipo]
    total = 0 if df is None else len(df)
    titulo = (f'<h2 style="margin:28px 0 2px;font-family:{FUENTE};font-size:15px;font-weight:800;letter-spacing:.04em;'
              f'color:{NEGRO};border-left:5px solid {color};padding-left:10px;">{cfg["titulo"]} · '
              f'{f"{total:,}".replace(",", ".")}</h2>')
    desc = f'<p style="margin:2px 0 6px;font-family:{FUENTE};font-size:12px;color:{TEXTO2};">{cfg["descripcion"]}</p>'
    if not total:
        return titulo + desc + (f'<p style="font-family:{FUENTE};font-size:12px;color:{TEXTO2};background:{GRIS};'
                                f'padding:10px 12px;border-radius:6px;">Sin novedades en esta ejecución.</p>')
    mostrar, prioritarios = seleccionar(df)
    if tipo == "modificados":
        programas = df["CODIGO_SNIES_DEL_PROGRAMA"].nunique()
        desc = desc.replace("</p>", f" {total} cambio(s) en {programas} programa(s).</p>")
    if prioritarios:
        nota = (f"Se muestran {len(mostrar)} registro(s) de la Universidad del Norte y de las Universidades de "
                f"Mayor Interés, de {total} detectado(s).")
    elif total > len(mostrar):
        nota = (f"Se muestran {len(mostrar)} de {total} registros; no hubo novedades de la Universidad del Norte "
                "ni de las Universidades de Mayor Interés.")
    else:
        nota = "No hubo novedades de la Universidad del Norte ni de las Universidades de Mayor Interés."
    enlace = (f'<p style="margin:4px 0 0;font-family:{FUENTE};font-size:11px;color:{TEXTO2};">{nota} '
              f'<a href="{DASHBOARD_URL}{cfg["pagina"]}" style="color:{ROJO};font-weight:700;">Ver todos en el Dashboard</a></p>')
    return titulo + desc + _tabla(mostrar, cfg["columnas"], color) + enlace


def _kpi(etiqueta: str, valor, color: str) -> str:
    return (f'<td width="25%" style="padding:4px;"><div style="background:#FFFFFF;border:1px solid {BORDE};'
            f'border-left:5px solid {color};border-radius:8px;padding:10px 12px;">'
            f'<div style="font-family:{FUENTE};font-size:10px;font-weight:700;letter-spacing:.05em;color:{TEXTO2};">{etiqueta}</div>'
            f'<div style="font-family:{FUENTE};font-size:24px;font-weight:800;color:{NEGRO};">{valor}</div></div></td>')


def construir_cuerpo(resultados: dict, fecha: date) -> str:
    res = resultados.get("pregrado") or {}
    nue, mod, ina = (res.get(k) for k in ("nuevos", "modificados", "inactivos"))
    n = lambda d: 0 if d is None else len(d)  # noqa: E731
    fmt = lambda v: f"{v:,}".replace(",", ".")  # noqa: E731
    activos = res.get("total_activos")
    kpis = (_kpi("PROGRAMAS ACTIVOS", fmt(activos) if activos is not None else "–", NEGRO)
            + _kpi("NUEVOS", fmt(n(nue)), ESTADO["nuevos"])
            + _kpi("MODIFICADOS", fmt(n(mod)), ESTADO["modificados"])
            + _kpi("INACTIVOS", fmt(n(ina)), ESTADO["inactivos"]))
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:{GRIS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{GRIS};"><tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="720" cellpadding="0" cellspacing="0" style="max-width:720px;width:100%;background:#FFFFFF;border-radius:10px;overflow:hidden;">
  <tr><td style="background:{NEGRO};border-bottom:4px solid {ROJO};padding:22px 24px;">
    <div style="font-family:{FUENTE};font-size:20px;font-weight:800;letter-spacing:.04em;color:#FFFFFF;">MONITOR SNIES PREGRADO · UNIVERSITARIO</div>
    <div style="font-family:{FUENTE};font-size:13px;color:#D9D3CC;margin-top:4px;">Programas Académicos Universitarios de Pregrado en Colombia</div>
    <div style="font-family:{FUENTE};font-size:12px;color:#D9D3CC;margin-top:10px;">Reporte de la ejecución del {fecha.strftime('%d/%m/%Y')}</div>
  </td></tr>
  <tr><td style="padding:16px 20px 4px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{kpis}</tr></table>
  </td></tr>
  <tr><td align="center" style="padding:12px 24px 0;">
    <a href="{DASHBOARD_URL}" style="display:inline-block;background:{ROJO};color:#FFFFFF;font-family:{FUENTE};font-size:13px;font-weight:700;letter-spacing:.06em;text-decoration:none;padding:12px 32px;border-radius:8px;">VER DASHBOARD COMPLETO</a>
  </td></tr>
  <tr><td style="padding:0 24px 8px;">
    {_seccion("nuevos", nue)}
    {_seccion("modificados", mod)}
    {_seccion("inactivos", ina)}
  </td></tr>
  <tr><td style="padding:18px 24px 22px;">
    <div style="background:{GRIS};border-left:4px solid {NEGRO};padding:10px 12px;font-family:{FUENTE};font-size:11px;color:{TEXTO2};">
      <b style="color:{NEGRO};">FUENTE:</b> SNIES — Sistema Nacional de Información de la Educación Superior, Ministerio de Educación Nacional.<br>
      Reporte generado automáticamente por la Dirección de Planeación, Universidad del Norte.
    </div>
  </td></tr>
</table></td></tr></table>
</body></html>"""


# ── Envío ─────────────────────────────────────────────────────────────────────

def _enviar(msg: MIMEMultipart, remitente: str, clave: str, destinatarios: list[str]) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
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
        {fila("ETAPA", etapa)}{fila("DETALLE", detalle)}{fila("LOG", "Logs/" + log_path.name)}
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
