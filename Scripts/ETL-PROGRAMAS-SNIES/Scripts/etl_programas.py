"""
ETL — Normalización de Programas.xlsx
Normaliza headers y valores de celdas (mayúsculas, sin tildes, conservando Ñ y
tratando cualquier espacio Unicode como un espacio) de Fuentes/Programas.xlsx y
exporta el resultado en Salidas/.
Valida el esquema esperado, excluye las filas cuyo código SNIES no es un entero
positivo (vacío, cero, negativo, decimal o texto; '103.0' y '102' son válidos y se
guardan como enteros) y, de cada código repetido, conserva la primera aparición y
excluye las demás (con WARNING en consola, que distingue si la excluida es idéntica
o tiene valores distintos a la conservada). Las filas excluidas se guardan tal como
vienen en la fuente, con su fila de Excel, motivo y columnas con diferencias, en
Revision/Revision <archivo>.xlsx.
Las filas intermedias totalmente vacías se omiten (solo se informan).
Tipifica sin pérdida las columnas numéricas y de fecha (también las que la fuente
entrega como texto) y alerta de valores fuera de rango plausible (ej. fechas
anteriores a 1950 o futuras), indicando fila de Excel y código SNIES.
Si no se encuentra Programas.xlsx en Fuentes/ el proceso termina con error y la
salida existente no se modifica.
Si las filas a exportar caen más de un 10% respecto de la salida existente (posible
fuente truncada), ese archivo se aborta y su salida se conserva; --force lo permite.
Cada ejecución deja un log en Logs/ (se conservan los últimos 10).
"""

import argparse
import contextlib
import datetime
import logging
import math
import os
import re
import sys
import time
import unicodedata
import warnings
from collections import Counter
from itertools import zip_longest
from pathlib import Path
from typing import Callable, NamedTuple, NoReturn

# pandas 3 (requirements.txt) exige Python 3.11+.
if sys.version_info < (3, 11):
    print(f"[ERROR] Se requiere Python 3.11+. Versión actual: {sys.version}")
    sys.exit(1)

try:
    import openpyxl
    import pandas as pd
    import python_calamine  # noqa: F401 — motor de lectura de pandas (engine="calamine")
    import xlsxwriter  # noqa: F401 — motor de escritura de pandas (engine="xlsxwriter")
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.datetime import to_excel as openpyxl_serial
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.markup import escape as rich_escape
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
    from rich.table import Table
except ImportError as exc:
    # exc.name puede ser un submódulo (rich.console): se informa el paquete a instalar
    # con su nombre de pip (python_calamine → python-calamine). Si falta una dependencia
    # transitiva, pandas lanza un ImportError sin .name: se muestra su mensaje original.
    if exc.name:
        print(f"[ERROR] Falta la librería '{exc.name.split('.')[0].replace('_', '-')}'.")
    else:
        print(f"[ERROR] No se pudo importar una dependencia: {exc}")
    print(
        "Instala las dependencias con: pip install -r "
        f"\"{Path(__file__).resolve().parents[1] / 'requirements.txt'}\""
    )
    sys.exit(1)

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# =============================================================================
# Consola y logging
# =============================================================================

console = Console(highlight=False)


def setup_console_streams() -> None:
    """
    Con la salida redirigida (tarea programada, '> archivo.txt') Windows usa la
    codificación del sistema (cp1252): un carácter de los datos no representable
    en ella lanzaría UnicodeEncodeError y haría fallar el archivo. Se sustituye
    por '?' solo en consola; el archivo de log (UTF-8) conserva el texto íntegro.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[
            RichHandler(console=console, rich_tracebacks=True, show_path=False, markup=False)
        ],
        force=True,
    )


logger = logging.getLogger("etl_programas")

# Solo escribe al archivo de log: resúmenes y detalles que en consola se muestran
# con rich (tablas, listas). NullHandler evita que, sin archivo, caigan a stderr.
file_log = logging.getLogger("etl_programas_archivo")
file_log.propagate = False
file_log.addHandler(logging.NullHandler())

_DETAIL_INDENT = " " * 20


def _log(msg: str, *args, level: str = "info") -> None:
    """Emite un mensaje de log y agrega una línea en blanco visual."""
    getattr(logger, level)(msg, *args)
    console.print()


def _detail(plain: str, markup: str | None = None, level: str = "info") -> None:
    """Línea de detalle bajo el mensaje anterior. En el log se escribe sin estilos."""
    console.print(_DETAIL_INDENT + (markup if markup is not None else f"[dim]{rich_escape(plain)}[/dim]"))
    getattr(file_log, level)("    %s", plain)


# =============================================================================
# Rutas
# =============================================================================

BASE_DIR    = Path(__file__).resolve().parents[1]
FUENTES_DIR = BASE_DIR / "Fuentes"
OUTPUT_DIR  = BASE_DIR / "Salidas"
REVIEW_DIR  = BASE_DIR / "Revision"   # filas excluidas por código SNIES inválido o repetido
REVIEW_PREFIX = "Revision "
LOGS_DIR    = BASE_DIR / "Logs"

_MAX_LOG_FILES = 10
_LOG_NAME_RE   = re.compile(r"etl_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?\.log")

_SOURCE_FILES = [
    {"stem": "programas", "standard_name": "Programas.xlsx"},
]


class FileProcessingError(Exception):
    """Error no fatal: falla el archivo actual pero el proceso puede continuar."""


# =============================================================================
# Archivo de log por ejecución
# =============================================================================

def setup_file_logging() -> Path | None:
    """
    Crea un log nuevo en LOGS_DIR para esta ejecución (etl_AAAA-MM-DD_HH-MM-SS.log)
    y conserva solo los últimos _MAX_LOG_FILES. Si no se puede crear, el ETL
    continúa sin log: un problema de logging no debe detener el proceso.
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = LOGS_DIR / f"etl_{stamp}.log"
        n = 2
        while path.exists():  # dos ejecuciones en el mismo segundo
            path = LOGS_DIR / f"etl_{stamp}_{n}.log"
            n += 1
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        _log(
            "No se pudo crear el archivo de log en %s (%s). Se continúa sin log.",
            LOGS_DIR,
            exc,
            level="warning",
        )
        return None

    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    file_log.addHandler(handler)
    _prune_logs()
    return path


def _prune_logs() -> None:
    """Elimina los logs más antiguos. Solo toca archivos con el nombre que genera este script."""
    # El nombre lleva la fecha en formato ordenable: orden alfabético = orden cronológico.
    logs = sorted(f for f in LOGS_DIR.iterdir() if f.is_file() and _LOG_NAME_RE.fullmatch(f.name))
    for old in logs[:-_MAX_LOG_FILES]:
        with contextlib.suppress(OSError):
            old.unlink()


# =============================================================================
# CLI
# =============================================================================


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETL — Normalización de Programas")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecuta el pipeline sin escribir archivos de salida ni de revisión.",
    )
    parser.add_argument(
        "--file",
        type=str,
        metavar="NOMBRE",
        help="Procesa solo el archivo indicado (ej. Programas.xlsx).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Termina con código 1 si hay alertas de validación (las salidas se "
            "escriben igualmente). Útil para ejecuciones automáticas."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Exporta aunque las filas caigan más de un 10%% respecto de la salida "
            "anterior (por defecto se aborta ese archivo por posible fuente truncada)."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Muestra detalles extendidos (headers renombrados, columnas excluidas, etc.).",
    )
    return parser.parse_args(argv)


# =============================================================================
# Normalización de headers y valores
# =============================================================================

_COMBINING_MARKS_RE = re.compile("[\u0300-\u036f]+")

# \s en patrones str cubre espacios Unicode (\xa0, \u2009, ...), no solo \n\r\t y ' '
_WHITESPACE_RE = re.compile(r"\s+")

_PH_ENIE_UPPER = "\uE000"  # Placeholder Unicode de uso privado (PUA) para Ñ
_PH_ENIE_LOWER = "\uE001"  # Placeholder Unicode de uso privado (PUA) para ñ


def _remove_diacritics_preserve_enie(text: str) -> str:
    """Elimina diacríticos pero conserva Ñ/ñ intactos."""
    # NFC primero: una Ñ descompuesta (N + U+0303) no coincidiría con "Ñ" y se perdería.
    text = unicodedata.normalize("NFC", text)
    text = text.replace("Ñ", _PH_ENIE_UPPER).replace("ñ", _PH_ENIE_LOWER)
    text = unicodedata.normalize("NFD", text)
    text = _COMBINING_MARKS_RE.sub("", text)
    return text.replace(_PH_ENIE_UPPER, "Ñ").replace(_PH_ENIE_LOWER, "ñ")


