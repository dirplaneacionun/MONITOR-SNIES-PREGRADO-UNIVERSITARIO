"""
generar_dashboard.py
--------------------
Genera los datos del Dashboard a partir de:
    Historico/Historico Programas Activos AAAA.parquet (un archivo por año, leídos en conjunto)
    Novedades/Nuevos.parquet, Modificados.parquet, Inactivos.parquet

Salidas:
    docs/data/*.js                       datos compactos de gráficas, filtros y tablas (window.SNIES.*)
    docs/*.html                          páginas regeneradas con generar_paginas.py (sufijos de versión
                                         actualizados según el contenido de estilos, scripts y datos)

La lógica de las páginas (docs/assets/) es estática. Los datos se escriben como scripts para que las
páginas funcionen en GitHub Pages. El detalle de programas activos por ejecución no se publica en el
Dashboard: se consulta en los archivos por año de Historico/.

Ejecución:
    python Scripts/generar_dashboard.py
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import historico  # noqa: E402
from catalogos import (  # noqa: E402
    GRUPOS_UNIVERSIDAD, MAYOR_INTERES, MODALIDAD_A_GRUPO, MODALIDADES_GRUPO, TIPOS_CAMBIO,
    TIPOS_CAMBIO_ETIQUETA, UNINORTE,
)

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
NOVEDADES_DIR = ROOT / "Novedades"

COL_SNIES = "CODIGO_SNIES_DEL_PROGRAMA"
COL_FECHA = "FECHA_DE_OBTENCION"
COL_CINE = "CINE_F_2013_AC_CAMPO_DETALLADO"
CINE_POR_DEFECTO = 5

# Atributos de las "versiones" de programa usadas en las gráficas de evolución de activos
COLS_VERSION = [
    COL_SNIES, "NOMBRE_DEL_PROGRAMA", "NOMBRE_INSTITUCION", "CODIGO_INSTITUCION", "SECTOR",
    "DEPARTAMENTO_OFERTA_PROGRAMA", "MODALIDAD", COL_CINE, "DIVISION_UNINORTE", "NUMERO_PERIODOS_DE_DURACION",
]
# Columnas del histórico que usa el Dashboard: las de las versiones de programas, la fecha y el municipio (para
# unificar sus variantes en las novedades). Leer solo estas columnas reduce a cerca de un tercio la memoria del
# proceso, que crece con cada ejecución acumulada.
COLS_HISTORICO = COLS_VERSION + [COL_FECHA, "MUNICIPIO_OFERTA_PROGRAMA"]

COLS_PRIMERO = {
    "nuevos": [COL_FECHA, "TIPO_NOVEDAD"],
    "modificados": [COL_FECHA, "TIPO_CAMBIO_DETECTADO", "CAMBIO_DETECTADO"],
    "inactivos": [COL_FECHA, "REACTIVADO_POSTERIORMENTE"],
    "activos": [COL_FECHA],
}


# ── Codificación compacta ─────────────────────────────────────────────────────

def _valor_json(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, float):
        if math.isnan(v):
            return None
        return int(v) if v.is_integer() else round(v, 6)
    if isinstance(v, (int, str, bool)):
        return v
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        return _valor_json(v.item())
    return str(v)


def codificar(df: pd.DataFrame) -> dict:
    """Tabla columnar; las columnas con valores repetidos se codifican con diccionario."""
    columnas, datos = list(df.columns), {}
    for col in columnas:
        valores = [_valor_json(v) for v in df[col].tolist()]
        unicos = list(dict.fromkeys(valores))
        if len(unicos) < max(2, len(valores) // 2):
            pos = {u: i for i, u in enumerate(unicos)}
            datos[col] = {"d": unicos, "i": [pos[v] for v in valores]}
        else:
            datos[col] = valores
    return {"columnas": columnas, "n": len(df), "datos": datos}


def escribir_js(nombre: str, clave: str, contenido) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / nombre
    texto = json.dumps(contenido, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    path.write_text(
        f"window.SNIES=window.SNIES||{{}};window.SNIES[{json.dumps(clave)}]={texto};\n", encoding="utf-8"
    )
    print(f"  {path.relative_to(ROOT)}: {path.stat().st_size / 1e6:.2f} MB")
    return path


def _ordenar_columnas(df: pd.DataFrame, tipo: str) -> pd.DataFrame:
    primero = [c for c in COLS_PRIMERO[tipo] if c in df.columns]
    return df[primero + [c for c in df.columns if c not in primero]]


def _rangos(indices: list[int]) -> list[list[int]]:
    indices = [int(i) for i in indices]
    rangos, ini, fin = [], indices[0], indices[0]
    for i in indices[1:]:
        if i == fin + 1:
            fin = i
        else:
            rangos.append([ini, fin])
            ini = fin = i
    rangos.append([ini, fin])
    return rangos


def nombres_mayor_interes(hist: pd.DataFrame, novedades: dict) -> dict[str, str]:
    """Nombre vigente (el más reciente registrado) de cada universidad de mayor interés, para el filtro."""
    cols = ["CODIGO_INSTITUCION", "NOMBRE_INSTITUCION", COL_FECHA]
    todo = pd.concat([hist[cols]] + [d[cols] for d in novedades.values() if set(cols) <= set(d.columns)])
    ultimo = todo.sort_values(COL_FECHA).groupby("CODIGO_INSTITUCION")["NOMBRE_INSTITUCION"].last()
    return {str(c): str(ultimo.get(c, f"INSTITUCIÓN {c}")).rstrip("- ").strip() for c in MAYOR_INTERES}


# ── Generación ────────────────────────────────────────────────────────────────

def main() -> int:
    print("Generando datos del Dashboard...")
    if not historico.existe():
        print(f"  ERROR: no hay archivos del histórico en {historico.HISTORICO_DIR}")
        return 1

    hist = historico.leer(COLS_HISTORICO)
    hist[COL_FECHA] = pd.to_datetime(hist[COL_FECHA]).dt.normalize()
    novedades = {}
    for tipo in ("nuevos", "modificados", "inactivos"):
        path = NOVEDADES_DIR / f"{tipo.capitalize()}.parquet"
        df = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=[COL_SNIES, COL_FECHA])
        df[COL_FECHA] = pd.to_datetime(df[COL_FECHA]).dt.normalize()
        novedades[tipo] = df.sort_values([COL_FECHA, COL_SNIES], ascending=[False, True], ignore_index=True)

    # Departamentos y municipios que solo difieren en puntuación (ej. "BOGOTA D.C." en 2024) → forma más frecuente
    for col in ("DEPARTAMENTO_OFERTA_PROGRAMA", "MUNICIPIO_OFERTA_PROGRAMA"):
        conteo = pd.concat([hist[col]] + [d[col] for d in novedades.values() if col in d]).value_counts()
        canonica = {}
        for valor in conteo.index:  # de más a menos frecuente
            canonica.setdefault(re.sub(r"[^A-Z0-9Ñ]", "", str(valor)), valor)
        mapa = {v: canonica[re.sub(r"[^A-Z0-9Ñ]", "", str(v))] for v in conteo.index}
        cambios = {v: c for v, c in mapa.items() if v != c}
        if cambios:
            print(f"  {col}: variantes unificadas {cambios}")
        for df in [hist, *novedades.values()]:
            if col in df:
                df[col] = df[col].map(lambda v: mapa.get(v, v))

    sin_grupo = sorted(set(hist["MODALIDAD"].dropna()) - set(MODALIDAD_A_GRUPO))
    if sin_grupo:
        print(f"  AVISO: modalidades sin agrupación (se muestran como SIN INFORMACIÓN): {sin_grupo}")

    # Fechas
    fechas_activos = sorted(hist[COL_FECHA].unique())
    fechas_ejecucion = sorted(set(fechas_activos).union(*(set(d[COL_FECHA]) for d in novedades.values())))
    ultima = max(fechas_ejecucion)
    iso = lambda f: pd.Timestamp(f).strftime("%Y-%m-%d")  # noqa: E731

    # Inactivos que luego se reactivaron
    nue, ina = novedades["nuevos"], novedades["inactivos"]
    reactivados = nue[nue.get("TIPO_NOVEDAD", pd.Series(dtype=str)) == "REACTIVADO"][[COL_SNIES, COL_FECHA]]
    def _reactivado(row):
        f = reactivados.loc[(reactivados[COL_SNIES] == row[COL_SNIES]) & (reactivados[COL_FECHA] > row[COL_FECHA]), COL_FECHA]
        return f.min() if not f.empty else pd.NaT
    ina["REACTIVADO_POSTERIORMENTE"] = ina.apply(_reactivado, axis=1) if not ina.empty else pd.NaT

    # KPIs de la última ejecución
    def _en_ultima(df):
        return df[df[COL_FECHA] == ultima]
    mod_u = _en_ultima(novedades["modificados"])
    ult_activos = hist[hist[COL_FECHA] == fechas_activos[-1]]
    kpis = {
        "activos": int(len(ult_activos)),
        "fecha_activos": iso(fechas_activos[-1]),
        "nuevos": int(len(_en_ultima(nue))),
        "modificados": int(len(mod_u)),
        "modificados_programas": int(mod_u[COL_SNIES].nunique()) if not mod_u.empty else 0,
        "inactivos": int(len(_en_ultima(ina))),
    }

    # Campos CINE con mayor presencia en los programas activos (selección por defecto)
    cine_top = ult_activos[COL_CINE].value_counts().head(CINE_POR_DEFECTO).index.tolist()
    cine_orden = ult_activos[COL_CINE].value_counts().index.tolist()

    comun = {
        "generado": dt.datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M"),
        "ultima_ejecucion": iso(ultima),
        "fechas_ejecucion": [iso(f) for f in fechas_ejecucion],
        "fechas_activos": [iso(f) for f in fechas_activos],
        "kpis": kpis,
        "cine_por_defecto": cine_top,
        "cine_orden": cine_orden,
        "catalogos": {
            "uninorte": UNINORTE,
            "mayor_interes": list(MAYOR_INTERES),
            "mayor_interes_nombres": nombres_mayor_interes(hist, novedades),
            "grupos_universidad": list(GRUPOS_UNIVERSIDAD),
            "modalidad_a_grupo": MODALIDAD_A_GRUPO,
            "grupos_modalidad": list(MODALIDADES_GRUPO),
            "tipos_cambio": list(TIPOS_CAMBIO),
            "tipos_cambio_etiqueta": TIPOS_CAMBIO_ETIQUETA,
        },
    }
    escribir_js("comun.js", "comun", comun)

    # Versiones de programas activos con sus rangos de fechas (índices de fechas_activos)
    indice = {f: i for i, f in enumerate(fechas_activos)}
    h = hist[COLS_VERSION + [COL_FECHA]].copy()
    h["_fi"] = h[COL_FECHA].map(indice)
    h["_k"] = h.groupby(COLS_VERSION, dropna=False, sort=False).ngroup()
    grupos = h.groupby("_k")
    versiones = grupos[COLS_VERSION].first().reset_index(drop=True)
    versiones["_RANGOS"] = grupos["_fi"].apply(lambda s: _rangos(sorted(s.unique()))).reset_index(drop=True)
    escribir_js("activos.js", "activos", {
        "versiones": codificar(versiones.drop(columns="_RANGOS")),
        "rangos": versiones["_RANGOS"].tolist(),
    })

    escribir_js("comun.js", "comun", comun)
    # Datos de versiones anteriores del Dashboard que ya no se publican (la tabla de programas activos de Inicio
    # y sus copias del histórico por ejecución)
    for obsoleto in ("activos_tabla.js", "historico.parquet"):
        (DATA_DIR / obsoleto).unlink(missing_ok=True)
    if (DATA_DIR / "historico").is_dir():
        shutil.rmtree(DATA_DIR / "historico")
        print("  docs/data/historico/: eliminado (el detalle por ejecución está en Historico/)")

    for tipo, df in novedades.items():
        escribir_js(f"{tipo}.js", tipo, codificar(_ordenar_columnas(df, tipo)))

    from generar_paginas import main as generar_paginas
    generar_paginas()

    print(f"  Última ejecución: {iso(ultima)} · KPIs: {kpis}")
    print("Dashboard generado OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
