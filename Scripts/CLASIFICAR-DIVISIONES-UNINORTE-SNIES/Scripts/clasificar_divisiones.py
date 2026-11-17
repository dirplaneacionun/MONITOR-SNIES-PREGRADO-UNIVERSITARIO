"""
Clasificación de programas académicos en divisiones UniNorte (lo ejecuta pipeline_divisiones.py).
Sistema de verificación cruzada con 3 capas de votación ponderada.
"""

import bisect
import hashlib
import os
import re
import time
import json
import shutil
import pandas as pd
from collections import defaultdict, Counter
from datetime import datetime
from typing import NamedTuple

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


from config import (
    PESO_CAPA1,
    PESO_CAPA2_NOMBRE,
    PESO_CAPA3_CINE, PESO_CAPA3_NBC,
    UMBRAL_ALTA, UMBRAL_MEDIA, MARGEN_MEDIA, UMBRAL_BAJA, MARGEN_BAJA,
    UMBRAL_SIMILITUD_NOMBRE,
    COLUMNAS_PROGRAMAS_UN, COLUMNAS_REMOVER, COLUMNAS_VOTO, COLUMNAS_REQUERIDAS,
    RETROALIMENTACION_FILE, MAX_BACKUPS_RETRO, PROGRAMAS_UN_FILE, PROGRAMAS_FILE,
    CONSENSO_MIN_REGISTROS, CONSENSO_UMBRAL_DOMINANCIA, MAX_REVISION_CONSOLA,
)
from normalizar import (
    normalizar_texto, normalizar_para_keywords, normalizar_serie, normalizar_serie_keywords, CARACTERES_DANADOS,
    MIN_TRUNCADO, forma_canonica,
)
import diccionarios as _dic_raw
from utils import find_file_insensitive, barra, rotar_archivos, VISIBLE

# Las reglas que deben cumplir pesos y umbrales se comprueban en validaciones.validar_pesos


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALIZACIÓN DE DICCIONARIOS (claves y valores sin comas/tildes/etc.)
# ═══════════════════════════════════════════════════════════════════════════════

def _normalizar_diccionarios():
    """Normaliza las claves de los diccionarios para que coincidan con los datos normalizados."""
    nbc_exc = {}
    for k, v in _dic_raw.NBC_EXCLUSIVO.items():
        nbc_exc[normalizar_texto(k)] = normalizar_texto(v)

    nbc_amb = {}
    for k, info in _dic_raw.NBC_AMBIGUO.items():
        reglas_norm = []
        for campo, patron, div in info["reglas"]:
            patron_norm = normalizar_para_keywords(patron) if campo == "nombre" else normalizar_texto(patron)
            reglas_norm.append((campo, patron_norm, normalizar_texto(div)))
        default_norm = normalizar_texto(info["default"]) if info["default"] else None
        nbc_amb[normalizar_texto(k)] = {"default": default_norm, "reglas": reglas_norm}

    cine_exc = {}
    for k, v in _dic_raw.CINE_EXCLUSIVO.items():
        cine_exc[normalizar_texto(k)] = normalizar_texto(v)

    keywords = {}
    for div, kws in _dic_raw.KEYWORDS.items():
        # Mismo formato que el nombre normalizado (sin tildes/ñ), si no "diseño" nunca coincide
        keywords[normalizar_texto(div)] = [normalizar_para_keywords(kw) for kw in kws]

    cine_vagos = _dic_raw.CINE_VAGOS_PATTERNS

    nbc_alias = {normalizar_texto(k): normalizar_texto(v) for k, v in _dic_raw.NBC_ALIAS.items()}

    return nbc_exc, nbc_amb, cine_exc, keywords, cine_vagos, nbc_alias


NBC_EXCLUSIVO, NBC_AMBIGUO, CINE_EXCLUSIVO, KEYWORDS, CINE_VAGOS_PATTERNS, NBC_ALIAS = _normalizar_diccionarios()


def huella_diccionarios():
    """Huella de la versión vigente de diccionarios.py: los primeros 12 caracteres del SHA-256 del archivo y sus conteos.
    Se registra en el log y en la retroalimentación para saber con qué reglas se produjo cada salida."""
    with open(_dic_raw.__file__, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()[:12]
    conteos = {
        "overrides": len(_dic_raw.NOMBRE_DIVISION_OVERRIDE),
        "reglas_nbc": sum(len(info["reglas"]) for info in _dic_raw.NBC_AMBIGUO.values()),
        "nbc": len(_dic_raw.NBC_EXCLUSIVO) + len(_dic_raw.NBC_AMBIGUO),
        "cine": len(_dic_raw.CINE_EXCLUSIVO),
        "keywords": sum(len(kws) for kws in _dic_raw.KEYWORDS.values()),
    }
    return sha, conteos


def normalizar_nbc(s):
    """normalizar_serie del NBC y, después, las equivalencias de NBC_ALIAS (variantes de la fuente → clave del catálogo)."""
    return normalizar_serie(s).replace(NBC_ALIAS)


# NOMBRE_DIVISION_OVERRIDE: nombre del programa -> división
# (una clave que no sea texto la reporta validar_divisiones_diccionarios y detiene el pipeline)
NOMBRE_OVERRIDE = {
    normalizar_para_keywords(_k): normalizar_texto(_v)
    for _k, _v in getattr(_dic_raw, "NOMBRE_DIVISION_OVERRIDE", {}).items()
    if isinstance(_k, str)
}


class Referencias(NamedTuple):
    """Referencias de la votación.
    snies: SNIES -> división (Capa 1): ProgramasUN, retroalimentación y Programas.xlsx clasificado.
    nombres: nombre -> división (Capa 2): ProgramasUN, NOMBRE_DIVISION_OVERRIDE y Programas.xlsx clasificado.
    snies_un: SNIES -> división de ProgramasUN (nada la cambia).
    snies_fijos: SNIES -> división fija: ProgramasUN y, para los parquets, la clasificación final de Programas.xlsx.
        Orden: override por nombre (exacto, comodín o truncado) → división de Programas.xlsx por SNIES → ProgramasUN.
        En los parquets, un registro con otro nombre (programa renombrado, nombres_ref) conserva el override de ese
        nombre, también dentro del mismo archivo: un mismo nombre tiene una sola división en todos los archivos.
    snies_auto: SNIES de Programas.xlsx clasificados solo por votación, sin SNIES verificado (programas nuevos).
        Se usan en esta ejecución pero no se guardan en la retroalimentación: se reevalúan en cada ejecución
        con las referencias vigentes, hasta que un override por nombre (o ProgramasUN) los fije.
    snies_revision: SNIES de snies_fijos que quedaron en revisión en Programas.xlsx: en los parquets también van
        a revisión, con la misma división.
    overrides_programas: SNIES de Programas.xlsx (no de ProgramasUN) cuya división fijó un override -> nombre del override
        (procedencia en la retroalimentación).
    fijos_parquets: SNIES que solo están en los parquets -> división del override de su nombre más reciente en todos
        los parquets (division_parquets_por_override).
    nombres_ref: SNIES -> nombres (normalizados) del programa en ProgramasUN y Programas.xlsx: un registro de los
        parquets con uno de estos nombres (o una variante: caracteres dañados, truncado) es el mismo programa.
    nombre_vigente: SNIES -> nombre (normalizado) con el que se verificó su división en esta ejecución (ProgramasUN y
        Programas.xlsx sin confianza BAJA): se guarda en la retroalimentación para detectar renombres.
    retro_info: SNIES -> {"division", "origen", "detalle", "nombre", "nombres"} de la retroalimentación cargada.
    registro: SNIES -> {nombre normalizado: división} de los nombres ya registrados en la retroalimentación (todos los
        nombres con que el programa apareció en las fuentes): su división queda fija aunque cambie la del SNIES.
    registro_completo: True si la retroalimentación ya tiene el registro de nombres de todas las fuentes; antes de eso
        (primera ejecución) ningún nombre cuenta como nuevo.
    canonicas_ejecucion: forma_canonica -> división decidida en los archivos ya procesados de esta ejecución (el primero que
        la decide; Programas.xlsx va primero): un nombre equivalente de un archivo posterior, decidido por votación, la toma.
    division_nombres: (SNIES, nombre) -> (división, origen, revisión) de los nombres de los parquets distintos al vigente
        y sin override: los registrados conservan su división; los nuevos se clasifican (resolver_nombres)."""
    snies: dict
    nombres: dict
    snies_un: dict
    snies_fijos: dict
    snies_auto: frozenset = frozenset()
    snies_revision: frozenset = frozenset()
    overrides_programas: tuple = ()
    fijos_parquets: dict = None
    nombres_ref: dict = None
    nombre_vigente: dict = None
    retro_info: dict = None
    registro: dict = None
    registro_completo: bool = False
    division_nombres: dict = None
    canonicas_ejecucion: dict = None


# ═══════════════════════════════════════════════════════════════════════════════
# CARGA DE REFERENCIAS Y RETROALIMENTACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

def cargar_referencias(fuentes_dir, logger):
    """Carga ProgramasUN y construye las referencias iniciales: SNIES→División (también fijo: ProgramasUN
    prevalece sobre todo) y nombre→División (nombres de ProgramasUN más los de NOMBRE_DIVISION_OVERRIDE)."""
    path = find_file_insensitive(fuentes_dir, PROGRAMAS_UN_FILE)
    if not path:
        raise FileNotFoundError(f"{PROGRAMAS_UN_FILE} no encontrado en la carpeta Fuentes.")
    df = pd.read_excel(path)
    logger.debug(f"{PROGRAMAS_UN_FILE} cargado: {len(df)} programas")

    col_snies = COLUMNAS_PROGRAMAS_UN["snies"]
    col_div = COLUMNAS_PROGRAMAS_UN["division"]
    col_nombre = COLUMNAS_PROGRAMAS_UN["nombre"]

    snies_num = pd.to_numeric(df[col_snies], errors="coerce")
    mask = snies_num.notna() & df[col_div].notna()
    df_valid = df.loc[mask]
    snies_map = dict(zip(
        snies_num.loc[mask].astype(int),
        df_valid[col_div].astype(str).apply(normalizar_texto),
    ))

    mask_nombre = df[col_nombre].notna() & df[col_div].notna()
    df_nombre = df.loc[mask_nombre]
    nombre_map = {}
    if not df_nombre.empty:
        nombres_kw = normalizar_serie_keywords(df_nombre[col_nombre])
        divisiones_norm = df_nombre[col_div].astype(str).apply(normalizar_texto)
        for nombre, div in zip(nombres_kw, divisiones_norm):
            if nombre and nombre not in nombre_map:
                nombre_map[nombre] = div

    # Los nombres con override manual también son referencia de la Capa 2 (match exacto o por similitud).
    # Con el mismo nombre prevalece el override, que de todos modos fuerza esa división al final.
    n_un = len(nombre_map)
    # Un nombre de ProgramasUN con override de otra división: el programa UniNorte conserva la suya (ProgramasUN prevalece
    # por SNIES), pero los demás programas con ese nombre toman la del override
    choques = sorted(n for n, d in nombre_map.items() if NOMBRE_OVERRIDE.get(n, d) != d)
    if choques:
        logger.warning(
            f"{len(choques)} nombre(s) de {PROGRAMAS_UN_FILE} con override de otra division (el programa UniNorte conserva la "
            "de ProgramasUN; los demas programas con ese nombre, la del override): "
            + "; ".join(f"'{n}': {nombre_map[n]} / override {NOMBRE_OVERRIDE[n]}" for n in choques[:10])
        )
    nombre_map.update(NOMBRE_OVERRIDE)

    nombres_ref = defaultdict(set)
    nombre_vigente = {}
    mask_ref = snies_num.notna() & df[col_nombre].notna()
    for s, n in zip(snies_num[mask_ref].astype(int), normalizar_serie_keywords(df.loc[mask_ref, col_nombre])):
        if n:
            nombres_ref[s].add(n)
            nombre_vigente.setdefault(s, n)

    logger.debug(f"Diccionario SNIES->Division: {len(snies_map)} entradas")
    logger.debug(
        f"Nombre map inicial: {n_un} nombres desde {PROGRAMAS_UN_FILE} "
        f"+ {len(nombre_map) - n_un} desde NOMBRE_DIVISION_OVERRIDE"
    )
    return Referencias(snies_map, nombre_map, dict(snies_map), dict(snies_map),
                       nombres_ref={s: frozenset(n) for s, n in nombres_ref.items()}, nombre_vigente=nombre_vigente)


# Procedencia de cada entrada de la retroalimentación (formato {"division", "origen", "detalle", "fecha"}):
ORIGENES_RETRO = {
    "PROGRAMAS_UN": "Fijada por ProgramasUN.xlsx",
    "OVERRIDE": "Fijada por un override por nombre (detalle: el nombre)",
    "VOTACION": "Clasificacion de la votacion aceptada con confianza ALTA/MEDIA",
    "HISTORICO": "Anterior a la trazabilidad (procedencia desconocida)",
}


def _division_de_entrada(entrada):
    """División de una entrada de la retroalimentación: texto (formato anterior) o {"division": ...}."""
    if isinstance(entrada, dict):
        entrada = entrada.get("division")
    return entrada if isinstance(entrada, str) and entrada.strip() else None


def cargar_retroalimentacion(retro_dir, logger, divisiones_validas_norm=None, info=None, meta_out=None):
    """Carga correcciones/clasificaciones previas desde retro_dir/retroalimentacion.json.
    Retorna None si el archivo existe pero es ilegible: continuar sin él cambiaría la salida
    y guardar después sobrescribiría el historial.
    info (dict opcional): se llena con SNIES -> {"division", "origen", "detalle", "nombre", "nombres"} de cada entrada
    válida; meta_out (dict opcional), con el _meta del archivo."""
    path = os.path.join(retro_dir, RETROALIMENTACION_FILE)
    if not os.path.exists(path):
        logger.warning(
            f"No se encontro {RETROALIMENTACION_FILE}: se clasificara sin retroalimentacion previa "
            "(esperado solo en la primera ejecucion)."
        )
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"se esperaba un objeto JSON {{snies: {{division, origen, ...}}}}, se encontro {type(data).__name__}")
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
        logger.error(f"{RETROALIMENTACION_FILE} esta corrupto o tiene un formato invalido: {e}")
        return None

    meta = data.pop("_meta", None)
    if meta_out is not None and isinstance(meta, dict):
        meta_out.update(meta)
    if meta is not None and not isinstance(meta, dict):
        logger.warning(f"  Retroalimentacion: '_meta' con formato invalido ({type(meta).__name__}); se omite la verificacion de integridad")
    elif meta is not None:
        expected = meta.get("count", -1)
        actual = len(data)
        if expected != actual:
            logger.warning(
                f"  Retroalimentacion: integridad — esperadas {expected} entradas, "
                f"encontradas {actual}. Posible corrupcion parcial."
            )

    # Solo contiene divisiones verificadas (ProgramasUN, overrides por nombre, correcciones manuales), OTRO incluida
    retro_map = {}
    claves_invalidas = 0
    divisiones_invalidas = 0
    nombres_invalidos = 0
    for snies_str, entrada in data.items():
        try:
            snies_int = int(snies_str)
        except (ValueError, TypeError):
            claves_invalidas += 1
            continue
        div = _division_de_entrada(entrada)
        if div is None:
            divisiones_invalidas += 1
            continue
        div_norm = normalizar_texto(div)
        if divisiones_validas_norm is not None and div_norm not in divisiones_validas_norm:
            divisiones_invalidas += 1
            continue
        retro_map[snies_int] = div_norm
        if info is not None:
            e = entrada if isinstance(entrada, dict) else {}
            nombres = {}
            for n, v in (e.get("nombres") if isinstance(e.get("nombres"), dict) else {}).items():
                # Misma validación que la división del SNIES: la de un nombre registrado se impone tal cual en los parquets
                d = normalizar_texto(_division_de_entrada(v)) if _division_de_entrada(v) else None
                if d is None or (divisiones_validas_norm is not None and d not in divisiones_validas_norm):
                    nombres_invalidos += 1
                    continue
                nombres[n] = d
            info[snies_int] = {"division": div_norm, "origen": e.get("origen", "HISTORICO"),
                               "detalle": e.get("detalle"), "nombre": e.get("nombre"), "nombres": nombres}
    if claves_invalidas > 0:
        logger.warning(f"  Retroalimentacion: {claves_invalidas} entradas descartadas por clave no numerica")
    if divisiones_invalidas > 0:
        logger.warning(f"  Retroalimentacion: {divisiones_invalidas} entradas descartadas por division invalida")
    if nombres_invalidos > 0:
        logger.warning(f"  Retroalimentacion: {nombres_invalidos} nombres registrados descartados por division invalida "
                       "(no conservan esa division: se vuelven a clasificar)")
    logger.info(f"Retroalimentacion: {len(retro_map):,} entradas previas", extra=VISIBLE)
    return retro_map


