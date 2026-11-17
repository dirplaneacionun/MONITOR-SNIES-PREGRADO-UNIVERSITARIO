"""
run_snies.py
------------
Orquestador semanal del monitor SNIES de programas universitarios de pregrado.

Flujo:
    1. Descarga el reporte de programas del portal SNIES con los filtros:
       institución activa, programa activo, pregrado, universitario.
    2. Normaliza el archivo con ETL-PROGRAMAS-SNIES (Scripts/ETL-PROGRAMAS-SNIES).
    3. Clasifica las divisiones con CLASIFICAR-DIVISIONES-UNINORTE-SNIES (Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES),
       procesando únicamente Programas.xlsx.
    4. Guarda el resultado en "Programas Activos/Programas Activos AAAA-MM-DD.xlsx".
    5. Compara con la ejecución anterior y detecta programas nuevos (o reactivados),
       modificados e inactivos.
    6. Acumula las novedades en Novedades/*.parquet y guarda el archivo completo en
       Historico/Historico Programas Activos AAAA-MM-DD.parquet (un archivo por ejecución).
    7. Aplica la retención (20 archivos en Programas Activos, 20 logs).
    8. Actualiza los datos del Dashboard (docs/data) y envía el correo institucional.

Si una etapa falla no se envía correo a DESTINATARIOS: se envía una alerta técnica
solo a la cuenta remitente (SMTP_USER) y el proceso termina con código 1. Si lo que falla
es el envío del reporte, los datos y el Dashboard ya quedan actualizados, pero el proceso
también termina con código 1 y se intenta la alerta técnica.

Ejecución:
    python Scripts/run_snies.py                      # ejecución semanal normal
    python Scripts/run_snies.py --sin-correo         # sin enviar correos
    python Scripts/run_snies.py --archivo RUTA.xlsx --fecha AAAA-MM-DD
                                                     # procesa un archivo ya descargado
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import historico
from catalogos import GRUPOS_UNIVERSIDAD, grupo_universidad

# ── Rutas ─────────────────────────────────────────────────────────────────────
SCRIPTS_DIR    = Path(__file__).resolve().parent
ROOT           = SCRIPTS_DIR.parent
ETL_DIR        = SCRIPTS_DIR / "ETL-PROGRAMAS-SNIES"
MAP_DIR        = SCRIPTS_DIR / "CLASIFICAR-DIVISIONES-UNINORTE-SNIES"
ETL_SCRIPT     = ETL_DIR / "Scripts" / "etl_programas.py"
MAP_SCRIPT     = MAP_DIR / "Scripts" / "pipeline_divisiones.py"
PROGRAMAS_DIR  = ROOT / "Programas Activos"
NOVEDADES_DIR  = ROOT / "Novedades"
LOGS_DIR       = ROOT / "Logs"
TMP_DIR        = ROOT / "tmp"

NUEVOS_FILE      = NOVEDADES_DIR / "Nuevos.parquet"
MODIFICADOS_FILE = NOVEDADES_DIR / "Modificados.parquet"
INACTIVOS_FILE   = NOVEDADES_DIR / "Inactivos.parquet"

# ── Retención ─────────────────────────────────────────────────────────────────
MAX_PROGRAMAS_ACTIVOS = 20   # archivos Excel semanales en Programas Activos/
MAX_LOGS              = 20   # logs consolidados en Logs/

# ── Parámetros ────────────────────────────────────────────────────────────────
ZONA_HORARIA     = ZoneInfo("America/Bogota")
SNIES_URL        = "https://hecaa.mineducacion.gov.co/consultaspublicas/programas"
DOWNLOAD_TIMEOUT = 120         # segundos máximos esperando la descarga
UMBRAL_MAXIMO    = 10_000      # más programas que esto indica una descarga sin filtros
SUBPROCESO_TIMEOUT = 1_800     # segundos máximos por herramienta (ETL o clasificador)

COL_SNIES = "CODIGO_SNIES_DEL_PROGRAMA"
COL_FECHA = "FECHA_DE_OBTENCION"
COLS_CLASIFICACION = ["DIVISION_UNINORTE", "PUNTAJE_TOTAL"]
# Columnas que ya no se guardan (el clasificador dejó de exportar REQUIERE_REVISION: los programas por revisar
# están en su lista de revisión). Se retiran también de los archivos acumulados.
COLS_OBSOLETAS = ["REQUIERE_REVISION"]
REVISION_FILE = SCRIPTS_DIR / "CLASIFICAR-DIVISIONES-UNINORTE-SNIES" / "Revision" / "Revision Programas.xlsx"

# Columnas vigiladas para detectar modificados → valor de TIPO_CAMBIO_DETECTADO
COLS_VIGILAR = {
    "NUMERO_CREDITOS":             "NUMERO DE CREDITOS",
    "MUNICIPIO_OFERTA_PROGRAMA":   "MUNICIPIO DE OFERTA",
    "NOMBRE_DEL_PROGRAMA":         "NOMBRE DEL PROGRAMA",
    "NUMERO_PERIODOS_DE_DURACION": "NUMERO DE PERIODOS",
    "MODALIDAD":                   "MODALIDAD",
}

_ARCHIVO_RE = re.compile(r"^Programas Activos (\d{4}-\d{2}-\d{2})\.xlsx$")

XPATHS = {
    "descarga": '//button[.//span[normalize-space()="Descargar programas"]]',
}

log = logging.getLogger("snies_monitor")


class EtapaError(Exception):
    """Falla de una etapa del flujo: detiene la ejecución y genera la alerta técnica."""

    def __init__(self, etapa: str, mensaje: str):
        super().__init__(mensaje)
        self.etapa = etapa


# ── Logging consolidado ───────────────────────────────────────────────────────

def _rutas_privadas() -> list[tuple[str, str]]:
    """Rutas locales que no deben quedar en logs publicados (carpeta del usuario y temporal) → marcador."""
    import tempfile
    pares = []
    for ruta, marca in ((tempfile.gettempdir(), "<TEMP>"), (str(Path.home()), "~")):
        for variante in {ruta, ruta.replace("\\", "/")}:
            if len(variante) > 3:
                pares.append((variante, marca))
    return pares


class _FormatoSeguro(logging.Formatter):
    """Formato de log que oculta rutas personales (carpeta del usuario y temporal) antes de escribir en consola o archivo."""

    _pares = _rutas_privadas()

    def format(self, record):
        texto = super().format(record)
        for ruta, marca in self._pares:
            texto = texto.replace(ruta, marca)
        return texto


def configurar_logging(ahora: dt.datetime, prefijo: str = "monitor") -> Path:
    """Log en consola y en Logs/<prefijo>_AAAA-MM-DD_HH-MM-SS.log; conserva los últimos MAX_LOGS por prefijo."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    sello = ahora.strftime("%Y-%m-%d_%H-%M-%S")
    log_path = LOGS_DIR / f"{prefijo}_{sello}.log"
    n = 2
    while log_path.exists():
        log_path = LOGS_DIR / f"{prefijo}_{sello}_{n}.log"
        n += 1

    formato = _FormatoSeguro("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    # Hora de Colombia en cada línea, igual que en el nombre del archivo (el servidor de GitHub Actions usa UTC)
    formato.converter = lambda t: dt.datetime.fromtimestamp(t, ZONA_HORARIA).timetuple()
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    archivo = logging.FileHandler(log_path, encoding="utf-8")
    archivo.setFormatter(formato)
    log.handlers[:] = [consola, archivo]
    log.setLevel(logging.INFO)
    log.propagate = False

    patron = re.compile(rf"^{re.escape(prefijo)}_\d{{4}}-\d{{2}}-\d{{2}}_\d{{2}}-\d{{2}}-\d{{2}}(?:_\d+)?\.log$")
    _depurar(LOGS_DIR, patron, MAX_LOGS, "log")
    return log_path


def _ruta(p: Path) -> str:
    """Ruta relativa al proyecto cuando es posible (más legible en el log)."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _depurar(carpeta: Path, patron: re.Pattern, maximo: int, etiqueta: str) -> list[Path]:
    """Conserva los `maximo` archivos más recientes que cumplen `patron` (orden por nombre = cronológico)."""
    archivos = sorted(f for f in carpeta.iterdir() if f.is_file() and patron.match(f.name))
    eliminados = archivos[:-maximo] if len(archivos) > maximo else []
    for f in eliminados:
        f.unlink()
        log.info(f"  Retención: eliminado {etiqueta} antiguo {f.name}")
    return eliminados


def _seccion(titulo: str) -> None:
    log.info("─" * 70)
    log.info(titulo)
    log.info("─" * 70)


# ── Descarga (Selenium) ───────────────────────────────────────────────────────

def _build_driver(download_dir: Path, headless: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
        },
    )
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)


def _safe_click(driver, xpath: str, timeout: int = 15) -> None:
    from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    locator = (By.XPATH, xpath)
    for attempt in range(2):
        try:
            WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator)).click()
            return
        except (StaleElementReferenceException, TimeoutException):
            if attempt == 1:
                raise
            time.sleep(2)


def _pf_select_radio(driver, label_prefix: str, *, exact: bool = False, timeout: int = 30) -> None:
    """
    Selecciona un radio button de PrimeFaces por el texto de su <label>, activando el
    <input> oculto y disparando su evento 'change' (invoca el PrimeFaces.ab del portal).
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    xpath = (
        f'//label[normalize-space()="{label_prefix}"]'
        if exact
        else f'//label[starts-with(normalize-space(),"{label_prefix}")]'
    )
    label_el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, xpath)))
    input_id = label_el.get_attribute("for")
    log.info(f"  Filtro {label_prefix!r} → input#{input_id}")
    driver.execute_script(
        """
        var el = document.getElementById(arguments[0]);
        if (el && !el.checked) {
            el.checked = true;
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }
        """,
        input_id,
    )


