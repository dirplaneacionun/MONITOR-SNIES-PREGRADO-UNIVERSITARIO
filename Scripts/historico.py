"""
historico.py
------------
Acceso al histórico de programas activos, guardado como un archivo Parquet por ejecución:
    Historico/Historico Programas Activos AAAA-MM-DD.parquet

Motivo de la partición: Git almacena completa cada versión de un archivo binario. Con un archivo único,
cada ejecución semanal guardaba de nuevo todo el histórico (más de 7 MB) en el repositorio; con un archivo
por fecha, cada ejecución agrega solo sus propios registros (alrededor de 0,35 MB) y los archivos anteriores
no cambian, salvo que una reclasificación modifique sus divisiones.

Leídos en conjunto con leer(), los archivos equivalen al histórico consolidado.

Tipos de datos: el reporte del SNIES no siempre entrega una columna con el mismo tipo (por ejemplo,
NUMERO_CREDITOS llega como número en una descarga y como texto en otra, y una columna sin datos llega sin
tipo). Para que todos los archivos compartan el mismo esquema, cada archivo nuevo adopta los tipos del archivo
anterior (alinear) y la lectura consolidada unifica cualquier diferencia restante (leer).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

HISTORICO_DIR = Path(__file__).resolve().parent.parent / "Historico"
PREFIJO = "Historico Programas Activos"
_ARCHIVO_RE = re.compile(rf"^{PREFIJO} (\d{{4}}-\d{{2}}-\d{{2}})\.parquet$")


def ruta(fecha: dt.date) -> Path:
    """Archivo del histórico correspondiente a la ejecución de `fecha`."""
    return HISTORICO_DIR / f"{PREFIJO} {fecha.isoformat()}.parquet"


def archivos(antes_de: dt.date | None = None) -> list[tuple[dt.date, Path]]:
    """Archivos del histórico ordenados por fecha; con `antes_de`, solo los de fechas estrictamente anteriores."""
    encontrados = []
    if HISTORICO_DIR.exists():
        for path in HISTORICO_DIR.glob(f"{PREFIJO} *.parquet"):
            m = _ARCHIVO_RE.match(path.name)
            if m:
                fecha = dt.date.fromisoformat(m.group(1))
                if antes_de is None or fecha < antes_de:
                    encontrados.append((fecha, path))
    return sorted(encontrados)


def fechas() -> set[dt.date]:
    """Fechas de ejecución presentes en el histórico."""
    return {fecha for fecha, _ in archivos()}


def _texto(v):
    """Representación textual de un valor, igual a la del histórico: 180.0 → "180"; vacíos se conservan."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def esquema_referencia(fecha: dt.date) -> pa.Schema | None:
    """Esquema del archivo más reciente del histórico distinto de `fecha` (None si no hay otro archivo)."""
    otros = [path for f, path in archivos() if f != fecha]
    return pq.read_schema(otros[-1]) if otros else None


def alinear(df: pd.DataFrame, esquema: pa.Schema | None) -> pd.DataFrame:
    """
    Ajusta los tipos de `df` a `esquema`: columnas de texto en el esquema → texto (números enteros sin ".0"),
    columnas de fecha → fecha con la misma precisión. Las columnas que no están en el esquema no cambian.
    """
    if esquema is None:
        return df
    df = df.copy()
    for campo in esquema:
        c, tipo = campo.name, campo.type
        if c not in df.columns:
            continue
        if pa.types.is_string(tipo) or pa.types.is_large_string(tipo):
            df[c] = df[c].map(_texto).astype("str")
        elif pa.types.is_timestamp(tipo):
            df[c] = pd.to_datetime(df[c], errors="coerce").astype(f"datetime64[{tipo.unit}]")
    return df


def leer(columnas: list[str] | None = None, antes_de: dt.date | None = None) -> pd.DataFrame:
    """
    Histórico consolidado (todas las fechas o las anteriores a `antes_de`). Con `columnas`, lee solo esas
    columnas; si un archivo antiguo no tiene alguna (por ejemplo, una columna que el ETL agregó después),
    esa columna queda vacía en sus registros.
    """
    partes = []
    for _, path in archivos(antes_de):
        if columnas is None:
            partes.append(pd.read_parquet(path))
        else:
            disponibles = set(pq.read_schema(path).names)
            partes.append(pd.read_parquet(path, columns=[c for c in columnas if c in disponibles]))
    if not partes:
        return pd.DataFrame(columns=columnas or [])
    df = pd.concat(partes, ignore_index=True) if len(partes) > 1 else partes[0]
    if columnas is not None:
        df = df.reindex(columns=columnas)
    # Una columna con tipos distintos entre archivos se une como objeto: se unifica en fecha o en texto
    for col in df.columns:
        if df[col].dtype == object:
            valores = df[col].dropna()
            if len(valores) and all(isinstance(v, (pd.Timestamp, dt.datetime, dt.date)) for v in valores):
                df[col] = pd.to_datetime(df[col])
            else:
                df[col] = df[col].map(_texto).astype("str")
    return df