def fusionar_retroalimentacion(snies_map, retro_map, divisiones_validas_norm, logger):
    """Agrega al SNIES map (ProgramasUN) las entradas de la retroalimentación.
    ProgramasUN prevalece; los SNIES en conflicto se reportan porque al guardar
    el historial se sobrescribe con el valor de ProgramasUN."""
    conflictos = []
    for snies, div in retro_map.items():
        actual = snies_map.get(snies)
        if actual is None:
            snies_map[snies] = div
        elif actual != div:
            conflictos.append((snies, div, actual))

    if conflictos:
        max_consola = 20
        logger.warning(
            f"{len(conflictos)} SNIES de {RETROALIMENTACION_FILE} contradicen {PROGRAMAS_UN_FILE} "
            f"(prevalece {PROGRAMAS_UN_FILE}; el historial se actualizara al guardar):"
        )
        for i, (snies, anterior, nueva) in enumerate(sorted(conflictos)):
            log = logger.warning if i < max_consola else logger.debug
            log(f"  SNIES {snies}: '{divisiones_validas_norm.get(anterior, anterior)}' -> "
                f"'{divisiones_validas_norm.get(nueva, nueva)}'")
        if len(conflictos) > max_consola:
            logger.warning(f"  ... y {len(conflictos) - max_consola} mas (detalle en el archivo de log)")
    return snies_map


def _respaldar_retroalimentacion(path, retro_dir, logger):
    """Copia el historial vigente a retro_dir/retroalimentacion_<fecha>.json.bak y conserva las últimas
    MAX_BACKUPS_RETRO copias. Un fallo se avisa pero no impide guardar."""
    base = os.path.splitext(RETROALIMENTACION_FILE)[0]
    destino = os.path.join(retro_dir, f"{base}_{datetime.now():%Y%m%d_%H%M%S}.json.bak")
    try:
        shutil.copy2(path, destino)
    except OSError as e:
        logger.warning(f"  No se pudo crear la copia de seguridad de {RETROALIMENTACION_FILE} ({e}); se guarda de todos modos.")
        return
    logger.debug(f"  Copia de seguridad: {destino}")
    rotar_archivos(retro_dir, f"{base}_*.json.bak", MAX_BACKUPS_RETRO, logger)


def _entrada_retro(division, anterior, origen, detalle, hoy, nombre=None):
    """Entrada con procedencia. anterior: entrada guardada (texto del formato anterior, dict o None).
    origen None = se conserva la procedencia anterior (HISTORICO si no la tenía, VOTACION si es nueva).
    fecha = día del último cambio de división (None si no cambió desde antes de la trazabilidad).
    nombre = nombre vigente (normalizado) con el que se verificó la división; None = se conserva el anterior."""
    previa = anterior if isinstance(anterior, dict) else {}
    div_previa = _division_de_entrada(anterior)
    if origen is None:
        if anterior is None:
            origen = "VOTACION"
        else:
            origen, detalle = previa.get("origen", "HISTORICO"), previa.get("detalle")
    elif (origen == "OVERRIDE" and previa.get("origen") == "OVERRIDE" and div_previa == division
          and _division_override(previa.get("detalle")) == normalizar_texto(division)):
        # Un SNIES con varios nombres con override (p. ej. uno truncado): se conserva el ya registrado mientras siga
        # vigente, para que la procedencia no cambie según los archivos procesados
        detalle = previa["detalle"]
    fecha = previa.get("fecha") if div_previa == division else hoy
    entrada = {"division": division, "origen": origen, "detalle": detalle, "fecha": fecha,
               "nombre": nombre or previa.get("nombre")}
    if isinstance(previa.get("nombres"), dict):
        entrada["nombres"] = previa["nombres"]
    return entrada


def guardar_retroalimentacion(snies_map, retro_dir, divisiones_validas_norm, logger, excluir=frozenset(),
                              snies_un=frozenset(), overrides=None, nombres=None, reclasificados=frozenset(), registro=None):
    """Persiste el SNIES map enriquecido para mejorar futuras ejecuciones, salvo los SNIES de excluir
    (clasificados solo por votación: no son una división verificada y se reevalúan en cada ejecución).
    Cada entrada guarda su procedencia (ORIGENES_RETRO): ProgramasUN (snies_un), override (overrides: SNIES -> nombre),
    votación (reclasificados: SNIES cuyo override ya no existe y cuya votación confirmó la división) o la anterior;
    las del formato anterior se convierten (HISTORICO). nombres: SNIES -> nombre vigente con el que se verificó la
    división (para detectar renombres en ejecuciones futuras); sin nombre se conserva el anterior.
    registro (construir_registro): SNIES -> {nombre: {"division", "fuentes", "desde", "hasta"}} de todos los nombres con
    que el programa aparece en las fuentes; se agrega a "nombres" de cada entrada (los ya registrados se actualizan, los
    que ya no aparecen se conservan). Solo con registro el archivo queda marcado como registro completo.
    Si el historial cambia, antes se respalda la versión anterior."""
    overrides = overrides or {}
    nombres = nombres or {}
    path = os.path.join(retro_dir, RETROALIMENTACION_FILE)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                raise ValueError(f"se esperaba un objeto JSON, se encontro {type(existing).__name__}")
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
            # Nunca sobrescribir un historial ilegible: se perderían las clasificaciones acumuladas
            logger.error(f"{RETROALIMENTACION_FILE} ilegible al guardar ({e}). No se actualizo para no perder el historial.")
            return False
    original = dict(existing)
    existing.pop("_meta", None)
    hoy = f"{datetime.now():%Y-%m-%d}"

    nuevos = 0
    actualizados = 0
    for snies, div_norm in snies_map.items():
        if snies in excluir:
            continue
        snies_str = str(snies)
        div_original = divisiones_validas_norm.get(div_norm, div_norm)
        anterior = existing.get(snies_str)
        if snies in snies_un:
            origen, detalle = "PROGRAMAS_UN", None
        elif snies in overrides:
            origen, detalle = "OVERRIDE", overrides[snies]
        elif snies in reclasificados:
            origen, detalle = "VOTACION", None
        else:
            origen, detalle = None, None
        if anterior is None:
            nuevos += 1
        elif _division_de_entrada(anterior) != div_original:
            actualizados += 1
        existing[snies_str] = _entrada_retro(div_original, anterior, origen, detalle, hoy, nombres.get(snies))
        if registro and snies in registro:
            previos = existing[snies_str].get("nombres") or {}
            nuevos_nombres = {n: {**e, "division": divisiones_validas_norm.get(e["division"], e["division"])}
                              for n, e in registro[snies].items()}
            existing[snies_str]["nombres"] = {**previos, **nuevos_nombres}

    # Entradas que esta ejecución no tocó (SNIES ausentes de las fuentes): solo se convierten al formato con procedencia
    # (una entrada editada a mano sin algún campo lo recibe con su valor por defecto)
    for snies_str, entrada in existing.items():
        if not isinstance(entrada, dict):
            existing[snies_str] = _entrada_retro(entrada, entrada, None, None, hoy)
        elif not {"origen", "detalle", "fecha", "nombre"} <= entrada.keys():
            existing[snies_str] = {"origen": "HISTORICO", "detalle": None, "fecha": None, "nombre": None, **entrada}

    n_entries = len(existing)
    registro_completo = bool(registro) or bool(isinstance(original.get("_meta"), dict) and original["_meta"].get("registro_nombres"))
    existing["_meta"] = {"count": n_entries, "formato": 4, "registro_nombres": registro_completo,
                         "origenes": dict(Counter(e["origen"] for e in existing.values()))}
    # Un cambio solo de la huella del diccionario no justifica un respaldo (desplazaría los que guardan cambios reales)
    if isinstance(original.get("_meta"), dict):
        original["_meta"] = {k: v for k, v in original["_meta"].items() if k != "diccionario"}
    historial_cambia = bool(original) and existing != original
    existing["_meta"]["diccionario"] = huella_diccionarios()[0]

    try:
        os.makedirs(retro_dir, exist_ok=True)
    except OSError as e:
        logger.error(f"No se pudo crear la carpeta de retroalimentacion '{retro_dir}': {e}")
        return False
    if historial_cambia:
        _respaldar_retroalimentacion(path, retro_dir, logger)

    def _escribir_json(tmp_path):
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

    ok = _escritura_atomica(path, _escribir_json, logger)
    if ok:
        msg = f"Retroalimentacion guardada: {n_entries:,} entradas totales (+{nuevos:,} nuevas"
        if actualizados > 0:
            msg += f", {actualizados:,} actualizadas"
        msg += ")"
        logger.info(msg, extra=VISIBLE)
        logger.info("  Procedencia: " + ", ".join(f"{o} {n:,}" for o, n in sorted(existing["_meta"]["origenes"].items(), key=lambda x: -x[1])))
        if excluir:
            logger.info(
                f"  {len(excluir):,} programa(s) de {PROGRAMAS_FILE} clasificados solo por votacion (sin SNIES "
                "verificado) no se guardan: se reevaluan en cada ejecucion hasta que un override los fije"
            )
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# CAPA 2: MATCH POR NOMBRE DEL PROGRAMA
# ═══════════════════════════════════════════════════════════════════════════════

_MATCH_STOPWORDS = frozenset({
    "de", "en", "la", "el", "y", "e", "los", "las", "del", "al",
    "con", "para", "por", "a", "o", "su", "un", "una",
    "especializacion", "maestria", "doctorado", "pregrado", "posgrado",
    "tecnologia", "tecnico", "tecnica", "profesional", "licenciatura",
    "tecnologica", "tecnologico",
})


# Largo mínimo del prefijo común para que dos tokens distintos cuenten como coincidencia parcial
_MIN_PREFIJO = 5


def _tokens_contenido(texto_kw):
    """Extrae tokens de contenido eliminando stopwords y niveles académicos."""
    if not texto_kw:
        return frozenset()
    tokens = set(texto_kw.split()) - _MATCH_STOPWORDS
    return frozenset(t for t in tokens if len(t) >= 3)


# Overrides que solo valen por nombre exacto (no entran al índice de parecido de la Capa 2):
# - Los que al quitar nivel académico y conectores quedan en una sola palabra ("tecnologia de alimentos"
#   -> "alimentos"): por parecido coincidirían con cualquier nombre que la contenga (medido con validación
#   cruzada: excluirlos corrige ~100 programas nuevos sin aumentar la revisión).
# - Las variantes con caracteres dañados de la fuente ("gesti¿n"): existen para el match exacto de esos
#   registros; por parecido sus palabras dañadas no cuentan y el resto puede coincidir con otro programa
#   ("tecnolog¿a de procesamiento de alimentos" -> OTRO frente a "tecnologia en procesamiento de alimentos"
#   -> INGENIERIAS de otras instituciones).
_CARACTERES_DANADOS = CARACTERES_DANADOS
_OVERRIDES_SOLO_EXACTO = frozenset(
    t for t in NOMBRE_OVERRIDE
    if len(_tokens_contenido(t)) == 1 or any(c in t for c in _CARACTERES_DANADOS)
)


def _indice_por_largo(mapa):
    """Nombres de mapa sin caracteres dañados, agrupados por largo: los candidatos de _match_comodin."""
    por_largo = defaultdict(list)
    for texto, div in mapa.items():
        if not any(c in texto for c in _CARACTERES_DANADOS):
            por_largo[len(texto)].append((texto, div))
    return dict(por_largo)


def _match_comodin(texto, por_largo):
    """División del nombre conocido que coincide con texto tomando cada carácter dañado ('¿', '�') como una letra
    cualquiera: la fuente cambia así una letra con tilde o una ñ ('gesti¿n' = 'gestion', 'dise¿o' = 'diseno').
    Solo si todas las coincidencias tienen la misma división; sin coincidencias o con divisiones distintas, None."""
    if not any(c in texto for c in _CARACTERES_DANADOS):
        return None
    patron = re.compile("".join("." if c in _CARACTERES_DANADOS else re.escape(c) for c in texto))
    divisiones = {div for cand, div in por_largo.get(len(texto), ()) if patron.fullmatch(cand)}
    return divisiones.pop() if len(divisiones) == 1 else None


# Nombres de override sin caracteres dañados por largo: una variante dañada sin override propio toma el de su nombre limpio
_OVERRIDES_POR_LARGO = _indice_por_largo(NOMBRE_OVERRIDE)


_MIN_TRUNCADO = MIN_TRUNCADO

def _indice_canonico(mapa):
    """forma_canonica -> división de los nombres de mapa, solo para las formas cuyos nombres tienen todos la misma división."""
    grupos = defaultdict(set)
    for texto, div in mapa.items():
        grupos[forma_canonica(texto)].add(div)
    return {k: next(iter(v)) for k, v in grupos.items() if len(v) == 1}


# Overrides por forma canónica: un nombre sin override propio toma el del mismo nombre con otro orden de palabras
_OVERRIDES_CANONICOS = _indice_canonico(NOMBRE_OVERRIDE)


# Nombres de override sin caracteres dañados, ordenados: candidatos de _match_truncado
_OVERRIDES_ORDENADOS = sorted(k for k in NOMBRE_OVERRIDE if not any(c in k for c in _CARACTERES_DANADOS))


def _match_truncado(texto):
    """División del override del mismo nombre truncado por la fuente: texto es el comienzo (de al menos _MIN_TRUNCADO
    caracteres) de un nombre con override, o un override de al menos ese largo es el comienzo de texto. Solo si todas
    las coincidencias tienen la misma división; si no, None."""
    if len(texto) < _MIN_TRUNCADO:
        return None
    divisiones = set()
    i = bisect.bisect_left(_OVERRIDES_ORDENADOS, texto)
    while i < len(_OVERRIDES_ORDENADOS) and _OVERRIDES_ORDENADOS[i].startswith(texto):
        if _OVERRIDES_ORDENADOS[i] != texto:
            divisiones.add(NOMBRE_OVERRIDE[_OVERRIDES_ORDENADOS[i]])
        i += 1
    for largo in range(_MIN_TRUNCADO, len(texto)):
        div = NOMBRE_OVERRIDE.get(texto[:largo])
        if div is not None:
            divisiones.add(div)
    return divisiones.pop() if len(divisiones) == 1 else None


def _division_override(nombre):
    """División que fija un override para nombre (normalizado): exacto; si no, el de su nombre limpio equivalente
    (caracteres dañados, _match_comodin), el de su nombre completo o truncado (_match_truncado) o el del mismo nombre con
    otro orden de palabras o conectores (forma_canonica)."""
    if not isinstance(nombre, str):
        return None
    return (NOMBRE_OVERRIDE.get(nombre) or _match_comodin(nombre, _OVERRIDES_POR_LARGO) or _match_truncado(nombre)
            or _OVERRIDES_CANONICOS.get(forma_canonica(nombre)))


def _mismo_nombre(a, b):
    """Dos nombres normalizados son el mismo nombre del programa: iguales salvo caracteres dañados de la fuente
    ('gesti¿n' = 'gestion') o uno truncado por la fuente (prefijo del otro, de al menos _MIN_TRUNCADO caracteres)."""
    if a == b:
        return True
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False
    corto, largo = (a, b) if len(a) <= len(b) else (b, a)
    if len(corto) < len(largo) and len(corto) < _MIN_TRUNCADO:
        return False
    return all(x == y or x in _CARACTERES_DANADOS or y in _CARACTERES_DANADOS for x, y in zip(corto, largo))


def _similitud_tokens(tokens_a, tokens_b):
    """Calcula similitud entre dos conjuntos de tokens (intersección + prefix matching)."""
    if not tokens_a or not tokens_b:
        return 0.0

    interseccion = tokens_a & tokens_b
    matches = len(interseccion)

    rest_a = tokens_a - interseccion
    rest_b = list(tokens_b - interseccion)
    used = set()
    for ta in rest_a:
        if len(ta) < _MIN_PREFIJO:
            continue
        for i, tb in enumerate(rest_b):
            if i in used or len(tb) < _MIN_PREFIJO:
                continue
            prefix_len = 0
            for ca, cb in zip(ta, tb):
                if ca == cb:
                    prefix_len += 1
                else:
                    break
            if prefix_len >= _MIN_PREFIJO:
                matches += 0.8
                used.add(i)
                break

    menor = min(len(tokens_a), len(tokens_b))
    return matches / menor if menor > 0 else 0.0


def _claves_indice(tokens):
    """Claves con las que dos conjuntos de tokens pueden tener similitud > 0 en _similitud_tokens:
    un token en común o, entre tokens de al menos _MIN_PREFIJO letras, el mismo prefijo de ese largo."""
    claves = {("t", t) for t in tokens}
    claves.update(("p", t[:_MIN_PREFIJO]) for t in tokens if len(t) >= _MIN_PREFIJO)
    return claves


