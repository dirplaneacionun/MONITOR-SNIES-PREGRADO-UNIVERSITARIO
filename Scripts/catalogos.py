"""
catalogos.py
------------
Agrupaciones compartidas por el Dashboard y el correo institucional.
Los valores de los datos vienen normalizados por SNIES-ETL-PROGRAMS (mayúsculas, sin tildes).
"""

from __future__ import annotations

# ── Universidades ─────────────────────────────────────────────────────────────
UNINORTE = 1713
MAYOR_INTERES = (2704, 1714, 2810, 1701, 1711, 1813, 1712, 1806, 1706, 1828, 1728, 2805)

GRUPO_UNINORTE = "UNIVERSIDAD DEL NORTE"
GRUPO_MAYOR_INTERES = "UNIVERSIDADES DE MAYOR INTERÉS"
GRUPO_OTRAS = "OTRAS UNIVERSIDADES"
GRUPOS_UNIVERSIDAD = (GRUPO_UNINORTE, GRUPO_MAYOR_INTERES, GRUPO_OTRAS)


def grupo_universidad(codigo_institucion) -> str:
    try:
        codigo = int(codigo_institucion)
    except (TypeError, ValueError):
        return GRUPO_OTRAS
    if codigo == UNINORTE:
        return GRUPO_UNINORTE
    if codigo in MAYOR_INTERES:
        return GRUPO_MAYOR_INTERES
    return GRUPO_OTRAS


# ── Modalidades (solo para gráficas y filtros; las tablas conservan el valor original) ──
MODALIDADES_GRUPO = {
    "A DISTANCIA": ("A DISTANCIA", "PRESENCIAL-A DISTANCIA", "PRESENCIAL-A DISTANCIA-DUAL"),
    "DUAL": ("DUAL", "A DISTANCIA-DUAL", "PRESENCIAL-DUAL"),
    "HIBRIDA": ("HIBRIDA (A DISTANCIA-VIRTUAL)", "HIBRIDA (DUAL-VIRTUAL)", "HIBRIDA (PRESENCIAL-VIRTUAL)"),
    "PRESENCIAL": ("PRESENCIAL",),
    "VIRTUAL": (
        "VIRTUAL", "PRESENCIAL-VIRTUAL", "PRESENCIAL-VIRTUAL-A DISTANCIA",
        "PRESENCIAL-VIRTUAL-A DISTANCIA-DUAL", "PRESENCIAL-VIRTUAL-DUAL", "VIRTUAL-A DISTANCIA",
        "VIRTUAL-A DISTANCIA-DUAL", "VIRTUAL-DUAL",
    ),
    "SIN INFORMACIÓN": ("SIN METODOLOGIA DEFINIDA", "SIN METODOLOGÍA DEFINIDA"),
}
SIN_INFORMACION = "SIN INFORMACIÓN"
MODALIDAD_A_GRUPO = {valor: grupo for grupo, valores in MODALIDADES_GRUPO.items() for valor in valores}


def modalidad_grupo(modalidad) -> str:
    if modalidad is None or str(modalidad).strip() in ("", "nan", "None"):
        return SIN_INFORMACION
    return MODALIDAD_A_GRUPO.get(str(modalidad).strip().upper(), SIN_INFORMACION)


# ── Tipos de cambio vigilados (orden de presentación) ────────────────────────
TIPOS_CAMBIO = ("NUMERO DE CREDITOS", "MUNICIPIO DE OFERTA", "NOMBRE DEL PROGRAMA", "NUMERO DE PERIODOS", "MODALIDAD")
TIPOS_CAMBIO_ETIQUETA = {
    "NUMERO DE CREDITOS": "NÚMERO DE CRÉDITOS",
    "MUNICIPIO DE OFERTA": "MUNICIPIO DE OFERTA",
    "NOMBRE DEL PROGRAMA": "NOMBRE DEL PROGRAMA",
    "NUMERO DE PERIODOS": "NÚMERO DE PERIODOS",
    "MODALIDAD": "MODALIDAD",
}
