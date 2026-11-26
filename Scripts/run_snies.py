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
    4. Valida el archivo procesado: que cada fila cumpla los filtros aplicados en el portal (FILTROS_PORTAL) y la
       continuidad con la ejecución anterior del histórico: si los programas caen más de un 10 %, la descarga se
       considera incompleta y no se guarda nada.
    5. Compara con la ejecución anterior, leída del histórico, y detecta programas nuevos
       (o reactivados), modificados e inactivos.
    6. Acumula las novedades en Novedades/*.parquet y agrega la ejecución al histórico
       (Historico/Historico Programas Activos AAAA.parquet, un archivo por año).
    7. Actualiza los datos del Dashboard (docs/data) y envía el correo institucional.

Retención: se conservan los últimos 10 logs de cada herramienta (este orquestador, ETL-PROGRAMAS-SNIES y
CLASIFICAR-DIVISIONES-UNINORTE-SNIES).

Vigilancia del repositorio: tras clasificar, se registra el tamaño de la retroalimentación del clasificador y se
advierte (log y anotación de GitHub Actions) cuando se acerca al límite de archivo de GitHub.

Si una etapa falla no se envía correo a DESTINATARIOS: se envía una alerta técnica
solo a la cuenta remitente (SMTP_USER) y el proceso termina con código 1. Si lo que falla
es el envío del reporte, los datos y el Dashboard ya quedan actualizados, pero el proceso
también termina con código 1 y se intenta la alerta técnica.

Ejecución:
    python Scripts/run_snies.py                      # ejecución semanal normal
    python Scripts/run_snies.py --sin-correo         # sin enviar correos
    python Scripts/run_snies.py --archivo RUTA.xlsx --fecha AAAA-MM-DD
                                                     # procesa un archivo ya descargado
    python Scripts/run_snies.py --forzar             # acepta una caída real de más del 10 %
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
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import catalogos
import historico

# ── Rutas ─────────────────────────────────────────────────────────────────────
SCRIPTS_DIR    = Path(__file__).resolve().parent
ROOT           = SCRIPTS_DIR.parent
ETL_DIR        = SCRIPTS_DIR / "ETL-PROGRAMAS-SNIES"
MAP_DIR        = SCRIPTS_DIR / "CLASIFICAR-DIVISIONES-UNINORTE-SNIES"
ETL_SCRIPT     = ETL_DIR / "Scripts" / "etl_programas.py"
MAP_SCRIPT     = MAP_DIR / "Scripts" / "pipeline_divisiones.py"
NOVEDADES_DIR  = ROOT / "Novedades"
LOGS_DIR       = ROOT / "Logs"
TMP_DIR        = ROOT / "tmp"

NUEVOS_FILE      = NOVEDADES_DIR / "Nuevos.parquet"
MODIFICADOS_FILE = NOVEDADES_DIR / "Modificados.parquet"
INACTIVOS_FILE   = NOVEDADES_DIR / "Inactivos.parquet"

# ── Retención ─────────────────────────────────────────────────────────────────
MAX_LOGS = 10   # logs consolidados en Logs/ (por prefijo: monitor y reclasificacion)

# ── Parámetros ────────────────────────────────────────────────────────────────
ZONA_HORARIA     = ZoneInfo("America/Bogota")
SNIES_URL        = "https://hecaa.mineducacion.gov.co/consultaspublicas/programas"
DOWNLOAD_TIMEOUT = 120         # segundos máximos esperando la descarga
UMBRAL_MAXIMO    = 10_000      # más programas que esto indica una descarga sin filtros
CAIDA_MAXIMA     = 0.10        # caída de programas frente a la ejecución anterior que indica una descarga incompleta
SUBPROCESO_TIMEOUT = 1_800     # segundos máximos por herramienta (ETL o clasificador)

COL_SNIES = "CODIGO_SNIES_DEL_PROGRAMA"
COL_FECHA = "FECHA_DE_OBTENCION"
# Valor que debe tener cada fila del reporte según los filtros aplicados en el portal (ya normalizado por el ETL).
# Un valor distinto indica que el portal no aplicó un filtro: ese reporte no se guarda.
FILTROS_PORTAL = {
    "ESTADO_INSTITUCION": "ACTIVA",
    "ESTADO_PROGRAMA": "ACTIVO",
    "NIVEL_ACADEMICO": "PREGRADO",
    "NIVEL_DE_FORMACION": "UNIVERSITARIO",
}
COLS_CLASIFICACION = ["DIVISION_UNINORTE", "PUNTAJE_TOTAL"]
# Columnas obsoletas, que no se guardan: REQUIERE_REVISION (los programas por revisar se consultan en la lista de
# revisión del clasificador). Se retiran también de los archivos acumulados.
COLS_OBSOLETAS = ["REQUIERE_REVISION"]
REVISION_FILE = SCRIPTS_DIR / "CLASIFICAR-DIVISIONES-UNINORTE-SNIES" / "Revision" / "Revision Programas.xlsx"
# Retroalimentación del clasificador: crece con cada ejecución y se versiona en el repositorio. GitHub advierte desde
# 50 MB por archivo y rechaza los de más de 100 MB; se avisa antes de llegar a esos límites.
RETROALIMENTACION_FILE = MAP_DIR / "Retroalimentacion" / "retroalimentacion.json"
RETRO_AVISO_MB = 40     # aviso: conviene planear la depuración o la división del archivo
RETRO_CRITICO_MB = 80   # crítico: cerca del límite de GitHub

# Columnas vigiladas para detectar modificados → tipo de cambio con el que se registra en CAMBIO_DETECTADO
COLS_VIGILAR = {
    "NUMERO_CREDITOS":             "NUMERO DE CREDITOS",
    "DEPARTAMENTO_OFERTA_PROGRAMA": "DEPARTAMENTO DE OFERTA",
    "NOMBRE_DEL_PROGRAMA":         "NOMBRE DEL PROGRAMA",
    "NUMERO_PERIODOS_DE_DURACION": "NUMERO DE PERIODOS",
    "MODALIDAD":                   "MODALIDAD",
    "COSTO_MATRICULA_ESTUD_NUEVOS": "COSTO DE MATRICULA",
}
# Columnas cuyo valor se compara sin considerar la puntuación (las variantes "BOGOTA D.C." y "BOGOTA, D.C." son el mismo
# departamento): una diferencia solo de puntuación no es un cambio.
COLS_SIN_PUNTUACION = {"DEPARTAMENTO_OFERTA_PROGRAMA"}

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


# Dirección de correo dentro de un texto (por ejemplo, la que incluye un error de SMTP al rechazar un destinatario).
_PATRON_CORREO = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


class _FormatoSeguro(logging.Formatter):
    """
    Formato de log que oculta datos privados antes de escribir en consola o archivo: rutas personales (carpeta del
    usuario y temporal) y direcciones de correo. Los logs se publican en el repositorio, que es público.
    """

    _pares = _rutas_privadas()

    def format(self, record):
        texto = super().format(record)
        for ruta, marca in self._pares:
            texto = texto.replace(ruta, marca)
        return _PATRON_CORREO.sub("<correo>", texto)


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
    """Encabezado de una etapa en el log consolidado."""
    log.info("─" * 70)
    log.info(titulo)
    log.info("─" * 70)


# ── Descarga (Selenium) ───────────────────────────────────────────────────────

def _build_driver(download_dir: Path, headless: bool = True):
    """Chrome controlado por Selenium que guarda las descargas en `download_dir` (sin ventana si `headless`)."""
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
    """Clic en el elemento de `xpath` cuando es clicable; reintenta una vez si la página lo reemplazó mientras esperaba."""
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
    """Log más reciente de `carpeta` que cumple `patron` y se modificó desde `desde` (marca de tiempo); None si no hay."""
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


def normalizar_etl(archivo_crudo: Path, forzar: bool = False) -> Path:
    """
    Copia el archivo a Fuentes/ del ETL, lo ejecuta y retorna Salidas/Programas.xlsx. Con `forzar`, el ETL
    acepta también una caída de filas mayor a la de su propia protección (--force).
    """
    fuente = ETL_DIR / "Fuentes" / "Programas.xlsx"
    salida = ETL_DIR / "Salidas" / "Programas.xlsx"
    fuente.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archivo_crudo, fuente)
    inicio = time.time()
    _ejecutar_herramienta(
        "NORMALIZACION (ETL-PROGRAMAS-SNIES)", ETL_DIR,
        [sys.executable, str(ETL_SCRIPT)] + (["--force"] if forzar else []), ETL_DIR / "Logs", "etl_*.log",
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
    # calamine entrega como hora (datetime.time) las fechas anteriores a 1900 (p. ej., 29/12/1899 en
    # FECHA_DE_RESOLUCION del SNIES 91335), lo que convertiría toda la columna en texto: esas columnas se leen con
    # openpyxl, que conserva la fecha exacta.
    con_hora = [c for c in df.columns if any(isinstance(v, dt.time) for v in df[c].dropna())]
    if con_hora:
        df[con_hora] = pd.read_excel(path, engine="openpyxl", dtype=object, usecols=con_hora)[con_hora]
    df[COL_SNIES] = pd.to_numeric(df[COL_SNIES], errors="raise").astype("int64")
    return df


def _tipos_estables(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tipos compatibles con parquet: columnas de fechas (con o sin hora) → datetime; columnas
    con tipos mezclados (ej. 123 y '0031') → texto, sin perder ceros a la izquierda. La fecha de ejecución
    (FECHA_DE_OBTENCION) se guarda como fecha sin hora.
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
    if COL_FECHA in df.columns:
        df[COL_FECHA] = pd.to_datetime(df[COL_FECHA], errors="coerce").dt.date
    return df


def _escribir_parquet(df: pd.DataFrame, path: Path) -> None:
    """Escribe `df` en `path` de forma atómica (archivo temporal y reemplazo), con tipos compatibles con Parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    _tipos_estables(df).to_parquet(tmp, index=False)
    os.replace(tmp, path)


def acumular_parquet(path: Path, nuevos: pd.DataFrame, fecha: dt.date, orden: list[str]) -> int:
    """Agrega los registros de `fecha` al parquet (reemplaza los de esa fecha si ya existían)."""
    marca = pd.Timestamp(fecha)
    partes = []
    if path.exists():
        # Los registros nuevos adoptan los tipos del archivo acumulado: los inactivos provienen del histórico,
        # donde los números pueden estar guardados como texto
        esquema = pq.read_schema(path)
        if "TIPO_CAMBIO_DETECTADO" in esquema.names:
            # Modificados con el formato anterior (un cambio por fila): se consolida en un registro por programa y fecha
            esquema = pa.schema([f for f in esquema if f.name != "TIPO_CAMBIO_DETECTADO"], metadata=esquema.metadata)
        nuevos = historico.alinear(nuevos, esquema)
        existente = catalogos.consolidar_modificados(pd.read_parquet(path))
        existente[COL_FECHA] = pd.to_datetime(existente[COL_FECHA])   # en memoria se trabaja como datetime64
        existente = existente[existente[COL_FECHA] != marca]
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


def validar_filtros(df: pd.DataFrame) -> None:
    """
    Protección contra un portal que no aplica algún filtro: una descarga con el universo ampliado (por ejemplo,
    sin el filtro de nivel de formación) conserva el esquema y puede quedar por debajo de UMBRAL_MAXIMO, pero
    registraría como nuevos miles de programas que no son del universo vigilado. Cada fila debe cumplir
    FILTROS_PORTAL; si una columna falta o algún valor difiere, lanza EtapaError sin que se guarde ningún dato.
    """
    faltan = [c for c in FILTROS_PORTAL if c not in df.columns]
    if faltan:
        raise EtapaError("VALIDACION", f"El archivo procesado no tiene las columnas de los filtros del portal: {faltan}.")
    problemas = []
    for columna, esperado in FILTROS_PORTAL.items():
        valores = df[columna].map(lambda v: "" if pd.isna(v) else str(v).strip().upper())
        distintos = valores[valores != esperado]
        if not distintos.empty:
            detalle = ", ".join(f"{v or 'VACIO'} ({n:,})" for v, n in distintos.value_counts().head(5).items())
            problemas.append(f"{columna}: {len(distintos):,} fila(s) distintas de {esperado} [{detalle}]")
    if problemas:
        raise EtapaError("VALIDACION", "El portal no aplicó los filtros esperados (" + "; ".join(problemas) + "). "
                                       "Posible cambio en el portal del SNIES: no se guardó ningún dato.")
    log.info("  Filtros del portal verificados: " + " · ".join(f"{c}={v}" for c, v in FILTROS_PORTAL.items()))


def categorias_desconocidas(df: pd.DataFrame) -> dict[str, list[str]]:
    """
    Valores del reporte que el Dashboard no contempla: sectores y modalidades fuera de los catálogos (el sector
    quedaría fuera de su gráfica de evolución y la modalidad se agruparía como SIN INFORMACIÓN) y más divisiones que
    colores en la paleta de series (los colores se repetirían). Retorna {campo: detalle}; vacío si todo está contemplado.
    """
    def distintos(col):
        if col not in df.columns:
            return set()
        return {str(v).strip().upper() for v in df[col].dropna() if str(v).strip()}

    hallazgos = {}
    sectores = distintos("SECTOR") - set(catalogos.SECTORES)
    if sectores:
        hallazgos["SECTOR"] = sorted(sectores)
    modalidades = distintos("MODALIDAD") - set(catalogos.MODALIDAD_A_GRUPO)
    if modalidades:
        hallazgos["MODALIDAD"] = sorted(modalidades)
    divisiones = distintos("DIVISION_UNINORTE")
    if len(divisiones) > catalogos.MAX_COLORES_SERIES:
        hallazgos["DIVISION_UNINORTE"] = [f"{len(divisiones)} divisiones y {catalogos.MAX_COLORES_SERIES} colores en la paleta de series"]
    return hallazgos


def avisar_categorias(df: pd.DataFrame) -> dict[str, list[str]]:
    """Registra una advertencia por cada campo con categorías no contempladas por el Dashboard; no detiene el flujo."""
    hallazgos = categorias_desconocidas(df)
    for campo, detalle in hallazgos.items():
        log.warning(f"  {campo}: valores no contemplados por el Dashboard ({'; '.join(detalle)}). "
                    "Revise los catálogos (Scripts/catalogos.py) y la paleta de series (docs/assets/app.js).")
    return hallazgos


def vigilar_retroalimentacion(path: Path = RETROALIMENTACION_FILE) -> str | None:
    """
    Registra el tamaño de la retroalimentación del clasificador y advierte cuando se acerca al límite de archivo de
    GitHub (RETRO_AVISO_MB y RETRO_CRITICO_MB). Nunca detiene el flujo. En GitHub Actions la advertencia se publica
    además como anotación de la ejecución, para que se vea sin abrir el log. Retorna el nivel ("aviso", "critico") o
    None si el tamaño es normal o el archivo no existe.
    """
    if not path.exists():
        return None
    mb = path.stat().st_size / 1024 ** 2
    nivel = "critico" if mb >= RETRO_CRITICO_MB else "aviso" if mb >= RETRO_AVISO_MB else None
    if nivel is None:
        log.info(f"  Retroalimentación del clasificador: {mb:.1f} MB (aviso desde {RETRO_AVISO_MB} MB)")
        return None
    texto = (f"La retroalimentación del clasificador pesa {mb:.1f} MB (nivel {nivel}: aviso desde {RETRO_AVISO_MB} MB, "
             f"crítico desde {RETRO_CRITICO_MB} MB). GitHub advierte desde 50 MB y rechaza los archivos de más de 100 MB: "
             "planee depurar o dividir Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Retroalimentacion/retroalimentacion.json.")
    log.warning(f"  {texto}")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=Tamaño de la retroalimentación::{texto}", flush=True)
    return nivel


def validar_continuidad(n_hoy: int, fecha: dt.date, forzar: bool = False) -> None:
    """
    Protección contra descargas incompletas: una descarga truncada del SNIES conserva el esquema y pasa las
    demás validaciones, pero registraría como inactivos los programas faltantes. Si los programas caen más de
    CAIDA_MAXIMA frente a la ejecución anterior del histórico, la ejecución se detiene antes de guardar datos
    (EtapaError → alerta técnica). Con `forzar` se acepta la caída y queda registrada en el log.
    """
    previas = [f for f in historico.fechas() if f < fecha]
    if not previas:
        return
    n_ant = len(historico.leer([COL_SNIES], fecha=previas[-1]))
    variacion = (n_hoy - n_ant) / n_ant if n_ant else 0.0
    log.info(f"  Continuidad: {n_hoy:,} programas frente a {n_ant:,} de la ejecución del "
             f"{previas[-1].isoformat()} ({variacion:+.1%})")
    if not n_ant or n_hoy >= n_ant * (1 - CAIDA_MAXIMA):
        return
    detalle = (f"La descarga tiene {n_hoy:,} programas frente a {n_ant:,} de la ejecución del "
               f"{previas[-1].isoformat()} ({-variacion:.0%} menos; umbral {CAIDA_MAXIMA:.0%}).")
    if not forzar:
        raise EtapaError("VALIDACION", f"{detalle} Posible descarga incompleta del SNIES: no se guardó ningún dato. "
                                       "Si la reducción es real, ejecute el workflow con la opción de forzar la validación.")
    log.warning(f"  {detalle} Se continúa por --forzar.")


# ── Detección de novedades ────────────────────────────────────────────────────

def _valor(v) -> str:
    """Representación comparable y legible de un valor vigilado."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _comparable(col: str, valor: str) -> str:
    """Valor de `col` en la forma en que se compara: sin puntuación ni mayúsculas en las columnas de COLS_SIN_PUNTUACION."""
    return re.sub(r"[^A-Z0-9Ñ]", "", valor.upper()) if col in COLS_SIN_PUNTUACION else valor


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
    h = historico.leer([COL_SNIES, COL_FECHA, col], antes_de=fecha, codigos_snies=snies)
    h = h.assign(_v=h[col].map(_valor))
    h = h[h["_v"] != ""].sort_values(COL_FECHA)
    return h.groupby(COL_SNIES)["_v"].last().to_dict()


def detectar_novedades(hoy: pd.DataFrame, anterior: pd.DataFrame, fecha: dt.date):
    """
    Nuevos: SNIES activos hoy que no estaban en la ejecución anterior (TIPO_NOVEDAD: NUEVO o
    REACTIVADO si ya existió antes). Inactivos: SNIES de la ejecución anterior que hoy no están.
    Modificados: un registro por programa con todos sus cambios en CAMBIO_DETECTADO ("TIPO: anterior → nuevo", uno por
    columna vigilada que cambió, separados por " | "). Un dato que desaparece no es un cambio; si el valor anterior
    estaba vacío se compara contra el último valor conocido, de modo que solo se registra un cambio entre dos valores
    informados (también en COSTO DE MATRICULA: nunca de o hacia un valor vacío). El departamento se compara sin puntuación.
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
    cambios: dict[int, list[tuple[str, str, str]]] = {}
    for col, tipo in COLS_VIGILAR.items():
        if col not in hoy_c.columns or col not in ant_c.columns:
            log.warning(f"  Columna vigilada ausente: {col}")
            continue
        v_hoy = hoy_c[col].map(_valor)
        v_ant = ant_c[col].reindex(v_hoy.index).map(_valor)
        distintos = v_hoy.index[v_hoy.map(lambda v: _comparable(col, v)) != v_ant.map(lambda v: _comparable(col, v))]
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
                if viejo == "" or _comparable(col, viejo) == _comparable(col, nuevo):
                    continue
            cambios.setdefault(snies, []).append((tipo, viejo, nuevo))
    filas = []
    posicion = {snies: i for i, snies in enumerate(hoy_c.index)}
    for snies in sorted(cambios, key=posicion.get):
        registro = hoy_c.loc[snies].to_dict()
        registro[COL_SNIES] = snies
        registro[COL_FECHA] = marca
        registro["CAMBIO_DETECTADO"] = catalogos.unir_cambios(cambios[snies])
        filas.append(registro)
    modificados = pd.DataFrame(filas)
    return nuevos, modificados, inactivos


# ── Flujo principal ───────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    """Opciones de la línea de comandos (ver el encabezado del módulo)."""
    p = argparse.ArgumentParser(description="Monitor SNIES de programas universitarios de pregrado.")
    p.add_argument("--archivo", type=Path, help="Procesa un Excel ya descargado en lugar de descargarlo.")
    p.add_argument("--fecha", type=dt.date.fromisoformat,
                   help="Fecha de obtención AAAA-MM-DD (por defecto, hoy en hora de Colombia).")
    p.add_argument("--sin-correo", action="store_true", help="No envía correos (pruebas).")
    p.add_argument("--forzar", action="store_true",
                   help="Acepta una caída de programas mayor al 10 %% frente a la ejecución anterior (cambio real "
                        "confirmado en el portal del SNIES).")
    return p.parse_args(argv)


def ejecutar(args: argparse.Namespace, fecha: dt.date) -> dict:
    """
    Etapas 1 a 6 del flujo (descarga, normalización, clasificación, validación, detección y guardado). Retorna los
    resultados que usa el correo; una falla de validación o de una herramienta lanza EtapaError antes de guardar datos.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    for carpeta in (NOVEDADES_DIR, historico.HISTORICO_DIR):
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
    normalizado = normalizar_etl(crudo, args.forzar)

    # 3. Clasificación
    _seccion("3. CLASIFICACION (CLASIFICAR-DIVISIONES-UNINORTE-SNIES)")
    clasificado = clasificar_divisiones(normalizado)
    vigilar_retroalimentacion()

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
    validar_filtros(hoy)
    avisar_categorias(hoy)
    validar_continuidad(len(hoy), fecha, args.forzar)
    try:
        revision = len(pd.read_excel(REVISION_FILE, engine="calamine")) if REVISION_FILE.exists() else 0
    except Exception:
        revision = "desconocido"
    hoy = hoy.drop(columns=[c for c in COLS_OBSOLETAS if c in hoy.columns])
    log.info(f"  Programas activos: {len(hoy):,} · columnas: {hoy.shape[1]} · requieren revisión: {revision} (lista: {_ruta(REVISION_FILE)})")

    # 5. Detección
    _seccion("5. DETECCION DE NOVEDADES")
    previo = historico.ejecucion_anterior(fecha)
    anterior = None
    orden_base = list(hoy.columns) + [COL_FECHA]
    if previo is None:
        log.warning("  No hay una ejecución anterior en el histórico: esta queda como línea base.")
        nuevos = modificados = inactivos = pd.DataFrame()
    else:
        fecha_ant, anterior = previo
        log.info(f"  Ejecución anterior: {fecha_ant.isoformat()} ({len(anterior):,} programas, desde el histórico)")
        nuevos, modificados, inactivos = detectar_novedades(hoy, anterior, fecha)
        reactivados = int((nuevos.get("TIPO_NOVEDAD", pd.Series(dtype=str)) == "REACTIVADO").sum())
        log.info(f"  Nuevos: {len(nuevos)} (reactivados: {reactivados}) · "
                 f"Modificados: {len(modificados)} programas con "
                 f"{sum(len(catalogos.separar_cambios(c)) for c in modificados.get('CAMBIO_DETECTADO', [])) if not modificados.empty else 0} "
                 f"cambios · Inactivos: {len(inactivos)}")
        if not modificados.empty:
            por_tipo = Counter(tipo for c in modificados["CAMBIO_DETECTADO"] for tipo in catalogos.tipos_de_cambio(c))
            for tipo in catalogos.TIPOS_CAMBIO:
                if por_tipo[tipo]:
                    log.info(f"    {tipo}: {por_tipo[tipo]}")

    # 6. Guardado
    _seccion("6. GUARDADO")
    ejecuciones = historico.guardar_ejecucion(
        hoy.sort_values(COL_SNIES, kind="stable", ignore_index=True)[list(hoy.columns)], fecha)
    log.info(f"  {historico.ruta(fecha.year).relative_to(ROOT)}: +{len(hoy):,} registros "
             f"(histórico: {ejecuciones} ejecuciones)")

    if previo is not None:
        for path, df, extra in (
            (NUEVOS_FILE, nuevos, ["TIPO_NOVEDAD"]),
            (MODIFICADOS_FILE, modificados, ["CAMBIO_DETECTADO"]),
            (INACTIVOS_FILE, inactivos, []),
        ):
            total = acumular_parquet(path, df, fecha, orden_base + extra)
            log.info(f"  {path.relative_to(ROOT)}: +{len(df)} · {total:,} registros acumulados")

    return {"nuevos": nuevos, "modificados": modificados, "inactivos": inactivos,
            "total_activos": len(hoy), "linea_base": previo is None,
            # Contexto del resumen del correo: ejecución anterior y su total de programas activos
            "fecha_anterior": previo[0] if previo else None,
            "total_anterior": len(anterior) if anterior is not None else None}


def main(argv=None) -> int:
    """Ejecución completa (etapas 1 a 8); retorna 0 si terminó correctamente y 1 si alguna etapa falló."""
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
    _seccion("7. DASHBOARD")
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

    _seccion("8. CORREO")
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