def _indexar_referencias(mapa):
    """Tokens de contenido de cada referencia, en el orden del mapa, e índice invertido clave -> posiciones."""
    refs = []
    indice = defaultdict(list)
    for texto, div in mapa.items():
        tokens = _tokens_contenido(texto)
        if tokens:
            for clave in _claves_indice(tokens):
                indice[clave].append(len(refs))
            refs.append((tokens, div))
    return refs, dict(indice)


def _solo_exacto(texto):
    """Mismo criterio que _OVERRIDES_SOLO_EXACTO: una sola palabra de contenido o caracteres dañados."""
    return len(_tokens_contenido(texto)) == 1 or any(c in texto for c in _CARACTERES_DANADOS)


def preparar_ref_tokens(nombre_map, solo_exacto=frozenset()):
    """Pre-computa tokens de contenido e índice invertido de las referencias de nombre.
    Retorna ((referencias, índice invertido, nombres por largo para el comodín), memo): el memo guarda el resultado
    de _match_fuzzy por texto y solo es válido con estas referencias (se crea de nuevo con ellas).
    Los overrides de una sola palabra de contenido (y los nombres de solo_exacto) no entran al índice de
    parecido (sí al match exacto y al comodín)."""
    referencias = {t: d for t, d in nombre_map.items() if t not in _OVERRIDES_SOLO_EXACTO and t not in solo_exacto}
    return (*_indexar_referencias(referencias), _indice_por_largo(nombre_map), _indice_canonico(nombre_map)), {}


def nombres_verificados(df, col_map, snies_map, nombre_map):
    """Referencias de la Capa 2 para los programas sin SNIES conocido del archivo: nombre -> división de los programas
    del mismo archivo cuyo SNIES ya tiene división verificada (retroalimentación o ProgramasUN), por mayoría (los
    nombres empatados se omiten), sumadas a nombre_map (que prevalece).
    Así un programa nuevo con el nombre de otro ya verificado hereda esa decisión (medido con validación cruzada:
    -35 % de errores silenciosos y la mitad de revisión con SNIES nuevo y nombre conocido).
    Retorna (nombre_map extendido, ref_tokens) o None si no hay nombres nuevos."""
    snies = pd.to_numeric(df[col_map["snies"]], errors="coerce")
    division = snies.dropna().astype(int).map(snies_map).reindex(df.index)
    nombres = normalizar_serie_keywords(df[col_map["nombre"]])
    pares = pd.DataFrame({"n": nombres, "d": division})
    pares = pares[pares.d.notna() & (pares.n != "") & ~pares.n.isin(nombre_map)]
    if pares.empty:
        return None
    conteo = pares.groupby(["n", "d"]).size()
    mayoria = {}
    for (n, d), k in conteo.items():
        actual = mayoria.get(n)
        if actual is None or k > actual[1]:
            mayoria[n] = (d, k, False)
        elif k == actual[1]:
            mayoria[n] = (actual[0], k, True)
    # Orden: primero nombre_map y luego los nombres en el orden en que aparecen en el archivo. Con igual similitud
    # gana la primera referencia (_buscar_match): así ProgramasUN y los overrides prevalecen en los empates.
    extendido = dict(nombre_map)
    for n in pd.unique(pares.n):
        d, _, empate = mayoria[n]
        if not empate:
            extendido[n] = d
    solo_exacto = frozenset(n for n in mayoria if _solo_exacto(n))
    return extendido, preparar_ref_tokens(extendido, solo_exacto)


def _match_fuzzy(texto_kw, mapa_directo, ref_tokens, memo=None):
    """Busca match exacto o fuzzy contra programas ya clasificados.
    El resultado depende solo del texto y de las referencias: con memo, un mismo nombre
    presente en varias combinaciones se busca una sola vez."""
    if memo is None:
        return _buscar_match(texto_kw, mapa_directo, ref_tokens)
    if texto_kw not in memo:
        memo[texto_kw] = _buscar_match(texto_kw, mapa_directo, ref_tokens)
    return memo[texto_kw]


def _buscar_match(texto_kw, mapa_directo, ref_tokens):
    """Match exacto; si no hay, el del nombre limpio equivalente (nombres con caracteres dañados, _match_comodin);
    si no, el de mayor similitud de tokens que supere UMBRAL_SIMILITUD_NOMBRE."""
    if not texto_kw:
        return None

    if texto_kw in mapa_directo:
        return mapa_directo[texto_kw]

    refs, indice, por_largo, canonicos = ref_tokens
    # El mismo nombre con otro orden de palabras o conectores cuenta como coincidencia exacta
    div_canonica = canonicos.get(forma_canonica(texto_kw))
    if div_canonica is not None:
        return div_canonica
    div_comodin = _match_comodin(texto_kw, por_largo)
    if div_comodin is not None:
        return div_comodin

    tokens_target = _tokens_contenido(texto_kw)
    if len(tokens_target) < 2:
        return None

    # Solo puntúan > 0 las referencias que comparten un token o un prefijo (las demás nunca superan
    # mejor_score = 0).
    candidatos = set()
    for clave in _claves_indice(tokens_target):
        candidatos.update(indice.get(clave, ()))

    # Con igual similitud gana la referencia con más palabras coincidentes ("medicina veterinaria"
    # antes que "medicina" para "medicina veterinaria y zootecnia"); si persiste el empate, la primera.
    mejor = (0.0, 0.0)
    mejor_div = None

    for pos in sorted(candidatos):
        tokens_ref, div = refs[pos]
        score = _similitud_tokens(tokens_target, tokens_ref)
        # round: el producto reconstruye el número de coincidencias y no debe variar por precisión de punto flotante
        clave = (score, round(score * min(len(tokens_target), len(tokens_ref)), 6))
        if clave > mejor:
            mejor = clave
            mejor_div = div

    if mejor[0] >= UMBRAL_SIMILITUD_NOMBRE:
        return mejor_div
    return None


def capa2_nombre(nombre_kw, nombre_map, ref_nombres, memo=None):
    """
    Capa 2: Match del nombre contra programas ya clasificados.
    Match exacto primero, luego fuzzy por similitud de tokens. Retorna la división votada o "".
    """
    return _match_fuzzy(nombre_kw, nombre_map, ref_nombres, memo) or ""


# ═══════════════════════════════════════════════════════════════════════════════
# CAPA 3: DICCIONARIOS NBC + CINE DETALLADO
# ═══════════════════════════════════════════════════════════════════════════════

def _resolver_conflicto_nbc_cine(voto_nbc, voto_cine, cine_det_norm, nombre_kw):
    """Resuelve conflictos cuando NBC y CINE votan divisiones distintas.
    Retorna (division_ganadora, fuente_perdedora) donde fuente_perdedora es
    "nbc", "cine", o None si ambos coinciden."""
    if voto_nbc == voto_cine:
        return voto_nbc, None

    if any(p in cine_det_norm for p in CINE_VAGOS_PATTERNS):
        return voto_nbc, "cine"

    score_nbc = sum(1 for kw in KEYWORDS.get(voto_nbc, []) if kw in nombre_kw) if nombre_kw else 0
    score_cine = sum(1 for kw in KEYWORDS.get(voto_cine, []) if kw in nombre_kw) if nombre_kw else 0

    if score_nbc >= score_cine:
        return voto_nbc, "cine"
    return voto_cine, "nbc"


def _contiene_con_comodin(texto, patron):
    """True si patron aparece en texto tomando cada carácter dañado del texto ('¿', '�') como una letra cualquiera."""
    n = len(patron)
    return any(all(a == b or a in _CARACTERES_DANADOS for a, b in zip(texto[i:i + n], patron))
               for i in range(len(texto) - n + 1))


def capa3_votar(nbc_norm, cine_det_norm, nombre_kw, disponibilidad):
    """
    Capa 3: Diccionarios NBC + CINE Detallado.
    Incluye resolución inteligente de conflictos cuando NBC y CINE votan diferente.
    """
    votos = defaultdict(float)
    voto_nbc_str = ""
    voto_cine_str = ""

    tiene_cine = disponibilidad.get("cine_detallado", True) and cine_det_norm is not None
    tiene_nbc = nbc_norm is not None

    peso_cine = PESO_CAPA3_CINE
    peso_nbc = PESO_CAPA3_NBC
    if not tiene_cine and tiene_nbc:
        peso_nbc = PESO_CAPA3_CINE + PESO_CAPA3_NBC
    elif tiene_cine and not tiene_nbc:
        peso_cine = PESO_CAPA3_CINE + PESO_CAPA3_NBC
    elif not tiene_cine and not tiene_nbc:
        return votos, voto_nbc_str, voto_cine_str

    # --- Determinar voto CINE ---
    if tiene_cine and cine_det_norm in CINE_EXCLUSIVO:
        voto_cine_str = CINE_EXCLUSIVO[cine_det_norm]

    # --- Determinar voto NBC ---
    if tiene_nbc:
        if nbc_norm in NBC_EXCLUSIVO:
            voto_nbc_str = NBC_EXCLUSIVO[nbc_norm]

        elif nbc_norm in NBC_AMBIGUO:
            info = NBC_AMBIGUO[nbc_norm]
            division_asignada = None
            # Nombre con caracteres dañados ('gesti¿n'): cada uno vale por una letra al buscar los patrones (como en la Capa 2
            # y en los overrides), para que la variante dañada vote lo mismo que su nombre limpio
            danado = bool(nombre_kw) and any(c in nombre_kw for c in _CARACTERES_DANADOS)

            for campo, patron, div_regla in info["reglas"]:
                texto_buscar = ""
                if campo == "nombre":
                    texto_buscar = nombre_kw
                elif campo == "cine_detallado":
                    texto_buscar = cine_det_norm or ""

                if patron in texto_buscar or (danado and campo == "nombre" and _contiene_con_comodin(texto_buscar, patron)):
                    division_asignada = div_regla
                    break

            if division_asignada is None and info["default"] is not None:
                division_asignada = info["default"]

            if division_asignada:
                voto_nbc_str = division_asignada

    # --- Resolución de conflictos ---
    if voto_nbc_str and voto_cine_str:
        peso_combinado = peso_nbc + peso_cine
        ganador, perdedor = _resolver_conflicto_nbc_cine(
            voto_nbc_str, voto_cine_str, cine_det_norm, nombre_kw,
        )
        votos[ganador] = peso_combinado
        if perdedor == "nbc":
            voto_nbc_str = ""
        elif perdedor == "cine":
            voto_cine_str = ""

    elif voto_nbc_str:
        votos[voto_nbc_str] = peso_nbc
    elif voto_cine_str:
        votos[voto_cine_str] = peso_cine

    return votos, voto_nbc_str, voto_cine_str


# ═══════════════════════════════════════════════════════════════════════════════
# AGREGADOR DE VOTOS
# ═══════════════════════════════════════════════════════════════════════════════

def decidir_clasificacion(votos_por_division):
    """
    Agrega votos de las 3 capas y decide la clasificación final.
    Retorna (division, puntaje, margen, confianza, requiere_revision).
    """
    if not votos_por_division:
        return ("OTRO", 0.0, 0.0, "BAJA", True)

    ranking = sorted(votos_por_division.items(), key=lambda x: x[1], reverse=True)
    mejor = ranking[0]
    segundo = ranking[1] if len(ranking) > 1 else (None, 0.0)

    division = mejor[0]
    puntaje = mejor[1]
    margen = puntaje - segundo[1]

    # Los umbrales coinciden con sumas exactas de pesos (p. ej. 0.50 - 0.40 = margen 0.10): la tolerancia
    # evita que el redondeo de punto flotante (0.5 - 0.4 = 0.0999...) deje un caso por debajo del umbral
    tol = 1e-9
    if puntaje >= UMBRAL_ALTA - tol:
        confianza = "ALTA"
        requiere_revision = False
    elif puntaje >= UMBRAL_MEDIA - tol and margen >= MARGEN_MEDIA - tol:
        confianza = "MEDIA"
        requiere_revision = False
    elif puntaje >= UMBRAL_BAJA - tol and margen >= MARGEN_BAJA - tol:
        confianza = "BAJA"
        requiere_revision = True
    else:
        division = "OTRO"
        confianza = "BAJA"
        requiere_revision = True

    return (division, puntaje, margen, confianza, requiere_revision)


def _empate_con_nombre(votos, voto_nombre, puntaje, margen):
    """True si la votación quedó empatada en el primer lugar y el nombre es una de las divisiones empatadas."""
    tol = 1e-9
    return bool(voto_nombre) and margen < tol and abs(votos[voto_nombre] - puntaje) < tol


def evaluar_consenso(voto_c1, voto_c2, voto_c3_nbc, voto_c3_cine):
    """Evalúa el nivel de consenso entre capas."""
    votos_activos = [v for v in [voto_c1, voto_c2, voto_c3_nbc, voto_c3_cine] if v]

    if not votos_activos:
        return "SIN_VOTOS"

    votos_unicos = set(votos_activos)
    if len(votos_unicos) == 1:
        return "UNANIME"

    conteo = Counter(votos_activos)
    mas_comun_count = conteo.most_common(1)[0][1]
    if mas_comun_count >= 2:
        return "MAYORIA"

    return "DIVIDIDO"


# ═══════════════════════════════════════════════════════════════════════════════
# CLASIFICAR DATAFRAME COMPLETO (deduplicación + vectorizado)
# ═══════════════════════════════════════════════════════════════════════════════

def _to_none(v):
    """Convierte valores NA/NaN a None de forma segura."""
    try:
        if pd.isna(v):
            return None
    except (ValueError, TypeError):
        pass
    return v


# Columnas que agrega _clasificar_fila (mismo orden), para dar forma a un resultado sin filas
_COLUMNAS_RESULTADO = [
    "DIVISION_UNINORTE", "PUNTAJE_TOTAL", "MARGEN", "CONFIANZA", "REQUIERE_REVISION",
    *COLUMNAS_VOTO, "CONSENSO", "ALERTA_INCONSISTENCIA",
]


def _clasificar_fila(nbc, cine_det, nombre, vc1, disponibilidad,
                     nombre_map=None, ref_tokens=None):
    """Clasifica una fila individual usando 3 capas de votación ponderada."""
    votos_total = defaultdict(float)

    # ── Capa 1: Match SNIES ──
    voto_c1_div = ""
    if vc1:
        votos_total[vc1] = PESO_CAPA1
        voto_c1_div = vc1

    # ── Capa 2: Match del nombre contra programas clasificados ──
    voto_c2_nombre = ""
    if nombre_map is not None and ref_tokens is not None:
        ref_nombres, memo = ref_tokens
        voto_c2_nombre = capa2_nombre(nombre, nombre_map, ref_nombres, memo)
        if voto_c2_nombre:
            votos_total[voto_c2_nombre] += PESO_CAPA2_NOMBRE

    # ── Capa 3: Diccionarios NBC + CINE Detallado ──
    votos_c3, voto_nbc, voto_cine = capa3_votar(nbc, cine_det, nombre, disponibilidad)
    for div, peso in votos_c3.items():
        votos_total[div] += peso

    # ── Decisión final ──
    division, puntaje, margen, confianza, requiere_revision = decidir_clasificacion(votos_total)

    if division == "OTRO" and voto_c1_div and voto_c1_div != "OTRO":
        # Votación sin ganador claro y SNIES conocido: se toma la división del SNIES, pero va a revisión.
        # (Si el SNIES es OTRO, el resultado de decidir_clasificacion ya es esa división con su confianza.)
        division = voto_c1_div
        confianza = "BAJA"
        requiere_revision = True
    elif not voto_c1_div and _empate_con_nombre(votos_total, voto_c2_nombre, puntaje, margen):
        # Sin SNIES conocido y con el nombre empatado con CINE/NBC: se toma la división del nombre
        # (con SNIES conocido, también OTRO, prevalece el SNIES)
        division = voto_c2_nombre

    consenso = evaluar_consenso(voto_c1_div, voto_c2_nombre, voto_nbc, voto_cine)

    alerta = False
    if voto_c1_div:
        otras_capas = {v for v in [voto_c2_nombre, voto_nbc, voto_cine] if v}
        if otras_capas and voto_c1_div not in otras_capas:
            alerta = True

    return {
        "DIVISION_UNINORTE": division,
        "PUNTAJE_TOTAL": round(puntaje, 4),
        "MARGEN": round(margen, 4),
        "CONFIANZA": confianza,
        "REQUIERE_REVISION": "VERDADERO" if requiere_revision else "FALSO",
        "VOTO_CAPA1": voto_c1_div,
        "VOTO_CAPA2_NOMBRE": voto_c2_nombre,
        "VOTO_CAPA3_NBC": voto_nbc,
        "VOTO_CAPA3_CINE": voto_cine,
        "CONSENSO": consenso,
        "ALERTA_INCONSISTENCIA": alerta,
    }