def _wait_ajax(driver, timeout: int = 20) -> None:
    """Espera a que terminen las peticiones AJAX de PrimeFaces/jQuery tras un filtro."""
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script(
                "return (typeof jQuery === 'undefined' || jQuery.active === 0) && "
                "(typeof PrimeFaces === 'undefined' || PrimeFaces.ajax.Queue.isEmpty())"
            )
        )
    except TimeoutException:
        log.warning("  AJAX no terminó en %ds; se continúa.", timeout)


def descargar_snies(download_dir: Path) -> Path:
    """
    Descarga el reporte de programas del SNIES con los filtros:
      "Activo" (estado de la institución), "Activo (" (estado del programa),
      "Pregrado (" (nivel académico) y "Universitario (" (nivel de formación).
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    expected_file = download_dir / "Programas.xlsx"
    partial_file  = download_dir / "Programas.crdownload"
    for f in (expected_file, partial_file):
        if f.exists():
            f.unlink()

    headless = os.environ.get("SNIES_HEADLESS", "1") != "0"
    driver = _build_driver(download_dir, headless=headless)
    try:
        log.info("  Abriendo el portal SNIES...")
        driver.get(SNIES_URL)
        time.sleep(8)
        driver.save_screenshot(str(TMP_DIR / "debug_snies.png"))

        log.info("  Aplicando filtros...")
        for etiqueta, exacto, espera in (
            ("Activo", True, 3),
            ("Activo (", False, 3),
            ("Pregrado (", False, 3),
            ("Universitario (", False, 5),
        ):
            _pf_select_radio(driver, etiqueta, exact=exacto)
            _wait_ajax(driver)
            time.sleep(espera)
        driver.save_screenshot(str(TMP_DIR / "debug_post_filtros.png"))

        log.info("  Solicitando descarga...")
        _safe_click(driver, XPATHS["descarga"])

        elapsed = 0
        while elapsed < DOWNLOAD_TIMEOUT:
            time.sleep(5)
            elapsed += 5
            if expected_file.exists() and not partial_file.exists():
                log.info(f"  Descarga completada en {elapsed}s.")
                break
        else:
            raise TimeoutError(
                f"El archivo no apareció tras {DOWNLOAD_TIMEOUT}s. "
                "Verifique que el portal no haya cambiado."
            )
    finally:
        driver.quit()
    return expected_file


# ── Herramientas externas (ETL y clasificador) ────────────────────────────────

def _log_mas_reciente(carpeta: Path, patron: str, desde: float) -> Path | None:
    candidatos = [f for f in carpeta.glob(patron) if f.stat().st_mtime >= desde - 1]
    return max(candidatos, key=lambda f: f.stat().st_mtime) if candidatos else None


def _ejecutar_herramienta(etapa: str, cwd: Path, comando: list[str], logs_dir: Path,
                          patron_log: str) -> None:
    """Ejecuta una herramienta, incorpora su log al consolidado y falla si termina con error."""
    inicio = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "COLUMNS": "120"}
    try:
        proc = subprocess.run(
            comando, cwd=cwd, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=SUBPROCESO_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise EtapaError(etapa, f"La herramienta superó {SUBPROCESO_TIMEOUT}s sin terminar.") from exc

    log_herramienta = _log_mas_reciente(logs_dir, patron_log, inicio)
    if log_herramienta is not None:
        log.info(f"  Log de la herramienta: {_ruta(log_herramienta)}")
        contenido = log_herramienta.read_text(encoding="utf-8", errors="replace").rstrip()
        log.info("  ┌─ inicio del log de %s\n%s\n  └─ fin del log de %s", etapa, contenido, etapa)
    else:
        log.warning(f"  La herramienta no generó log; salida de consola:\n{proc.stdout[-6000:]}")
    if proc.stderr.strip():
        log.info(f"  Salida de error de la herramienta:\n{proc.stderr.strip()[-4000:]}")

    if proc.returncode != 0:
        raise EtapaError(etapa, f"La herramienta terminó con código {proc.returncode}. Revise su log.")
    log.info(f"  {etapa} completado en {time.time() - inicio:.1f}s.")


def normalizar_etl(archivo_crudo: Path) -> Path:
    """Copia el archivo a Fuentes/ del ETL, lo ejecuta y retorna Salidas/Programas.xlsx."""
    fuente = ETL_DIR / "Fuentes" / "Programas.xlsx"
    salida = ETL_DIR / "Salidas" / "Programas.xlsx"
    fuente.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archivo_crudo, fuente)
    inicio = time.time()
    _ejecutar_herramienta(
        "NORMALIZACION (ETL-PROGRAMAS-SNIES)", ETL_DIR,
        [sys.executable, str(ETL_SCRIPT)], ETL_DIR / "Logs", "etl_*.log",
    )
    if not salida.exists() or salida.stat().st_mtime < inicio - 1:
        raise EtapaError("NORMALIZACION (ETL-PROGRAMAS-SNIES)", "El ETL no actualizó Salidas/Programas.xlsx.")
    return salida


def clasificar_divisiones(archivo_normalizado: Path) -> Path:
    """Copia el archivo a Fuentes/ del clasificador, lo ejecuta (-a 1) y retorna Salidas/Programas.xlsx."""
    fuente = MAP_DIR / "Fuentes" / "Programas.xlsx"
    salida = MAP_DIR / "Salidas" / "Programas.xlsx"
    shutil.copy2(archivo_normalizado, fuente)
    inicio = time.time()
    _ejecutar_herramienta(
        "CLASIFICACION (CLASIFICAR-DIVISIONES-UNINORTE-SNIES)", MAP_DIR,
        [sys.executable, str(MAP_SCRIPT), "-a", "1"], MAP_DIR / "Logs", "pipeline_*.log",
    )
    if not salida.exists() or salida.stat().st_mtime < inicio - 1:
        raise EtapaError("CLASIFICACION (CLASIFICAR-DIVISIONES-UNINORTE-SNIES)",
                         "El clasificador no actualizó Salidas/Programas.xlsx.")
    return salida


# ── Lectura y escritura ───────────────────────────────────────────────────────

def leer_programas(path: Path) -> pd.DataFrame:
    """
    Lee un archivo de programas conservando el tipo de cada celda (dtype=object): sin esto pandas convierte a
    número los textos numéricos y trunca los códigos largos (ej. CODIGO_ANTERIOR_ICFES de 21 dígitos) o deja
    sufijos ".0" en códigos y conteos guardados como texto.
    """
    df = pd.read_excel(path, engine="calamine", dtype=object)
    df[COL_SNIES] = pd.to_numeric(df[COL_SNIES], errors="raise").astype("int64")
    return df


def _tipos_estables(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tipos compatibles con parquet: columnas de fechas (con o sin hora) → datetime; columnas
    con tipos mezclados (ej. 123 y '0031') → texto, sin perder ceros a la izquierda.
    """
    import numpy as np

    df = df.copy()
    fechas = {dt.date, dt.datetime, pd.Timestamp, np.datetime64}
    for col in df.columns:
        if df[col].dtype != object:
            continue
        tipos = {type(v) for v in df[col].dropna()}
        if tipos and tipos <= fechas:
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif len(tipos) > 1:
            df[col] = df[col].map(lambda v: v if pd.isna(v) else str(v))
    return df


