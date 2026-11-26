"""
catalogos.py
------------
Agrupaciones compartidas por el Dashboard y el correo institucional.
Los valores de los datos vienen normalizados por ETL-PROGRAMAS-SNIES (mayúsculas, sin tildes).
"""

from __future__ import annotations

import re

# ── Universidades ─────────────────────────────────────────────────────────────
# Los grupos se definen por el código de la institución PADRE (CODIGO_INSTITUCION_PADRE): las seccionales y sedes
# (por ejemplo, Universidad Libre o Javeriana Cali) quedan en el mismo grupo que su institución principal.
COL_INSTITUCION = "CODIGO_INSTITUCION"
COL_PADRE = "CODIGO_INSTITUCION_PADRE"
UNINORTE = 1713
MAYOR_INTERES = (2704, 1714, 2810, 1701, 1711, 1813, 1712, 1806, 1706, 1828, 1728, 2805)

GRUPO_UNINORTE = "UNIVERSIDAD DEL NORTE"
GRUPO_MAYOR_INTERES = "UNIVERSIDADES DE MAYOR INTERES"
GRUPO_OTRAS = "OTRAS UNIVERSIDADES"
GRUPOS_UNIVERSIDAD = (GRUPO_UNINORTE, GRUPO_MAYOR_INTERES, GRUPO_OTRAS)


def grupo_universidad(codigo_institucion_padre) -> str:
    """Grupo de universidades según el código SNIES de la institución padre; un código vacío o no numérico cuenta como "otras"."""
    try:
        codigo = int(codigo_institucion_padre)
    except (TypeError, ValueError):
        return GRUPO_OTRAS
    if codigo == UNINORTE:
        return GRUPO_UNINORTE
    if codigo in MAYOR_INTERES:
        return GRUPO_MAYOR_INTERES
    return GRUPO_OTRAS


def grupos_de_registros(registros):
    """Grupo de universidades de cada registro de una tabla, por CODIGO_INSTITUCION_PADRE (o por CODIGO_INSTITUCION si la tabla no lo trae)."""
    col = COL_PADRE if COL_PADRE in registros.columns else COL_INSTITUCION
    return registros[col].map(grupo_universidad)


# ── Sector y paleta de series ─────────────────────────────────────────────────
# El Dashboard dibuja la evolución por sector solo con estos dos valores.
SECTORES = ("OFICIAL", "PRIVADO")
# Colores de la paleta de series del Dashboard (CATEGORICA en docs/assets/app.js): con más categorías se repetirían.
MAX_COLORES_SERIES = 15

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
    "SIN INFORMACION": ("SIN METODOLOGIA DEFINIDA", "SIN METODOLOGÍA DEFINIDA"),
}
SIN_INFORMACION = "SIN INFORMACION"
MODALIDAD_A_GRUPO = {valor: grupo for grupo, valores in MODALIDADES_GRUPO.items() for valor in valores}


def modalidad_grupo(modalidad) -> str:
    """Modalidad agrupada (presencial, virtual, a distancia, híbrida o dual); un valor vacío o desconocido da SIN INFORMACION."""
    if modalidad is None or str(modalidad).strip() in ("", "nan", "None"):
        return SIN_INFORMACION
    return MODALIDAD_A_GRUPO.get(str(modalidad).strip().upper(), SIN_INFORMACION)


# ── Tipos de cambio vigilados (orden de presentación) ────────────────────────
TIPOS_CAMBIO = ("NUMERO DE CREDITOS", "DEPARTAMENTO DE OFERTA", "NOMBRE DEL PROGRAMA", "NUMERO DE PERIODOS", "MODALIDAD",
                "COSTO DE MATRICULA")