def clasificar_dataframe(df, col_map, snies_map, disponibilidad, logger, nombre_archivo="", cache=None, nombre_map=None, ref_tokens=None,
                         refs_sin_snies=None):
    """Aplica clasificación con deduplicación: clasifica combinaciones únicas, luego broadcast.
    Si cache (dict) se pasa, reutiliza resultados entre llamadas (útil para batches).
    nombre_map es el diccionario nombre→división de referencia de la Capa 2.
    ref_tokens (de preparar_ref_tokens) pre-computado evita recalcular tokens y comparte el memo
    de la Capa 2 entre los batches del mismo archivo.
    refs_sin_snies (de nombres_verificados): (nombre_map, ref_tokens) de la Capa 2 para las filas sin SNIES conocido;
    las filas con SNIES conocido usan siempre nombre_map (su propio nombre no debe confirmar su división)."""
    for clave_req in COLUMNAS_REQUERIDAS:
        if clave_req not in col_map:
            raise ValueError(f"col_map debe contener la clave '{clave_req}'")
        col_real = col_map[clave_req]
        if col_real and col_real not in df.columns:
            raise ValueError(f"Columna requerida '{col_real}' ({clave_req}) no encontrada en '{nombre_archivo}'")

    total = len(df)
    if total == 0:
        # Sin filas la clave de deduplicación no se puede construir (pandas no concatena str con una serie vacía sin tipo)
        logger.warning(f"  [{nombre_archivo}] No tiene registros para clasificar.")
        return pd.concat([df.reset_index(drop=True), pd.DataFrame(columns=_COLUMNAS_RESULTADO)], axis=1)

    t0 = time.time()
    logger.debug("  Pre-normalizando columnas...")

    df = df.copy()
    df.reset_index(drop=True, inplace=True)

    col_snies = col_map["snies"]
    col_nombre = col_map["nombre"]
    col_nbc = col_map["nbc"]
    col_cine_det = col_map.get("cine_detallado")

    nombre_kw = normalizar_serie_keywords(df[col_nombre]) if col_nombre and col_nombre in df.columns else pd.Series("", index=df.index)
    nbc_norm = normalizar_nbc(df[col_nbc]) if col_nbc and col_nbc in df.columns else pd.Series(None, index=df.index)
    cine_det_norm = normalizar_serie(df[col_cine_det]) if col_cine_det and col_cine_det in df.columns else pd.Series(None, index=df.index)

    snies_series = df[col_snies]
    snies_numeric = pd.to_numeric(snies_series, errors="coerce")
    snies_lookup = snies_numeric.dropna().astype(int).map(snies_map)
    voto_c1 = snies_lookup.reindex(df.index)

    df["_NORM_NBC"] = nbc_norm
    df["_NORM_CINE_DET"] = cine_det_norm
    df["_NORM_NOMBRE_KW"] = nombre_kw

    # Reutilizar ref_tokens pre-computado o calcular si no se proporcionó
    if ref_tokens is None and nombre_map is not None:
        ref_tokens = preparar_ref_tokens(nombre_map)

    elapsed_prep = time.time() - t0
    logger.debug(f"  Pre-normalizacion completada en {elapsed_prep:.1f}s")

    # --- Deduplicación: agrupar por combinación única de campos ---
    _NA_SENTINEL = "\x00"
    # Solo los campos que votan (SNIES, NBC, CINE detallado y nombre)
    key_str = (
        snies_numeric.fillna(-1).astype(int).astype(str) + "|" +
        nbc_norm.fillna(_NA_SENTINEL) + "|" +
        cine_det_norm.fillna(_NA_SENTINEL) + "|" +
        nombre_kw
    )

    group_ids, unique_keys = pd.factorize(key_str)

    first_idx = pd.Series(range(total)).groupby(group_ids).first().values
    n_unique = len(first_idx)

    ratio = n_unique / total * 100 if total > 0 else 0
    logger.debug(f"  {total:,} registros -> {n_unique:,} combinaciones unicas ({ratio:.1f}%). Clasificando...")

    # --- Pre-extraer valores en índices únicos para evitar overhead de pandas ---
    nbc_list = [_to_none(v) for v in nbc_norm.values[first_idx]]
    cine_list = [_to_none(v) for v in cine_det_norm.values[first_idx]]
    vc1_list = [_to_none(v) for v in voto_c1.values[first_idx]]
    nombre_list = nombre_kw.values[first_idx].tolist()

    # --- Clasificar solo combinaciones únicas (con caché inter-batch opcional) ---
    unique_results = [None] * n_unique
    cache_hits = 0

    for count in barra(range(n_unique), desc="  Clasificando", unidad="comb"):
        key = unique_keys[count]
        if cache is not None and key in cache:
            unique_results[count] = cache[key]
            cache_hits += 1
        else:
            # La clave incluye el SNIES, así que la elección de referencias es estable dentro de la caché
            nm, rt = (refs_sin_snies if refs_sin_snies is not None and not vc1_list[count] else (nombre_map, ref_tokens))
            result = _clasificar_fila(
                nbc_list[count], cine_list[count],
                nombre_list[count] or "",
                vc1_list[count], disponibilidad,
                nombre_map=nm,
                ref_tokens=rt,
            )
            unique_results[count] = result
            if cache is not None:
                cache[key] = result

    # --- Broadcast: mapear resultados a todas las filas ---
    resultados = [unique_results[gid] for gid in group_ids]

    df_res = pd.DataFrame(resultados)
    elapsed_total = time.time() - t0
    cache_msg = f" (cache: {cache_hits:,} reutilizados)" if cache_hits > 0 else ""
    logger.debug(f"  Clasificacion completada: {total:,} registros en {elapsed_total:.1f}s{cache_msg}")
    return pd.concat([df, df_res], axis=1)


# Origen de la división final de cada registro (columna interna ORIGEN_DECISION, para el resumen).
# Cada etapa posterior reemplaza a la anterior: votación -> override por nombre -> división del SNIES (Programas.xlsx
# o, si el SNIES solo está en los parquets, la de sus overrides) -> división de ProgramasUN -> en los parquets, división
# de los nombres distintos al vigente sin override (registrada o, si el nombre es nuevo, la de su clasificación). Un
# registro con otro nombre (programa renombrado) y override propio conserva la división de ese override.
ORIGENES = {
    "FIJO_UN": "Fija por ProgramasUN",
    "FIJO_PROGRAMAS": f"Fija por {PROGRAMAS_FILE} (mismo SNIES)",
    "OVERRIDE": "Override por nombre",
    "VOTO_SNIES": "Votacion con SNIES conocido (retroalimentacion)",
    "VOTO_SIN_SNIES": "Votacion sin SNIES conocido (nombre + NBC/CINE)",
    "REGISTRO": "Nombre registrado en la retroalimentacion (division fija)",
    "NOMBRE_NUEVO": "Nombre nuevo de un SNIES registrado (votacion sin Capa 1)",
}


def marcar_origen_votacion(df):
    """Inicializa ORIGEN_DECISION tras la votación y el refinamiento, según si hubo voto SNIES (Capa 1)."""
    con_snies = df["VOTO_CAPA1"].astype(bool) if "VOTO_CAPA1" in df.columns else pd.Series(False, index=df.index)
    df["ORIGEN_DECISION"] = pd.Categorical(
        np.where(con_snies, "VOTO_SNIES", "VOTO_SIN_SNIES"), categories=list(ORIGENES),
    )
    return df


def _imponer_division(df, mask, destino, origen):
    """Asigna destino (división normalizada) a las filas de mask con confianza ALTA y sin revisión, y registra
    su origen (clave de ORIGENES o Series de claves). Retorna cuántas de esas filas cambian de división."""
    cambian = int((mask & (df["DIVISION_UNINORTE"] != destino)).sum())
    df.loc[mask, "DIVISION_UNINORTE"] = destino[mask]
    if "ORIGEN_DECISION" in df.columns:
        df.loc[mask, "ORIGEN_DECISION"] = origen[mask] if isinstance(origen, pd.Series) else origen
    if "CONFIANZA" in df.columns:
        df.loc[mask, "CONFIANZA"] = "ALTA"
    if "REQUIERE_REVISION" in df.columns:
        df.loc[mask, "REQUIERE_REVISION"] = "FALSO"
    return cambian


def aplicar_nombre_overrides(df, col_map, logger):
    """Aplica los overrides manuales por nombre exacto (NOMBRE_DIVISION_OVERRIDE). Un nombre sin override propio toma
    el de su nombre limpio equivalente si tiene caracteres dañados (_match_comodin) o el de su nombre completo si la
    fuente lo truncó (_match_truncado)."""
    if not NOMBRE_OVERRIDE:
        return df
    col_kw = "_NORM_NOMBRE_KW"
    if col_kw in df.columns:
        nombres = df[col_kw]
    else:
        col_nombre = col_map.get("nombre")
        if not col_nombre or col_nombre not in df.columns:
            return df
        nombres = normalizar_serie_keywords(df[col_nombre])

    # Un solo map (match exacto por nombre) en vez de una pasada completa por regla
    destino = nombres.map(NOMBRE_OVERRIDE)
    comodin = {n: d for n in pd.unique(nombres[destino.isna()])
               if isinstance(n, str) and (d := _match_comodin(n, _OVERRIDES_POR_LARGO) or _match_truncado(n)
                                        or _OVERRIDES_CANONICOS.get(forma_canonica(n))) is not None}
    if comodin:
        destino = destino.fillna(nombres.map(comodin))
    mask = destino.notna()
    total_override = int(mask.sum())

    if total_override > 0:
        cambian = _imponer_division(df, mask, destino, "OVERRIDE")
        por_comodin = int(nombres[mask].isin(comodin).sum())
        logger.info(
            f"  Override por nombre: {total_override:,} registros coinciden "
            f"({nombres[mask].nunique():,} de {len(NOMBRE_OVERRIDE):,} nombres; {por_comodin:,} por nombre con caracteres "
            f"danados, truncado o con otro orden de palabras equivalente a un override); {cambian:,} cambian de division"
        )
    return df


def _renombrados_con_override(df, mask, snies, nombres_ref):
    """Filas de mask cuya división fijó un override por nombre y cuyo nombre no es ninguno de los de referencia de su
    SNIES (_mismo_nombre): el programa tiene en ese registro otro nombre (renombrado) con decisión propia."""
    if "ORIGEN_DECISION" not in df.columns or "_NORM_NOMBRE_KW" not in df.columns:
        return pd.Series(False, index=df.index)
    cand = mask & (df["ORIGEN_DECISION"] == "OVERRIDE")
    if not cand.any():
        return cand
    pares = pd.DataFrame({"s": snies[cand], "n": df.loc[cand, "_NORM_NOMBRE_KW"]}).drop_duplicates()
    otros = [(s, n) for s, n in zip(pares.s, pares.n)
             if not any(_mismo_nombre(n, r) for r in nombres_ref.get(int(s), ()))]
    if not otros:
        return pd.Series(False, index=df.index)
    clave = pd.MultiIndex.from_arrays([snies[cand], df.loc[cand, "_NORM_NOMBRE_KW"]])
    res = pd.Series(False, index=df.index)
    res[cand] = clave.isin(otros)
    return res


def aplicar_division_por_snies(df, col_map, snies_fijos, snies_un, logger, snies_revision=frozenset(), etiqueta="",
                               origen_fijo="FIJO_PROGRAMAS", nombres_ref=None):
    """Impone la división fija de cada SNIES de snies_fijos a sus registros. Se aplica después de los overrides por
    nombre: la clasificación final de Programas.xlsx en los parquets y, al final, ProgramasUN.
    nombres_ref (solo parquets): SNIES -> nombres del programa en ProgramasUN y Programas.xlsx. Un registro cuyo nombre
    no es ninguno de ellos (programa renombrado: otro nombre en ese archivo) y que tiene override propio conserva la
    división del override; un SNIES sin nombres de referencia (solo en los parquets) conserva todos sus overrides.
    Sin nombres_ref (Programas.xlsx) la división fija se impone a todos los registros del SNIES.
    Los SNIES de snies_revision quedan también en revisión. origen_fijo: clave de ORIGENES de los SNIES que no son
    de ProgramasUN."""
    col_snies = col_map.get("snies")
    if not snies_fijos or not col_snies or col_snies not in df.columns:
        return df

    snies = pd.to_numeric(df[col_snies], errors="coerce")
    destino = snies.map(snies_fijos)
    mask = destino.notna()
    if nombres_ref is not None:
        conservan = _renombrados_con_override(df, mask, snies, nombres_ref)
        if conservan.any():
            mask &= ~conservan
            # Sin nombres de referencia (SNIES solo en los parquets) todo registro con override lo conserva
            logger.debug(
                f"  {int(conservan.sum()):,} registros ({snies[conservan].nunique():,} SNIES"
                f"{', ' + etiqueta if etiqueta else ''}) conservan el override de su nombre"
                f"{' (programa renombrado)' if nombres_ref else ''}"
            )
    total = int(mask.sum())

    if total > 0:
        origen = pd.Series(np.where(snies.isin(list(snies_un)), "FIJO_UN", origen_fijo), index=df.index)
        cambian = _imponer_division(df, mask, destino, origen)
        if snies_revision:
            en_revision = mask & snies.isin(list(snies_revision))
            if "CONFIANZA" in df.columns:
                df.loc[en_revision, "CONFIANZA"] = "BAJA"
            if "REQUIERE_REVISION" in df.columns:
                df.loc[en_revision, "REQUIERE_REVISION"] = "VERDADERO"
        nivel = logger.info if cambian else logger.debug
        nivel(
            f"  Division fija por SNIES{' (' + etiqueta + ')' if etiqueta else ''}: {total:,} registros ({snies[mask].nunique():,} SNIES); "
            f"{cambian:,} cambian de division"
        )
    return df


# Orígenes que son decisiones explícitas (override por nombre o división fija): un SNIES con varias divisiones solo por
# ellos es un programa renombrado cuyos nombres tienen decisión propia, no una duda de la votación
_ORIGENES_DECIDIDOS = ("OVERRIDE", "FIJO_PROGRAMAS", "FIJO_UN", "REGISTRO", "NOMBRE_NUEVO")


def _clave_periodo(df, columnas_periodo):
    """Periodo de cada registro como un número ordenable (p. ej. AÑO*100 + SEMESTRE); sin columnas de periodo, 0."""
    cols = [c for c in columnas_periodo if c in df.columns]
    clave = pd.Series(0.0, index=df.index)
    for i, c in enumerate(cols):
        clave += pd.to_numeric(df[c], errors="coerce").fillna(-1) * 100 ** (len(cols) - 1 - i)
    return clave


def unificar_division_por_snies(df, col_map, logger, etiqueta="", columnas_periodo=(), nombres_ref=None):
    """Un SNIES con varias divisiones en el archivo:
    - Si todas vienen de decisiones explícitas (programa renombrado: cada nombre con su override o con la división del
      SNIES), cada nombre conserva su división, también dentro del mismo archivo (los parquets abarcan varios años y el
      programa pudo cambiar de nombre entre ellos).
    - Si alguna viene de la votación (p. ej. un SNIES nuevo, solo en los parquets, con nombres que votan distinto), es una
      duda: el SNIES toma la división de sus registros con el nombre vigente (nombres_ref: ProgramasUN y Programas.xlsx)
      si aparece y, si no, la de su registro más reciente (columnas_periodo; empate: más registros, luego orden
      alfabético), y queda en revisión.
    Retorna df."""
    col_snies = col_map.get("snies")
    if not col_snies or col_snies not in df.columns or "DIVISION_UNINORTE" not in df.columns:
        return df
    snies = pd.to_numeric(df[col_snies], errors="coerce")
    base = pd.DataFrame({"s": snies, "d": df["DIVISION_UNINORTE"]})
    base = base[base.s.notna() & base.d.notna()]
    varias = base.groupby("s")["d"].nunique()
    varias = varias[varias > 1].index
    if not len(varias):
        return df
    mask = snies.isin(varias)
    if "ORIGEN_DECISION" in df.columns:
        dudosos = pd.Index(snies[mask & ~df["ORIGEN_DECISION"].isin(_ORIGENES_DECIDIDOS)].unique())
    else:
        dudosos = pd.Index(varias)
    renombrados = varias.difference(dudosos)
    if len(renombrados):
        # Permitido y sin acción: el resumen global lo da verificar_division_por_snies; aquí solo el log
        logger.debug(
            f"  {len(renombrados)} SNIES{' (' + etiqueta + ')' if etiqueta else ''} renombrados: cada nombre conserva su "
            f"division: {sorted(int(x) for x in renombrados)[:10]}"
        )
    if not len(dudosos):
        return df

    mask = snies.isin(dudosos)
    sub = base[mask[base.index]].assign(p=_clave_periodo(df.loc[mask], columnas_periodo), v=False)
    if nombres_ref and "_NORM_NOMBRE_KW" in df.columns:
        sub["nn"] = df.loc[sub.index, "_NORM_NOMBRE_KW"]
        vigentes = {(s, n) for s, n in sub[["s", "nn"]].drop_duplicates().itertuples(index=False)
                    if any(_mismo_nombre(n, r) for r in nombres_ref.get(int(s), ()))}
        sub["v"] = pd.MultiIndex.from_arrays([sub.s, sub.nn]).isin(list(vigentes)) if vigentes else False
    resumen = sub.groupby(["s", "d"], as_index=False).agg(v=("v", "max"), p=("p", "max"), n=("p", "size"))
    elegida = (resumen.sort_values(["s", "v", "p", "n", "d"], ascending=[True, False, False, False, True])
               .drop_duplicates("s").set_index("s")["d"])
    df.loc[mask, "DIVISION_UNINORTE"] = snies[mask].map(elegida)
    if "CONFIANZA" in df.columns:
        df.loc[mask, "CONFIANZA"] = "BAJA"
    if "REQUIERE_REVISION" in df.columns:
        df.loc[mask, "REQUIERE_REVISION"] = "VERDADERO"
    logger.warning(
        f"  {len(dudosos)} SNIES{' (' + etiqueta + ')' if etiqueta else ''} tenian mas de una division por votacion: "
        f"se unificaron ({int(mask.sum()):,} registros) y quedan en revision: {sorted(int(x) for x in dudosos)[:10]}"
    )
    return df


