"""
historico.py
------------
Acceso al histórico de programas activos, guardado en un archivo Parquet por año:
    Historico/Historico Programas Activos AAAA.parquet

Motivo de la división por año: GitHub rechaza archivos de más de 100 MB y advierte desde 50 MB. Un archivo
único crecería unos 17 MB por año (una ejecución semanal de ~5.500 programas); por año, cada archivo queda entre
17 y 25 MB y el límite no se alcanza, sin importar cuántos años funcione la herramienta. leer() entrega los
archivos en conjunto, como un solo histórico.

Dentro de cada archivo, cada ejecución ocupa su propio grupo de filas (row group), en orden de fecha. Al agregar
una ejecución, los grupos anteriores se vuelven a escribir con los mismos bytes y Git almacena solo la diferencia
(alrededor de 0,2 MB por ejecución). Una reclasificación solo cambia las columnas de clasificación, que Git
también almacena como diferencia.

Fecha de ejecución: FECHA_DE_OBTENCION se guarda como fecha sin hora (tipo date, AAAA-MM-DD) y en memoria se
trabaja como datetime64 normalizado a la medianoche. migrar_fecha_a_date() convierte los archivos que aún la
tengan como marca de tiempo.

Tipos de datos: el reporte del SNIES no siempre entrega una columna con el mismo tipo (por ejemplo,
NUMERO_CREDITOS llega como número en una descarga y como texto en otra, y una columna sin datos llega sin
tipo). Cada ejecución adopta el esquema del archivo de su año (o, en un año nuevo, el del año anterior) y los
números guardados como texto usan una sola representación ("10", no "10.0"), para que las comparaciones entre
ejecuciones sean exactas.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

HISTORICO_DIR = Path(__file__).resolve().parent.parent / "Historico"
PREFIJO = "Historico Programas Activos"
COL_FECHA = "FECHA_DE_OBTENCION"
TIPO_FECHA = pa.date32()   # FECHA_DE_OBTENCION se guarda como fecha (sin hora): AAAA-MM-DD
COMPRESION = "snappy"   # compatible con pandas, Power BI, Excel (Power Query) y la mayoría de herramientas
_ARCHIVO_RE = re.compile(rf"^{PREFIJO} (\d{{4}})\.parquet$")
_ENTERO_DECIMAL = re.compile(r"^-?\d+\.0+$")


def ruta(anio: int) -> Path:
    """Archivo del histórico del año `anio`."""
    return HISTORICO_DIR / f"{PREFIJO} {anio}.parquet"


def archivos() -> list[tuple[int, Path]]:
    """Archivos del histórico, ordenados por año: [(año, ruta)]."""
    if not HISTORICO_DIR.exists():
        return []
    encontrados = [(int(m.group(1)), p) for p in HISTORICO_DIR.iterdir() if (m := _ARCHIVO_RE.match(p.name))]
    return sorted(encontrados)


def existe() -> bool:
    """Indica si hay al menos un archivo del histórico."""
    return bool(archivos())


def esquema() -> pa.Schema | None:
    """Esquema del archivo más reciente del histórico (None si aún no hay archivos)."""
    lista = archivos()
    return pq.read_schema(lista[-1][1]) if lista else None


def fechas() -> list[dt.date]:
    """Fechas de ejecución presentes en el histórico, en orden."""
    encontradas = set()
    for _, path in archivos():
        columna = pq.read_table(path, columns=[COL_FECHA]).column(COL_FECHA).to_pandas()
        encontradas |= {pd.Timestamp(v).date() for v in columna.dropna().unique()}
    return sorted(encontradas)


def con_fecha_date(esquema: pa.Schema) -> pa.Schema:
    """
    `esquema` con la fecha de ejecución (COL_FECHA) como fecha sin hora (date32) y su anotación de pandas coherente
    con ese tipo (fecha, no fecha y hora); los demás campos y anotaciones no cambian.
    """
    if COL_FECHA not in esquema.names:
        return esquema
    if esquema.field(COL_FECHA).type != TIPO_FECHA:
        esquema = esquema.set(esquema.get_field_index(COL_FECHA), pa.field(COL_FECHA, TIPO_FECHA))
    bruto = (esquema.metadata or {}).get(b"pandas")
    if not bruto:
        return esquema
    anotacion = json.loads(bruto)
    for columna in anotacion.get("columns", []):
        if columna.get("name") == COL_FECHA:
            columna["pandas_type"], columna["numpy_type"] = "date", "object"
    return esquema.with_metadata({**esquema.metadata, b"pandas": json.dumps(anotacion).encode()})


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
    fecha → fecha (marca de tiempo con la misma precisión, o fecha sin hora) y columnas numéricas → número. Las conversiones a fecha y a número
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
        elif pa.types.is_date(tipo):   # fecha sin hora: en memoria se trabaja como datetime64 normalizado a la medianoche
            fechas_c = pd.to_datetime(df[c], errors="coerce")
            if (fechas_c.isna() == df[c].isna()).all():
                df[c] = fechas_c.dt.normalize()
        elif pa.types.is_null(tipo):   # columna sin datos en el archivo: se conserva sin tipo si sigue vacía
            if df[c].isna().all():
                df[c] = pd.Series([None] * len(df), index=df.index, dtype=object)
        elif pa.types.is_floating(tipo) or pa.types.is_integer(tipo):
            numeros = pd.to_numeric(df[c], errors="coerce")
            if (numeros.isna() == df[c].isna()).all():
                df[c] = numeros.astype("float64" if pa.types.is_floating(tipo) else "Int64")
    return df


def _tipos_iniciales(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tipos para la primera ejecución del histórico (sin esquema de referencia): columnas con tipos mezclados
    (p. ej., 160 y "180") → texto con representación única; columnas solo de fechas → fecha.
    """
    df = df.copy()
    for col in df.columns:
        if df[col].dtype != object:
            continue
        valores = df[col].dropna()
        tipos = {type(v) for v in valores}
        if valores.empty:
            continue
        if all(isinstance(v, (pd.Timestamp, dt.datetime, dt.date)) for v in valores):
            df[col] = pd.to_datetime(df[col])
        elif len(tipos) > 1:
            df[col] = df[col].map(_texto).astype("str")
    return df