def _escribir_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    _tipos_estables(df).to_parquet(tmp, index=False)
    os.replace(tmp, path)


def acumular_parquet(path: Path, nuevos: pd.DataFrame, fecha: dt.date, orden: list[str]) -> int:
    """Agrega los registros de `fecha` al parquet (reemplaza los de esa fecha si ya existían)."""
    marca = pd.Timestamp(fecha)
    partes = []
    if path.exists():
        existente = pd.read_parquet(path)
        existente = existente[pd.to_datetime(existente[COL_FECHA]) != marca]
        partes.append(existente)
    if not nuevos.empty:
        partes.append(nuevos)
    if not partes:
        return 0
    combinado = pd.concat(partes, ignore_index=True) if len(partes) > 1 else partes[0]
    combinado = combinado.drop(columns=[c for c in COLS_OBSOLETAS if c in combinado.columns])
    columnas = [c for c in orden if c in combinado.columns] + [c for c in combinado.columns if c not in orden]
    combinado = combinado[columnas].sort_values([COL_FECHA, COL_SNIES], kind="stable", ignore_index=True)
    _escribir_parquet(combinado, path)
    return len(combinado)


def archivo_anterior(fecha: dt.date) -> tuple[dt.date, Path] | None:
    """Archivo de Programas Activos más reciente con fecha estrictamente anterior a `fecha`."""
    candidatos = []
    for f in PROGRAMAS_DIR.glob("Programas Activos *.xlsx"):
        m = _ARCHIVO_RE.match(f.name)
        if m:
            f_fecha = dt.date.fromisoformat(m.group(1))
            if f_fecha < fecha:
                candidatos.append((f_fecha, f))
    return max(candidatos) if candidatos else None