def _referencias_canonicas(refs):
    """forma_canonica -> división de los nombres ya decididos: los de la Capa 2 (ProgramasUN, overrides y Programas.xlsx
    clasificado) y los registrados en la retroalimentación (todas las fuentes). Solo formas cuyos nombres tienen una sola
    división."""
    grupos = defaultdict(set)
    for n, div in (refs.nombres or {}).items():
        grupos[forma_canonica(n)].add(div)
    for nombres in (refs.registro or {}).values():
        for n, div in nombres.items():
            grupos[forma_canonica(n)].add(div)
    decididas = {k: next(iter(v)) for k, v in grupos.items() if len(v) == 1}
    # Lo decidido en archivos anteriores de esta ejecución, solo para formas sin referencia (las referencias prevalecen)
    for k, div in (refs.canonicas_ejecucion or {}).items():
        if k not in grupos:
            decididas[k] = div
    return decididas


# Orígenes de una división no decidida explícitamente: los únicos que la consistencia por forma canónica puede cambiar
_ORIGENES_VOTACION_NUEVA = ("VOTO_SIN_SNIES", "NOMBRE_NUEVO")


def unificar_division_por_forma_canonica(df, refs, logger, etiqueta=""):
    """El mismo nombre con otro orden de palabras o conectores (forma_canonica: 'gestion e innovacion de empresas' =
    'innovacion y gestion de empresas') tiene una sola división:
    - Un programa decidido por votación (sin SNIES conocido o nombre nuevo de un SNIES) cuyo nombre equivale a uno ya
      decidido (referencias de la Capa 2 o registro de la retroalimentación) toma esa división.
    - Programas por votación con nombres equivalentes entre sí, sin referencia y con divisiones distintas: toman la del de
      mayor puntaje (empate: más registros, luego orden alfabético) y quedan en revisión.
    Las divisiones decididas (override, ProgramasUN, SNIES conocido, registro) no se cambian: si dos de ellas difieren, lo
    informa verificar_division_por_forma_canonica. Retorna df."""
    if not {"_NORM_NOMBRE_KW", "ORIGEN_DECISION", "DIVISION_UNINORTE"} <= set(df.columns):
        return df
    nombres = df["_NORM_NOMBRE_KW"]
    canon = nombres.map({n: forma_canonica(n) for n in pd.unique(nombres) if isinstance(n, str) and n})
    votacion = df["ORIGEN_DECISION"].isin(_ORIGENES_VOTACION_NUEVA) & canon.notna()
    if not votacion.any():
        return df
    sufijo = f" ({etiqueta})" if etiqueta else ""
    destino = canon.where(votacion).map(_referencias_canonicas(refs))
    cambia = destino.notna() & (destino != df["DIVISION_UNINORTE"])
    if cambia.any():
        df.loc[cambia, "DIVISION_UNINORTE"] = destino[cambia]
        logger.info(f"  Nombres equivalentes a uno ya decidido (otro orden de palabras){sufijo}: {int(cambia.sum()):,} registros "
                    f"({nombres[cambia].nunique():,} nombres) toman su division")
    libres = votacion & destino.isna()
    puntaje = pd.to_numeric(df["PUNTAJE_TOTAL"], errors="coerce").fillna(0) if "PUNTAJE_TOTAL" in df.columns else 0.0
    sub = pd.DataFrame({"c": canon, "n": nombres, "d": df["DIVISION_UNINORTE"], "p": puntaje})[libres]
    varias = sub.groupby("c")["d"].nunique()
    varias = varias[varias > 1].index
    if len(varias):
        sub = sub[sub.c.isin(varias)]
        resumen = sub.groupby(["c", "d"], as_index=False).agg(p=("p", "max"), k=("p", "size"))
        elegida = (resumen.sort_values(["c", "p", "k", "d"], ascending=[True, False, False, True])
                   .drop_duplicates("c").set_index("c")["d"])
        df.loc[sub.index, "DIVISION_UNINORTE"] = sub.c.map(elegida)
        if "CONFIANZA" in df.columns:
            df.loc[sub.index, "CONFIANZA"] = "BAJA"
        if "REQUIERE_REVISION" in df.columns:
            df.loc[sub.index, "REQUIERE_REVISION"] = "VERDADERO"
        logger.warning(f"  {len(varias)} nombre(s) nuevos equivalentes entre si (otro orden de palabras){sufijo} tenian divisiones "
                       f"distintas: se unificaron ({len(sub):,} registros) y quedan en revision: {sorted(sub.n.unique())[:6]}")
    return df


def canonicas_de_archivo(df_final, col_map, previas=None):
    """Agrega a previas (forma_canonica -> división normalizada) las formas de un archivo ya clasificado que tienen una sola
    división en él; las ya presentes en previas se conservan (gana el primer archivo de la ejecución). Retorna el dict nuevo."""
    salida = dict(previas or {})
    if "DIVISION_UNINORTE" not in df_final.columns:
        return salida
    nombres = df_final["_NORM_NOMBRE_KW"] if "_NORM_NOMBRE_KW" in df_final.columns else normalizar_serie_keywords(df_final[col_map["nombre"]])
    t = pd.DataFrame({"n": nombres, "d": df_final["DIVISION_UNINORTE"].map(normalizar_texto)}).dropna().drop_duplicates()
    t = t[t.n != ""]
    t = t.assign(c=t.n.map({n: forma_canonica(n) for n in pd.unique(t.n)}))
    una = t.groupby("c")["d"].nunique()
    for c, div in t[t.c.isin(una[una == 1].index)].drop_duplicates("c")[["c", "d"]].itertuples(index=False):
        salida.setdefault(c, div)
    return salida


def verificar_division_por_forma_canonica(tablas, logger):
    """Comprobación final de la ejecución (tablas: archivo -> divisiones_por_nombre): los nombres equivalentes por orden de
    palabras o conectores (forma_canonica) tienen una sola división en todos los archivos. Los nombres en revisión no
    cuentan. Retorna {forma canónica: detalle} de lo que incumple la regla (vacío si todo está bien)."""
    if not tablas:
        return {}
    t = pd.concat([x.assign(archivo=a) for a, x in tablas.items()], ignore_index=True)
    t = t[~t.rev.astype(bool) & t.n.map(lambda n: isinstance(n, str) and bool(n))]
    if t.empty:
        return {}
    t = t.assign(c=t.n.map({n: forma_canonica(n) for n in pd.unique(t.n)}))
    varias = t.groupby("c")["d"].nunique()
    errores = {}
    for c in varias[varias > 1].index:
        x = t[t.c == c].drop_duplicates(["n", "d"])
        errores[c] = "; ".join(f"'{n}' -> {d}" for n, d in zip(x.n, x.d))
    if errores:
        logger.error(f"{len(errores)} grupo(s) de nombres equivalentes (otro orden de palabras) con divisiones distintas:")
        for c in sorted(errores)[:20]:
            logger.error(f"  {errores[c]}")
    else:
        grupos = int((t.groupby("c")["n"].nunique() > 1).sum())
        logger.info(f"Nombres equivalentes por orden de palabras verificados: {grupos:,} grupos con una sola division",
                    extra=VISIBLE)
    return errores


def overrides_eliminados(retro_info):
    """SNIES cuya división guardó un override por nombre que ya no existe en NOMBRE_DIVISION_OVERRIDE: su división de la
    retroalimentación ya no tiene respaldo. Retorna {SNIES: nombre del override eliminado}."""
    return {s: e["detalle"] for s, e in (retro_info or {}).items()
            if e.get("origen") == "OVERRIDE" and e.get("detalle") and _division_override(e["detalle"]) is None}


def revisar_referencias_dudosas(df, col_map, refs, disponibilidad, logger):
    """Programas.xlsx: la Capa 1 (retroalimentación, peso 0.50) no pierde la votación, así que un programa renombrado sin
    override propio, o cuyo override fue eliminado, conservaría en silencio la división de su nombre anterior. Para esos
    SNIES (no de ProgramasUN, sin override para su nombre actual):
    - Renombrado: si el nombre nuevo ya está registrado para el SNIES, toma la división registrada; si no, se clasifica
      como nombre nuevo (_division_nombre_nuevo): votación sin la Capa 1 (nombre + NBC/CINE) que se adopta con confianza
      ALTA/MEDIA y va a revisión con BAJA (conserva la división actual).
    - Override eliminado: si la votación sin la Capa 1 da otra división, queda en revisión con la actual.
    La división anterior queda registrada con el nombre anterior, que la conserva en los parquets. Retorna df."""
    info = refs.retro_info or {}
    col_snies = col_map.get("snies")
    if not info or not col_snies or col_snies not in df.columns or "_NORM_NOMBRE_KW" not in df.columns:
        return df
    snies = pd.to_numeric(df[col_snies], errors="coerce")
    eliminados = overrides_eliminados(info)
    candidatos = {}
    for idx, s, n in zip(df.index, snies, df["_NORM_NOMBRE_KW"]):
        if pd.isna(s) or int(s) in refs.snies_un or int(s) not in info or _division_override(n) is not None:
            continue
        e = info[int(s)]
        if int(s) in eliminados:
            candidatos[idx] = f"override '{eliminados[int(s)]}' eliminado"
        elif e.get("nombre") and not _mismo_nombre(n, e["nombre"]):
            candidatos[idx] = f"renombrado: '{e['nombre']}' -> '{n}'"
    if not candidatos:
        return df
    nombre_map = refs.nombres
    ref_tokens = preparar_ref_tokens(nombre_map)
    revisar, adoptados = [], []
    for idx, motivo in candidatos.items():
        s, n, actual = int(snies[idx]), df.at[idx, "_NORM_NOMBRE_KW"], df.at[idx, "DIVISION_UNINORTE"]
        if motivo.startswith("renombrado"):
            registrada = _division_registrada((refs.registro or {}).get(s, {}), n)
            if registrada is not None:
                division, conf, revision = registrada, "ALTA", False
            else:
                division, conf, revision = _division_nombre_nuevo(
                    _to_none(df.at[idx, "_NORM_NBC"]) if "_NORM_NBC" in df.columns else None,
                    _to_none(df.at[idx, "_NORM_CINE_DET"]) if "_NORM_CINE_DET" in df.columns else None,
                    n, actual, disponibilidad, nombre_map, ref_tokens)
            if revision:
                revisar.append((idx, motivo, division))
            elif division != actual:
                adoptados.append((idx, motivo, actual, division, conf))
            continue
        voto = _clasificar_fila(_to_none(df.at[idx, "_NORM_NBC"]) if "_NORM_NBC" in df.columns else None,
                                _to_none(df.at[idx, "_NORM_CINE_DET"]) if "_NORM_CINE_DET" in df.columns else None,
                                n, "", disponibilidad, nombre_map, ref_tokens)
        if voto["DIVISION_UNINORTE"] != actual:
            revisar.append((idx, motivo, voto["DIVISION_UNINORTE"]))
    logger.info(f"  Referencias dudosas en la retroalimentacion (renombrados u overrides eliminados): {len(candidatos):,}; "
                f"{len(adoptados):,} toman otra division (nombre nuevo clasificado o registrado), {len(revisar):,} a revision")
    for idx, motivo, antes, division, conf in adoptados:
        df.at[idx, "DIVISION_UNINORTE"] = division
        if "ORIGEN_DECISION" in df.columns:
            df.at[idx, "ORIGEN_DECISION"] = "NOMBRE_NUEVO"
        if "CONFIANZA" in df.columns:
            df.at[idx, "CONFIANZA"] = conf
        if "REQUIERE_REVISION" in df.columns:
            df.at[idx, "REQUIERE_REVISION"] = "FALSO"
        logger.info(f"    SNIES {int(snies[idx])}: {motivo}; {antes} -> {division}")
    if revisar:
        filas = [i for i, _, _ in revisar]
        if "CONFIANZA" in df.columns:
            df.loc[filas, "CONFIANZA"] = "BAJA"
        if "REQUIERE_REVISION" in df.columns:
            df.loc[filas, "REQUIERE_REVISION"] = "VERDADERO"
        for i, motivo, voto in revisar[:20]:
            logger.warning(f"    SNIES {int(snies[i])}: {motivo}; division actual {df.at[i, 'DIVISION_UNINORTE']}, "
                           f"votacion sin SNIES {voto}")
    return df


def _division_registrada(registro_snies, nombre):
    """División registrada para nombre (o una variante suya: caracteres dañados, truncado) en el registro de un SNIES."""
    if nombre in registro_snies:
        return registro_snies[nombre]
    return next((d for n, d in registro_snies.items() if _mismo_nombre(nombre, n)), None)


def _division_nombre_nuevo(nbc, cine, nombre, division_snies, disponibilidad, nombre_map, ref_tokens):
    """Clasificación de un nombre nuevo de un SNIES ya registrado (programa renombrado), con las capas y puntajes de
    siempre pero sin la Capa 1: el voto del SNIES corresponde a su nombre anterior.
    - Igual a la división del SNIES: se mantiene.
    - Otra división con confianza ALTA/MEDIA: se adopta.
    - Otra división con confianza BAJA: conserva la del SNIES y va a revisión.
    Retorna (división, confianza, requiere_revision)."""
    voto = _clasificar_fila(nbc, cine, nombre, "", disponibilidad, nombre_map, ref_tokens)
    division, conf = voto["DIVISION_UNINORTE"], voto["CONFIANZA"]
    if division == division_snies:
        return division_snies, "ALTA", False
    if conf in ("ALTA", "MEDIA"):
        return division, conf, False
    return division_snies, "BAJA", True


def inventario_nombres(rutas_parquet, col_map, columnas_periodo=()):
    """Nombres con que aparece cada SNIES en TODOS los parquets de Fuentes (no solo los seleccionados), para que el
    registro y la clasificación de nombres no dependan de la selección. Retorna DataFrame con una fila por (SNIES, nombre
    normalizado, archivo): s, n, archivo, p (periodo más reciente), k (registros), desde, hasta (años), y el NBC y CINE
    detallado (normalizados) más frecuentes del nombre: nbc, cine."""
    col_snies, col_nombre = col_map["snies"], col_map["nombre"]
    col_nbc, col_cine = col_map.get("nbc"), col_map.get("cine_detallado")
    partes = []
    for ruta in rutas_parquet:
        try:
            nombres_cols = pq.read_schema(ruta).names
            cols_p = [c for c in columnas_periodo if c in nombres_cols]
            extra = [c for c in (col_nbc, col_cine) if c and c in nombres_cols]
            t = pq.read_table(ruta, columns=[col_snies, col_nombre, *extra, *cols_p]).to_pandas()
        except Exception as e:
            # El error de pyarrow no siempre nombra el archivo
            raise RuntimeError(f"'{os.path.basename(ruta)}': {type(e).__name__}: {e}") from e
        t["p"] = _clave_periodo(t, cols_p)
        t["anio"] = pd.to_numeric(t[cols_p[0]], errors="coerce") if cols_p else np.nan
        for c in (col_nbc, col_cine):
            if c not in t.columns:
                t[c] = None
        g = t.groupby([col_snies, col_nombre, col_nbc, col_cine], as_index=False, dropna=False).agg(
            p=("p", "max"), k=("p", "size"), desde=("anio", "min"), hasta=("anio", "max"))
        g["archivo"] = os.path.basename(ruta)
        partes.append(g)
    columnas = ["s", "n", "archivo", "p", "k", "desde", "hasta", "nbc", "cine"]
    if not partes:
        return pd.DataFrame(columns=columnas)
    t = pd.concat(partes, ignore_index=True)
    t["s"] = pd.to_numeric(t[col_snies], errors="coerce")
    t = t[t.s.notna()]
    nombres = pd.Series(pd.unique(t[col_nombre].dropna()))
    t["n"] = t[col_nombre].map(dict(zip(nombres, normalizar_serie_keywords(nombres))))
    t = t[t.n.notna() & (t.n != "")]
    t["nbc"] = normalizar_nbc(t[col_nbc].astype(object))
    t["cine"] = normalizar_serie(t[col_cine].astype(object))
    t["s"] = t.s.astype(int)
    # NBC/CINE más frecuentes de cada (SNIES, nombre)
    frec = (t.groupby(["s", "n", "nbc", "cine"], dropna=False)["k"].sum().reset_index()
            .sort_values(["s", "n", "k"], ascending=[True, True, False]).drop_duplicates(["s", "n"]))
    inv = t.groupby(["s", "n", "archivo"], as_index=False).agg(p=("p", "max"), k=("k", "sum"), desde=("desde", "min"),
                                                                hasta=("hasta", "max"))
    inv = inv.merge(frec[["s", "n", "nbc", "cine"]], on=["s", "n"], how="left")
    return inv[columnas]


