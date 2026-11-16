"""Configuración central del pipeline de clasificación."""

import os

# ── Rutas ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUENTES_DIR = os.path.join(BASE_DIR, "Fuentes")
SALIDAS_DIR = os.path.join(BASE_DIR, "Salidas")
LOGS_DIR = os.path.join(BASE_DIR, "Logs")
# Listas de programas con REQUIERE_REVISION=VERDADERO (fuera de Salidas para no mezclarse con las salidas)
REVISION_DIR = os.path.join(BASE_DIR, "Revision")
# Historial de clasificaciones verificadas (retroalimentacion.json) y sus copias de seguridad
RETROALIMENTACION_DIR = os.path.join(BASE_DIR, "Retroalimentacion")

# ── Logs de ejecución ──────────────────────────────────────────────────────────
MAX_LOGS = 20  # Se conservan los últimos N logs en LOGS_DIR

# ── Archivos de referencia ─────────────────────────────────────────────────────
# El catálogo de divisiones vive en diccionarios.DIVISIONES
PROGRAMAS_UN_FILE = "ProgramasUN.xlsx"

# ── Archivos procesables ───────────────────────────────────────────────────────
# SNIES-MONITOR-UNDERGRAD solo clasifica Programas.xlsx: los parquets (Consolidado, Estudiantes inscritos,
# admitidos, matriculados, matriculados en primer curso y graduados) no se procesan en esta copia.
ARCHIVOS_PARQUET = []

# Programas.xlsx se procesa primero: su clasificación enriquece las referencias (Capa 1 y Capa 2) de los parquets
PROGRAMAS_FILE = "Programas.xlsx"

ARCHIVOS_EXCEL = [
    PROGRAMAS_FILE,
]

ARCHIVOS_PROCESABLES = ARCHIVOS_EXCEL + ARCHIVOS_PARQUET

# ── Pesos del sistema de votación (3 capas) ───────────────────────────────────
# PESO_CAPA1 debe ser >= la suma de las demás capas (0.50): así la división de un SNIES conocido
# nunca pierde la votación y las demás capas solo definen la confianza.
PESO_CAPA1 = 0.50                   # Match SNIES
PESO_CAPA2_NOMBRE = 0.25            # Match nombre del programa            Capa 2: 0.25
PESO_CAPA3_CINE = 0.15              # CINE Detallado                     ┐ Capa 3: 0.25
PESO_CAPA3_NBC = 0.10               # NBC                                ┘
# Empate entre el nombre (Capa 2) y CINE/NBC (Capa 3) sin SNIES conocido: se toma la división del nombre.

# ── Umbral de similitud para match fuzzy de nombres ──────────────────────────
UMBRAL_SIMILITUD_NOMBRE = 0.70

# ── Umbrales de decisión ───────────────────────────────────────────────────────
# Sin match SNIES el máximo posible es 0.50 (Capa 2 + Capa 3).
# Con match SNIES el máximo es ~1.00.
# UMBRAL_MEDIA debe ser mayor que el peso de la Capa 2 (0.25): el nombre por sí solo no basta
# para evitar la revisión (es la señal menos precisa).
UMBRAL_ALTA = 0.60
UMBRAL_MEDIA = 0.35
MARGEN_MEDIA = 0.10
UMBRAL_BAJA = 0.15
MARGEN_BAJA = 0.05

# ── Procesamiento por lotes ────────────────────────────────────────────────────
BATCH_SIZE = 500_000

# ── Columnas de voto por capa (metadatos internos) ────────────────────────────
COLUMNAS_VOTO = [
    "VOTO_CAPA1",
    "VOTO_CAPA2_NOMBRE",
    "VOTO_CAPA3_NBC",
    "VOTO_CAPA3_CINE",
]

# ── Columnas internas a excluir de los archivos de salida ─────────────────────
COLUMNAS_REMOVER = [
    "MARGEN",
    "CONFIANZA",
    *COLUMNAS_VOTO,
    "CONSENSO",
    "ALERTA_INCONSISTENCIA",
    "ORIGEN_DECISION",
]

# ── Retroalimentación continua ────────────────────────────────────────────────
RETROALIMENTACION_FILE = "retroalimentacion.json"
# Antes de sobrescribirlo con cambios, se copia a RETROALIMENTACION_DIR/retroalimentacion_<fecha>.json.bak;
# se conservan las últimas N copias
MAX_BACKUPS_RETRO = 10

# ── Refinamiento (una pasada: consenso por vecindario NBC|CINE + keywords) ────
REFINAMIENTO_HABILITADO = True
CONSENSO_MIN_REGISTROS = 3
CONSENSO_UMBRAL_DOMINANCIA = 0.90

# ── Mapeo de nombres de columna por tipo de archivo ────────────────────────────
COLUMNAS_PARQUET = {
    "snies": "CODIGO_SNIES_DEL_PROGRAMA",
    "nombre": "PROGRAMA_ACADEMICO",
    "nbc": "NUCLEO_BASICO_DEL_CONOCIMIENTO_NBC",
    "cine_detallado": "DESC_CINE_CAMPO_DETALLADO",
}

# Periodo de cada registro de los parquets (de mayor a menor peso). Si un SNIES quedó con divisiones distintas por la
# votación (duda), toma la de su nombre vigente o, si no está en el archivo, la de su registro más reciente
COLUMNAS_PERIODO_PARQUET = ("AÑO", "SEMESTRE")

COLUMNAS_EXCEL = {
    "snies": "CODIGO_SNIES_DEL_PROGRAMA",
    "nombre": "NOMBRE_DEL_PROGRAMA",
    "nbc": "NUCLEO_BASICO_DEL_CONOCIMIENTO",
    "cine_detallado": "CINE_F_2013_AC_CAMPO_DETALLADO",
}

# Claves de COLUMNAS_PARQUET/COLUMNAS_EXCEL sin las que un archivo no se puede clasificar
# (sin CINE detallado la Capa 3 vota solo con NBC)
COLUMNAS_REQUERIDAS = ("snies", "nombre", "nbc")

# Columnas de ProgramasUN.xlsx que usa el pipeline (todas requeridas): SNIES (Capa 1 y división fija),
# nombre (Capa 2) y división. NBC y CINE no se usan: la Capa 3 vota con los diccionarios.
COLUMNAS_PROGRAMAS_UN = {
    "snies": "CODIGO_SNIES_DEL_PROGRAMA",
    "nombre": "NOMBRE_DEL_PROGRAMA",
    "division": "DIVISION_UNINORTE",
}