# ── Detección de novedades ────────────────────────────────────────────────────

def _valor(v) -> str:
    """Representación comparable y legible de un valor vigilado."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _snies_conocidos(fecha: dt.date) -> set[int]:
    """SNIES que ya existieron antes de `fecha`: en el histórico de activos o en Inactivos."""
    partes = [historico.leer([COL_SNIES], antes_de=fecha)[COL_SNIES]]
    if INACTIVOS_FILE.exists():
        df = pd.read_parquet(INACTIVOS_FILE, columns=[COL_SNIES, COL_FECHA])
        partes.append(df.loc[pd.to_datetime(df[COL_FECHA]) < pd.Timestamp(fecha), COL_SNIES])
    conocidos: set[int] = set()
    for serie in partes:
        conocidos |= set(pd.to_numeric(serie, errors="coerce").dropna().astype("int64"))
    return conocidos


def _ultimo_valor_conocido(fecha: dt.date, col: str, snies: list[int]) -> dict[int, str]:
    """Último valor no vacío de `col` en el histórico antes de `fecha`, por SNIES."""
    if not snies:
        return {}
    h = historico.leer([COL_SNIES, COL_FECHA, col], antes_de=fecha)
    h = h[h[COL_SNIES].isin(snies)]
    h = h.assign(_v=h[col].map(_valor))
    h = h[h["_v"] != ""].sort_values(COL_FECHA)
    return h.groupby(COL_SNIES)["_v"].last().to_dict()


def detectar_novedades(hoy: pd.DataFrame, anterior: pd.DataFrame, fecha: dt.date):
    """
    Nuevos: SNIES activos hoy que no estaban en la ejecución anterior (TIPO_NOVEDAD: NUEVO o
    REACTIVADO si ya existió antes). Inactivos: SNIES de la ejecución anterior que hoy no están.
    Modificados: un registro por cada columna vigilada que cambió. Un dato que desaparece no es
    un cambio; si el valor anterior estaba vacío se compara contra el último valor conocido.
    """
    snies_hoy, snies_ant = set(hoy[COL_SNIES]), set(anterior[COL_SNIES])
    marca = pd.Timestamp(fecha)

    nuevos = hoy[hoy[COL_SNIES].isin(snies_hoy - snies_ant)].copy()
    nuevos[COL_FECHA] = marca
    conocidos = _snies_conocidos(fecha)
    nuevos["TIPO_NOVEDAD"] = nuevos[COL_SNIES].map(lambda s: "REACTIVADO" if s in conocidos else "NUEVO")

    inactivos = anterior[anterior[COL_SNIES].isin(snies_ant - snies_hoy)].copy()
    inactivos[COL_FECHA] = marca

    comunes = snies_hoy & snies_ant
    hoy_c = hoy[hoy[COL_SNIES].isin(comunes)].set_index(COL_SNIES)
    ant_c = anterior[anterior[COL_SNIES].isin(comunes)].set_index(COL_SNIES)
    filas = []
    for col, tipo in COLS_VIGILAR.items():
        if col not in hoy_c.columns or col not in ant_c.columns:
            log.warning(f"  Columna vigilada ausente: {col}")
            continue
        v_hoy = hoy_c[col].map(_valor)
        v_ant = ant_c[col].reindex(v_hoy.index).map(_valor)
        distintos = v_hoy.index[v_hoy != v_ant]
        sin_dato_hoy = [s for s in distintos if v_hoy[s] == ""]
        if sin_dato_hoy:
            log.warning(f"  {col}: {len(sin_dato_hoy)} programa(s) sin dato hoy (no se registran como cambio): "
                        f"{sin_dato_hoy[:10]}")
        sin_dato_ant = [s for s in distintos if v_ant[s] == "" and v_hoy[s] != ""]
        ultimos = _ultimo_valor_conocido(fecha, col, sin_dato_ant)
        for snies in distintos:
            nuevo, viejo = v_hoy[snies], v_ant[snies]
            if nuevo == "":
                continue
            if viejo == "":
                viejo = ultimos.get(snies, "")
                if viejo in ("", nuevo):
                    continue
            registro = hoy_c.loc[snies].to_dict()
            registro[COL_SNIES] = snies
            registro[COL_FECHA] = marca
            registro["TIPO_CAMBIO_DETECTADO"] = tipo
            registro["CAMBIO_DETECTADO"] = f"{viejo} → {nuevo}"
            filas.append(registro)
    modificados = pd.DataFrame(filas)
    return nuevos, modificados, inactivos


# ── Flujo principal ───────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Monitor SNIES de programas universitarios de pregrado.")
    p.add_argument("--archivo", type=Path, help="Procesa un Excel ya descargado en lugar de descargarlo.")
    p.add_argument("--fecha", type=dt.date.fromisoformat,
                   help="Fecha de obtención AAAA-MM-DD (por defecto, hoy en hora de Colombia).")
    p.add_argument("--sin-correo", action="store_true", help="No envía correos (pruebas).")
    return p.parse_args(argv)


def ejecutar(args: argparse.Namespace, fecha: dt.date) -> dict:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    for carpeta in (PROGRAMAS_DIR, NOVEDADES_DIR, historico.HISTORICO_DIR):
        carpeta.mkdir(parents=True, exist_ok=True)

    # 1. Descarga
    _seccion("1. DESCARGA DEL REPORTE SNIES")
    if args.archivo:
        if not args.archivo.exists():
            raise EtapaError("DESCARGA", f"No existe el archivo indicado: {args.archivo}")
        crudo = args.archivo
        log.info(f"  Se usa el archivo indicado: {crudo}")
    else:
        try:
            crudo = descargar_snies(TMP_DIR / "descarga")
        except Exception as exc:
            raise EtapaError("DESCARGA", f"{type(exc).__name__}: {exc}") from exc

    # 2. Normalización
    _seccion("2. NORMALIZACION (ETL-PROGRAMAS-SNIES)")
    normalizado = normalizar_etl(crudo)

    # 3. Clasificación
    _seccion("3. CLASIFICACION (CLASIFICAR-DIVISIONES-UNINORTE-SNIES)")
    clasificado = clasificar_divisiones(normalizado)

    # 4. Validación del resultado
    _seccion("4. VALIDACION DEL ARCHIVO PROCESADO")
    try:
        hoy = leer_programas(clasificado)
        n_etl = len(pd.read_excel(normalizado, engine="calamine", usecols=[COL_SNIES]))
    except Exception as exc:
        raise EtapaError("VALIDACION", f"No se pudo leer el archivo procesado: {exc}") from exc
    faltantes = [c for c in COLS_CLASIFICACION if c not in hoy.columns]
    if faltantes:
        raise EtapaError("VALIDACION", f"Faltan columnas de clasificación: {faltantes}")
    if len(hoy) != n_etl:
        raise EtapaError("VALIDACION", f"El clasificador devolvió {len(hoy)} filas y el ETL {n_etl}.")
    if len(hoy) > UMBRAL_MAXIMO:
        raise EtapaError("VALIDACION", f"{len(hoy):,} programas: probable descarga sin filtros.")
    if hoy[COL_SNIES].duplicated().any():
        raise EtapaError("VALIDACION", "Hay códigos SNIES repetidos en el archivo procesado.")
    try:
        revision = len(pd.read_excel(REVISION_FILE, engine="calamine")) if REVISION_FILE.exists() else 0
    except Exception:
        revision = "desconocido"
    hoy = hoy.drop(columns=[c for c in COLS_OBSOLETAS if c in hoy.columns])
    log.info(f"  Programas activos: {len(hoy):,} · columnas: {hoy.shape[1]} · requieren revisión: {revision} (lista: {_ruta(REVISION_FILE)})")

    # 5. Detección
    _seccion("5. DETECCION DE NOVEDADES")
    previo = archivo_anterior(fecha)
    anterior = None
    orden_base = list(hoy.columns) + [COL_FECHA]
    if previo is None:
        log.warning("  No hay una ejecución anterior en Programas Activos: esta queda como línea base.")
        nuevos = modificados = inactivos = pd.DataFrame()
    else:
        fecha_ant, path_ant = previo
        anterior = leer_programas(path_ant)
        log.info(f"  Ejecución anterior: {path_ant.name} ({len(anterior):,} programas)")
        nuevos, modificados, inactivos = detectar_novedades(hoy, anterior, fecha)
        reactivados = int((nuevos.get("TIPO_NOVEDAD", pd.Series(dtype=str)) == "REACTIVADO").sum())
        log.info(f"  Nuevos: {len(nuevos)} (reactivados: {reactivados}) · "
                 f"Modificados: {len(modificados)} cambios en "
                 f"{modificados[COL_SNIES].nunique() if not modificados.empty else 0} programas · "
                 f"Inactivos: {len(inactivos)}")
        if not modificados.empty:
            for tipo, n in modificados["TIPO_CAMBIO_DETECTADO"].value_counts().items():
                log.info(f"    {tipo}: {n}")

    # 6. Guardado
    _seccion("6. GUARDADO")
    destino = PROGRAMAS_DIR / f"Programas Activos {fecha.isoformat()}.xlsx"
    shutil.copy2(clasificado, destino)
    log.info(f"  {destino.relative_to(ROOT)} ({len(hoy):,} programas)")

    hoy_hist = hoy.copy()
    hoy_hist[COL_FECHA] = pd.Timestamp(fecha)
    path_hist = historico.ruta(fecha)
    hoy_hist = historico.alinear(hoy_hist[orden_base], historico.esquema_referencia(fecha))  # mismos tipos que el histórico
    _escribir_parquet(hoy_hist.sort_values(COL_SNIES, kind="stable", ignore_index=True), path_hist)
    log.info(f"  {path_hist.relative_to(ROOT)}: {len(hoy_hist):,} registros "
             f"(histórico: {len(historico.archivos())} ejecuciones)")

    if previo is not None:
        for path, df, extra in (
            (NUEVOS_FILE, nuevos, ["TIPO_NOVEDAD"]),
            (MODIFICADOS_FILE, modificados, ["TIPO_CAMBIO_DETECTADO", "CAMBIO_DETECTADO"]),
            (INACTIVOS_FILE, inactivos, []),
        ):
            total = acumular_parquet(path, df, fecha, orden_base + extra)
            log.info(f"  {path.relative_to(ROOT)}: +{len(df)} · {total:,} registros acumulados")

    # 7. Retención
    _seccion("7. RETENCION")
    fechas_hist = historico.fechas()
    archivos = sorted(f for f in PROGRAMAS_DIR.iterdir() if _ARCHIVO_RE.match(f.name))
    exceso = archivos[:-MAX_PROGRAMAS_ACTIVOS] if len(archivos) > MAX_PROGRAMAS_ACTIVOS else []
    for f in exceso:
        f_fecha = dt.date.fromisoformat(_ARCHIVO_RE.match(f.name).group(1))
        if f_fecha in fechas_hist:
            f.unlink()
            log.info(f"  Eliminado {f.name} (sus registros están en el histórico)")
        else:
            log.warning(f"  Se conserva {f.name}: su fecha no está en el histórico")
    restantes = sum(1 for f in PROGRAMAS_DIR.iterdir() if _ARCHIVO_RE.match(f.name))
    log.info(f"  Programas Activos: {restantes} archivo(s) (máximo {MAX_PROGRAMAS_ACTIVOS})")

    return {"nuevos": nuevos, "modificados": modificados, "inactivos": inactivos,
            "total_activos": len(hoy), "linea_base": previo is None,
            # Contexto del resumen del correo: ejecución anterior y programas activos por grupo de universidades
            "fecha_anterior": previo[0] if previo else None,
            "total_anterior": len(anterior) if anterior is not None else None,
            "activos_grupo": activos_por_grupo(hoy),
            "activos_grupo_anterior": activos_por_grupo(anterior) if anterior is not None else None}


def activos_por_grupo(df: pd.DataFrame) -> dict[str, int]:
    """Programas activos por grupo de universidades (Uninorte, mayor interés, otras)."""
    conteo = df["CODIGO_INSTITUCION"].map(grupo_universidad).value_counts()
    return {g: int(conteo.get(g, 0)) for g in GRUPOS_UNIVERSIDAD}


def main(argv=None) -> int:
    args = _parse_args(argv)
    ahora = dt.datetime.now(ZONA_HORARIA)
    fecha = args.fecha or ahora.date()
    log_path = configurar_logging(ahora)
    log.info(f"MONITOR SNIES PREGRADO · UNIVERSITARIO · Ejecución del {fecha.isoformat()}")
    log.info(f"Log consolidado: {_ruta(log_path)}")

    try:
        resultados = ejecutar(args, fecha)
    except Exception as exc:
        etapa = exc.etapa if isinstance(exc, EtapaError) else "ERROR INESPERADO"
        log.exception(f"FALLA EN {etapa}: {exc}")
        if not args.sin_correo:
            try:
                from send_report import enviar_alerta_falla
                enviar_alerta_falla(fecha, etapa, str(exc), log_path)
            except Exception:
                log.exception("No se pudo enviar la alerta técnica.")
        log.info("EJECUCION TERMINADA CON ERRORES")
        return 1

    # El Dashboard se actualiza antes del correo, para que su enlace muestre esta ejecución
    _seccion("8. DASHBOARD")
    try:
        import contextlib
        import io
        from generar_dashboard import main as generar_dashboard
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            codigo = generar_dashboard()
        for linea in salida.getvalue().splitlines():
            log.info(f"  {linea.strip()}")
        if codigo != 0:
            raise RuntimeError(f"generar_dashboard terminó con código {codigo}")
    except Exception as exc:
        log.exception(f"FALLA EN DASHBOARD: {exc}")
        if not args.sin_correo:
            try:
                from send_report import enviar_alerta_falla
                enviar_alerta_falla(fecha, "DASHBOARD", str(exc), log_path)
            except Exception:
                log.exception("No se pudo enviar la alerta técnica.")
        log.info("EJECUCION TERMINADA CON ERRORES")
        return 1

    _seccion("9. CORREO")
    if args.sin_correo:
        log.info("  Envío omitido (--sin-correo).")
    elif resultados["linea_base"]:
        log.info("  Primera ejecución (línea base): no hay novedades que reportar.")
    else:
        try:
            from send_report import enviar_reporte
            enviar_reporte({"pregrado": resultados}, fecha)
        except Exception as exc:
            # Los datos y el Dashboard ya quedaron actualizados; la ejecución se marca como fallida para que
            # el workflow lo indique y se avisa a la cuenta remitente.
            log.exception(f"FALLA EN CORREO: {exc}")
            try:
                from send_report import enviar_alerta_falla
                enviar_alerta_falla(fecha, "CORREO", f"{type(exc).__name__}: {exc}", log_path)
            except Exception:
                log.exception("No se pudo enviar la alerta técnica.")
            log.info("EJECUCION TERMINADA CON ERRORES (datos y Dashboard actualizados; correo no enviado)")
            return 1

    shutil.rmtree(TMP_DIR, ignore_errors=True)
    log.info("EJECUCION COMPLETADA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
