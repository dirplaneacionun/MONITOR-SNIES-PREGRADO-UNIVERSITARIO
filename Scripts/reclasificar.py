"""
reclasificar.py
---------------
Reclasificación de divisiones UniNorte tras ajustar reglas u overrides de
CLASIFICAR-DIVISIONES-UNINORTE-SNIES (Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Scripts/diccionarios.py).

Actualiza DIVISION_UNINORTE y PUNTAJE_TOTAL en los archivos que
alimentan el Dashboard:
    Historico/Historico Programas Activos AAAA.parquet (todas las ejecuciones, un archivo por año)
    Novedades/Nuevos.parquet, Novedades/Modificados.parquet, Novedades/Inactivos.parquet

Solo se reescriben los archivos en los que cambia alguna clasificación, para que el repositorio
no almacene de nuevo archivos sin cambios.

Método: se arma un catálogo con las combinaciones únicas de (código SNIES, nombre, NBC,
CINE detallado) de los cuatro archivos y se clasifica una sola vez con el clasificador,
ejecutado en una copia temporal: la retroalimentación real del clasificador no cambia.

Memoria: el histórico se procesa un año a la vez (primero para armar el catálogo y luego para aplicar la
clasificación), de modo que la memoria necesaria depende del tamaño de un año y no de cuántos años acumule
la herramienta.

Resumen: al terminar se registra, por archivo, cuántos registros cambian de división y las transiciones
principales (programas del histórico). En GitHub Actions el resumen se publica además en la página de la
ejecución (GITHUB_STEP_SUMMARY).

Ejecución:
    python Scripts/reclasificar.py              # reclasifica y reescribe los archivos con cambios
    python Scripts/reclasificar.py --simular    # solo informa qué cambiaría; no escribe ningún archivo

En GitHub: Actions → "Reclasificar divisiones" → Run workflow (.github/workflows/reclasificar.yml).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import tempfile
from collections import Counter
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


def armar_catalogo(fuentes) -> tuple[pd.DataFrame, int]:
    """
    Combinaciones únicas de CLAVE de todas las fuentes, cada una con su registro más reciente (mayor fecha y, en
    la misma fecha, la última fuente). `fuentes` entrega (orden, DataFrame) de a una, para no tener todo el
    histórico en memoria a la vez. Retorna (catálogo con la columna _K, registros revisados).
    """
    partes, columnas, total = [], {}, 0
    for orden, df in fuentes:
        if df.empty:
            continue
        total += len(df)
        base = [c for c in df.columns if c not in COLS_NO_BASE]
        columnas.update(dict.fromkeys(base))
        parte = df[base].assign(_K=claves(df), _F=pd.to_datetime(df[COL_FECHA]).values, _S=orden)
        partes.append(parte.sort_values("_F", kind="stable").drop_duplicates("_K", keep="last"))
    combinado = pd.concat(partes, ignore_index=True).sort_values(["_F", "_S"], kind="stable")
    catalogo = combinado.drop_duplicates("_K", keep="last")
    return catalogo[list(columnas) + ["_K"]], total


def clasificar_catalogo(catalogo: pd.DataFrame, registros: int) -> pd.DataFrame:
    """
    Clasifica el catálogo (ver armar_catalogo) con CLASIFICAR-DIVISIONES-UNINORTE-SNIES en una copia temporal.
    Retorna un DataFrame indexado por la clave con DIVISION_UNINORTE y PUNTAJE_TOTAL.
    """
    log.info(f"  Catálogo: {len(catalogo):,} combinaciones únicas de {registros:,} registros "
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


def resumen_cambios(antes: pd.DataFrame, despues: pd.DataFrame, anterior: dict | None = None) -> dict:
    """
    Cambios de DIVISION_UNINORTE entre dos versiones de un archivo (mismas filas, mismo orden): registros que
    cambian, programas distintos afectados y transiciones "anterior → nueva" contadas por programa. Con
    `anterior` (el resumen de otro año del mismo conjunto) acumula ambos, sin contar dos veces un programa.
    """
    previa = (antes["DIVISION_UNINORTE"] if "DIVISION_UNINORTE" in antes.columns
              else pd.Series([None] * len(antes))).map(_valor).reset_index(drop=True)
    nueva = despues["DIVISION_UNINORTE"].map(_valor).reset_index(drop=True)
    cambia = previa != nueva
    snies = despues[COL_SNIES].reset_index(drop=True)[cambia]
    pares = set(zip(snies, previa[cambia], nueva[cambia]))
    programas = set(snies)
    registros, cambian = len(despues), int(cambia.sum())
    if anterior:
        pares |= anterior["_pares"]; programas |= anterior["_programas"]
        registros += anterior["registros"]; cambian += anterior["cambian"]
    transiciones = Counter(f"{a or 'SIN DIVISIÓN'} → {b or 'SIN DIVISIÓN'}" for _, a, b in pares)
    return {"registros": registros, "cambian": cambian, "programas": len(programas), "transiciones": transiciones,
            "_pares": pares, "_programas": programas}


def publicar_resumen(resumen: dict[Path, dict], simular: bool) -> None:
    """Registra el resumen en el log y, en GitHub Actions, en la página de la ejecución (GITHUB_STEP_SUMMARY)."""
    hay_cambios = any(r["cambian"] for r in resumen.values())
    modo = "SIMULACIÓN (no se escribió ningún archivo)" if simular else "CAMBIOS APLICADOS"
    lineas = [f"## Reclasificación de divisiones · {modo if hay_cambios else 'SIN CAMBIOS'}", "",
              "| Archivo | Registros | Cambian de división | Programas afectados |", "|---|---:|---:|---:|"]
    for p, r in resumen.items():
        lineas.append(f"| {_ruta(p)} | {r['registros']:,} | {r['cambian']:,} | {r['programas']:,} |")
    transiciones = resumen.get(historico.HISTORICO_DIR, {}).get("transiciones", Counter())
    if transiciones:
        lineas += ["", "### Transiciones principales (programas del histórico)", "",
                   "| División anterior → nueva | Programas |", "|---|---:|"]
        lineas += [f"| {t} | {n:,} |" for t, n in transiciones.most_common(15)]
    if not hay_cambios:
        lineas += ["", "Ninguna división cambió con las reglas actuales: no hay nada que publicar."]
    for linea in lineas:
        if linea.startswith("|") and not linea.startswith("|---"):
            log.info("  " + linea)
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")


def reclasificar(simular: bool = False) -> dict[Path, dict]:
    """
    Reclasifica el histórico y las novedades con las reglas vigentes del clasificador. Con `simular` solo calcula
    y registra los cambios. Retorna el resumen por archivo (ver resumen_cambios).
    """
    # Unidades de trabajo: cada año del histórico (leído por separado) y cada archivo de novedades. El resumen
    # agrupa los años bajo la clave HISTORICO_DIR.
    unidades = [(historico.HISTORICO_DIR, historico.ruta(anio), lambda a=anio: historico.leer_anio(a))
                for anio, _ in historico.archivos()]
    unidades += [(p, p, lambda p=p: pd.read_parquet(p)) for p in (NUEVOS_FILE, MODIFICADOS_FILE, INACTIVOS_FILE)
                 if p.exists()]
    if not unidades:
        raise EtapaError("LECTURA", "No hay archivos para reclasificar.")

    _seccion("1. CATALOGO (lectura de un archivo a la vez)")
    catalogo, registros = armar_catalogo((i, leer()) for i, (_, _, leer) in enumerate(unidades))
    log.info(f"  {len(unidades)} archivo(s) revisados: {registros:,} registros")

    _seccion("2. CLASIFICACION DEL CATALOGO")
    mapa = clasificar_catalogo(catalogo, registros)

    _seccion("3. SIMULACION" if simular else "3. ACTUALIZACION")
    sin_cambios = 0
    resumen = {}
    for grupo, path, leer in unidades:
        d = leer()
        nuevo = aplicar_clasificacion(d, mapa)
        parcial = resumen_cambios(d, nuevo)
        resumen[grupo] = resumen_cambios(d, nuevo, resumen.get(grupo))
        cambian = parcial["cambian"]
        # Cualquier diferencia de clasificación o de columnas obliga a reescribir; si no hay, el archivo se conserva
        difiere = list(d.columns) != list(nuevo.columns) or any(
            not d[c].map(_valor).reset_index(drop=True).equals(nuevo[c].map(_valor).reset_index(drop=True))
            for c in COLS_CLASIFICACION
        )
        if not difiere:
            sin_cambios += 1
            continue
        if simular:
            log.info(f"  {_ruta(path)}: {cambian:,} registro(s) cambiarían de división (simulación: no se escribe)")
            continue
        if grupo == historico.HISTORICO_DIR:   # un grupo de filas por ejecución, en el archivo de su año
            historico.reescribir(nuevo)
        else:
            _escribir_parquet(nuevo, path)
        log.info(f"  {_ruta(path)}: {cambian:,} registro(s) cambian de división")
    log.info(f"  Archivos sin cambios (no se reescriben): {sin_cambios} de {len(unidades)}")
    _seccion("4. RESUMEN")
    publicar_resumen(resumen, simular)
    return resumen


def main(argv=None) -> int:
    """Punto de entrada: reclasifica (o simula) y retorna el código de salida (0 = correcto, 1 = falla)."""
    parser = argparse.ArgumentParser(description="Reclasificación de divisiones UniNorte del histórico y las novedades.")
    parser.add_argument("--simular", action="store_true", help="Solo informa qué cambiaría; no escribe ningún archivo.")
    args = parser.parse_args(argv)
    ahora = dt.datetime.now(ZONA_HORARIA)
    log_path = configurar_logging(ahora, prefijo="reclasificacion")
    log.info("RECLASIFICACION DE DIVISIONES UNINORTE" + (" · SIMULACION" if args.simular else ""))
    log.info(f"Log: {_ruta(log_path)}")
    try:
        reclasificar(args.simular)
    except Exception as exc:
        log.exception(f"RECLASIFICACION FALLIDA: {exc}")
        return 1
    log.info("RECLASIFICACION COMPLETADA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