def resolver_nombres(inventario, refs, logger):
    """División de los nombres de los parquets que no son el vigente del programa (Programas.xlsx/ProgramasUN) y no
    tienen override, para los SNIES con división conocida (Programas.xlsx, ProgramasUN o, si solo están en los parquets,
    la de sus overrides):
    - Nombre registrado en la retroalimentación: conserva su división registrada (aunque cambie la del SNIES).
    - Nombre nuevo de un SNIES ya registrado (p. ej. renombrado en una fecha posterior a la del registro): se clasifica con _division_nombre_nuevo.
    - Sin registro todavía (primera ejecución) o SNIES sin registro: no se incluye (toma la división del SNIES).
    Retorna {(SNIES, nombre): (división, origen, requiere_revision)}."""
    if inventario is None or inventario.empty:
        return {}
    division_snies = {**(refs.fijos_parquets or {}), **refs.snies_fijos}
    registro = refs.registro or {}
    nombres_ref = refs.nombres_ref or {}
    pares = inventario.drop_duplicates(["s", "n"])
    pares = pares[pares.s.isin(list(division_snies))]
    resultado, nuevos = {}, []
    ref_tokens = None
    for s, n, nbc, cine in zip(pares.s, pares.n, pares.nbc, pares.cine):
        if any(_mismo_nombre(n, r) for r in nombres_ref.get(s, ())) or _division_override(n) is not None:
            continue
        registrada = _division_registrada(registro.get(s, {}), n)
        if registrada is not None:
            resultado[(s, n)] = (registrada, "REGISTRO", False)
            continue
        if not (refs.registro_completo and registro.get(s)):
            continue
        if ref_tokens is None:
            ref_tokens = preparar_ref_tokens(refs.nombres)
        division, conf, revision = _division_nombre_nuevo(_to_none(nbc), _to_none(cine), n, division_snies[s],
                                                          {"cine_detallado": True}, refs.nombres, ref_tokens)
        resultado[(s, n)] = (division, "NOMBRE_NUEVO", revision)
        nuevos.append((s, n, division_snies[s], division, conf, revision))
    distintos = sum(1 for x in nuevos if x[3] != x[2])
    logger.info(f"  Nombres de los parquets: {sum(1 for v in resultado.values() if v[1] == 'REGISTRO'):,} registrados "
                f"(division fija); {len(nuevos):,} nuevos de SNIES registrados ({distintos:,} con otra division, "
                f"{sum(1 for x in nuevos if x[5]):,} a revision)")
    for s, n, antes, division, conf, revision in nuevos[:20]:
        logger.info(f"    SNIES {s}: nombre nuevo '{n}' -> {division} ({conf})"
                    + (" en revision" if revision else "") + (f"; division del SNIES {antes}" if division != antes or revision else ""))
    return resultado


def aplicar_division_por_nombre(df, col_map, division_nombres, logger):
    """Parquets: impone a los registros de cada (SNIES, nombre) de division_nombres (resolver_nombres) su división: la
    registrada o la de su clasificación como nombre nuevo; los nombres nuevos con confianza BAJA quedan en revisión."""
    col_snies = col_map.get("snies")
    if not division_nombres or not col_snies or col_snies not in df.columns or "_NORM_NOMBRE_KW" not in df.columns:
        return df
    snies = pd.to_numeric(df[col_snies], errors="coerce")
    con_snies = snies.isin(list({s for s, _ in division_nombres}))
    if not con_snies.any():
        return df
    claves = pd.Series([(int(s), n) if c else None for s, n, c in zip(snies, df["_NORM_NOMBRE_KW"], con_snies)],
                       index=df.index)
    info = claves[con_snies].map(division_nombres).dropna()
    if info.empty:
        return df
    mask = pd.Series(False, index=df.index)
    mask[info.index] = True
    if "ORIGEN_DECISION" in df.columns:
        mask &= df["ORIGEN_DECISION"] != "OVERRIDE"
    info = info[mask[info.index]]
    if info.empty:
        return df
    destino = pd.Series([x[0] for x in info], index=info.index).reindex(df.index)
    origen = pd.Series([x[1] for x in info], index=info.index).reindex(df.index)
    cambian = _imponer_division(df, mask, destino, origen)
    en_revision = [i for i, x in info.items() if x[2]]
    if en_revision:
        if "CONFIANZA" in df.columns:
            df.loc[en_revision, "CONFIANZA"] = "BAJA"
        if "REQUIERE_REVISION" in df.columns:
            df.loc[en_revision, "REQUIERE_REVISION"] = "VERDADERO"
    nivel = logger.info if cambian or en_revision else logger.debug
    nivel(f"  Division por nombre (registrados y nuevos): {int(mask.sum()):,} registros; {cambian:,} cambian de division; "
          f"{len(en_revision):,} en revision")
    return df


def construir_registro(inventario, refs, division_nombres):
    """Registro de nombres para la retroalimentación: por SNIES con división conocida, cada nombre con que aparece en
    Programas.xlsx/ProgramasUN y en los parquets, con su división en esta ejecución, los archivos donde aparece y sus años.
    No se registran los nombres nuevos que quedaron en revisión ni los SNIES en revisión en Programas.xlsx (su división
    aún no está confirmada): se vuelven a clasificar hasta que se decidan.
    Retorna {SNIES: {nombre: {"division", "fuentes", "desde", "hasta"}}} (divisiones normalizadas)."""
    division_snies = {s: d for s, d in {**(refs.fijos_parquets or {}), **refs.snies_fijos}.items()
                      if s not in refs.snies_revision}
    nombres_ref = refs.nombres_ref or {}
    division_nombres = division_nombres or {}
    registro = defaultdict(dict)
    for s, nombres in nombres_ref.items():
        if s in division_snies:
            fuente = PROGRAMAS_UN_FILE if s in refs.snies_un else PROGRAMAS_FILE
            for n in nombres:
                registro[s][n] = {"division": division_snies[s], "fuentes": [fuente], "desde": None, "hasta": None}
    if inventario is not None and not inventario.empty:
        inv = inventario[inventario.s.isin(list(division_snies))]
        agg = inv.groupby(["s", "n"]).agg(fuentes=("archivo", lambda a: sorted(set(a))), desde=("desde", "min"),
                                         hasta=("hasta", "max"))
        for (s, n), fila in agg.iterrows():
            s = int(s)
            if any(_mismo_nombre(n, r) for r in nombres_ref.get(s, ())):
                division = division_snies[s]
            elif _division_override(n) is not None:
                division = _division_override(n)
            elif (s, n) in division_nombres:
                division, _, revision = division_nombres[(s, n)]
                if revision:
                    continue
            else:
                division = division_snies[s]
            previo = registro[s].get(n, {})
            registro[s][n] = {"division": division, "fuentes": sorted(set(previo.get("fuentes", [])) | set(fila.fuentes)),
                              "desde": None if pd.isna(fila.desde) else int(fila.desde),
                              "hasta": None if pd.isna(fila.hasta) else int(fila.hasta)}
    return dict(registro)


def divisiones_por_nombre(df, col_map):
    """Pares (SNIES, nombre normalizado) de un archivo clasificado con su división y si alguno de sus registros quedó en
    revisión: lo que comprueba verificar_division_por_snies. Retorna DataFrame (s, n, d, rev)."""
    col_snies = col_map.get("snies")
    if not col_snies or col_snies not in df.columns or "DIVISION_UNINORTE" not in df.columns:
        return pd.DataFrame(columns=["s", "n", "d", "rev"])
    if "_NORM_NOMBRE_KW" in df.columns:
        nombres = df["_NORM_NOMBRE_KW"]
    else:
        nombres = normalizar_serie_keywords(df[col_map["nombre"]])
    rev = (df["REQUIERE_REVISION"] == "VERDADERO") if "REQUIERE_REVISION" in df.columns else False
    t = pd.DataFrame({"s": pd.to_numeric(df[col_snies], errors="coerce"), "n": nombres,
                      "d": df["DIVISION_UNINORTE"], "rev": rev})
    t = t[t.s.notna() & t.d.notna()]
    t = t.groupby(["s", "n", "d"], as_index=False, observed=True)["rev"].max()
    t["s"] = t.s.astype(int)
    return t


def verificar_division_por_snies(tablas, logger):
    """Comprueba la regla de división por SNIES de una ejecución (tablas: archivo -> divisiones_por_nombre):
    - Un mismo SNIES con un mismo nombre tiene una sola división en todos los archivos (si no, error).
    - Dos nombres de un SNIES que son el mismo nombre escrito distinto (caracteres dañados, truncado) tienen la misma
      división (si no, error).
    - Un SNIES puede tener divisiones distintas solo por nombres distintos (programa renombrado): se informa.
    Los pares en revisión (dudas de la votación) no cuentan como error: ya están en las listas de revisión.
    Retorna {SNIES: detalle} de lo que incumple la regla (vacío si todo está bien)."""
    if not tablas:
        return {}
    t = pd.concat([x.assign(archivo=a) for a, x in tablas.items()], ignore_index=True)
    en_revision = set(map(tuple, t.loc[t.rev, ["s", "n"]].drop_duplicates().itertuples(index=False)))
    ok = t[[(s, n) not in en_revision for s, n in zip(t.s, t.n)]]
    errores = {}

    por_par = ok.groupby(["s", "n"])["d"].nunique()
    for s, n in por_par[por_par > 1].index:
        x = ok[(ok.s == s) & (ok.n == n)]
        errores.setdefault(s, []).append(f"'{n}': " + "; ".join(f"{d} ({', '.join(sorted(g.archivo))})" for d, g in x.groupby("d")))

    divs = ok.drop_duplicates(["s", "n", "d"])
    varias = divs.groupby("s")["d"].nunique()
    renombrados = {}
    for s in varias[varias > 1].index:
        x = divs[divs.s == s].groupby("n")["d"].first()
        nombres = list(x.items())
        for i, (n1, d1) in enumerate(nombres):
            for n2, d2 in nombres[i + 1:]:
                if d1 != d2 and _mismo_nombre(n1, n2):
                    errores.setdefault(s, []).append(f"'{n1}' -> {d1} y '{n2}' -> {d2} son el mismo nombre")
        if s not in errores:
            renombrados[s] = "; ".join(f"'{n}' -> {d}" for n, d in nombres)

    if errores:
        logger.error(f"{len(errores)} SNIES con divisiones distintas para un mismo nombre:")
        for s in sorted(errores)[:20]:
            logger.error(f"  SNIES {s}: " + " | ".join(errores[s]))
    if renombrados:
        # Permitido y sin acción: solo un resumen en consola; el detalle queda en el archivo de log
        logger.info(f"{len(renombrados):,} SNIES renombrados con divisiones distintas por nombre (cada nombre con su decision; "
                    "permitido; detalle en el log o con --verbose)", extra=VISIBLE)
        for s in sorted(renombrados):
            logger.debug(f"  SNIES {s}: {renombrados[s]}")
    if not errores:
        logger.info(f"Division por SNIES verificada en {t.s.nunique():,} SNIES: un mismo nombre tiene una sola division en "
                    "todos los archivos", extra=VISIBLE)
    return errores


def division_parquets_por_override(inventario, excluir, logger):
    """División de referencia de los SNIES que solo están en los parquets (no en excluir: Programas.xlsx y ProgramasUN),
    a partir de los overrides de sus nombres en TODOS los parquets (inventario_nombres: misma referencia en cualquier
    selección de archivos): la del override de su nombre más reciente (empate: más registros, luego orden alfabético).
    Es la que toman sus registros sin override propio y la que se guarda en la retroalimentación.
    Retorna ({SNIES: división}, {SNIES: nombre del override})."""
    if inventario is None or inventario.empty:
        return {}, {}
    pares = inventario[~inventario.s.isin(list(excluir))]
    div_nombre = {n: d for n in pd.unique(pares.n) if (d := _division_override(n)) is not None}
    pares = pares.assign(d=pares.n.map(div_nombre))
    pares = pares[pares.d.notna()]
    if pares.empty:
        return {}, {}
    por_nombre = pares.groupby(["s", "n", "d"], as_index=False).agg(p=("p", "max"), k=("k", "sum"))
    elegida = (por_nombre.sort_values(["s", "p", "k", "d", "n"], ascending=[True, False, False, True, True])
               .drop_duplicates("s"))
    division = {int(s): d for s, d in zip(elegida.s, elegida.d)}
    nombre = {int(s): n for s, n in zip(elegida.s, elegida.n)}
    varias = int((por_nombre.groupby("s")["d"].nunique() > 1).sum())
    logger.debug(f"  SNIES solo en parquets con division por override: {len(division):,}"
                 + (f" ({varias} con overrides de divisiones distintas en sus nombres: toma la del mas reciente)" if varias else ""))
    return division, nombre


# ═══════════════════════════════════════════════════════════════════════════════
# ENRIQUECER REFERENCIAS CON PROGRAMAS.XLSX CLASIFICADO
# ═══════════════════════════════════════════════════════════════════════════════

def enriquecer_snies_map(snies_map, df_programas_clasificado, col_snies, snies_un, logger):
    """Lleva la clasificación final de Programas.xlsx a las referencias de los parquets.
    - Capa 1 (y retroalimentación): solo ALTA/MEDIA; prevalece sobre la retroalimentación previa, de modo que la
      corrección de un override por nombre (incluida OTRO) queda guardada para ese SNIES. Nunca cambia un SNIES
      de ProgramasUN.
    - División fija de los parquets: todos sus SNIES, también los que quedaron en revisión.
    Retorna (mapa SNIES -> división final, SNIES decididos solo por votación sin SNIES verificado —no se guardan
    en la retroalimentación—, SNIES que quedaron en revisión, SNIES cuya división fijó un override -> nombre)."""
    df = df_programas_clasificado
    snies_num = pd.to_numeric(df[col_snies], errors="coerce")
    mask_todos = snies_num.notna() & df["DIVISION_UNINORTE"].notna()
    if not mask_todos.any():
        logger.warning(f"{PROGRAMAS_FILE} no produjo registros validos para enriquecer SNIES map")
        return {}, frozenset(), frozenset(), {}
    snies_todos = snies_num[mask_todos].astype(int)
    programas_map = dict(zip(snies_todos, df.loc[mask_todos, "DIVISION_UNINORTE"].astype(str).apply(normalizar_texto)))

    def _snies_donde(columna, valores):
        if columna not in df.columns:
            return frozenset()
        return frozenset(snies_todos[df.loc[mask_todos, columna].isin(valores).values])

    snies_revision = _snies_donde("REQUIERE_REVISION", ["VERDADERO"])
    # Mismo criterio que enriquecer_nombre_map: una clasificación BAJA no debe convertirse en voto SNIES (peso 0.50)
    snies_baja = _snies_donde("CONFIANZA", ["BAJA"])
    snies_auto = _snies_donde("ORIGEN_DECISION", ["VOTO_SIN_SNIES"]) - snies_baja
    overrides = {}
    if "ORIGEN_DECISION" in df.columns and "_NORM_NOMBRE_KW" in df.columns:
        m = (df.loc[mask_todos, "ORIGEN_DECISION"] == "OVERRIDE").values
        overrides = {s: n for s, n in zip(snies_todos[m], df.loc[mask_todos, "_NORM_NOMBRE_KW"].values[m])
                     if s not in snies_un and s not in snies_baja}

    nuevos = 0
    actualizados = 0
    for snies_int, div_norm in programas_map.items():
        if snies_int in snies_un or snies_int in snies_baja:
            continue
        anterior = snies_map.get(snies_int)
        if anterior is None:
            nuevos += 1
        elif anterior != div_norm:
            actualizados += 1
        snies_map[snies_int] = div_norm
    nivel = logger.info if actualizados else logger.debug
    nivel(
        f"  SNIES map enriquecido con {PROGRAMAS_FILE}: +{nuevos:,} nuevos ({len(snies_auto):,} solo por votacion), "
        f"{actualizados:,} actualizados por su clasificacion final (total: {len(snies_map):,})"
    )
    return programas_map, snies_auto, snies_revision, overrides