def normalize_header(name) -> str:
    """
    Normaliza un header en 6 pasos:
      1. Cualquier secuencia de espacios en blanco (incl. \\xa0, \\n, \\t) → un espacio
      2. Strip
      3. Mayúsculas
      4. Quitar tildes, preservar Ñ
      5. Espacios → _
      6. Eliminar chars no permitidos (solo A-Z, 0-9, _, Ñ)
    """
    if not isinstance(name, str):
        name = str(name)
    name = _WHITESPACE_RE.sub(" ", name)
    name = name.strip()
    name = name.upper()
    name = _remove_diacritics_preserve_enie(name)
    name = name.replace(" ", "_")
    name = re.sub(r"[^A-Z0-9_Ñ]", "", name)
    return name


_SKIP_VALUE_PREFIXES = ("CODIGO_", "ID_", "NUMERO_", "FECHA_", "COSTO_")
# REGISTRO_UNICO y RESOLUCION_DE_APROBACION son identificadores numéricos: se
# excluyen para que el aviso de columnas numéricas solo señale columnas nuevas.
# VIGENCIA_TRANSITORIA es una fecha (ver _TYPE_CONVERSIONS).
_SKIP_VALUE_EXACT = {
    "ID", "VIGENCIA_AÑOS", "VIGENCIA_TRANSITORIA", "REGISTRO_UNICO", "RESOLUCION_DE_APROBACION",
}


def _should_skip_value_norm(col: str) -> bool:
    """Columnas de códigos, IDs, valores numéricos o fechas no se normalizan en valor."""
    return col in _SKIP_VALUE_EXACT or any(col.startswith(p) for p in _SKIP_VALUE_PREFIXES)


# =============================================================================
# Contrato de esquema, clave y tipos
# =============================================================================

# Headers normalizados del reporte de programas del SNIES, en el orden de la fuente.
# Si el Ministerio cambia el reporte, actualice esta lista.
EXPECTED_COLUMNS: tuple[str, ...] = (
    "CODIGO_INSTITUCION_PADRE",
    "CODIGO_INSTITUCION",
    "NOMBRE_INSTITUCION",
    "ESTADO_INSTITUCION",
    "CARACTER_ACADEMICO",
    "SECTOR",
    "REGISTRO_UNICO",
    "CODIGO_SNIES_DEL_PROGRAMA",
    "CODIGO_ANTERIOR_ICFES",
    "NOMBRE_DEL_PROGRAMA",
    "TITULO_OTORGADO",
    "ESTADO_PROGRAMA",
    "JUSTIFICACION",
    "JUSTIFICACION_DETALLADA",
    "RECONOCIMIENTO_DEL_MINISTERIO",
    "RESOLUCION_DE_APROBACION",
    "FECHA_DE_RESOLUCION",
    "FECHA_EJECUTORIA",
    "VIGENCIA_AÑOS",
    "FECHA_DE_REGISTRO_EN_SNIES",
    "CINE_F_2013_AC_CAMPO_AMPLIO",
    "CINE_F_2013_AC_CAMPO_ESPECIFIC",
    "CINE_F_2013_AC_CAMPO_DETALLADO",
    "AREA_DE_CONOCIMIENTO",
    "NUCLEO_BASICO_DEL_CONOCIMIENTO",
    "NIVEL_ACADEMICO",
    "NIVEL_DE_FORMACION",
    "MODALIDAD",
    "NUMERO_CREDITOS",
    "NUMERO_PERIODOS_DE_DURACION",
    "PERIODICIDAD",
    "SE_OFRECE_POR_CICLOS_PROPEDEUT",
    "PERIODICIDAD_ADMISIONES",
    "PROGRAMA_EN_CONVENIO",
    "DEPARTAMENTO_OFERTA_PROGRAMA",
    "MUNICIPIO_OFERTA_PROGRAMA",
    "COSTO_MATRICULA_ESTUD_NUEVOS",
    "VIGENCIA_TRANSITORIA",
    "OBSERVACION_DECRETO_117423",
)

KEY_COLUMN = "CODIGO_SNIES_DEL_PROGRAMA"

_BLANK_HEADER_PREFIX = "SIN_NOMBRE_"
_MAX_EXAMPLES = 5

_DATE_FORMAT = "%d/%m/%Y"
_DECIMAL_COMMA_RE = re.compile(r"[0-9]+(?:,[0-9]+)?")
# Sin ceros a la izquierda ('0123' → 123 perdería información) y hasta 15 dígitos,
# la precisión máxima de un número en Excel (códigos más largos deben quedar como texto).
# Admite un entero guardado como decimal en texto ('103.0', '103,0'): un solo cero tras
# el separador no puede ser separador de miles, a diferencia de '1.000'.
_CODE_RE = re.compile(r"(0|[1-9][0-9]{0,14})(?:[.,]0)?")


def _to_number(value):
    """'7' → 7, '8,5' → 8.5 (coma decimal). Números ya tipados se conservan."""
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not _DECIMAL_COMMA_RE.fullmatch(text):
        raise ValueError(text)
    return float(text.replace(",", ".")) if "," in text else int(text)


def _to_code(value):
    """
    '1101' → 1101, 103.0 / '103.0' → 103. Solo enteros exactos: sin ceros a la
    izquierda ni más de 15 dígitos.
    """
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(value)
        value = int(value)
    match = _CODE_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(value)
    return int(match.group(1))


def _to_date(value):
    """
    '31/12/2025' → date(2025, 12, 31). Un datetime a medianoche se reduce a date
    (sin pérdida: se exporta como fecha, sin '00:00:00'); con hora se conserva.
    """
    if isinstance(value, datetime.datetime):
        return value.date() if value.time() == datetime.time() else value
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value).strip(), _DATE_FORMAT).date()


class TypeRule(NamedTuple):
    label: str
    convert: Callable
    min: object = None   # rango plausible (inclusive); None = sin límite
    max: object = None   # _TODAY = fecha de ejecución


_TODAY = "hoy"
# Antes de 1950 solo aparecen valores espurios de Excel: un número (0, 1, ...) escrito
# en una celda con formato fecha se lee como 1899-12-30, 1900-01-01, etc.
_MIN_DATE = datetime.date(1950, 1, 1)

# Contrato de tipos de todas las columnas numéricas y de fecha. La conversión es
# todo o nada: si un solo valor no es convertible, la columna se conserva como
# viene en la fuente y se emite una alerta. Los valores fuera del rango plausible
# solo generan alerta (no se alteran). Los códigos se tipifican como enteros para
# cruzarlos con otros reportes SNIES; CODIGO_ANTERIOR_ICFES (hasta 21 dígitos)
# se conserva como texto de forma deliberada. Las FECHA_* registran hechos pasados
# (máx. hoy); VIGENCIA_TRANSITORIA puede ser futura.
_TYPE_CONVERSIONS: dict[str, TypeRule] = {
    "CODIGO_INSTITUCION_PADRE":     TypeRule("entero", _to_code),
    "CODIGO_INSTITUCION":           TypeRule("entero", _to_code),
    "REGISTRO_UNICO":               TypeRule("entero", _to_code),
    "CODIGO_SNIES_DEL_PROGRAMA":    TypeRule("entero", _to_code),  # inválidos ya excluidos
    "RESOLUCION_DE_APROBACION":     TypeRule("entero", _to_code),
    "FECHA_DE_RESOLUCION":          TypeRule("fecha",  _to_date, _MIN_DATE, _TODAY),
    "FECHA_EJECUTORIA":             TypeRule("fecha",  _to_date, _MIN_DATE, _TODAY),
    "VIGENCIA_AÑOS":                TypeRule("número", _to_number, 0, 50),
    "FECHA_DE_REGISTRO_EN_SNIES":   TypeRule("fecha",  _to_date, _MIN_DATE, _TODAY),
    "NUMERO_CREDITOS":              TypeRule("entero", _to_code),
    "NUMERO_PERIODOS_DE_DURACION":  TypeRule("entero", _to_code),
    "COSTO_MATRICULA_ESTUD_NUEVOS": TypeRule("número", _to_number, 0),
    "VIGENCIA_TRANSITORIA":         TypeRule("fecha",  _to_date, _MIN_DATE),
}

