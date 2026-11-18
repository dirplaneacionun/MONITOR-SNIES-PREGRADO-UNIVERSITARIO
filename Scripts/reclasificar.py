"""
reclasificar.py
---------------
Reclasificación de divisiones UniNorte tras ajustar reglas u overrides de
CLASIFICAR-DIVISIONES-UNINORTE-SNIES (Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Scripts/diccionarios.py).

Actualiza DIVISION_UNINORTE y PUNTAJE_TOTAL en los archivos que
alimentan el Dashboard:
    Historico/Historico Programas Activos.parquet (todas las ejecuciones)
    Novedades/Nuevos.parquet, Novedades/Modificados.parquet, Novedades/Inactivos.parquet

Solo se reescriben los archivos en los que cambia alguna clasificación, para que el repositorio
no almacene de nuevo archivos sin cambios.

Método: se arma un catálogo con las combinaciones únicas de (código SNIES, nombre, NBC,
CINE detallado) de los cuatro archivos y se clasifica una sola vez con el clasificador,
ejecutado en una copia temporal: la retroalimentación real del clasificador no cambia.

Ejecución:
    python Scripts/reclasificar.py
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import historico  # noqa: E402
from run_snies import (  # noqa: E402
    COL_FECHA, COL_SNIES, COLS_CLASIFICACION, COLS_OBSOLETAS, INACTIVOS_FILE, MAP_DIR,
    MODIFICADOS_FILE, NUEVOS_FILE, ZONA_HORARIA, EtapaError, _ejecutar_herramienta,
    _escribir_parquet, _ruta, _seccion, _valor, configurar_logging, log,
)

CLAVE = [COL_SNIES, "NOMBRE_DEL_PROGRAMA", "NUCLEO_BASICO_DEL_CONOCIMIENTO", "CINE_F_2013_AC_CAMPO_DETALLADO"]
COLS_NO_BASE = set(COLS_CLASIFICACION) | set(COLS_OBSOLETAS) | {COL_FECHA, "TIPO_NOVEDAD", "TIPO_CAMBIO_DETECTADO", "CAMBIO_DETECTADO"}
_SEP = "\x1f"


def claves(df: pd.DataFrame) -> pd.Series:
    """Clave de clasificación de cada fila (valores normalizados como texto)."""
    partes = [df[c].map(_valor) for c in CLAVE]
    clave = partes[0]
    for p in partes[1:]:
        clave = clave + _SEP + p
    return clave


def clasificar_catalogo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clasifica las combinaciones únicas de CLAVE de `df` con CLASIFICAR-DIVISIONES-UNINORTE-SNIES en una copia
    temporal. Retorna un DataFrame indexado por la clave con DIVISION_UNINORTE y PUNTAJE_TOTAL.
    De cada combinación se usa su registro más reciente (último en `df`).
    """
    base = [c for c in df.columns if c not in COLS_NO_BASE]
    catalogo = df[base].assign(_K=claves(df)).drop_duplicates("_K", keep="last")
    log.info(f"  Catálogo: {len(catalogo):,} combinaciones únicas de {len(df):,} registros "
             f"({catalogo[COL_SNIES].nunique():,} códigos SNIES)")

    with tempfile.TemporaryDirectory(prefix="snies_clasificacion_") as tmp:
        sandbox = Path(tmp)
        shutil.copytree(MAP_DIR / "Scripts", sandbox / "Scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for carpeta in ("Fuentes", "Salidas", "Logs", "Revision", "Retroalimentacion"):
            (sandbox / carpeta).mkdir()
        shutil.copy2(MAP_DIR / "Fuentes" / "ProgramasUN.xlsx", sandbox / "Fuentes")
        shutil.copy2(MAP_DIR / "Retroalimentacion" / "retroalimentacion.json", sandbox / "Retroalimentacion")
        catalogo.drop(columns="_K").to_excel(sandbox / "Fuentes" / "Programas.xlsx", index=False)

        _ejecutar_herramienta(
            "CLASIFICACION (CLASIFICAR-DIVISIONES-UNINORTE-SNIES, copia temporal)", sandbox,
            [sys.executable, str(sandbox / "Scripts" / "pipeline_divisiones.py"), "-a", "1"],
            sandbox / "Logs", "pipeline_*.log",
        )
        salida = pd.read_excel(sandbox / "Salidas" / "Programas.xlsx", engine="calamine")
        # Lista de programas por revisar del catálogo, junto a la del flujo semanal
        revision = sandbox / "Revision" / "Revision Programas.xlsx"
        if revision.exists():
            destino = MAP_DIR / "Revision" / "Revision Catalogo Historico.xlsx"
            shutil.copy2(revision, destino)
            log.info(f"  Lista de revisión del catálogo: {_ruta(destino)}")

    if len(salida) != len(catalogo):
        raise EtapaError("CLASIFICACION", f"El clasificador devolvió {len(salida)} filas de {len(catalogo)}.")
    salida["_K"] = claves(salida)
    mapa = salida.drop_duplicates("_K").set_index("_K")[COLS_CLASIFICACION]
    faltan = set(catalogo["_K"]) - set(mapa.index)
    if faltan:
        raise EtapaError("CLASIFICACION", f"{len(faltan)} combinaciones sin clasificación en la salida.")
    return mapa


def aplicar_clasificacion(df: pd.DataFrame, mapa: pd.DataFrame) -> pd.DataFrame:
    """Reemplaza las columnas de clasificación de `df` según `mapa`, conservando el orden de columnas."""
    if df.empty:
        return df
    orden = list(df.columns)
    if not all(c in orden for c in COLS_CLASIFICACION):
        pos = orden.index(COL_FECHA) if COL_FECHA in orden else len(orden)
        orden = orden[:pos] + [c for c in COLS_CLASIFICACION if c not in orden] + orden[pos:]
    k = claves(df)
    resultado = df.drop(columns=[c for c in COLS_CLASIFICACION + COLS_OBSOLETAS if c in df.columns])
    for c in COLS_CLASIFICACION:
        resultado[c] = k.map(mapa[c]).values
    return resultado[[c for c in orden if c not in COLS_OBSOLETAS]]


def reclasificar() -> None:
    archivos = [p for p in (historico.ruta(), NUEVOS_FILE, MODIFICADOS_FILE, INACTIVOS_FILE) if p.exists()]
    if not archivos:
        raise EtapaError("LECTURA", "No hay archivos para reclasificar.")

    _seccion("1. LECTURA")
    datos = {}
    for p in archivos:
        datos[p] = pd.read_parquet(p)
        log.info(f"  {_ruta(p)}: {len(datos[p]):,} registros")

    _seccion("2. CLASIFICACION DEL CATALOGO")
    combinado = pd.concat(
        [d.sort_values(COL_FECHA) for d in datos.values()], ignore_index=True
    ).sort_values(COL_FECHA, kind="stable")
    mapa = clasificar_catalogo(combinado)

    _seccion("3. ACTUALIZACION")
    sin_cambios = 0
    for p, d in datos.items():
        nuevo = aplicar_clasificacion(d, mapa)
        if "DIVISION_UNINORTE" in d.columns:
            cambian = int((d["DIVISION_UNINORTE"].fillna("") != nuevo["DIVISION_UNINORTE"].fillna("")).sum())
        else:
            cambian = len(nuevo)
        # Cualquier diferencia de clasificación o de columnas obliga a reescribir; si no hay, el archivo se conserva
        difiere = list(d.columns) != list(nuevo.columns) or any(
            not d[c].map(_valor).reset_index(drop=True).equals(nuevo[c].map(_valor).reset_index(drop=True))
            for c in COLS_CLASIFICACION
        )
        if not difiere:
            sin_cambios += 1
            continue
        if p == historico.ruta():   # conserva el esquema y un grupo de filas por ejecución
            historico.reescribir(nuevo)
        else:
            _escribir_parquet(nuevo, p)
        log.info(f"  {_ruta(p)}: {cambian:,} registro(s) cambian de división")
    log.info(f"  Archivos sin cambios (no se reescriben): {sin_cambios} de {len(datos)}")


def main() -> int:
    ahora = dt.datetime.now(ZONA_HORARIA)
    log_path = configurar_logging(ahora, prefijo="reclasificacion")
    log.info("RECLASIFICACION DE DIVISIONES UNINORTE")
    log.info(f"Log: {_ruta(log_path)}")
    try:
        reclasificar()
    except Exception as exc:
        log.exception(f"RECLASIFICACION FALLIDA: {exc}")
        return 1
    log.info("RECLASIFICACION COMPLETADA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
