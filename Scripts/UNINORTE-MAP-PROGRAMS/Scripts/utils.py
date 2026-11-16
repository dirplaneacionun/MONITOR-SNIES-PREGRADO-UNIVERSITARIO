"""Utilidades auxiliares: búsqueda insensible a mayúsculas, logging y barras de progreso."""

import os
import sys
import glob
import logging
from datetime import datetime

from tqdm import tqdm

# Registros con extra=PLAIN se emiten sin hora/nivel (tablas, separadores, líneas en blanco)
PLAIN = {"plain": True}

_LOG_PREFIX = "pipeline_"


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


class _TqdmHandler(logging.StreamHandler):
    """Handler de consola que escribe sin romper las barras de progreso activas."""

    def emit(self, record):
        try:
            tqdm.write(self.format(record), file=self.stream)
        except Exception:
            self.handleError(record)


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
    Consola: INFO (DEBUG con verbose). Archivo en logs_dir: siempre DEBUG, con trazas de error.
    Retorna (logger, ruta_log) — ruta_log es None si no se pudo crear el archivo."""
    logger = logging.getLogger("clasificacion")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)

    ch = _TqdmHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(_PlainAwareFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)

    log_path = None
    if logs_dir:
        try:
            os.makedirs(logs_dir, exist_ok=True)
            log_path = os.path.join(logs_dir, f"{_LOG_PREFIX}{datetime.now():%Y%m%d_%H%M%S}.log")
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
