"""
completar_modificados.py
------------------------
Completa Novedades/Modificados.parquet con los cambios que el histórico contiene y la tabla no registró.

Cada ejecución semanal compara el histórico de la ejecución actual con el de la anterior (run_snies.detectar_novedades).
Esta herramienta repite esa comparación para cada par de ejecuciones consecutivas del histórico y agrega a
Modificados los cambios que la tabla no tiene. Nunca elimina un cambio registrado: solo agrega los faltantes, que se
identifican por programa, ejecución y tipo de cambio. Un programa sin registro en esa ejecución recibe uno nuevo; si ya
tiene uno con otros cambios, se le agregan los que faltan en su CAMBIO_DETECTADO (un registro por programa y ejecución).

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
from collections import Counter
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalogos  # noqa: E402
import historico  # noqa: E402
from run_snies import COL_FECHA, COL_SNIES, MODIFICADOS_FILE, _escribir_parquet, detectar_novedades  # noqa: E402

DESDE_DEFECTO = dt.date(2026, 4, 24)
CAMBIO = "CAMBIO_DETECTADO"
FALTAN = "_CAMBIOS_FALTANTES"   # columna auxiliar de los faltantes: los cambios que la tabla no tenía (mismo formato)


def _por_programa(df: pd.DataFrame) -> dict[int, list[tuple[str, str, str]]]:
    """Cambios registrados de cada programa en `df` (una ejecución): {código SNIES: [(tipo, anterior, nuevo)]}."""
    return {int(s): catalogos.separar_cambios(c) for s, c in zip(df[COL_SNIES].astype("int64"), df[CAMBIO])}


def faltantes_de_la_ejecucion(hoy: pd.DataFrame, anterior: pd.DataFrame, fecha: dt.date,
                              registrados: pd.DataFrame) -> pd.DataFrame:
    """
    Programas de la ejecución `fecha` (comparada con la anterior) con algún tipo de cambio que no está en `registrados`,
    los registros de Modificados de esa ejecución. Cada fila trae el CAMBIO_DETECTADO completo (los cambios ya
    registrados y los que faltan) y, en FALTAN, solo los que faltan. Vacío si la tabla ya los tiene todos.
    """
    _, recalculados, _ = detectar_novedades(hoy, anterior, fecha)
    if recalculados.empty:
        return recalculados
    ya = _por_programa(registrados) if len(registrados) else {}
    posiciones, completos, faltan = [], [], []
    for i, (snies, texto) in enumerate(zip(recalculados[COL_SNIES].astype("int64"), recalculados[CAMBIO])):
        registrados_del_programa = ya.get(int(snies), [])
        con_tipo = {c[0] for c in registrados_del_programa}
        nuevos = [c for c in catalogos.separar_cambios(texto) if c[0] not in con_tipo]
        if nuevos:
            posiciones.append(i)
            completos.append(catalogos.unir_cambios(registrados_del_programa + nuevos))
            faltan.append(catalogos.unir_cambios(nuevos))
    salida = recalculados.iloc[posiciones].copy()
    salida[CAMBIO] = completos
    salida[FALTAN] = faltan
    return salida


def incorporar(existente: pd.DataFrame, faltantes: pd.DataFrame, esquema) -> pd.DataFrame:
    """
    Agrega `faltantes` a `existente` con las columnas y los tipos del archivo de la tabla (`esquema`): el registro de un
    programa que ya tenía uno en esa ejecución se reemplaza por el completo, y los demás se agregan. Queda ordenada por
    fecha y código SNIES como el resto de los acumulados.
    """
    claves = set(zip(faltantes[COL_FECHA], faltantes[COL_SNIES].astype("int64")))
    quedan = existente[[k not in claves for k in zip(existente[COL_FECHA], existente[COL_SNIES].astype("int64"))]]
    nuevos = historico.alinear(faltantes.drop(columns=[FALTAN], errors="ignore").reindex(columns=existente.columns), esquema)
    combinado = pd.concat([quedan, nuevos], ignore_index=True)
    return combinado.sort_values([COL_FECHA, COL_SNIES], kind="stable", ignore_index=True)


def _verificar(existente: pd.DataFrame, combinado: pd.DataFrame, n_nuevos: int) -> None:
    """Falla si la tabla resultante repite un programa en una ejecución o pierde algún cambio ya registrado."""
    llave = [COL_FECHA, COL_SNIES]
    if len(combinado) != len(existente) + n_nuevos or combinado[llave].duplicated().any():
        raise RuntimeError("La tabla resultante no coincide con lo esperado: no se escribió ningún archivo.")
    por_clave = {(f, int(s)): set(catalogos.separar_cambios(c))
                 for f, s, c in zip(combinado[COL_FECHA], combinado[COL_SNIES].astype("int64"), combinado[CAMBIO])}
    for f, s, c in zip(existente[COL_FECHA], existente[COL_SNIES].astype("int64"), existente[CAMBIO]):
        if not set(catalogos.separar_cambios(c)) <= por_clave.get((f, int(s)), set()):
            raise RuntimeError("La tabla resultante pierde un cambio ya registrado: no se escribió ningún archivo.")


def completar(simular: bool = False, desde: dt.date = DESDE_DEFECTO, path: Path = MODIFICADOS_FILE) -> pd.DataFrame:
    """
    Calcula los cambios faltantes desde `desde` y, salvo en simulación, los agrega a `path`. Retorna un registro por
    programa y ejecución con cambios faltantes (ver faltantes_de_la_ejecucion).
    """
    existente = catalogos.consolidar_modificados(pd.read_parquet(path))
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
        esquema = pq.read_schema(path)
        if "TIPO_CAMBIO_DETECTADO" in esquema.names:   # archivo con el formato anterior (un cambio por fila)
            esquema = pa.schema([f for f in esquema if f.name != "TIPO_CAMBIO_DETECTADO"], metadata=esquema.metadata)
        combinado = incorporar(existente, faltantes, esquema)
        existentes = set(zip(existente[COL_FECHA], existente[COL_SNIES].astype("int64")))
        nuevos = sum(k not in existentes for k in zip(faltantes[COL_FECHA], faltantes[COL_SNIES].astype("int64")))
        _verificar(existente, combinado, nuevos)
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
    por_tipo = Counter((f.date(), tipo) for f, texto in zip(faltantes[COL_FECHA], faltantes[FALTAN])
                       for tipo in catalogos.tipos_de_cambio(texto))
    print(f"Faltan {sum(por_tipo.values())} cambio(s) en {len(faltantes)} programa(s) de "
          f"{faltantes[COL_FECHA].nunique()} ejecución(es):")
    for (fecha, tipo), n in sorted(por_tipo.items()):
        print(f"  {fecha.isoformat()} · {tipo}: {n}")
    print("SIMULACIÓN: no se escribió ningún archivo." if args.simular else f"Agregados a {MODIFICADOS_FILE.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
