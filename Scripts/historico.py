"""
historico.py
------------
Acceso al histórico de programas activos, guardado en un único archivo Parquet:
    Historico/Historico Programas Activos.parquet

Cada ejecución ocupa su propio grupo de filas (row group) dentro del archivo, en orden de fecha. Al agregar una
ejecución, los grupos anteriores se vuelven a escribir con los mismos bytes y Git almacena solo la diferencia
(alrededor de 0,2 MB por ejecución) en lugar de una copia completa del archivo. Solo una reclasificación que
modifique divisiones antiguas reescribe el contenido de grupos anteriores.

Tipos de datos: el reporte del SNIES no siempre entrega una columna con el mismo tipo (por ejemplo,
NUMERO_CREDITOS llega como número en una descarga y como texto en otra, y una columna sin datos llega sin
tipo). Cada ejecución nueva adopta el esquema del archivo (alinear) y los números guardados como texto usan
una sola representación ("10", no "10.0"), para que las comparaciones entre ejecuciones sean exactas.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

HISTORICO_DIR = Path(__file__).resolve().parent.parent / "Historico"
NOMBRE = "Historico Programas Activos.parquet"
COL_FECHA = "FECHA_DE_OBTENCION"
COMPRESION = "snappy"   # compatible con pandas, Power BI, Excel (Power Query) y la mayoría de herramientas
_ENTERO_DECIMAL = re.compile(r"^-?\d+\.0+$")


def ruta() -> Path:
    """Archivo del histórico."""
    return HISTORICO_DIR / NOMBRE


def existe() -> bool:
    return ruta().exists()


def esquema() -> pa.Schema | None:
    """Esquema del histórico (None si aún no existe)."""
    return pq.read_schema(ruta()) if existe() else None


def fechas() -> list[dt.date]:
    """Fechas de ejecución presentes en el histórico, en orden."""
    if not existe():
        return []
    columna = pq.read_table(ruta(), columns=[COL_FECHA]).column(COL_FECHA).to_pandas()
    return sorted({pd.Timestamp(v).date() for v in columna.dropna().unique()})


def _texto(v):
    """Representación textual única de un valor: 180.0 y "180.0" → "180"; vacíos se conservan como nulos."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    texto = str(v)
    return texto.split(".")[0] if _ENTERO_DECIMAL.match(texto) else texto


def alinear(df: pd.DataFrame, esquema_ref: pa.Schema | None) -> pd.DataFrame:
    """
    Ajusta los tipos de `df` a `esquema_ref`: columnas de texto → texto con representación única, columnas de
    fecha → fecha con la misma precisión y columnas numéricas → número. Las conversiones a fecha y a número
    se aplican solo si todos los valores se pueden convertir; si alguno no, la columna no se modifica para no
    perder datos (la escritura posterior falla y la ejecución se detiene con alerta técnica). Las columnas que
    no están en el esquema no cambian.
    """
    if esquema_ref is None:
        return df
    df = df.copy()
    for campo in esquema_ref:
        c, tipo = campo.name, campo.type
        if c not in df.columns:
            continue
        if pa.types.is_string(tipo) or pa.types.is_large_string(tipo):
            df[c] = df[c].map(_texto).astype("str")
        elif pa.types.is_timestamp(tipo):
            fechas_c = pd.to_datetime(df[c], errors="coerce")
            if (fechas_c.isna() == df[c].isna()).all():
                df[c] = fechas_c.astype(f"datetime64[{tipo.unit}]")
        elif pa.types.is_null(tipo):   # columna sin datos en el archivo: se conserva sin tipo si sigue vacía
            if df[c].isna().all():
                df[c] = pd.Series([None] * len(df), index=df.index, dtype=object)
        elif pa.types.is_floating(tipo) or pa.types.is_integer(tipo):
            numeros = pd.to_numeric(df[c], errors="coerce")
            if (numeros.isna() == df[c].isna()).all():
                df[c] = numeros.astype("float64" if pa.types.is_floating(tipo) else "Int64")
    return df