def enriquecer_nombre_map(nombre_map, df_clasificado, col_map, logger):
    """Enriquece nombre_map con los nombres de los programas clasificados ALTA/MEDIA."""
    col_nombre = col_map["nombre"]
    col_div = "DIVISION_UNINORTE"

    if "CONFIANZA" in df_clasificado.columns:
        mask_conf = df_clasificado["CONFIANZA"].isin(["ALTA", "MEDIA"])
    else:
        mask_conf = pd.Series(True, index=df_clasificado.index)

    mask = mask_conf & df_clasificado[col_nombre].notna() & df_clasificado[col_div].notna()
    df_valid = df_clasificado.loc[mask]
    if df_valid.empty:
        return

    nombres_kw = normalizar_serie_keywords(df_valid[col_nombre])
    divisiones_norm = df_valid[col_div].astype(str).apply(normalizar_texto)

    nuevos_n = 0
    for nombre, div in zip(nombres_kw, divisiones_norm):
        if nombre and nombre not in nombre_map:
            nombre_map[nombre] = div
            nuevos_n += 1

    logger.debug(f"  Nombre map enriquecido: +{nuevos_n} nombres (total: {len(nombre_map)} nombres)")


# ═══════════════════════════════════════════════════════════════════════════════
# GUARDAR RESULTADOS
# ═══════════════════════════════════════════════════════════════════════════════

def limpiar_columnas_salida(df):
    """Elimina columnas internas de metadatos antes de guardar."""
    cols_remover = [c for c in COLUMNAS_REMOVER if c in df.columns]
    cols_remover += [c for c in df.columns if c.startswith("_NORM_")]
    return df.drop(columns=cols_remover)


def _escritura_atomica(salida_path, escribir_fn, logger, verificar=None):
    """Escribe a un temporal y renombra atomicamente al finalizar.
    verificar(tmp_path), si se pasa, revisa el temporal antes de reemplazar la salida: si lanza una excepción, la salida
    anterior queda intacta."""
    base, ext = os.path.splitext(salida_path)
    tmp_path = base + ".tmp" + ext
    try:
        escribir_fn(tmp_path)
        if verificar is not None:
            verificar(tmp_path)
        os.replace(tmp_path, salida_path)
        return True
    except PermissionError:
        logger.error(f"No se pudo guardar '{salida_path}'. El archivo esta abierto en otra aplicacion.")
        return False
    except Exception as e:
        # Mensaje breve en consola; la traza completa va al archivo de log (y a consola con --verbose)
        logger.error(f"Error al guardar '{salida_path}': {type(e).__name__}: {e}")
        if os.path.exists(salida_path):
            logger.error("  No se reemplazo la salida: se conserva la version anterior.")
        logger.debug("Traza del error:", exc_info=True)
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _tabla_con_tipos_fuente(df, esquema_fuente):
    """Tabla Arrow del DataFrame con cada columna original en el tipo que tiene en la fuente: pandas convierte, p. ej.,
    los enteros con nulos en decimales (5 -> 5.0). La conversión es segura: falla si se perdiera información."""
    tabla = pa.Table.from_pandas(df, preserve_index=False)
    for campo in esquema_fuente:
        i = tabla.schema.get_field_index(campo.name)
        if i >= 0 and tabla.schema.field(i).type != campo.type:
            tabla = tabla.set_column(i, campo, tabla.column(i).cast(campo.type))
    return tabla


def guardar_parquet(df, salida_path, logger, esquema_fuente=None, verificar=None):
    """Guarda DataFrame como parquet, sin columnas internas de metadatos; con esquema_fuente (pyarrow.Schema del
    archivo de Fuentes), las columnas originales conservan su tipo de origen. verificar: ver _escritura_atomica."""
    df_limpio = limpiar_columnas_salida(df)

    def escribir(path):
        if esquema_fuente is None:
            df_limpio.to_parquet(path, index=False, engine="pyarrow")
        else:
            pq.write_table(_tabla_con_tipos_fuente(df_limpio, esquema_fuente), path)

    logger.info(f"  Guardando parquet ({len(df_limpio):,} filas)...")
    ok = _escritura_atomica(salida_path, escribir, logger, verificar)
    if ok:
        logger.info(f"Guardado: {salida_path}")
    return ok


def guardar_excel_con_formato(df, salida_path, logger, verificar=None):
    """Guarda DataFrame como Excel, preservando precisión completa de float64. verificar: ver _escritura_atomica."""
    df_limpio = limpiar_columnas_salida(df)

    def escribir(path):
        try:
            import openpyxl.compat.strings as _compat_strings
            import openpyxl.cell._writer as _cell_writer
            from math import isnan, isinf
            from datetime import datetime as _dt
            from openpyxl.compat.numbers import NUMERIC_TYPES as _NT
        except (ImportError, AttributeError):
            import openpyxl
            logger.warning(
                f"Parche de precision float64 no disponible (openpyxl {openpyxl.__version__}). "
                "Valores numericos pueden perder precision (~15 digitos en vez de 17)."
            )
            df_limpio.to_excel(path, index=False, engine="openpyxl")
            return

        _orig_compat = _compat_strings.safe_string
        _orig_writer = _cell_writer.safe_string

        def _safe_string_17g(value):
            if isinstance(value, _NT):
                if isnan(value) or isinf(value):
                    return ""
                return "%.17g" % value
            elif value is None:
                return "none"
            elif isinstance(value, _dt):
                return value.isoformat()
            elif not isinstance(value, str):
                return str(value)
            return value

        _compat_strings.safe_string = _safe_string_17g
        _cell_writer.safe_string = _safe_string_17g
        try:
            df_limpio.to_excel(path, index=False, engine="openpyxl")
        except AttributeError:
            import openpyxl
            logger.warning(
                f"Parche de precision float64 fallo durante escritura (openpyxl {openpyxl.__version__}). "
                "Reintentando sin parche — valores numericos pueden perder precision."
            )
            _compat_strings.safe_string = _orig_compat
            _cell_writer.safe_string = _orig_writer
            df_limpio.to_excel(path, index=False, engine="openpyxl")
        finally:
            _compat_strings.safe_string = _orig_compat
            _cell_writer.safe_string = _orig_writer

    logger.info(f"  Guardando Excel ({len(df_limpio):,} filas, puede tardar)...", extra=VISIBLE)
    ok = _escritura_atomica(salida_path, escribir, logger, verificar)
    if ok:
        logger.info(f"Guardado con formato: {salida_path}")
    return ok


_TRANSICIONES_EN_CONSOLA = 5


def _pares_snies_nombre(snies, nombres_kw, division):
    """Una fila por (SNIES, nombre normalizado) con su división más frecuente (d) y su número de registros (k)."""
    t = pd.DataFrame({"s": pd.to_numeric(snies, errors="coerce").to_numpy(), "n": nombres_kw.to_numpy(),
                      "d": division.to_numpy()})
    t = t[t.s.notna() & t.n.notna() & (t.n != "") & t.d.notna()]
    g = t.groupby(["s", "n", "d"]).size().reset_index(name="k")
    return g.sort_values(["s", "n", "k", "d"], ascending=[True, True, False, True]).drop_duplicates(["s", "n"])


def _comparar_por_pares(df, previa, col_map, logger):
    """Comparación con la salida anterior cuando la fuente cambió (otras filas): por (SNIES, nombre) en lugar de registro a
    registro. Informa los pares que cambian de división, los nuevos y los que ya no están.
    Retorna (registros actuales de los pares que cambian de división, None)."""
    col_snies, col_nombre = col_map["snies"], col_map["nombre"]
    unicos = pd.unique(previa[col_nombre].dropna())
    nombres_prev = previa[col_nombre].map(dict(zip(unicos, normalizar_serie_keywords(pd.Series(unicos)))))
    nombres_new = df["_NORM_NOMBRE_KW"] if "_NORM_NOMBRE_KW" in df.columns else normalizar_serie_keywords(df[col_nombre])
    ant = _pares_snies_nombre(previa[col_snies], nombres_prev, previa["DIVISION_UNINORTE"])
    nue = _pares_snies_nombre(df[col_snies], nombres_new, df["DIVISION_UNINORTE"])
    m = ant.merge(nue, on=["s", "n"], how="outer", suffixes=("_a", "_n"), indicator=True)
    comunes = int((m["_merge"] == "both").sum())
    nuevos, fuera = int((m["_merge"] == "right_only").sum()), int((m["_merge"] == "left_only").sum())
    cambian = m[(m["_merge"] == "both") & (m.d_a != m.d_n)]
    resto = f"{nuevos:,} pares nuevos, {fuera:,} que ya no estan"
    if cambian.empty:
        logger.info(f"  Sin cambios de division en los {comunes:,} pares (SNIES, nombre) comunes con la salida anterior; {resto}",
                    extra=VISIBLE)
        return 0, None
    n_reg = int(cambian.k_n.sum())
    logger.info(f"  Cambios respecto a la salida anterior: {len(cambian):,} de {comunes:,} pares (SNIES, nombre) comunes cambian de "
                f"division ({n_reg:,} registros, {cambian.s.nunique():,} SNIES); {resto}", extra=VISIBLE)
    transiciones = (cambian.groupby(["d_a", "d_n"]).agg(p=("s", "size"), r=("k_n", "sum")).sort_values("r", ascending=False))
    for i, ((antes, ahora), fila) in enumerate(transiciones.iterrows()):
        (logger.info if i < _TRANSICIONES_EN_CONSOLA else logger.debug)(
            f"    {antes} -> {ahora}: {int(fila.r):,} registros ({int(fila.p):,} pares)", extra=VISIBLE)
    return n_reg, None


def comparar_con_salida_anterior(df, col_map, salida_path, logger):
    """Compara la clasificación recién calculada con la salida vigente (la que se va a reemplazar). Con la misma fuente
    (mismas filas y SNIES en cada posición), registro a registro: cuántos registros cambian de DIVISION_UNINORTE, a
    cuántos SNIES afectan y las transiciones principales. Si la fuente cambió, por (SNIES, nombre)
    (_comparar_por_pares). La revisión no se compara: REQUIERE_REVISION no se exporta en las salidas (los programas por
    revisar están en REVISION_DIR). Solo informa: ningún fallo detiene el pipeline.
    Retorna (registros que cambian de división, None) o None si la salida anterior no es comparable."""
    if not os.path.exists(salida_path):
        logger.info("  Sin salida anterior para comparar (primera ejecucion de este archivo)", extra=VISIBLE)
        return None
    try:
        col_snies, col_nombre = col_map["snies"], col_map["nombre"]
        deseadas = {"DIVISION_UNINORTE", col_snies, col_nombre}
        if salida_path.lower().endswith(".parquet"):
            previa = pq.read_table(salida_path, columns=[c for c in pq.read_schema(salida_path).names if c in deseadas]).to_pandas()
        else:
            previa = pd.read_excel(salida_path, usecols=lambda c: c in deseadas, dtype=object, keep_default_na=False)
        if "DIVISION_UNINORTE" not in previa.columns:
            logger.info("  La salida anterior no es comparable: no tiene la columna DIVISION_UNINORTE", extra=VISIBLE)
            return None
        # Registro a registro solo si la fuente es la misma (mismas filas y mismo SNIES en cada posición); si cambió (p. ej.
        # un semestre nuevo), por (SNIES, nombre)
        alineada = len(previa) == len(df) and (
            col_snies not in previa.columns
            or np.array_equal(pd.to_numeric(previa[col_snies], errors="coerce").to_numpy(dtype=float),
                              pd.to_numeric(df[col_snies], errors="coerce").to_numpy(dtype=float), equal_nan=True))
        if not alineada:
            if col_snies not in previa.columns or col_nombre not in previa.columns:
                logger.info(f"  La salida anterior no es comparable ({len(previa):,} filas frente a {len(df):,} y sin columnas "
                            "de SNIES y nombre)", extra=VISIBLE)
                return None
            logger.info(f"  La fuente cambio ({len(previa):,} filas en la salida anterior, {len(df):,} ahora): "
                        "se compara por (SNIES, nombre)", extra=VISIBLE)
            return _comparar_por_pares(df, previa, col_map, logger)

        def _textos(serie):
            return serie.astype(object).where(serie.notna(), "").astype(str).to_numpy()

        nueva, anterior = _textos(df["DIVISION_UNINORTE"]), _textos(previa["DIVISION_UNINORTE"])
        cambia = nueva != anterior
        n_div = int(cambia.sum())

        if n_div == 0:
            logger.info(f"  Sin cambios de division respecto a la salida anterior ({len(df):,} registros)", extra=VISIBLE)
            return 0, None
        snies = pd.to_numeric(df[col_map["snies"]], errors="coerce").to_numpy()
        n_snies = int(pd.Series(snies[cambia]).nunique())
        logger.info(f"  Cambios respecto a la salida anterior: {n_div:,} registros cambian de division ({n_snies:,} SNIES)",
                    extra=VISIBLE)
        transiciones = (pd.DataFrame({"antes": anterior[cambia], "ahora": nueva[cambia], "s": snies[cambia]})
                        .groupby(["antes", "ahora"]).agg(k=("s", "size"), s=("s", "nunique"))
                        .sort_values("k", ascending=False))
        for i, ((antes, ahora), fila) in enumerate(transiciones.iterrows()):
            (logger.info if i < _TRANSICIONES_EN_CONSOLA else logger.debug)(
                f"    {antes or '(vacio)'} -> {ahora or '(vacio)'}: {int(fila.k):,} registros ({int(fila.s):,} SNIES)",
                extra=VISIBLE)
        return n_div, None
    except Exception as e:
        logger.warning(f"  No se pudo comparar con la salida anterior: {type(e).__name__}: {e}")
        logger.debug("Traza del error:", exc_info=True)
        return None


def ruta_revision(nombre_archivo, revision_dir):
    """Ruta de la lista de revisión de un archivo: revision_dir/"Revision <archivo>.xlsx"."""
    return os.path.join(revision_dir, f"Revision {os.path.splitext(nombre_archivo)[0]}.xlsx")


def _linea_override(nombre_normalizado, division):
    """Línea lista para pegar en NOMBRE_DIVISION_OVERRIDE: '"nombre normalizado": "DIVISION",' ("" si no hay nombre)."""
    if not nombre_normalizado:
        return ""
    clave = nombre_normalizado.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{clave}": "{division}",'


def tabla_revision(df, col_map):
    """Programas con REQUIERE_REVISION=VERDADERO, uno por fila, con sus votos por capa y dos columnas para resolverlos:
    NOMBRE_NORMALIZADO (la clave exacta que usa un override) y OVERRIDE_SUGERIDO (la línea completa para
    NOMBRE_DIVISION_OVERRIDE con la división propuesta; se modifica si la decisión es otra).
    Retorna (tabla, registros en revisión) o None si el DataFrame no tiene REQUIERE_REVISION."""
    if "REQUIERE_REVISION" not in df.columns:
        return None

    mask = df["REQUIERE_REVISION"] == "VERDADERO"
    n_registros = int(mask.sum())

    cols_origen = [col_map.get(k) for k in ("snies", "nombre", "nbc", "cine_detallado")]
    claves = [c for c in cols_origen if c and c in df.columns]
    claves += [c for c in ("DIVISION_UNINORTE", "CONFIANZA", *COLUMNAS_VOTO) if c in df.columns]
    columnas = claves + (["PUNTAJE_TOTAL"] if "PUNTAJE_TOTAL" in df.columns else []) + ["REGISTROS"]

    if n_registros:
        grupos = df.loc[mask].groupby(claves, dropna=False, sort=False)
        rev = grupos.size().rename("REGISTROS").to_frame()
        if "PUNTAJE_TOTAL" in df.columns:
            rev.insert(0, "PUNTAJE_TOTAL", grupos["PUNTAJE_TOTAL"].max())
        rev = rev.reset_index().sort_values("REGISTROS", ascending=False, kind="stable")[columnas]
    else:
        rev = pd.DataFrame(columns=columnas)

    col_nombre = col_map.get("nombre")
    if col_nombre in rev.columns and "DIVISION_UNINORTE" in rev.columns:
        rev["NOMBRE_NORMALIZADO"] = rev[col_nombre].map(normalizar_para_keywords)
        rev["OVERRIDE_SUGERIDO"] = [_linea_override(n, d) for n, d in zip(rev["NOMBRE_NORMALIZADO"], rev["DIVISION_UNINORTE"])]
    return rev, n_registros


