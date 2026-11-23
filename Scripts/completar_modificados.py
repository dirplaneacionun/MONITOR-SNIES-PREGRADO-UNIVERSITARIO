"""
completar_modificados.py
------------------------
Completa Novedades/Modificados.parquet con los cambios que el histórico contiene y la tabla no registró.

Cada ejecución semanal compara el histórico de la ejecución actual con el de la anterior (run_snies.detectar_novedades).
Esta herramienta repite esa comparación para cada par de ejecuciones consecutivas del histórico y agrega a
Modificados los cambios que la tabla no tiene. Nunca elimina ni modifica un registro existente: solo agrega los
faltantes, identificados por (código SNIES, tipo de cambio, cambio detectado) dentro de cada ejecución.

Punto de partida (--desde, 2026-04-24 por defecto): desde esa ejecución, los programas nuevos e inactivos registrados
coinciden exactamente con los que da el histórico, de modo que sus pares de ejecuciones son comparables. Las
ejecuciones anteriores provienen de descargas espaciadas y no comparables entre sí.

Es idempotente: una segunda ejecución no encuentra faltantes. Los datos del Dashboard (docs/data) se regeneran
después con generar_dashboard.py.

Ejecución:
    python Scripts/completar_modificados.py --simular    # informa los faltantes; no escribe ningún archivo
    python Scripts/completar_modificados.py              # agrega los faltantes a Novedades/Modificados.parquet
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import historico  # noqa: E402
from run_snies import COL_FECHA, COL_SNIES, MODIFICADOS_FILE, _escribir_parquet, detectar_novedades  # noqa: E402

DESDE_DEFECTO = dt.date(2026, 4, 24)
TIPO, CAMBIO = "TIPO_CAMBIO_DETECTADO", "CAMBIO_DETECTADO"


def _claves(df: pd.DataFrame) -> list[tuple]:
    """Identificador de cada registro de cambio: (código SNIES, tipo de cambio, cambio detectado)."""
    return list(zip(df[COL_SNIES].astype("int64"), df[TIPO], df[CAMBIO]))


def faltantes_de_la_ejecucion(hoy: pd.DataFrame, anterior: pd.DataFrame, fecha: dt.date,
                              registrados: pd.DataFrame) -> pd.DataFrame:
    """
    Cambios de la ejecución `fecha` (comparada con la anterior) que no están en `registrados`, los registros de
    Modificados de esa ejecución. Vacío si la tabla ya los tiene todos.
    """
    _, recalculados, _ = detectar_novedades(hoy, anterior, fecha)
    if recalculados.empty:
        return recalculados
    ya = set(_claves(registrados)) if len(registrados) else set()
    return recalculados[[k not in ya for k in _claves(recalculados)]]


def incorporar(existente: pd.DataFrame, faltantes: pd.DataFrame, esquema) -> pd.DataFrame:
    """
    Agrega `faltantes` a `existente` con las columnas y los tipos del archivo de la tabla (`esquema`), ordenada por
    fecha y código SNIES como el resto de los acumulados (los registros de la misma fecha y código conservan su orden
    relativo).
    """
    nuevos = historico.alinear(faltantes.reindex(columns=existente.columns), esquema)
    combinado = pd.concat([existente, nuevos], ignore_index=True)
    return combinado.sort_values([COL_FECHA, COL_SNIES], kind="stable", ignore_index=True)


def completar(simular: bool = False, desde: dt.date = DESDE_DEFECTO, path: Path = MODIFICADOS_FILE) -> pd.DataFrame:
    """Calcula los cambios faltantes desde `desde` y, salvo en simulación, los agrega a `path`. Retorna los faltantes."""
    existente = pd.read_parquet(path)
    existente[COL_FECHA] = pd.to_datetime(existente[COL_FECHA])
    fechas = historico.fechas()
    partes, anterior = [], None
    for i, fecha in enumerate(fechas):
        hoy = historico.leer(fecha=fecha).drop(columns=[COL_FECHA])
        if i > 0 and fecha >= desde:
            registrados = existente[existente[COL_FECHA] == pd.Timestamp(fecha)]
            falta = faltantes_de_la_ejecucion(hoy, anterior, fecha, registrados)
            if len(falta):
                partes.append(falta)
        anterior = hoy
    faltantes = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=existente.columns)
    if len(faltantes) and not simular:
        combinado = incorporar(existente, faltantes, pq.read_schema(path))
        if len(combinado) != len(existente) + len(faltantes) or combinado[[COL_FECHA, COL_SNIES, TIPO, CAMBIO]].duplicated().any():
            raise RuntimeError("La tabla resultante no coincide con lo esperado: no se escribió ningún archivo.")
        _escribir_parquet(combinado, path)
    return faltantes


def main(argv=None) -> int:
    """Punto de entrada: informa (o agrega) los cambios faltantes; retorna 0 si terminó correctamente."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Completa Novedades/Modificados.parquet con los cambios que faltan.")
    parser.add_argument("--simular", action="store_true", help="Solo informa los faltantes; no escribe ningún archivo.")
    parser.add_argument("--desde", type=dt.date.fromisoformat, default=DESDE_DEFECTO,
                        help="Primera ejecución a revisar, AAAA-MM-DD (por defecto %(default)s).")
    args = parser.parse_args(argv)
    logging.getLogger("snies_monitor").setLevel(logging.CRITICAL)   # las advertencias de la detección ya se registraron en su día
    faltantes = completar(args.simular, args.desde)
    if faltantes.empty:
        print("Modificados está completo: no faltan registros.")
        return 0
    print(f"Faltan {len(faltantes)} registro(s) de cambio en {faltantes[COL_FECHA].nunique()} ejecución(es):")
    for (fecha, tipo), n in faltantes.groupby([faltantes[COL_FECHA].dt.date, TIPO]).size().items():
        print(f"  {fecha.isoformat()} · {tipo}: {n}")
    print("SIMULACIÓN: no se escribió ningún archivo." if args.simular else f"Agregados a {MODIFICADOS_FILE.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