def leer(columnas: list[str] | None = None, antes_de: dt.date | None = None,
         fecha: dt.date | None = None) -> pd.DataFrame:
    """
    Histórico (todas las ejecuciones, las anteriores a `antes_de` o solo la de `fecha`). Con `columnas`, lee solo
    esas columnas; una columna que no existe en el histórico se entrega vacía.
    """
    if not existe():
        return pd.DataFrame(columns=columnas or [])
    nombres = esquema().names
    leidas = None if columnas is None else [c for c in columnas if c in nombres]
    filtros = []
    if antes_de is not None:
        filtros.append((COL_FECHA, "<", pd.Timestamp(antes_de)))
    if fecha is not None:
        filtros.append((COL_FECHA, "==", pd.Timestamp(fecha)))
    df = pq.read_table(ruta(), columns=leidas, filters=filtros or None).to_pandas()
    return df.reindex(columns=columnas) if columnas is not None else df


def ejecucion_anterior(fecha: dt.date) -> tuple[dt.date, pd.DataFrame] | None:
    """Ejecución más reciente con fecha estrictamente anterior a `fecha`: (fecha, programas sin la columna de fecha)."""
    previas = [f for f in fechas() if f < fecha]
    if not previas:
        return None
    return previas[-1], leer(fecha=previas[-1]).drop(columns=[COL_FECHA])


def _escribir(grupos: list[pa.Table], esquema_final: pa.Schema) -> None:
    """Escribe un grupo de filas por ejecución, en el orden recibido, de forma atómica."""
    HISTORICO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ruta().with_suffix(".tmp.parquet")
    with pq.ParquetWriter(tmp, esquema_final, compression=COMPRESION) as escritor:
        for grupo in grupos:
            escritor.write_table(grupo)
    os.replace(tmp, ruta())


def _fecha_grupo(tabla: pa.Table) -> dt.date:
    return pd.Timestamp(tabla.column(COL_FECHA)[0].as_py()).date()


def _ajustar(tabla: pa.Table, esquema_final: pa.Schema) -> pa.Table:
    """Tabla con las columnas y tipos de `esquema_final` (columnas faltantes como nulas)."""
    columnas = [tabla.column(f.name).cast(f.type) if f.name in tabla.column_names else pa.nulls(len(tabla), f.type)
                for f in esquema_final]
    return pa.Table.from_arrays(columnas, schema=esquema_final)


def guardar_ejecucion(df: pd.DataFrame, fecha: dt.date) -> int:
    """
    Agrega (o reemplaza, si se repite la fecha) los programas activos de la ejecución `fecha`. Los grupos de las
    demás ejecuciones se conservan sin cambios. Si la descarga trae columnas nuevas, se agregan al esquema y
    quedan vacías en las ejecuciones anteriores. Retorna el número de ejecuciones del histórico.
    """
    actual = esquema()
    nueva = pa.Table.from_pandas(alinear(df.assign(**{COL_FECHA: pd.Timestamp(fecha)}), actual), preserve_index=False)
    grupos = []
    if actual is not None:
        with open(ruta(), "rb") as fh:   # se cierra antes de reemplazar el archivo (requisito en Windows)
            archivo = pq.ParquetFile(fh)
            grupos = [archivo.read_row_group(i) for i in range(archivo.num_row_groups)]
        grupos = [g for g in grupos if _fecha_grupo(g) != fecha]
        extra = [f for f in nueva.schema if f.name not in actual.names]
        esquema_final = pa.schema(list(actual) + extra, metadata=actual.metadata) if extra else actual
        if extra:
            grupos = [_ajustar(g, esquema_final) for g in grupos]
    else:
        esquema_final = nueva.schema
    nueva = _ajustar(nueva, esquema_final)
    grupos.append(nueva)
    grupos.sort(key=_fecha_grupo)
    _escribir(grupos, esquema_final)
    return len(grupos)


def reescribir(df: pd.DataFrame) -> None:
    """Reescribe el histórico completo desde `df` (un grupo de filas por fecha), conservando el esquema vigente."""
    actual = esquema()
    df = alinear(df, actual)
    tablas = [pa.Table.from_pandas(parte.reset_index(drop=True), preserve_index=False)
              for _, parte in df.sort_values(COL_FECHA, kind="stable").groupby(COL_FECHA, sort=True)]
    if actual is None:
        esquema_final = tablas[0].schema
    else:   # columnas que no estaban en el esquema se agregan al final (nunca se descartan datos)
        extra = [f for f in tablas[0].schema if f.name not in actual.names]
        esquema_final = pa.schema(list(actual) + extra, metadata=actual.metadata) if extra else actual
    _escribir([_ajustar(t, esquema_final) for t in tablas], esquema_final)