def lineas_revision(rev, col_map, maximo=MAX_REVISION_CONSOLA):
    """Los primeros `maximo` programas de la tabla de revisión como líneas de texto (SNIES, nombre, división propuesta y
    confianza) más, si hay más, una línea con el resto. Lista vacía si no hay programas por revisar."""
    col_snies, col_nombre = col_map.get("snies"), col_map.get("nombre")
    lineas = []
    for _, fila in rev.head(maximo).iterrows():
        snies = pd.to_numeric(fila.get(col_snies), errors="coerce")
        snies = f"{int(snies)}" if pd.notna(snies) else str(fila.get(col_snies, "-"))
        lineas.append(f"    SNIES {snies}: {fila.get(col_nombre, '-')} -> {fila.get('DIVISION_UNINORTE', '-')} "
                      f"({fila.get('CONFIANZA', '-')})")
    if len(rev) > maximo:
        lineas.append(f"    ... y {len(rev) - maximo:,} programa(s) mas en la lista de revision")
    return lineas


def exportar_revision(df, col_map, nombre_archivo, revision_dir, logger, escribir=True):
    """Exporta a ruta_revision los programas con REQUIERE_REVISION=VERDADERO (tabla_revision). Se genera en cada
    ejecución, aunque no haya nada por revisar (solo encabezados), como constancia; reemplaza la lista anterior.
    Con escribir=False (simulación) solo calcula el resultado, sin tocar el disco.
    Retorna (programas, registros, líneas de consola con los programas por revisar) o None si falla la escritura."""
    resultado = tabla_revision(df, col_map)
    if resultado is None:
        return 0, 0, []
    rev, n_registros = resultado
    lineas = lineas_revision(rev, col_map)
    if not escribir:
        return len(rev), n_registros, lineas

    salida_path = ruta_revision(nombre_archivo, revision_dir)

    def escribir_excel(path):
        rev.to_excel(path, index=False, engine="openpyxl")

    try:
        os.makedirs(revision_dir, exist_ok=True)
    except OSError as e:
        logger.error(f"No se pudo crear la carpeta de revision '{revision_dir}': {e}")
        return None
    if not _escritura_atomica(salida_path, escribir_excel, logger):
        return None
    logger.info(f"  Revision: {len(rev):,} programas ({n_registros:,} registros) -> {salida_path}")
    return len(rev), n_registros, lineas


# ═══════════════════════════════════════════════════════════════════════════════
# RESUMEN DE ESTADÍSTICAS
# ═══════════════════════════════════════════════════════════════════════════════

def conteo_origenes(df):
    """Registros por origen de la decisión (claves de ORIGENES, en ese orden); vacío si no hay columna."""
    if "ORIGEN_DECISION" not in df.columns:
        return {}
    conteo = df["ORIGEN_DECISION"].value_counts()
    return {clave: int(conteo.get(clave, 0)) for clave in ORIGENES}


def imprimir_resumen(df, logger):
    """Imprime estadísticas de clasificación (usa metadatos internos antes de limpiarlos)."""
    total = len(df)
    if total == 0:
        return

    logger.info(f"  Resumen: {total:,} registros")

    # Ancho de la etiqueta más larga: las cifras quedan en una sola columna
    ancho = max(len(t) for t in ORIGENES.values())
    origenes = conteo_origenes(df)
    if origenes:
        logger.info("  Origen de la division:")
        for clave, n in origenes.items():
            detalle = ""
            if clave.startswith("VOTO") and n and "CONFIANZA" in df.columns:
                conf = df.loc[df["ORIGEN_DECISION"] == clave, "CONFIANZA"].value_counts()
                detalle = "  [" + ", ".join(f"{nivel} {int(conf.get(nivel, 0)):,}" for nivel in ("ALTA", "MEDIA", "BAJA")) + "]"
            logger.info(f"    {ORIGENES[clave]:<{ancho}} {n:>12,}  ({n / total * 100:5.1f}%){detalle}")

    if "REQUIERE_REVISION" in df.columns:
        n_rev = int((df["REQUIERE_REVISION"] == "VERDADERO").sum())
        logger.info(f"  {'Requieren revision':<{ancho + 2}} {n_rev:>12,}  ({n_rev / total * 100:5.1f}%)")

    if "ALERTA_INCONSISTENCIA" in df.columns:
        n_alertas = int((df["ALERTA_INCONSISTENCIA"] == True).sum())
        if n_alertas > 0:
            logger.debug(f"  Alertas inconsistencia: {n_alertas:,}")

    logger.info("Distribucion por division:")
    for div, count in df["DIVISION_UNINORTE"].value_counts().items():
        logger.info(f"  {div:<55} {count:>8,} ({count / total * 100:5.1f}%)")


# ═══════════════════════════════════════════════════════════════════════════════
# REFINAMIENTO (una pasada para registros de baja confianza)
# ═══════════════════════════════════════════════════════════════════════════════

def _aplicar_consenso(df, group_key, logger):
    """
    Fase 1: Consenso por vecindario NBC+CINE.
    Si >=90% de registros ALTA en un grupo (NBC|CINE) coinciden en división,
    esa división se aplica a registros BAJA/inconsistentes del mismo grupo
    (sin contradecir el SNIES conocido).
    """
    mask_alta = df["CONFIANZA"] == "ALTA"

    df_groups = pd.DataFrame({
        "gk": group_key[mask_alta],
        "div": df.loc[mask_alta, "DIVISION_UNINORTE"],
    })

    consensus = {}
    # group_key es NaN para registros sin vecindario (sin NBC o sin CINE): groupby los excluye
    for gk, grp in df_groups.groupby("gk"):
        n = len(grp)
        if n < CONSENSO_MIN_REGISTROS:
            continue
        counts = grp["div"].value_counts()
        dominancia = counts.iloc[0] / n
        if dominancia >= CONSENSO_UMBRAL_DOMINANCIA:
            consensus[gk] = (counts.index[0], round(dominancia, 4))

    if not consensus:
        logger.debug("    Consenso: sin grupos dominantes")
        return 0

    logger.debug(f"    Consenso: {len(consensus):,} grupos con dominancia >= {CONSENSO_UMBRAL_DOMINANCIA:.0%}")

    consenso_div_map = {gk: info[0] for gk, info in consensus.items()}
    consenso_dom_map = {gk: info[1] for gk, info in consensus.items()}

    consenso_div = group_key.map(consenso_div_map)
    consenso_dom = group_key.map(consenso_dom_map)

    mask_baja = df["CONFIANZA"] == "BAJA"
    alerta_series = df["ALERTA_INCONSISTENCIA"] if "ALERTA_INCONSISTENCIA" in df.columns else pd.Series(False, index=df.index)
    mask_media_inc = (df["CONFIANZA"] == "MEDIA") & (alerta_series == True)

    voto_c1 = df["VOTO_CAPA1"] if "VOTO_CAPA1" in df.columns else pd.Series("", index=df.index)
    consenso_no_contradice_snies = (voto_c1 == "") | (consenso_div == voto_c1)
    mask_mejorable = (mask_baja | mask_media_inc) & consenso_div.notna() & consenso_no_contradice_snies

    mejorados = int(mask_mejorable.sum())

    if mejorados > 0:
        df.loc[mask_mejorable, "DIVISION_UNINORTE"] = consenso_div.loc[mask_mejorable]

        puntaje_vals = consenso_dom.loc[mask_mejorable].astype(float) * 0.85
        df.loc[mask_mejorable, "PUNTAJE_TOTAL"] = puntaje_vals.round(4)

        alta_subset = puntaje_vals >= UMBRAL_ALTA
        idx_alta = puntaje_vals.index[alta_subset]
        idx_media = puntaje_vals.index[~alta_subset]

        df.loc[idx_alta, "CONFIANZA"] = "ALTA"
        df.loc[idx_media, "CONFIANZA"] = "MEDIA"
        df.loc[mask_mejorable, "REQUIERE_REVISION"] = "FALSO"

    return mejorados


def _keywords_profundo(df, nombre_kw, logger):
    """
    Fase 2: Análisis profundo de keywords con matching parcial y stemming simple.
    Substring vectorizado via pandas; prefix matching solo para registros sin match por substring,
    calculado una vez por nombre distinto (el puntaje depende solo del nombre).
    """
    mask_baja = df["CONFIANZA"] == "BAJA"
    indices_baja = df.index[mask_baja]

    if len(indices_baja) == 0:
        return 0

    tiene_voto_c1 = "VOTO_CAPA1" in df.columns

    nombres_baja = nombre_kw.loc[indices_baja]
    mask_valid = nombres_baja.str.len() >= 4
    indices_valid = indices_baja[mask_valid]
    nombres_valid = nombres_baja.loc[indices_valid]

    if len(indices_valid) == 0:
        return 0

    logger.debug(f"    Keywords profundo: {len(indices_valid):,} registros a evaluar")

    div_list = list(KEYWORDS.keys())
    n_divs = len(div_list)
    n_rows = len(indices_valid)
    scores = np.zeros((n_rows, n_divs), dtype=np.float64)

    total_kw = sum(len(kws) for kws in KEYWORDS.values())
    pares_kw = [(div_idx, kw) for div_idx, div_norm in enumerate(div_list) for kw in KEYWORDS[div_norm]]

    for div_idx, kw in barra(pares_kw, desc="  Refinamiento: keywords", unidad="kw"):
        hits = nombres_valid.str.contains(kw, regex=False).fillna(False).values
        scores[hits, div_idx] += 2.0

    logger.debug(f"    Keywords: substring completado ({total_kw} keywords)")

    mask_needs_prefix = scores.max(axis=1) < 2.0
    prefix_positions = np.where(mask_needs_prefix)[0]
    indices_prefix = indices_valid[mask_needs_prefix]

    if len(indices_prefix) > 0:
        nombres_prefix = nombres_valid.values[mask_needs_prefix]
        unicos = pd.unique(nombres_prefix)
        logger.debug(
            f"    Prefijos: {len(indices_prefix):,} registros sin match por substring ({len(unicos):,} nombres distintos)"
        )
        # Palabras de cada keyword con el largo mínimo de prefijo, por división (las demás nunca puntúan)
        kw_palabras = [
            [w for kw in KEYWORDS[div_norm] for w in kw.split() if len(w) >= _MIN_PREFIJO]
            for div_norm in div_list
        ]
        fila_por_nombre = {}
        for nombre in barra(unicos, desc="  Refinamiento: prefijos", unidad="nombre"):
            fila = np.zeros(n_divs, dtype=np.float64)
            palabras = [p for p in nombre.split() if len(p) >= _MIN_PREFIJO]
            for div_idx, palabras_kw in enumerate(kw_palabras):
                for pn in palabras:
                    for kw_w in palabras_kw:
                        prefix_len = 0
                        for a, b in zip(pn, kw_w):
                            if a == b:
                                prefix_len += 1
                            else:
                                break
                        if prefix_len >= _MIN_PREFIJO:
                            fila[div_idx] += 0.7
            fila_por_nombre[nombre] = fila
        # Las filas sin match por substring tienen puntaje 0: se asigna el de su nombre
        scores[prefix_positions] = np.array([fila_por_nombre[n] for n in nombres_prefix])

    best_col = np.argmax(scores, axis=1)
    mejor_score = scores[np.arange(n_rows), best_col]

    scores_for_second = scores.copy()
    scores_for_second[np.arange(n_rows), best_col] = -1.0
    segundo_score = np.maximum(scores_for_second.max(axis=1), 0.0)

    mask_ok = (mejor_score >= 2.0) & (
        (segundo_score == 0) | (mejor_score >= segundo_score * 1.8)
    )

    mejor_div = np.array(div_list)[best_col]

    if tiene_voto_c1:
        snies_vals = df.loc[indices_valid, "VOTO_CAPA1"].values
        snies_conflict = np.array([
            bool(s) and s != d for s, d in zip(snies_vals, mejor_div)
        ])
        mask_ok = mask_ok & ~snies_conflict

    idx_mejorar = indices_valid[mask_ok]
    mejorados = len(idx_mejorar)

    if mejorados > 0:
        df.loc[idx_mejorar, "DIVISION_UNINORTE"] = mejor_div[mask_ok]
        df.loc[idx_mejorar, "PUNTAJE_TOTAL"] = round(UMBRAL_MEDIA + 0.05, 4)
        df.loc[idx_mejorar, "CONFIANZA"] = "MEDIA"
        df.loc[idx_mejorar, "REQUIERE_REVISION"] = "FALSO"

    return mejorados


def refinamiento(df, col_map, logger):
    """
    Refinamiento de registros de baja confianza (una pasada).
    Fase 1: Consenso por vecindario (NBC + CINE detallado) — registros con la
            misma combinación NBC|CINE que otros clasificados con ALTA confianza
            heredan esa clasificación.
    Fase 2: Keywords profundo con matching parcial y stemming — para registros
            que permanecen BAJA, intenta resolver con coincidencias parciales.
    Repetir las fases no mejora nada más: el consenso solo asigna su división a grupos ya dominantes (no crea
    grupos nuevos) y las keywords dan el mismo resultado para los mismos registros BAJA.
    """
    if "CONFIANZA" not in df.columns:
        return df

    n_baja_inicial = int((df["CONFIANZA"] == "BAJA").sum())
    n_media_inicial = int((df["CONFIANZA"] == "MEDIA").sum())

    if n_baja_inicial == 0:
        logger.debug("  Refinamiento: sin registros BAJA, omitiendo")
        return df

    logger.debug(f"  Refinamiento: {n_baja_inicial:,} BAJA, {n_media_inicial:,} MEDIA")

    df = df.copy()

    has_cached = "_NORM_NBC" in df.columns

    if has_cached:
        nbc_norm = df["_NORM_NBC"]
        cine_norm = df["_NORM_CINE_DET"]
        nombre_kw = df["_NORM_NOMBRE_KW"] if "_NORM_NOMBRE_KW" in df.columns else None
    else:
        col_nbc = col_map.get("nbc")
        col_cine = col_map.get("cine_detallado")
        col_nombre = col_map.get("nombre")

        nbc_norm = normalizar_nbc(df[col_nbc]) if col_nbc and col_nbc in df.columns else pd.Series(None, index=df.index)
        cine_norm = normalizar_serie(df[col_cine]) if col_cine and col_cine in df.columns else pd.Series(None, index=df.index)
        nombre_kw = normalizar_serie_keywords(df[col_nombre]) if col_nombre and col_nombre in df.columns else None

    # Sin NBC o sin CINE detallado no hay vecindario: esos registros no participan del consenso.
    # Se marca con NaN y no con un centinela de texto, que pandas 3 puede perder al convertir a str
    # (p. ej. cuando falta la columna CINE y la serie de respaldo es float64).
    tiene_vecindario = nbc_norm.notna() & cine_norm.notna()
    group_key = (nbc_norm.astype(str) + "|" + cine_norm.astype(str)).where(tiene_vecindario)

    # Sin SNIES conocido y con confianza BAJA, la división la da la Capa 3 (NBC/CINE) si votó; el refinamiento solo decide
    # si salen de revisión. Medido con programas nuevos: en esos casos la Capa 3 acierta 91 %,
    # el nombre parecido 11-32 % y la fase de keywords 68 % (errores silenciosos 65 -> 30, misma revisión)
    voto_c3 = df["VOTO_CAPA3_NBC"].where(df["VOTO_CAPA3_NBC"] != "", df["VOTO_CAPA3_CINE"])
    mask_c3 = (df["CONFIANZA"] == "BAJA") & (df["VOTO_CAPA1"] == "") & voto_c3.fillna("").astype(bool)

    mejorados_c = _aplicar_consenso(df, group_key, logger)
    mejorados_k = _keywords_profundo(df, nombre_kw, logger) if nombre_kw is not None else 0
    mejorados_total = mejorados_c + mejorados_k
    if mask_c3.any():
        cambian_c3 = int((df.loc[mask_c3, "DIVISION_UNINORTE"] != voto_c3[mask_c3]).sum())
        df.loc[mask_c3, "DIVISION_UNINORTE"] = voto_c3[mask_c3]
        logger.debug(f"    Capa 3 sin SNIES conocido: {int(mask_c3.sum()):,} registros BAJA ({cambian_c3:,} cambian de division)")

    n_baja_final = int((df["CONFIANZA"] == "BAJA").sum())
    n_media_final = int((df["CONFIANZA"] == "MEDIA").sum())
    logger.info(
        f"  Refinamiento completado: {mejorados_total:,} mejorados ({mejorados_c:,} consenso, {mejorados_k:,} keywords; "
        f"BAJA: {n_baja_inicial:,} -> {n_baja_final:,}, "
        f"MEDIA: {n_media_inicial:,} -> {n_media_final:,})"
    )

    return df