def leer(columnas: list[str] | None = None, antes_de: dt.date | None = None,
         fecha: dt.date | None = None, codigos_snies: list[int] | None = None) -> pd.DataFrame:
    """
    Histórico consolidado de todos los años (todas las ejecuciones, las anteriores a `antes_de` o solo la de
    `fecha`). Con `columnas`, lee solo esas columnas; una columna que no existe en algún año se entrega vacía en
    los registros de ese año. Con `codigos_snies`, solo los registros de esos programas: el filtro se aplica al
    leer, así que la memoria depende de los programas pedidos y no del tamaño del histórico.
    """
    partes = []
    for anio, path in archivos():
        if (fecha is not None and anio != fecha.year) or (antes_de is not None and anio > antes_de.year):
            continue   # el año no puede contener las fechas pedidas
        esquema_archivo = pq.read_schema(path)
        nombres = esquema_archivo.names
        leidas = None if columnas is None else [c for c in columnas if c in nombres]
        # El literal de la fecha depende del tipo de la columna en el archivo (fecha sin hora o marca de tiempo)
        como_fecha = pa.types.is_date(esquema_archivo.field(COL_FECHA).type)
        literal = (lambda d: pd.Timestamp(d).date()) if como_fecha else pd.Timestamp
        filtros = []
        if codigos_snies is not None:
            filtros.append(("CODIGO_SNIES_DEL_PROGRAMA", "in", [int(c) for c in codigos_snies]))
        if antes_de is not None:
            filtros.append((COL_FECHA, "<", literal(antes_de)))
        if fecha is not None:
            filtros.append((COL_FECHA, "==", literal(fecha)))
        parte = pq.read_table(path, columns=leidas, filters=filtros or None).to_pandas()
        if len(parte):
            partes.append(parte)
    if not partes:
        return pd.DataFrame(columns=columnas or (esquema().names if existe() else []))
    df = pd.concat(partes, ignore_index=True) if len(partes) > 1 else partes[0]
    if columnas is not None:
        df = df.reindex(columns=columnas)
    # Una columna con tipos distintos entre años (p. ej., vacía en un año) se une como objeto: se unifica
    for col in df.columns:
        if df[col].dtype == object:
            valores = df[col].dropna()
            if len(valores) and all(isinstance(v, (pd.Timestamp, dt.datetime, dt.date)) for v in valores):
                df[col] = pd.to_datetime(df[col])
            elif len(valores):
                df[col] = df[col].map(_texto).astype("str")
    return df


def leer_anio(anio: int) -> pd.DataFrame:
    """Todas las ejecuciones del año `anio`, con todas sus columnas (vacío si el año no tiene archivo)."""
    path = ruta(anio)
    if not path.exists():
        return pd.DataFrame()
    df = pq.read_table(path).to_pandas()
    df[COL_FECHA] = pd.to_datetime(df[COL_FECHA])
    return df


def ejecucion_anterior(fecha: dt.date) -> tuple[dt.date, pd.DataFrame] | None:
    """Ejecución más reciente con fecha estrictamente anterior a `fecha`: (fecha, programas sin la columna de fecha)."""
    previas = [f for f in fechas() if f < fecha]
    if not previas:
        return None
    return previas[-1], leer(fecha=previas[-1]).drop(columns=[COL_FECHA])


def _escribir(path: Path, grupos: list[pa.Table], esquema_final: pa.Schema) -> None:
    """Escribe un grupo de filas por ejecución, en el orden recibido, de forma atómica."""
    HISTORICO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    with pq.ParquetWriter(tmp, esquema_final, compression=COMPRESION) as escritor:
        for grupo in grupos:
            escritor.write_table(grupo)
    os.replace(tmp, path)


def _fecha_grupo(tabla: pa.Table) -> dt.date:
    """Fecha de ejecución de un grupo de filas (todas sus filas comparten la misma fecha)."""
    return pd.Timestamp(tabla.column(COL_FECHA)[0].as_py()).date()