_INVALID = object()


def _alert(alerts: list[str], msg: str) -> None:
    """Registra una alerta de validación (no fatal) y la acumula para el resumen."""
    alerts.append(msg)
    _log("%s", msg, level="warning")


def _fmt_value(v) -> str:
    """Formato legible para alertas: fechas como dd/mm/aaaa; el resto con repr."""
    if isinstance(v, datetime.datetime):
        return v.strftime("%d/%m/%Y %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.strftime("%d/%m/%Y")
    return repr(v)


def _examples(values, fmt=_fmt_value) -> str:
    vals = [fmt(v) for v in list(values)[:_MAX_EXAMPLES]]
    extra = len(values) - len(vals)
    return ", ".join(vals) + (f" (+{extra} más)" if extra > 0 else "")


# Índice 0 del DataFrame = fila 2 de Excel (fila 1: encabezados). Al recortar, omitir
# o excluir filas el índice no se reasigna, de modo que las alertas citen siempre la
# fila de Excel de origen (_trim_trailing_rows, _drop_blank_rows, _exclude_invalid_keys).
_EXCEL_ROW_OFFSET = 2


def _where(df: pd.DataFrame, idx) -> str:
    """Ubicación de una fila en la fuente, ej. 'fila 585, SNIES 91335'."""
    loc = f"fila {idx + _EXCEL_ROW_OFFSET:,}"
    snies = df.at[idx, KEY_COLUMN] if KEY_COLUMN in df.columns else None
    return loc if snies is None or pd.isna(snies) else f"{loc}, SNIES {snies}"


def _located(df: pd.DataFrame, items) -> list[str]:
    """[(índice, valor)] → ['29/12/1899 (fila 585, SNIES 91335)', ...]."""
    return [f"{_fmt_value(v)} ({_where(df, i)})" for i, v in items]


def _build_headers(raw_headers: list) -> list[str]:
    """Normaliza los headers crudos; los vacíos se nombran SIN_NOMBRE_<letra de columna>."""
    names = []
    for i, raw in enumerate(raw_headers, start=1):
        name = "" if pd.isna(raw) else normalize_header(raw)
        names.append(name or f"{_BLANK_HEADER_PREFIX}{get_column_letter(i)}")
    return names


def _guess_renames(cols: list[str], missing: list[str]) -> list[tuple[str, str, str]]:
    """
    Empareja columnas faltantes con columnas no reconocidas que ocupan su mismo
    hueco entre dos columnas esperadas presentes. Solo propone el par cuando el
    hueco tiene exactamente una faltante y una no reconocida (sin ambigüedad).
    Retorna [(faltante, no_reconocida, letra_de_columna)].
    """
    pos = {c: i for i, c in enumerate(EXPECTED_COLUMNS)}
    missing_set = set(missing)
    pairs = []
    prev = -1                                # índice en EXPECTED_COLUMNS de la última esperada vista
    gap_new: list[tuple[str, str]] = []      # no reconocidas desde esa columna
    for j, col in enumerate([*cols, None]):
        if col is not None and col not in pos:
            gap_new.append((col, get_column_letter(j + 1)))
            continue
        nxt = len(EXPECTED_COLUMNS) if col is None else pos[col]
        gap_missing = [m for m in EXPECTED_COLUMNS[prev + 1:nxt] if m in missing_set]
        if len(gap_missing) == 1 and len(gap_new) == 1:
            pairs.append((gap_missing[0], *gap_new[0]))
        if col is not None:
            prev = pos[col]
        gap_new = []
    return pairs


def _validate_schema(cols: list[str], path: Path, sheet: str, alerts: list[str]) -> None:
    """
    Compara los headers normalizados con EXPECTED_COLUMNS.
    Faltantes → error (rompen el consumo aguas abajo); el mensaje incluye las
    columnas no reconocidas y los posibles renombres. Columnas nuevas u orden
    distinto → alerta; las columnas nuevas se exportan igualmente.
    """
    expected = set(EXPECTED_COLUMNS)
    missing = [c for c in EXPECTED_COLUMNS if c not in cols]
    unexpected = [c for c in cols if c not in expected]
    if missing:
        detail = ""
        if unexpected:
            letters = {c: get_column_letter(j + 1) for j, c in enumerate(cols)}
            detail = (
                f" Columnas no reconocidas en la fuente: "
                f"{_examples(unexpected, fmt=lambda c: f'{c!r} ({letters[c]})')}."
            )
        if len(missing) > len(EXPECTED_COLUMNS) // 2:
            hint = (
                "Verifique que sea la hoja de programas y que la fila 1 contenga "
                "los encabezados del reporte."
            )
        else:
            renames = _guess_renames(cols, missing)
            if renames:
                detail += " Posible renombre: " + ", ".join(
                    f"{old!r} → {new!r} (columna {letter})" for old, new, letter in renames
                ) + "."
            hint = "Si el reporte oficial cambió, actualice EXPECTED_COLUMNS."
        raise FileProcessingError(
            f"'{path.name}' (hoja '{sheet}') no cumple el esquema esperado: faltan "
            f"{len(missing)} de {len(EXPECTED_COLUMNS)} columna(s): {_examples(missing)}."
            f"{detail} {hint}"
        )

    if unexpected:
        _alert(
            alerts,
            f"{len(unexpected)} columna(s) fuera del esquema esperado (se exportan igualmente): "
            f"{unexpected}.",
        )
    if [c for c in cols if c in expected] != list(EXPECTED_COLUMNS):
        _alert(alerts, "Las columnas esperadas no siguen el orden de EXPECTED_COLUMNS.")


# Motivos de exclusión por código SNIES inválido, en el orden en que se reportan:
# motivo → (texto de la columna MOTIVO_EXCLUSION del archivo de revisión, texto de la alerta).
_KEY_REJECT_REASONS: dict[str, tuple[str, str]] = {
    "vacío":    ("Vacío",    "vacío"),
    "cero":     ("Cero",     "cero"),
    "negativo": ("Negativo", "negativo"),
    "decimal":  ("Decimal",  "decimal"),
    "texto":    ("Texto (no numérico)", "de texto (no numérico)"),
    "formato":  (
        "Formato no válido (ceros a la izquierda, separador de miles o más de 15 dígitos)",
        "con formato no válido (ceros a la izquierda, separador de miles o más de 15 dígitos)",
    ),
}
REVIEW_ROW_COLUMN    = "FILA_EXCEL"
REVIEW_REASON_COLUMN = "MOTIVO_EXCLUSION"
REVIEW_DIFF_COLUMN   = "COLUMNAS_CON_DIFERENCIAS"   # solo repetidos: difiere de la fila conservada
_NUMERIC_TEXT_RE = re.compile(r"[+-]?[0-9]+(?:[.,][0-9]+)?")


def _key_reject_reason(value) -> str | None:
    """None si el código SNIES es un entero positivo válido; si no, su motivo de exclusión."""
    if pd.isna(value) or (isinstance(value, str) and not value.strip()):
        return "vacío"
    try:
        return "cero" if _to_code(value) == 0 else None
    except ValueError:
        pass
    if isinstance(value, bool):
        return "texto"
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and _NUMERIC_TEXT_RE.fullmatch(value.strip()):
        number = float(value.strip().replace(",", "."))
    else:
        return "texto"
    if number < 0:
        return "negativo"
    if number == 0:
        return "cero"
    if not number.is_integer():
        return "decimal"
    return "formato"


def _is_blank(value) -> bool:
    """Celda vacía: nula o texto con solo espacios."""
    if isinstance(value, str):
        return not value.strip()
    return value is None or pd.isna(value)


def _drop_blank_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Omite las filas intermedias sin ningún valor (celdas vacías o con solo espacios).
    Corresponden a formato, no a datos; de no omitirlas se excluirían como código
    SNIES vacío, irían a Revision/ y contarían como alerta. Solo se revisan las filas con la clave vacía
    (una fila en blanco la tiene vacía). El índice se conserva para que las alertas
    posteriores sigan citando la fila de Excel. Retorna (filas restantes, omitidas).
    """
    candidates = df.index[df[KEY_COLUMN].map(_is_blank).astype(bool)]
    blank = [i for i in candidates if all(_is_blank(v) for v in df.loc[i])]
    if not blank:
        return df, 0
    _log(
        "Filas vacías intermedias omitidas: %d (filas %s).",
        len(blank),
        _examples([f"{i + _EXCEL_ROW_OFFSET:,}" for i in blank], fmt=str),
    )
    return df.drop(index=blank), len(blank)


class KeyExclusion(NamedTuple):
    valid: pd.DataFrame      # filas a exportar
    excluded: pd.DataFrame   # filas para el archivo de revisión
    n_invalid: int           # excluidas por código SNIES inválido
    n_repeated: int          # excluidas por código SNIES repetido
    n_repeated_diff: int     # de ellas, con diferencias respecto a la fila conservada


def _comparable(col: str, value):
    """
    Valor normalizado de una celda (tipificado y con la normalización de texto del
    pipeline), para comparar filas repetidas sin marcar como diferencia lo que no
    cambia el dato: 'Activa ' vs 'ACTIVA', '7' vs 7, '31/12/2025' vs
    datetime(2025, 12, 31). Vacíos → None.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    rule = _TYPE_CONVERSIONS.get(col)
    if rule is not None:
        with contextlib.suppress(ValueError, TypeError):
            return rule.convert(value)
    if isinstance(value, str):
        value = _WHITESPACE_RE.sub(" ", value).strip()
        if not value:
            return None
        if not _should_skip_value_norm(col):
            value = _remove_diacritics_preserve_enie(value.upper())
    return value


def _differing_columns(df: pd.DataFrame, i, j) -> list[str]:
    """Columnas (salvo la clave) en que las filas i y j difieren tras normalizar."""
    return [
        c for c in df.columns
        if c != KEY_COLUMN and _comparable(c, df.at[i, c]) != _comparable(c, df.at[j, c])
    ]


def _exclude_invalid_keys(df: pd.DataFrame, alerts: list[str]) -> KeyExclusion:
    """
    Separa las filas que no se exportan, con un WARNING por motivo y fila de Excel:
      1. Código SNIES que no es un entero positivo (vacío, cero, negativo, decimal, texto).
      2. Código SNIES repetido: se conserva la primera aparición y se excluyen las
         demás. '102', 102 y 102.0 son el mismo código. Cada excluida se compara con
         la conservada: si difiere en algún valor (ya normalizado), se indica en qué
         columnas y se emite un WARNING aparte, porque podría ser la fila correcta.
    El índice se conserva para que las alertas posteriores ubiquen la fila. Las
    excluidas conservan los valores tal como vienen en la fuente, precedidas de
    FILA_EXCEL, MOTIVO_EXCLUSION y COLUMNAS_CON_DIFERENCIAS.
    """
    key = df[KEY_COLUMN]
    reasons = key.map(_key_reject_reason)
    invalid = reasons.notna()
    motives = pd.Series(None, index=df.index, dtype=object)
    motives[invalid] = reasons[invalid].map(lambda r: _KEY_REJECT_REASONS[r][0])
    for reason, (_, label) in _KEY_REJECT_REASONS.items():
        rows = reasons.index[reasons == reason]
        if rows.empty:
            continue
        if reason == "vacío":
            detail = "filas " + _examples([f"{i + _EXCEL_ROW_OFFSET:,}" for i in rows], fmt=str)
        else:
            detail = _examples(
                [f"{_fmt_value(df.at[i, KEY_COLUMN])} (fila {i + _EXCEL_ROW_OFFSET:,})" for i in rows],
                fmt=str,
            )
        _alert(alerts, f"{KEY_COLUMN}: {len(rows):,} fila(s) excluida(s) por código {label}: {detail}.")

    # Los repetidos se comparan por el código ya tipificado (todos los restantes son válidos).
    codes = key[~invalid].map(_to_code)
    repeated = codes.duplicated(keep="first")
    diffs = pd.Series(None, index=df.index, dtype=object)
    with_diff: list[str] = []
    if repeated.any():
        kept_idx = {code: i for i, code in codes[~repeated].items()}
        rows_by_code: dict[int, list[str]] = {}
        for i, code in codes[repeated].items():
            kept = kept_idx[code]
            kept_row = f"{kept + _EXCEL_ROW_OFFSET:,}"
            row = f"{i + _EXCEL_ROW_OFFSET:,}"
            differing = _differing_columns(df, i, kept)
            if differing:
                motives.at[i] = f"Repetido con diferencias (se conserva la fila {kept_row})"
                diffs.at[i] = ", ".join(differing)
                with_diff.append(f"{code} (fila {row} vs {kept_row}: {', '.join(differing)})")
            else:
                motives.at[i] = f"Repetido idéntico (se conserva la fila {kept_row})"
            rows_by_code.setdefault(code, []).append(row)
        n_repeated = int(repeated.sum())
        items = [
            f"{code} (se conserva la fila {kept_idx[code] + _EXCEL_ROW_OFFSET:,}; "
            f"se excluye(n) {', '.join(rows)})"
            for code, rows in rows_by_code.items()
        ]
        _alert(
            alerts,
            f"{KEY_COLUMN}: {len(rows_by_code):,} código(s) repetido(s), {n_repeated:,} fila(s) "
            f"excluida(s) (se conserva la primera aparición; idénticas: {n_repeated - len(with_diff):,}, "
            f"con diferencias: {len(with_diff):,}): {_examples(items, fmt=str)}.",
        )
        if with_diff:
            _alert(
                alerts,
                f"{KEY_COLUMN}: {len(with_diff):,} fila(s) repetida(s) excluida(s) con valores "
                f"distintos a la conservada; verifique cuál es la correcta en la carpeta "
                f"{REVIEW_DIR.name}/: {_examples(with_diff, fmt=str)}.",
            )

    drop = motives.notna()
    excluded = df.loc[drop].copy()
    excluded.insert(0, REVIEW_DIFF_COLUMN, diffs[drop])
    excluded.insert(0, REVIEW_REASON_COLUMN, motives[drop])
    excluded.insert(0, REVIEW_ROW_COLUMN, excluded.index + _EXCEL_ROW_OFFSET)
    return KeyExclusion(
        df.loc[~drop].copy(), excluded, int(invalid.sum()), int(repeated.sum()), len(with_diff)
    )


def _range_key(v):
    """Hace comparables datetime y date (compararlos directamente lanza TypeError)."""
    return v.date() if isinstance(v, datetime.datetime) else v


def _check_range(
    col: str, rule: TypeRule, parsed: pd.Series, df: pd.DataFrame, alerts: list[str]
) -> None:
    """
    Alerta (sin alterar datos) por valores fuera del rango plausible de la columna,
    con su fila y código SNIES.
    """
    lo = rule.min
    hi = datetime.date.today() if rule.max == _TODAY else rule.max
    if lo is None and hi is None:
        return
    keys = parsed.map(_range_key)
    out = pd.Series(False, index=parsed.index)
    if lo is not None:
        out |= keys.map(lambda v: v < lo).astype(bool)
    if hi is not None:
        out |= keys.map(lambda v: v > hi).astype(bool)
    if not out.any():
        return

    items = sorted(parsed[out].items(), key=lambda kv: (_range_key(kv[1]), kv[0]))
    bounds = f"{'-∞' if lo is None else _fmt_value(lo)} a {'∞' if hi is None else _fmt_value(hi)}"
    _alert(
        alerts,
        f"{col}: {len(items):,} valor(es) fuera del rango plausible ({bounds}), "
        f"se conservan sin cambios: {_examples(_located(df, items), fmt=str)}.",
    )


class TypeReport(NamedTuple):
    converted: list[str]   # convertidas desde texto, ej. ['VIGENCIA_AÑOS (número)']
    n_valid: int           # columnas que cumplen el contrato de tipo
    n_total: int           # columnas del contrato presentes


def _convert_types(df: pd.DataFrame, alerts: list[str]) -> TypeReport:
    """Aplica _TYPE_CONVERSIONS (in-place) y valida rangos."""
    converted = []
    n_valid = 0
    for col, rule in _TYPE_CONVERSIONS.items():
        if col not in df.columns:
            continue
        values = df[col]
        # Una celda con solo espacios es una celda vacía: sin esto se reportaría como
        # no convertible y dejaría sin tipificar toda la columna (y la clave, sin su
        # alerta de "sin valor").
        blank = values.map(lambda v: isinstance(v, str) and not v.strip()).astype(bool)
        if blank.any():
            values = values.mask(blank)
            df[col] = values
            _log("%s: %d celda(s) con solo espacios tratadas como vacías.", col, int(blank.sum()))
        mask = values.notna()
        if not mask.any():
            n_valid += 1
            continue

        def _safe(v, convert=rule.convert):
            try:
                return convert(v)
            except (ValueError, TypeError):
                return _INVALID

        # dtype=object: Series.map inferiría float64 al mezclar 7 y 8.5 (7 → 7.0).
        parsed = pd.Series([_safe(v) for v in values[mask]], index=values[mask].index, dtype=object)
        invalid = values[mask][parsed.map(lambda v: v is _INVALID).astype(bool)]
        if not invalid.empty:
            _alert(
                alerts,
                f"{col} se conserva como viene en la fuente: {len(invalid):,} valor(es) no "
                f"convertible(s) a {rule.label}: "
                f"{_examples(_located(df, invalid.items()), fmt=str)}.",
            )
            continue

        n_valid += 1
        _check_range(col, rule, parsed, df, alerts)
        result = values.copy()
        result[mask] = parsed
        df[col] = result
        if values[mask].map(lambda v: isinstance(v, str)).any():
            converted.append(f"{col} ({rule.label})")

    n_rules = sum(c in df.columns for c in _TYPE_CONVERSIONS)
    _log(
        "Tipos validados: %d de %d columna(s). Convertidas desde texto: %s.",
        n_valid,
        n_rules,
        ", ".join(converted) or "ninguna",
    )
    return TypeReport(converted, n_valid, n_rules)


# =============================================================================
# Validación de entrada
# =============================================================================

def _validate_directories() -> None:
    """Verifica que FUENTES_DIR exista. OUTPUT_DIR se crea al exportar (no en dry-run)."""
    if not FUENTES_DIR.is_dir():
        logger.error(
            "No se encontró la carpeta de fuentes: %s. "
            "Verifique que la ruta existe y es accesible.",
            FUENTES_DIR,
        )
        console.print()
        _exit(1)


def find_source_file(stem: str, standard_name: str) -> Path | None:
    """
    Localiza un archivo .xlsx en FUENTES_DIR cuyo stem coincida
    (case-insensitive). Devuelve None si no se encuentra.
    """
    all_matches = [
        f for f in FUENTES_DIR.iterdir()
        if f.is_file() and f.stem.lower() == stem
    ]
    xlsx_matches = [f for f in all_matches if f.suffix.lower() == ".xlsx"]

    if not xlsx_matches:
        if all_matches:
            _log(
                "Se encontró un archivo '%s' pero con extensión incorrecta: %s. "
                "Solo se acepta .xlsx. Se omitirá.",
                stem,
                [f.name for f in all_matches],
                level="warning",
            )
        return None

    if len(xlsx_matches) > 1:
        _log(
            "Se encontraron múltiples archivos que coinciden con '%s': %s. "
            "Se usará el primero: '%s'.",
            standard_name,
            [f.name for f in xlsx_matches],
            xlsx_matches[0].name,
            level="warning",
        )

    match = xlsx_matches[0]
    if match.name != standard_name:
        _log(
            "Archivo encontrado como '%s'. El nombre estándar sugerido es '%s'. "
            "Se procesará igualmente.",
            match.name,
            standard_name,
            level="warning",
        )

    return match


def _validate_not_empty(df: pd.DataFrame, path: Path) -> None:
    # Columnas primero: df.empty también es True sin columnas y ocultaría este mensaje.
    if len(df.columns) == 0:
        raise FileProcessingError(
            f"El archivo '{path.name}' no tiene columnas reconocibles."
        )
    if df.empty:
        raise FileProcessingError(
            f"El archivo '{path.name}' no contiene filas de datos. Verifique el contenido."
        )


def _validate_no_duplicate_headers(cols: list[str], path: Path) -> None:
    """Falla si la normalización de headers genera nombres duplicados."""
    dupes = [c for c, n in Counter(cols).items() if n > 1]
    if dupes:
        raise FileProcessingError(
            f"Tras normalizar los headers se generaron columnas duplicadas en "
            f"'{path.name}': {dupes}. Revise el archivo fuente."
        )


# =============================================================================
# Selección de hoja y depuración de filas no válidas
# =============================================================================

_SHEET_MARKER = "INSTITUCI"

# calamine se usa por su velocidad de lectura, muy superior a la de openpyxl, con los
# mismos valores y tipos salvo las celdas que corrige _restore_time_cells.
_READ_ENGINE = "calamine"


def _select_sheet(xls: pd.ExcelFile) -> str:
    """
    Selecciona la hoja que contiene datos de programas.
    Busca un header que contenga 'INSTITUCI' (presente en la hoja de programas
    pero no en hojas de cobertura). Si no lo encuentra, usa la hoja con más columnas.
    """
    sheets = xls.sheet_names
    if len(sheets) == 1:
        return sheets[0]

    for sheet in sheets:
        headers = pd.read_excel(xls, sheet_name=sheet, dtype=object, nrows=0)
        if any(_SHEET_MARKER in str(c).upper() for c in headers.columns):
            discarded = [s for s in sheets if s != sheet]
            _log(
                "Hoja seleccionada: '%s'. Hojas descartadas: %s.",
                sheet,
                discarded,
            )
            return sheet

    best = max(
        sheets,
        key=lambda s: len(pd.read_excel(xls, sheet_name=s, dtype=object, nrows=0).columns),
    )
    _log(
        "No se identificó la hoja por headers. "
        "Se usará '%s' (mayor número de columnas).",
        best,
        level="warning",
    )
    return best


def _restore_time_cells(raw: pd.DataFrame, path: Path, sheet: str) -> int:
    """
    calamine lee como hora (datetime.time) toda celda con formato de fecha y serial
    menor que 1, incluidos los negativos: -1 → 00:00 en vez de 29/12/1899 (openpyxl),
    y la columna dejaría de tipificarse como fecha. Esas celdas (valores espurios que luego
    alertan por rango, u horas reales) se releen con openpyxl, solo hasta la última
    fila afectada, para conservar el valor exacto (in-place). Retorna cuántas se releyeron.
    """
    is_time = raw.map(lambda v: isinstance(v, datetime.time))
    # header=None: posición i del DataFrame = fila i + 1 de Excel.
    cells = [(i, j) for j in range(raw.shape[1]) for i in is_time.index[is_time.iloc[:, j]]]
    if not cells:
        return 0
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb[sheet].iter_rows(
            min_row=1, max_row=max(i for i, _ in cells) + 1, max_col=raw.shape[1], values_only=True
        ))
    finally:
        wb.close()
    for i, j in cells:
        raw.iat[i, j] = rows[i][j]
    return len(cells)


