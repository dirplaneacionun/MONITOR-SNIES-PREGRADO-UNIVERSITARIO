"""Utilidades auxiliares: búsqueda insensible a mayúsculas, logging y barras de progreso."""

import os
import sys
import glob
import logging
from datetime import datetime

from tqdm import tqdm

try:
    import colorama
except ImportError:  # el color es opcional: sin colorama la consola se muestra sin estilos
    colorama = None

# Registros con extra=PLAIN se emiten sin hora/nivel (tablas, separadores, líneas en blanco)
PLAIN = {"plain": True}
# Registros INFO con extra=VISIBLE se muestran en consola sin --verbose; el resto de INFO solo se registra en el archivo de log
VISIBLE = {"visible": True}
# Estilos de consola (solo con color): resultado satisfactorio (verde) y aviso que requiere una acción (amarillo)
EXITO = {"visible": True, "estilo": "exito"}
AVISO = {"visible": True, "estilo": "aviso"}

_LOG_PREFIX = "pipeline_"

_RESET = "\033[0m"
_ESTILOS_NIVEL = {
    logging.DEBUG: "\033[2m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[1;31m",
    logging.CRITICAL: "\033[1;31m",
}
_ESTILOS_REGISTRO = {"exito": "\033[1;32m", "aviso": "\033[33m"}


def find_file_insensitive(directory, target_name):
    """Busca un archivo ignorando mayúsculas/minúsculas."""
    for f in os.listdir(directory):
        if f.lower() == target_name.lower():
            return os.path.join(directory, f)
    return None


class _PlainAwareFormatter(logging.Formatter):
    """Formato estándar, salvo registros marcados como PLAIN que se emiten tal cual."""

    def format(self, record):
        if getattr(record, "plain", False):
            return record.getMessage()
        return super().format(record)


class _ConsolaFormatter(_PlainAwareFormatter):
    """Formato de consola: el estándar, con color según el estilo del registro o su nivel cuando el terminal lo admite."""

    def __init__(self, fmt, datefmt, color):
        super().__init__(fmt, datefmt=datefmt)
        self._color = color

    def format(self, record):
        texto = super().format(record)
        if not self._color:
            return texto
        estilo = _ESTILOS_REGISTRO.get(getattr(record, "estilo", None)) or _ESTILOS_NIVEL.get(record.levelno)
        return f"{estilo}{texto}{_RESET}" if estilo else texto


class _FiltroResumen(logging.Filter):
    """Consola sin --verbose: los INFO de detalle solo van al archivo de log; se muestran los marcados con VISIBLE o PLAIN."""

    def filter(self, record):
        return record.levelno != logging.INFO or getattr(record, "visible", False) or getattr(record, "plain", False)


class _TqdmHandler(logging.StreamHandler):
    """Handler de consola que escribe sin romper las barras de progreso activas."""

    def emit(self, record):
        try:
            tqdm.write(self.format(record), file=self.stream)
        except Exception:
            self.handleError(record)


def _consola_con_color(stream):
    """True si la consola admite color: es un terminal, NO_COLOR no está definida y, en Windows, colorama habilita
    las secuencias de escape."""
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    es_terminal = getattr(stream, "isatty", None)
    if not (es_terminal and es_terminal()):
        return False
    if os.name == "nt":
        if colorama is None:
            return False
        colorama.just_fix_windows_console()
    return True


def rotar_archivos(directorio, patron, conservar, logger):
    """Conserva solo los últimos `conservar` archivos que cumplen el patrón
    (el nombre incluye la fecha, así que el orden alfabético es el cronológico)."""
    archivos = sorted(glob.glob(os.path.join(directorio, patron)))
    for viejo in archivos[:-conservar]:
        try:
            os.remove(viejo)
        except OSError as e:
            logger.warning(f"No se pudo eliminar el archivo antiguo '{viejo}': {e}")


def setup_logger(verbose=False, logs_dir=None, max_logs=10):
    """Configura el logger del pipeline.
    Consola: resumen (INFO marcados con VISIBLE o PLAIN, avisos y errores), con color si el terminal lo admite; con verbose,
    todo el detalle, incluido DEBUG. Archivo en logs_dir: siempre completo (DEBUG), sin color y con trazas de error.
    Retorna (logger, ruta_log) — ruta_log es None si no se pudo crear el archivo."""
    logger = logging.getLogger("clasificacion")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)

    # Se decide antes de crear el handler: colorama puede reemplazar sys.stdout en consolas antiguas de Windows
    color = _consola_con_color(sys.stdout)
    ch = _TqdmHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(_ConsolaFormatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S", color))
    if not verbose:
        ch.addFilter(_FiltroResumen())
    logger.addHandler(ch)

    log_path = None
    if logs_dir:
        try:
            os.makedirs(logs_dir, exist_ok=True)
            log_path = os.path.join(logs_dir, f"{_LOG_PREFIX}{datetime.now():%Y-%m-%d_%H-%M-%S}.log")   # mismo formato que monitor_ y etl_
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(_PlainAwareFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(fh)
            rotar_archivos(logs_dir, f"{_LOG_PREFIX}*.log", max_logs, logger)
        except OSError as e:
            log_path = None
            logger.warning(f"No se pudo crear el archivo de log en '{logs_dir}' ({e}). Solo se registrara en consola.")

    return logger, log_path


def barra(iterable=None, total=None, desc="", unidad="it"):
    """Barra de progreso transitoria (se borra al terminar; el resultado queda en el log).
    Se desactiva automáticamente si la salida no es una terminal."""
    return tqdm(
        iterable, total=total, desc=desc, unit=unidad,
        leave=False, dynamic_ncols=True, disable=None,
        file=sys.stderr, mininterval=0.3,
    )