def _ajustar(tabla: pa.Table, esquema_final: pa.Schema) -> pa.Table:
    """Tabla con las columnas y tipos de `esquema_final` (columnas faltantes como nulas)."""
    columnas = [tabla.column(f.name).cast(f.type) if f.name in tabla.column_names else pa.nulls(len(tabla), f.type)
                for f in esquema_final]
    return pa.Table.from_arrays(columnas, schema=esquema_final)


def _con_extra(base: pa.Schema, otro: pa.Schema) -> pa.Schema:
    """`base` más las columnas de `otro` que no tiene, al final (nunca se descartan datos)."""
    extra = [f for f in otro if f.name not in base.names]
    return pa.schema(list(base) + extra, metadata=base.metadata) if extra else base


def guardar_ejecucion(df: pd.DataFrame, fecha: dt.date) -> int:
    """
    Agrega (o reemplaza, si se repite la fecha) los programas activos de la ejecución `fecha` en el archivo de su
    año. Los grupos de las demás ejecuciones se conservan sin cambios. Un año nuevo comienza con el esquema del
    año anterior; si la descarga trae columnas nuevas, se agregan al esquema del año y quedan vacías en sus
    ejecuciones anteriores. Retorna el número total de ejecuciones del histórico.
    """
    path = ruta(fecha.year)
    actual = pq.read_schema(path) if path.exists() else None
    referencia = actual if actual is not None else esquema()
    df = df.assign(**{COL_FECHA: pd.Timestamp(fecha)})
    df = alinear(df, referencia) if referencia is not None else _tipos_iniciales(df)
    nueva = pa.Table.from_pandas(df, preserve_index=False)
    grupos = []
    if actual is not None:
        with open(path, "rb") as fh:   # se cierra antes de reemplazar el archivo (requisito en Windows)
            archivo = pq.ParquetFile(fh)
            grupos = [archivo.read_row_group(i) for i in range(archivo.num_row_groups)]
        grupos = [g for g in grupos if _fecha_grupo(g) != fecha]
    esquema_final = con_fecha_date(_con_extra(referencia, nueva.schema) if referencia is not None else nueva.schema)
    if actual is not None and not esquema_final.equals(actual):
        grupos = [_ajustar(g, esquema_final) for g in grupos]
    grupos.append(_ajustar(nueva, esquema_final))
    grupos.sort(key=_fecha_grupo)
    _escribir(path, grupos, esquema_final)
    if referencia is None:
        _normalizar_esquema(path, grupos)
    return len(fechas())


def _normalizar_esquema(path: Path, grupos: list[pa.Table]) -> None:
    """
    Primera escritura del histórico: se reescribe con el esquema tal como queda guardado en el archivo, para que las
    escrituras posteriores (que parten de ese esquema) produzcan exactamente los mismos bytes si los datos no cambian.
    """
    canonico = pq.read_schema(path)
    _escribir(path, [_ajustar(g, canonico) for g in grupos], canonico)


def reescribir(df: pd.DataFrame) -> None:
    """
    Reescribe el histórico desde `df` (un archivo por año y un grupo de filas por fecha), conservando el esquema
    vigente de cada año. Un año cuyo contenido no cambió se reescribe con los mismos bytes.
    """
    anios = pd.to_datetime(df[COL_FECHA]).dt.year
    for anio, parte_anio in df.groupby(anios.values, sort=True):
        path = ruta(int(anio))
        actual = pq.read_schema(path) if path.exists() else esquema()
        parte_anio = alinear(parte_anio, actual)
        tablas = [pa.Table.from_pandas(parte.reset_index(drop=True), preserve_index=False)
                  for _, parte in parte_anio.sort_values(COL_FECHA, kind="stable").groupby(COL_FECHA, sort=True)]
        esquema_final = con_fecha_date(_con_extra(actual, tablas[0].schema) if actual is not None else tablas[0].schema)
        grupos = [_ajustar(t, esquema_final) for t in tablas]
        _escribir(path, grupos, esquema_final)
        if actual is None:
            _normalizar_esquema(path, grupos)


def migrar_fecha_a_date() -> list[Path]:
    """
    Convierte FECHA_DE_OBTENCION de marca de tiempo a fecha sin hora, y corrige su anotación de pandas, en los archivos
    del histórico que aún no los tienen (conserva el orden y los grupos de filas por ejecución). Es idempotente.
    Retorna los archivos reescritos.
    """
    reescritos = []
    for _, path in archivos():
        actual = pq.read_schema(path)
        final = con_fecha_date(actual)
        if final.equals(actual, check_metadata=True):
            continue
        with open(path, "rb") as fh:   # se cierra antes de reemplazar el archivo (requisito en Windows)
            archivo = pq.ParquetFile(fh)
            grupos = [_ajustar(archivo.read_row_group(i), final) for i in range(archivo.num_row_groups)]
        _escribir(path, grupos, final)
        reescritos.append(path)
    return reescritos