def _trim_trailing_rows(df: pd.DataFrame, verbose: bool = False) -> pd.DataFrame:
    """
    Elimina filas no válidas al final del DataFrame iterando desde abajo:
    filas vacías y filas de pie de página (<=2 celdas con valor, ej. 'NOTA: ...').
    Se detiene al encontrar una fila con datos reales.
    """
    original_len = len(df)
    total_cols = len(df.columns)

    trim_from = len(df)
    for i in range(len(df) - 1, -1, -1):
        non_null = int(df.iloc[i].notna().sum())
        if non_null == 0 or (non_null <= 2 and total_cols > 5):
            trim_from = i
        else:
            break

    if trim_from < original_len:
        trimmed = df.iloc[trim_from:]
        df = df.iloc[:trim_from].copy()
        _log("Filas no válidas eliminadas del final: %d.", len(trimmed))
        if verbose:
            for _, row in trimmed.iterrows():
                vals = [str(v) for v in row.dropna().tolist()]
                if vals:
                    preview = ", ".join(vals[:3])
                    if len(vals) > 3:
                        preview += f" (+{len(vals) - 3} más)"
                    _detail(preview)
            console.print()

    return df


# =============================================================================
# Procesamiento principal
# =============================================================================


def _normalize_values(df: pd.DataFrame, verbose: bool = False) -> tuple[int, int, int]:
    """
    Normaliza valores de texto en el DataFrame (in-place).
    Retorna (celdas_modificadas, columnas_con_cambios, total_columnas_texto).
    """
    all_cols = list(df.columns)
    text_cols = [c for c in all_cols if not _should_skip_value_norm(c) and df[c].dtype == object]
    skipped = [c for c in all_cols if _should_skip_value_norm(c)]
    if skipped:
        if verbose:
            _log("Columnas excluidas de normalización de valores (%d):", len(skipped))
            for col in skipped:
                if col in _SKIP_VALUE_EXACT:
                    reason = "nombre exacto"
                else:
                    reason = f"prefijo {next(p for p in _SKIP_VALUE_PREFIXES if col.startswith(p))}"
                _detail(f"{col} ({reason})", f"[dim]{rich_escape(col)}[/dim] ({reason})")
            console.print()
        else:
            _log("Columnas excluidas de normalización de valores: %d.", len(skipped))

    numeric_warnings = []
    for col in text_cols:
        non_null_vals = df[col].dropna()
        if len(non_null_vals) > 0:
            numeric_ratio = pd.to_numeric(non_null_vals, errors="coerce").notna().mean()
            if numeric_ratio > 0.8:
                numeric_warnings.append((col, numeric_ratio))
    if numeric_warnings:
        _log(
            "Columna(s) predominantemente numérica(s) no excluidas de la normalización "
            "(evalúe agregarlas a _SKIP_VALUE_EXACT):",
            level="warning",
        )
        for col, ratio in numeric_warnings:
            _detail(
                f"{col} ({ratio:.0%} numérico)",
                f"[dim]{rich_escape(col)}[/dim] ({ratio:.0%} numérico)",
                level="warning",
            )
        console.print()

    total_modified = 0
    cols_with_changes = 0
    with Progress(
        TextColumn("[bold]Normalizando valores[/bold]"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TextColumn("·"),
        TextColumn("{task.fields[col]}"),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("", total=len(text_cols), col="")
        for col in text_cols:
            progress.update(task_id, col=col)
            # Solo se normalizan celdas de texto: números, fechas y nulos se
            # conservan con su tipo original (evita exportarlos como texto).
            original = df[col]
            is_str = original.map(lambda v: isinstance(v, str)).astype(bool)
            s = original[is_str]
            s = s.str.replace(_WHITESPACE_RE, " ", regex=True)
            s = s.str.strip()
            s = s.str.upper()
            s = s.apply(_remove_diacritics_preserve_enie)
            n_changed = int((s != original[is_str]).sum())
            if n_changed:
                result = original.copy()
                result[is_str] = s
                df[col] = result
            total_modified += n_changed
            if n_changed > 0:
                cols_with_changes += 1
            progress.advance(task_id)

    return total_modified, cols_with_changes, len(text_cols)


def process_programas(
    path: Path, verbose: bool = False
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Retorna (filas a exportar, filas excluidas para el archivo de revisión, estadísticas)."""
    try:
        with (
            console.status(f"Abriendo '{path.name}'...") as status,
            pd.ExcelFile(path, engine=_READ_ENGINE) as xls,
        ):
            sheet = _select_sheet(xls)
            status.update(f"Leyendo '{path.name}' — hoja '{sheet}'...")
            # header=None: headers crudos, sin que pandas renombre duplicados
            # (X → X.1) ni vacíos (Unnamed: N), para validarlos con exactitud.
            raw = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=object)
            n_restored = _restore_time_cells(raw, path, sheet)
    except Exception as exc:
        raise FileProcessingError(
            f"No se pudo leer '{path.name}': {type(exc).__name__}: {exc}"
        ) from exc
    if raw.empty:
        raise FileProcessingError(f"La hoja '{sheet}' de '{path.name}' está vacía.")

    original_cols = raw.iloc[0].tolist()
    df = raw.iloc[1:].reset_index(drop=True)
    _log("%s fila(s) × %d columna(s) leídas de la hoja '%s'.", f"{df.shape[0]:,}", df.shape[1], sheet)
    if n_restored:
        restored_msg = "%d celda(s) con formato de fecha y serial menor que 1 releída(s) con openpyxl."
        if verbose:
            _log(restored_msg, n_restored)
        else:
            file_log.info(restored_msg, n_restored)

    df = _trim_trailing_rows(df, verbose=verbose)
    _validate_not_empty(df, path)
    alerts: list[str] = []

    # ── Normalización y validación de headers ────────────────────────────────────────────────────
    df.columns = _build_headers(original_cols)
    normalized_cols = list(df.columns)

    _validate_no_duplicate_headers(normalized_cols, path)
    _validate_schema(normalized_cols, path, sheet, alerts)

    renamed = [(o, n) for o, n in zip(original_cols, normalized_cols) if str(o) != n]
    if renamed:
        if verbose:
            _log("Headers renombrados (%d):", len(renamed))
            for orig, norm in renamed:
                _detail(
                    f"{orig!r} -> {norm!r}",
                    f"[dim]{rich_escape(repr(orig))}[/dim] -> [cyan]{rich_escape(repr(norm))}[/cyan]",
                )
            console.print()
        else:
            _log("Headers renombrados: %d.", len(renamed))
    else:
        _log("Headers: sin cambios necesarios.")

    # ── Clave y tipos ──────────────────────────────────────────────────────────────────────────────
    df, n_blank = _drop_blank_rows(df)
    # Primero se excluyen las filas con código SNIES inválido o repetido: así la clave
    # siempre se tipifica como entero único y las demás alertas no citan filas que no
    # se exportan.
    exclusion = _exclude_invalid_keys(df, alerts)
    df, excluded = exclusion.valid, exclusion.excluded
    if df.empty:
        raise FileProcessingError(
            f"'{path.name}': ninguna fila tiene un {KEY_COLUMN} válido (entero positivo)."
        )
    types = _convert_types(df, alerts)

    # ── Normalización de valores de texto ───────────────────────────────────────────────────────────
    total_modified, cols_with_changes, n_text_cols = _normalize_values(df, verbose=verbose)
    _log(
        "Valores normalizados: %s celda(s) modificada(s) en %d de %d columna(s).",
        f"{total_modified:,}",
        cols_with_changes,
        n_text_cols,
    )

    stats = {
        "headers_renamed": len(renamed),
        "rows_blank": n_blank,
        "rows_excluded": len(excluded),
        "rows_invalid": exclusion.n_invalid,
        "rows_repeated": exclusion.n_repeated,
        "rows_repeated_diff": exclusion.n_repeated_diff,
        "cells_modified": total_modified,
        "types_converted": types.converted,
        "types_valid": types.n_valid,
        "types_total": types.n_total,
        "alerts": alerts,
    }
    return df, excluded, stats


# =============================================================================
# Resumen de salida
# =============================================================================

_ALERTS_FIELD = "Alertas de validación"
_EXCLUDED_FIELD = "Filas excluidas"


def _print_table(title: str, rows: list[tuple[str, str]]) -> None:
    """Muestra filas campo/valor en consola y las escribe en el log bajo `title`."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Campo", style="bold")
    # fold: en consolas estrechas parte el valor en varias líneas en vez de truncarlo con "…".
    table.add_column("Valor", style="cyan", overflow="fold")
    for field, value in rows:
        text = rich_escape(value)
        if field in (_ALERTS_FIELD, _EXCLUDED_FIELD) and value != "0":
            text = f"[yellow]{text}[/yellow]"
        table.add_row(field, text)
    console.print(table)

    width = max(len(field) for field, _ in rows)
    file_log.info("%s:\n%s", title, "\n".join(f"    {f:<{width}}  {v}" for f, v in rows))


def _print_summary(
    df: pd.DataFrame,
    source_path: Path,
    output_path: Path,
    review_path: Path,
    standard_name: str,
    elapsed: float,
    stats: dict,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    rows: list[tuple[str, str]] = []
    if source_path.name != standard_name:
        rows.append(("Nombre fuente", f"{source_path.name} (esperado: {standard_name})"))
    rows.append(("Filas exportadas",     f"{len(df):,}"))
    if stats["rows_excluded"]:
        rows.append((
            _EXCLUDED_FIELD,
            f"{stats['rows_excluded']:,} (código SNIES inválido: {stats['rows_invalid']:,} · "
            f"repetido: {stats['rows_repeated']:,}, con diferencias: {stats['rows_repeated_diff']:,})",
        ))
    if stats["rows_blank"]:
        rows.append(("Filas vacías omitidas", f"{stats['rows_blank']:,}"))
    rows.append(("Columnas exportadas",  str(len(df.columns))))
    if verbose:
        col_names = list(df.columns)
        if len(col_names) <= 10:
            cols_str = ", ".join(col_names)
        else:
            cols_str = ", ".join(col_names[:10]) + f" (+{len(col_names) - 10} más)"
        rows.append(("Detalle columnas",     cols_str))
    rows.append(("Headers renombrados",  str(stats["headers_renamed"])))
    rows.append(("Celdas normalizadas",  f"{stats['cells_modified']:,}"))
    rows.append(("Tipos validados",      f"{stats['types_valid']} de {stats['types_total']}"))
    rows.append(("Tipos convertidos",    ", ".join(stats["types_converted"]) or "ninguno"))
    rows.append((_ALERTS_FIELD,          str(len(stats["alerts"]))))
    if dry_run:
        rows.append(("Archivo de salida",    f"{output_path} (no escrito — dry-run)"))
        rows.append(("Archivo de revisión",  f"{review_path} (no escrito — dry-run)"))
    else:
        size = output_path.stat().st_size
        size_str = (
            f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024**2:.2f} MB"
        )
        rows.append(("Archivo de salida",    str(output_path)))
        rows.append(("Tamaño",               size_str))
        rows.append(("Archivo de revisión",  f"{review_path} ({stats['rows_excluded']:,} fila(s))"))
    rows.append(("Tiempo transcurrido",  f"{elapsed:.2f}s"))
    console.print()
    _print_table(f"Resumen de '{source_path.name}'", rows)


# =============================================================================
# Exportación de un archivo procesado
# =============================================================================

# Mismo formato que la fuente (dd/mm/aaaa). pandas aplica _EXCEL_DATE_FORMAT a los date
# y _EXCEL_DATETIME_FORMAT a los datetime, así que la hora solo se muestra en las celdas
# que realmente la tienen (_excel_ready reduce a date los datetime a medianoche).
_EXCEL_DATE_FORMAT     = "DD/MM/YYYY"
_EXCEL_DATETIME_FORMAT = "DD/MM/YYYY HH:MM:SS"

# xlsxwriter se usa por su velocidad de escritura, superior a la de openpyxl. Los textos
# se escriben siempre como texto: sin estas opciones, un valor que empieza por '='
# se guardaría como fórmula y uno con forma de URL como hipervínculo.
_WRITE_ENGINE = "xlsxwriter"
_WRITER_OPTIONS = {"strings_to_formulas": False, "strings_to_urls": False}

# Excel no admite fechas anteriores a 1900. Para los seriales negativos (valores
# espurios que generan alerta por rango, ej. 29/12/1899 = -1), xlsxwriter produce un
# serial desplazado un día respecto del que interpreta openpyxl; _fix_pre_1900_dates
# los reescribe con el serial original.
_EXCEL_FIRST_DATE = datetime.date(1900, 1, 1)


def _excel_ready(df: pd.DataFrame) -> pd.DataFrame:
    """
    Copia con los datetime a medianoche reducidos a date (mismo serial en Excel), para
    que se exporten con _EXCEL_DATE_FORMAT sin '00:00:00'. En Salidas/ _to_date ya lo
    hizo en las columnas tipificadas; en Revision/ los valores van sin tipificar.
    """
    out = df
    for col in df.columns:
        values = df[col]
        midnight = values.map(
            lambda v: isinstance(v, datetime.datetime) and v.time() == datetime.time()
        ).astype(bool)
        if not midnight.any():
            continue
        if out is df:
            out = df.copy()
        # dtype=object: evita que pandas infiera otro tipo al reconstruir la columna.
        out[col] = pd.Series(
            [v.date() if m else v for v, m in zip(values, midnight)], index=values.index, dtype=object
        )
    return out


def _fix_pre_1900_dates(writer: pd.ExcelWriter, df: pd.DataFrame) -> None:
    """Reescribe las fechas anteriores a 1900 con el serial que les asigna openpyxl."""
    ws = writer.sheets[next(iter(writer.sheets))]
    formats: dict[str, object] = {}
    for j, col in enumerate(df.columns):
        for i, v in enumerate(df[col]):
            if isinstance(v, datetime.date) and _range_key(v) < _EXCEL_FIRST_DATE:
                fmt = _EXCEL_DATETIME_FORMAT if isinstance(v, datetime.datetime) else _EXCEL_DATE_FORMAT
                if fmt not in formats:
                    formats[fmt] = writer.book.add_format({"num_format": fmt})
                ws.write_number(i + 1, j, openpyxl_serial(v), formats[fmt])   # fila 0: headers


def _locked_error(output_path: Path) -> FileProcessingError:
    return FileProcessingError(
        f"No se pudo escribir '{output_path.name}'. "
        f"Si el archivo está abierto en Excel, ciérrelo y vuelva a ejecutar."
    )


def _ensure_writable(output_path: Path) -> None:
    """Falla rápido si Excel tiene el destino abierto, antes de exportar."""
    if not output_path.exists():
        return
    try:
        with open(output_path, "r+b"):
            pass
    except PermissionError:
        raise _locked_error(output_path) from None


def _export(df: pd.DataFrame, output_path: Path) -> None:
    """
    Escritura atómica: exporta a un archivo temporal en la misma carpeta, lo
    verifica y solo entonces reemplaza el destino. Si algo falla (o Ctrl+C),
    la salida anterior queda intacta y el temporal se elimina.
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FileProcessingError(
            f"No se pudo crear la carpeta de salida '{output_path.parent}': {exc}"
        ) from exc

    exists = output_path.exists()
    _ensure_writable(output_path)
    tmp_path = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")
    _log("%s '%s'...", "Reemplazando" if exists else "Creando", output_path.name)
    try:
        try:
            with (
                console.status(f"Exportando a '{output_path.name}'..."),
                pd.ExcelWriter(
                    tmp_path,
                    engine=_WRITE_ENGINE,
                    date_format=_EXCEL_DATE_FORMAT,
                    datetime_format=_EXCEL_DATETIME_FORMAT,
                    engine_kwargs={"options": _WRITER_OPTIONS},
                ) as writer,
            ):
                data = _excel_ready(df)
                data.to_excel(writer, index=False)
                _fix_pre_1900_dates(writer, data)
        except Exception as exc:
            raise FileProcessingError(
                f"Error al exportar '{output_path.name}': {type(exc).__name__}: {exc}"
            ) from exc

        _verify_output(df, tmp_path, output_path.name)

        try:
            os.replace(tmp_path, output_path)
        except PermissionError:
            raise _locked_error(output_path) from None
    finally:
        # No debe ocultar la excepción original si el temporal sigue bloqueado.
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
    _log("Escrito: %s", output_path)


# Una descarga truncada del SNIES conserva el esquema y pasa todas las validaciones:
# sin esta guarda reemplazaría la salida anterior (válida) por una incompleta.
_MAX_ROW_DROP = 0.10   # caída máxima tolerada de filas respecto de la salida anterior


def _previous_row_count(output_path: Path) -> int | None:
    """Filas de datos de la salida existente (instantáneo, por dimensiones); None si no hay o no se lee."""
    if not output_path.is_file():
        return None
    try:
        wb = openpyxl.load_workbook(output_path, read_only=True)
        try:
            max_row = wb.worksheets[0].max_row
        finally:
            wb.close()
    except Exception:
        return None
    return None if max_row is None else max(max_row - 1, 0)


def _check_row_drop(n_rows: int, output_path: Path, force: bool, alerts: list[str]) -> None:
    """
    Compara las filas a exportar con las de la salida anterior. Si caen más de
    _MAX_ROW_DROP, la fuente podría estar truncada: se aborta el archivo (la salida
    anterior queda intacta) salvo con --force, que exporta y deja la alerta.
    """
    previous = _previous_row_count(output_path)
    if not previous or n_rows >= previous * (1 - _MAX_ROW_DROP):
        return
    drop = 1 - n_rows / previous
    detail = (
        f"La fuente tiene {n_rows:,} fila(s) frente a {previous:,} de la salida anterior "
        f"({drop:.0%} menos; umbral {_MAX_ROW_DROP:.0%})"
    )
    if not force:
        raise FileProcessingError(
            f"{detail}. Posible fuente truncada: la salida anterior se conserva. "
            f"Si la reducción es legítima, ejecute con --force."
        )
    _alert(alerts, f"{detail}; se exporta igualmente por --force.")


def _blank_to_none(value):
    """Vacío (None, NaN, texto vacío) → None: así se escriben y se releen en Excel."""
    if value is None or (isinstance(value, str) and value == "") or (
        not isinstance(value, (str, bytes)) and pd.isna(value)
    ):
        return None
    return value


def _same_cell(expected, found) -> bool:
    """Igualdad de una celda tras el viaje de ida y vuelta a Excel (102.0 == 102; floats con tolerancia)."""
    expected, found = _blank_to_none(expected), _blank_to_none(found)
    if isinstance(expected, float) and isinstance(found, (int, float)):
        return math.isclose(expected, found, rel_tol=1e-12)
    return expected == found


def _verify_output(df: pd.DataFrame, path: Path, label: str) -> None:
    """
    Relee el archivo exportado y verifica que headers (nombres y orden), número de
    filas y valores de la columna clave (en su orden) coincidan con el DataFrame.
    Las filas se cuentan con las dimensiones de la hoja (instantáneo) y solo se
    relee la columna clave.
    """
    key_values: list | None = None
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        try:
            ws = wb.worksheets[0]
            headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
            data_rows = ws.max_row - 1
        finally:
            wb.close()
        if KEY_COLUMN in headers:
            # calamine: la relectura de una sola columna es mucho más rápida que
            # recorrer la hoja con openpyxl (read_only).
            key_values = pd.read_excel(
                path, engine=_READ_ENGINE, usecols=[KEY_COLUMN], dtype=object
            )[KEY_COLUMN].tolist()
    except Exception as exc:
        raise FileProcessingError(
            f"Verificación fallida: no se pudo releer '{label}': "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    expected = list(df.columns)
    if headers != expected:
        missing = [c for c in expected if c not in headers]
        extra = [c for c in headers if c not in expected]
        raise FileProcessingError(
            f"Verificación fallida en '{label}': los headers no coinciden "
            f"(esperadas {len(expected)}, encontradas {len(headers)}; "
            f"faltantes: {missing}; inesperadas: {extra})."
        )
    if data_rows != len(df):
        raise FileProcessingError(
            f"Verificación fallida en '{label}': "
            f"se esperaban {len(df):,} filas, se encontraron {data_rows:,}."
        )
    if key_values is not None:
        bad = [
            (i, exp, got)
            for i, (exp, got) in enumerate(zip_longest(df[KEY_COLUMN], key_values))
            if not _same_cell(exp, got)
        ]
        if bad:
            i, exp, got = bad[0]
            raise FileProcessingError(
                f"Verificación fallida en '{label}': {len(bad):,} valor(es) de {KEY_COLUMN} "
                f"no coinciden con los datos a exportar; primero: fila {i + _EXCEL_ROW_OFFSET:,} "
                f"(se esperaba {exp!r}, se encontró {got!r})."
            )
    _log(
        "Verificación OK — %d columna(s) y %s fila(s) confirmadas en '%s'.",
        len(headers),
        f"{data_rows:,}",
        label,
    )


# =============================================================================
# Ejecución principal
# =============================================================================

def _exit(code: int) -> NoReturn:
    """Cierra el log con el código de salida y termina el proceso."""
    file_log.info("Fin de la ejecución — código de salida: %d.", code)
    sys.exit(code)


def main(argv: list[str] | None = None) -> NoReturn:
    """
    Punto de entrada: termina con _exit (código 0, 1 o 130). Con argumentos
    inválidos argparse termina antes, con código 2 y sin crear log.
    """
    setup_console_streams()
    setup_logging()
    args = _parse_args(argv)
    console.print()
    if args.dry_run:
        console.rule("[bold yellow]ETL — Programas (DRY-RUN)[/bold yellow]")
    else:
        console.rule("[bold white]ETL — Programas[/bold white]")
    console.print()

    log_path = setup_file_logging()
    file_log.info(
        "Inicio — modo: %s · --strict: %s · --force: %s · --file: %s · --verbose: %s · Python %s · pandas %s",
        "dry-run" if args.dry_run else "normal",
        "sí" if args.strict else "no",
        "sí" if args.force else "no",
        args.file or "todos",
        "sí" if args.verbose else "no",
        sys.version.split()[0],
        pd.__version__,
    )
    if log_path:
        _log("Log de esta ejecución: %s", log_path)

    try:
        _validate_directories()

        # ── Localizar el archivo fuente ────────────────────────────────────────
        sources: list[tuple[Path, Path, str]] = []
        for entry in _SOURCE_FILES:
            found = find_source_file(entry["stem"], entry["standard_name"])
            if found:
                output_path = OUTPUT_DIR / entry["standard_name"]
                sources.append((found, output_path, entry["standard_name"]))

        if not sources:
            logger.error(
                "No se encontró el archivo fuente en %s. Se esperaba: %s.",
                FUENTES_DIR,
                [e["standard_name"] for e in _SOURCE_FILES],
            )
            console.print()
            _exit(1)

        if args.file:
            file_lower = args.file.lower()
            sources = [
                (s, o, sn) for s, o, sn in sources
                if s.name.lower() == file_lower or sn.lower() == file_lower
            ]
            if not sources:
                logger.error(
                    "El archivo '%s' no se encontró entre las fuentes disponibles.",
                    args.file,
                )
                console.print()
                _exit(1)

        _log(
            "Archivo(s) a procesar: %s",
            [s.name for s, _, _ in sources],
        )

        # ── Procesar y exportar el archivo fuente ───────────────────────────────────────────
        failed: list[tuple[str, str]] = []
        total_alerts = 0

        for source_path, output_path, standard_name in sources:
            console.print()
            console.rule(f"[bold white]{rich_escape(source_path.name)}[/bold white]")
            console.print()
            file_log.info("── Procesando '%s' ──", source_path.name)

            try:
                file_start = time.perf_counter()
                df, excluded, stats = process_programas(source_path, verbose=args.verbose)
                _check_row_drop(len(df), output_path, args.force, stats["alerts"])
                # Se escribe siempre (aunque no haya exclusiones) para que la revisión
                # corresponda a esta ejecución y no quede una de una ejecución anterior.
                review_path = REVIEW_DIR / f"{REVIEW_PREFIX}{standard_name}"
                if args.dry_run:
                    _log("Dry-run: '%s' y '%s' no se escribirán.", output_path.name, review_path.name)
                else:
                    # Ambos destinos se comprueban antes de escribir: si uno está abierto
                    # en Excel no se actualiza solo el otro.
                    _ensure_writable(output_path)
                    _ensure_writable(review_path)
                    _export(df, output_path)
                    _export(excluded, review_path)
                _print_summary(
                    df,
                    source_path,
                    output_path,
                    review_path,
                    standard_name,
                    time.perf_counter() - file_start,
                    stats,
                    dry_run=args.dry_run,
                    verbose=args.verbose,
                )
                total_alerts += len(stats["alerts"])
            except FileProcessingError as exc:
                _log("FALLO — %s: %s", source_path.name, exc, level="error")
                failed.append((source_path.name, str(exc)))
            except Exception as exc:
                # Un error imprevisto se registra como fallo del archivo para cerrar la
                # ejecución con resumen, log y código de salida, en lugar de abortarla.
                logger.exception(
                    "FALLO INESPERADO — %s: %s: %s", source_path.name, type(exc).__name__, exc
                )
                console.print()
                failed.append((source_path.name, f"{type(exc).__name__}: {exc}"))

        console.print()

        # --strict: las alertas cuentan como fallo en el código de salida.
        strict_failed = args.strict and total_alerts > 0

        if failed:
            result, color, level = "PROCESO COMPLETADO CON ERRORES", "red", "error"
        elif strict_failed:
            written_note = "sin archivos escritos" if args.dry_run else "archivos escritos"
            result = (
                f"PROCESO FALLIDO — {total_alerts} alerta(s) en modo --strict ({written_note})"
            )
            color, level = "red", "error"
        elif args.dry_run:
            alerts_note = f" · {total_alerts} alerta(s)" if total_alerts else ""
            result = f"DRY-RUN COMPLETADO — sin archivos escritos{alerts_note}"
            color, level = "yellow", "warning" if total_alerts else "info"
        elif total_alerts:
            result, color, level = "PROCESO COMPLETADO CON ALERTAS", "yellow", "warning"
        else:
            result, color, level = "PROCESO COMPLETADO", "green", "info"
        console.rule(f"[bold {color}]{rich_escape(result)}[/bold {color}]")
        console.print()
        getattr(file_log, level)("Resultado: %s.", result)
        _exit(1 if failed or strict_failed else 0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Ejecución cancelada por el usuario (Ctrl+C).[/yellow]")
        file_log.warning("Ejecución cancelada por el usuario (Ctrl+C).")
        _exit(130)
    except Exception as exc:
        logger.exception("ERROR INESPERADO %s: %s", type(exc).__name__, exc)
        _exit(1)


if __name__ == "__main__":
    main()