# ── Cambios detectados de un programa (columna CAMBIO_DETECTADO de Modificados) ──────────────────────────────────────
# Un registro de Modificados reúne todos los cambios de un programa en una ejecución:
#     "NUMERO DE CREDITOS: 141 → 130 | MODALIDAD: A DISTANCIA → HIBRIDA (PRESENCIAL-VIRTUAL)"
# Cada cambio es "TIPO: valor anterior → valor nuevo", en el orden de TIPOS_CAMBIO, separados por SEPARADOR_CAMBIOS.
SEPARADOR_CAMBIOS = " | "
FLECHA_CAMBIO = " → "
# Un cambio empieza al principio del texto o tras el separador, con uno de los tipos conocidos y ": ". Buscar los tipos
# (y no cortar por el separador) evita partir el texto si un nombre de programa contiene " | " o ": ".
_INICIO_CAMBIO = re.compile(r"(?:^|\s\|\s)(" + "|".join(re.escape(t) for t in TIPOS_CAMBIO) + r"): ")


def unir_cambios(cambios) -> str:
    """
    Texto de CAMBIO_DETECTADO a partir de [(tipo, valor anterior, valor nuevo)]: un cambio por tipo, en el orden de
    TIPOS_CAMBIO. Lanza ValueError con un tipo desconocido o repetido, o sin cambios.
    """
    lista = list(cambios)
    tipos = [c[0] for c in lista]
    if not lista or len(set(tipos)) != len(tipos) or any(t not in TIPOS_CAMBIO for t in tipos):
        raise ValueError(f"Cambios no válidos: {tipos}")
    lista.sort(key=lambda c: TIPOS_CAMBIO.index(c[0]))
    return SEPARADOR_CAMBIOS.join(f"{tipo}: {anterior}{FLECHA_CAMBIO}{nuevo}" for tipo, anterior, nuevo in lista)


def separar_cambios(texto) -> list[tuple[str, str, str]]:
    """
    Cambios de un texto de CAMBIO_DETECTADO como [(tipo, valor anterior, valor nuevo)], en su orden. Un texto vacío o
    nulo no tiene cambios. Un cambio sin flecha queda con el valor nuevo vacío.
    """
    if not isinstance(texto, str) or not texto:
        return []
    marcas = list(_INICIO_CAMBIO.finditer(texto))
    cambios = []
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        anterior, flecha, nuevo = texto[m.end():fin].partition(FLECHA_CAMBIO)
        cambios.append((m.group(1), anterior, nuevo if flecha else ""))
    return cambios


def tipos_de_cambio(texto) -> list[str]:
    """Tipos de cambio de un texto de CAMBIO_DETECTADO, en su orden."""
    return [c[0] for c in separar_cambios(texto)]


def consolidar_modificados(df):
    """
    Convierte las filas de Modificados con un cambio por fila (columnas TIPO_CAMBIO_DETECTADO y CAMBIO_DETECTADO, formato
    anterior) en un registro por programa y fecha de obtención, con todos sus cambios en CAMBIO_DETECTADO. Los demás
    valores de cada grupo salen de su primera fila (todas coinciden: son los del programa en esa ejecución). Sin la
    columna TIPO_CAMBIO_DETECTADO, retorna el DataFrame sin cambios.
    """
    if "TIPO_CAMBIO_DETECTADO" not in df.columns:
        return df
    clave = ["CODIGO_SNIES_DEL_PROGRAMA", "FECHA_DE_OBTENCION"]
    orden = list(df.columns)
    base = df.drop(columns=["TIPO_CAMBIO_DETECTADO", "CAMBIO_DETECTADO"])
    textos = {}
    for k, grupo in df.groupby(clave, sort=False, dropna=False):
        cambios = []
        for tipo, valor in zip(grupo["TIPO_CAMBIO_DETECTADO"], grupo["CAMBIO_DETECTADO"]):
            anterior, _, nuevo = str(valor).partition(FLECHA_CAMBIO)
            cambios.append((tipo, anterior, nuevo))
        textos[k] = unir_cambios(cambios)
    unico = base.drop_duplicates(clave, keep="first").copy()
    unico["CAMBIO_DETECTADO"] = [textos[k] for k in zip(unico[clave[0]], unico[clave[1]])]
    columnas = [c for c in orden if c != "TIPO_CAMBIO_DETECTADO"]
    return unico[columnas].reset_index(drop=True)
