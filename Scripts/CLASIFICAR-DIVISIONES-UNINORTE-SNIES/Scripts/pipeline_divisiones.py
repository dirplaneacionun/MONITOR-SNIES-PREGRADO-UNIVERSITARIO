"""Ejecuta el pipeline de clasificación con selección interactiva de archivos."""

import argparse
import logging
import os
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clasificar_divisiones import (
    cargar_referencias, cargar_retroalimentacion, fusionar_retroalimentacion, guardar_retroalimentacion,
    clasificar_dataframe, preparar_ref_tokens, enriquecer_snies_map,
    enriquecer_nombre_map, aplicar_nombre_overrides, aplicar_division_por_snies,
    guardar_parquet, guardar_excel_con_formato, exportar_revision, ruta_revision, imprimir_resumen,
    marcar_origen_votacion, conteo_origenes, nombres_verificados, unificar_division_por_snies, division_parquets_por_override,
    revisar_referencias_dudosas, overrides_eliminados, inventario_nombres, resolver_nombres, aplicar_division_por_nombre,
    construir_registro,
    divisiones_por_nombre, verificar_division_por_snies,
    unificar_division_por_forma_canonica, verificar_division_por_forma_canonica, canonicas_de_archivo,
    refinamiento, huella_diccionarios, comparar_con_salida_anterior,
)
from config import (
    FUENTES_DIR, SALIDAS_DIR, LOGS_DIR, REVISION_DIR, RETROALIMENTACION_DIR, MAX_LOGS,
    ARCHIVOS_PROCESABLES, ARCHIVOS_PARQUET, PROGRAMAS_FILE, PROGRAMAS_UN_FILE, RETROALIMENTACION_FILE, BATCH_SIZE,
    COLUMNAS_PARQUET, COLUMNAS_EXCEL, COLUMNAS_VOTO, COLUMNAS_PERIODO_PARQUET,
    REFINAMIENTO_HABILITADO,
)
from diccionarios import DIVISIONES_VALIDAS
from validaciones import (
    validar_division_no_existe, validar_referencias,
    validar_columnas_clasificacion, resolver_archivos,
    validar_divisiones_diccionarios, validar_catalogo_divisiones, validar_pesos, verificar_integridad,
    archivos_bloqueados,
)
from utils import setup_logger, barra, find_file_insensitive, PLAIN, VISIBLE, EXITO, AVISO
from normalizar import normalizar_texto

import pandas as pd
import pyarrow.parquet as pq


def parse_args():
    parser = argparse.ArgumentParser(description="Clasificacion de programas academicos en divisiones UniNorte.")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Muestra en consola el detalle de depuracion (el archivo de log en Logs/ siempre lo incluye).",
    )
    parser.add_argument(
        "-a", "--archivos", metavar="N[,N...]",
        help="Numeros de los archivos a procesar (los del menu; 0 = todos). Omite el menu interactivo.",
    )
    parser.add_argument(
        "-s", "--simular", action="store_true",
        help="Simulacion: clasifica y compara con las salidas actuales sin escribir Salidas, Revision ni la retroalimentacion.",
    )
    return parser.parse_args()


def _parsear_seleccion(texto):
    """Convierte '1,3,5' (0 = todos) en índices de ARCHIVOS_PROCESABLES. Retorna (índices, error)."""
    try:
        nums = [int(x.strip()) for x in texto.split(",")]
    except ValueError:
        return None, "Entrada invalida. Use numeros separados por coma."

    if 0 in nums:
        return list(range(len(ARCHIVOS_PROCESABLES))), None

    fuera_de_rango = [n for n in nums if n < 1 or n > len(ARCHIVOS_PROCESABLES)]
    if fuera_de_rango:
        return None, f"Numero(s) fuera de rango: {fuera_de_rango}. Rango valido: 0-{len(ARCHIVOS_PROCESABLES)}."

    return list(dict.fromkeys(n - 1 for n in nums)), None


def menu_seleccion():
    """Muestra menú interactivo y retorna los índices seleccionados."""
    MAX_INTENTOS = 3

    print("\n" + "=" * 60)
    print("  PIPELINE DE CLASIFICACION - UNINORTE")
    print("=" * 60)
    print("\nArchivos disponibles:\n")
    print("  [0] TODOS")
    for i, nombre in enumerate(ARCHIVOS_PROCESABLES, 1):
        print(f"  [{i}] {nombre}")

    print("\nIngrese los numeros separados por coma (ej: 1,3,5) o 0 para procesar todos.")

    for intento in range(1, MAX_INTENTOS + 1):
        try:
            respuesta = input("\nSeleccion: ").strip()
        except EOFError:
            # Sin entrada estándar (ejecución no interactiva o stdin cerrado)
            print("\nNo hay entrada disponible. Abortando.")
            return None

        if not respuesta:
            print("No se selecciono nada. Abortando.")
            return None

        indices, error = _parsear_seleccion(respuesta)
        if error is None:
            return indices
        restantes = MAX_INTENTOS - intento
        if restantes > 0:
            print(f"{error} ({restantes} intento(s) restante(s))")
            continue
        print(f"{error} Abortando.")
        return None

    return None


def _es_parquet(nombre):
    return nombre.lower().endswith(".parquet")


def _col_map(nombre):
    return COLUMNAS_PARQUET if _es_parquet(nombre) else COLUMNAS_EXCEL


AVISO_SIN_ENRIQUECIMIENTO = (
    f"  Los parquets no se procesaran (se conservan sus salidas anteriores): sin las referencias de {PROGRAMAS_FILE} "
    "su clasificacion diferiria de una ejecucion completa."
)


def _clasificar_archivo(nombre, path, col_map, disponibilidad, snies_map, nombre_map, logger, cache):
    """Lee y clasifica un archivo completo (por lotes si el parquet supera BATCH_SIZE filas).
    cache se comparte entre lotes y entre archivos con las mismas referencias y columnas disponibles;
    ref_tokens (con el memo de la Capa 2) se crea aquí, así que nunca sobrevive a un cambio de referencias."""
    kwargs = dict(
        nombre_map=nombre_map,
        ref_tokens=preparar_ref_tokens(nombre_map),
        cache=cache,
    )
    n_cache_inicial = len(cache)

    if not _es_parquet(nombre):
        logger.info("  Cargando Excel...")
        # Cada celda tal como viene (texto sigue siendo texto: sin inferir números ni convertir "NA" en vacío);
        # si no, p. ej. un código guardado como texto '110146580001100111100' saldría como 1.1014658000110012e+20
        df = pd.read_excel(path, dtype=object, keep_default_na=False)
        logger.info(f"  Cargado: {len(df):,} filas")
        # Programas sin SNIES conocido: también votan por nombre los programas ya verificados del mismo archivo
        refs_sin_snies = nombres_verificados(df, col_map, snies_map, nombre_map)
        if refs_sin_snies is not None:
            logger.debug(f"  Capa 2 para programas sin SNIES conocido: +{len(refs_sin_snies[0]) - len(nombre_map):,} nombres verificados")
        return clasificar_dataframe(df, col_map, snies_map, disponibilidad, logger, nombre, refs_sin_snies=refs_sin_snies, **kwargs)

    pf = pq.ParquetFile(path)
    total_filas = pf.metadata.num_rows

    if total_filas <= BATCH_SIZE:
        df = pf.read().to_pandas()
        logger.info(f"  Cargado: {len(df):,} filas")
        return clasificar_dataframe(df, col_map, snies_map, disponibilidad, logger, nombre, **kwargs)

    total_batches = (total_filas + BATCH_SIZE - 1) // BATCH_SIZE
    logger.info(f"  {total_filas:,} filas: procesando en {total_batches} lotes de {BATCH_SIZE:,}")
    resultados = []
    for batch in barra(pf.iter_batches(batch_size=BATCH_SIZE), total=total_batches, desc="  Lotes", unidad="lote"):
        resultados.append(clasificar_dataframe(
            batch.to_pandas(), col_map, snies_map, disponibilidad, logger, nombre, **kwargs,
        ))
    logger.debug(f"  Cache de clasificacion: {len(cache):,} combinaciones (+{len(cache) - n_cache_inicial:,} de este archivo)")
    return pd.concat(resultados, ignore_index=True)


def _mapear_divisiones_display(df_final, divisiones_validas_norm):
    """Convierte divisiones normalizadas a su forma oficial (con comas y tildes)."""
    div_mapped = df_final["DIVISION_UNINORTE"].map(divisiones_validas_norm)
    df_final["DIVISION_UNINORTE"] = div_mapped.fillna(df_final["DIVISION_UNINORTE"])
    for col_voto in COLUMNAS_VOTO:
        if col_voto in df_final.columns:
            mapped = df_final[col_voto].map(divisiones_validas_norm)
            df_final[col_voto] = mapped.fillna(df_final[col_voto]).where(
                df_final[col_voto].astype(bool), ""
            )
    return df_final


def clasificar_archivo_completo(nombre, path, refs, divisiones_validas_norm, logger, caches):
    """Clasificación completa en memoria: votación por capas, refinamiento, overrides por nombre, división del SNIES
    (Programas.xlsx o, en los parquets, la de sus overrides), división de ProgramasUN, en los parquets la de los nombres
    registrados o nuevos, unificación de las dudas de la votación, renombres en Programas.xlsx y nombres oficiales. refs: Referencias. caches: {columnas
    disponibles: caché de combinaciones}."""
    col_map = _col_map(nombre)
    disponibilidad = validar_columnas_clasificacion(path, _es_parquet(nombre), logger)
    # La clave de caché no incluye qué columnas existen: solo se comparte entre archivos con la misma disponibilidad
    cache = caches.setdefault(tuple(sorted(disponibilidad.items())), {})

    df_final = _clasificar_archivo(nombre, path, col_map, disponibilidad, refs.snies, refs.nombres, logger, cache)

    if REFINAMIENTO_HABILITADO:
        df_final = refinamiento(df_final, col_map, logger)

    df_final = marcar_origen_votacion(df_final)
    # Orden: override por nombre (exacto, comodín o truncado) → división del SNIES (Programas.xlsx; en los SNIES que solo
    # están en los parquets, la de sus overrides) → ProgramasUN. En los parquets, un registro con otro nombre que el del
    # programa en ProgramasUN/Programas.xlsx (renombrado) y con override propio conserva la división de ese override,
    # también dentro del mismo archivo (abarca varios años); uno sin override toma su división registrada o, si es nuevo,
    # la de su clasificación (aplicar_division_por_nombre). Solo las dudas de la votación se unifican y van a revisión
    df_final = aplicar_nombre_overrides(df_final, col_map, logger)
    es_parquet = _es_parquet(nombre)
    nombres_ref = (refs.nombres_ref or {}) if es_parquet else None
    fijos_programas = {s: d for s, d in refs.snies_fijos.items() if s not in refs.snies_un}
    df_final = aplicar_division_por_snies(df_final, col_map, fijos_programas, refs.snies_un, logger,
                                          refs.snies_revision, etiqueta=PROGRAMAS_FILE, nombres_ref=nombres_ref)
    if refs.fijos_parquets:
        df_final = aplicar_division_por_snies(df_final, col_map, refs.fijos_parquets, refs.snies_un, logger,
                                              etiqueta="SNIES solo en parquets, por sus overrides", origen_fijo="OVERRIDE",
                                              nombres_ref={} if es_parquet else None)
    df_final = aplicar_division_por_snies(df_final, col_map, refs.snies_un, refs.snies_un, logger, etiqueta=PROGRAMAS_UN_FILE,
                                          nombres_ref=nombres_ref)
    if es_parquet:
        # Nombres distintos al vigente y sin override: los registrados conservan su división; los nuevos se clasifican
        df_final = aplicar_division_por_nombre(df_final, col_map, refs.division_nombres, logger)
    df_final = unificar_division_por_snies(df_final, col_map, logger, etiqueta=nombre,
                                           columnas_periodo=COLUMNAS_PERIODO_PARQUET if es_parquet else (),
                                           nombres_ref=nombres_ref)
    if _es_programas(nombre):
        df_final = revisar_referencias_dudosas(df_final, col_map, refs, disponibilidad, logger)
    # El mismo nombre con otro orden de palabras o conectores: una sola división (solo cambia lo decidido por votación)
    df_final = unificar_division_por_forma_canonica(df_final, refs, logger, etiqueta=nombre)
    return _mapear_divisiones_display(df_final, divisiones_validas_norm)


# Pasada de reclasificación de Programas.xlsx: solo avisos y errores (el detalle ya se informó en la primera pasada)
_LOGGER_RECLASIFICACION = logging.getLogger("clasificacion.reclasificacion")
_LOGGER_RECLASIFICACION.setLevel(logging.WARNING)


def _capa1_siguiente(df_programas, refs):
    """Capa 1 con la que empezaría la próxima ejecución: la actual más lo que la retroalimentación guardará de la
    clasificación final de Programas.xlsx (ALTA/MEDIA; los decididos solo por votación no se guardan).
    Retorna (mapa, SNIES del archivo cuya división cambia) o (None, 0) si no cambia ninguno."""
    col_snies = COLUMNAS_EXCEL["snies"]
    cols = [c for c in (col_snies, "DIVISION_UNINORTE", "CONFIANZA", "REQUIERE_REVISION", "ORIGEN_DECISION", "_NORM_NOMBRE_KW")
            if c in df_programas.columns]
    siguiente = dict(refs.snies)
    _, snies_auto, _, _ = enriquecer_snies_map(siguiente, df_programas[cols].copy(), col_snies, refs.snies_un,
                                               _LOGGER_RECLASIFICACION)
    for s in snies_auto:
        if s in refs.snies:
            siguiente[s] = refs.snies[s]
        else:
            siguiente.pop(s, None)
    snies_archivo = set(pd.to_numeric(df_programas[col_snies], errors="coerce").dropna().astype(int))
    cambian = sum(1 for s in snies_archivo if siguiente.get(s) != refs.snies.get(s))
    return (siguiente, cambian) if cambian else (None, 0)


def _clasificar_programas(path, refs, divisiones_validas_norm, logger, caches):
    """Clasifica Programas.xlsx. Si su clasificación final cambia la división guardada (Capa 1) de algún SNIES del archivo,
    lo vuelve a clasificar con la Capa 1 que tendrá la próxima ejecución: así PUNTAJE_TOTAL y la confianza quedan
    definitivos en una sola ejecución."""
    df = clasificar_archivo_completo(PROGRAMAS_FILE, path, refs, divisiones_validas_norm, logger, caches)
    siguiente, cambian = _capa1_siguiente(df, refs)
    if siguiente is None:
        return df
    logger.info(f"  La clasificacion final cambia la division guardada de {cambian:,} SNIES: se reclasifica con ella para que "
                "el resultado quede definitivo")
    t0 = time.time()
    # La caché guarda resultados con la Capa 1 anterior
    caches.clear()
    df = clasificar_archivo_completo(PROGRAMAS_FILE, path, refs._replace(snies=siguiente), divisiones_validas_norm,
                                     _LOGGER_RECLASIFICACION, caches)
    logger.info(f"  Reclasificacion completada en {time.time() - t0:.1f}s")
    return df


def procesar_archivo(nombre, path, refs, divisiones_validas_norm, logger, caches, simular=False):
    """Clasifica, resume, guarda y exporta la lista de revisión de un archivo. Con simular=True solo clasifica y compara
    con la salida vigente, sin escribir ningún archivo. Retorna (df_final, stats)."""
    t_archivo = time.time()
    if _es_programas(nombre):
        df_final = _clasificar_programas(path, refs, divisiones_validas_norm, logger, caches)
    else:
        df_final = clasificar_archivo_completo(nombre, path, refs, divisiones_validas_norm, logger, caches)

    imprimir_resumen(df_final, logger)

    salida_path = os.path.join(SALIDAS_DIR, nombre)
    # Antes de que se reemplace la salida vigente: qué cambia respecto a lo ya entregado
    cambios = comparar_con_salida_anterior(df_final, _col_map(nombre), salida_path, logger)

    integridad = []

    def _verificar(tmp_path):
        # Los datos de Fuentes deben llegar intactos (valor y tipo) a la salida; si no, no se publica
        t0 = time.time()
        verificar_integridad(path, tmp_path)
        integridad.append(True)
        logger.info(f"  Integridad verificada: valores y tipos originales intactos ({time.time() - t0:.1f}s)")

    if simular:
        # Sin escritura: la salida, la lista de revisión y la retroalimentación vigentes no se tocan
        guardado_ok = True
        revision = exportar_revision(df_final, _col_map(nombre), nombre, REVISION_DIR, logger, escribir=False)
    else:
        if _es_parquet(nombre):
            guardado_ok = guardar_parquet(df_final, salida_path, logger, esquema_fuente=pq.read_schema(path), verificar=_verificar)
        else:
            guardado_ok = guardar_excel_con_formato(df_final, salida_path, logger, verificar=_verificar)

        if guardado_ok:
            revision = exportar_revision(df_final, _col_map(nombre), nombre, REVISION_DIR, logger)
        else:
            # Se conservó la salida anterior: su lista de revisión también, para que sigan correspondiéndose
            revision = None
            logger.warning(f"  Se conserva la lista de revision anterior (la de la salida anterior): "
                           f"{ruta_revision(nombre, REVISION_DIR)}")

    elapsed = time.time() - t_archivo
    logger.debug(f"Procesado en {elapsed:.1f}s")

    stats = {"nombre": nombre, "registros": len(df_final), "tiempo": elapsed, "guardado": guardado_ok,
             "error": False, "revision": revision, "cambios": cambios, "integridad": bool(integridad)}
    n_total = len(df_final)
    origenes = conteo_origenes(df_final)
    if origenes and n_total > 0:
        stats["origen"] = {clave: n / n_total * 100 for clave, n in origenes.items()}
    return df_final, stats


def _linea_resultado(s):
    """Línea compacta de un archivo procesado: estado, registros, programas por revisar, cambios frente a la salida anterior,
    integridad y tiempo."""
    revision, cambios = s.get("revision"), s.get("cambios")
    ok = s["guardado"] and revision is not None
    por_revisar = f"{revision[0]:,}" if revision else "?"
    if cambios is None:
        diferencias = "sin salida anterior comparable"
    else:
        diferencias = f"{cambios[0]:,} registros cambian de division" if cambios[0] else "sin cambios"
    integridad = " · integridad verificada" if s.get("integridad") else ""
    return (f"{'✔' if ok else '✖'} {s['nombre']}: {s['registros']:,} registros · {por_revisar} programas por revisar · "
            f"{diferencias}{integridad} · {_formato_duracion(s['tiempo'])}")


def _stats_error(nombre, tiempo=0.0):
    """Entrada del resumen final para un archivo que no se pudo procesar."""
    return {"nombre": nombre, "registros": 0, "tiempo": tiempo, "guardado": False, "error": True}


def _registrar_error(logger, contexto, exc):
    """Mensaje breve en consola; la traza completa va al archivo de log (y a consola con --verbose)."""
    logger.error(f"{contexto}: {type(exc).__name__}: {exc}")
    logger.debug("Traza del error:", exc_info=True)


def _es_programas(nombre):
    return nombre.lower() == PROGRAMAS_FILE.lower()


def _enriquecer_referencias(df_programas, refs, logger):
    """Agrega a las referencias los programas de Programas.xlsx clasificados ALTA/MEDIA: Capa 1 (y
    retroalimentación), Capa 2 y división fija por SNIES de los parquets (ProgramasUN sigue prevaleciendo).
    Retorna las referencias nuevas; si algo falla, se conservan las anteriores completas."""
    try:
        col_snies = COLUMNAS_EXCEL["snies"]
        cols_enriq = [c for c in (col_snies, "DIVISION_UNINORTE", "CONFIANZA", "REQUIERE_REVISION", "ORIGEN_DECISION", "_NORM_NOMBRE_KW")
                      if c in df_programas.columns]
        snies_nuevo = dict(refs.snies)
        programas_map, snies_auto, snies_revision, overrides = enriquecer_snies_map(snies_nuevo, df_programas[cols_enriq].copy(), col_snies, refs.snies_un, logger)
        # Nombres de referencia del programa: los de ProgramasUN más el de Programas.xlsx
        nombres_ref = {s: set(n) for s, n in (refs.nombres_ref or {}).items()}
        s_num = pd.to_numeric(df_programas[col_snies], errors="coerce")
        if "_NORM_NOMBRE_KW" in df_programas.columns:
            ok = s_num.notna() & (df_programas["_NORM_NOMBRE_KW"] != "")
            for s_, n_ in zip(s_num[ok].astype(int), df_programas.loc[ok, "_NORM_NOMBRE_KW"]):
                nombres_ref.setdefault(s_, set()).add(n_)
        # Nombre con el que se verificó la división (no BAJA): la retroalimentación lo guarda para detectar renombres
        nombre_vigente = dict(refs.nombre_vigente or {})
        if "_NORM_NOMBRE_KW" in df_programas.columns and "CONFIANZA" in df_programas.columns:
            ok = s_num.notna() & (df_programas["_NORM_NOMBRE_KW"] != "") & (df_programas["CONFIANZA"] != "BAJA")
            for s_, n_ in zip(s_num[ok].astype(int), df_programas.loc[ok, "_NORM_NOMBRE_KW"]):
                if s_ not in refs.snies_un:
                    nombre_vigente[s_] = n_
        nombre_nuevo = dict(refs.nombres)
        enriquecer_nombre_map(nombre_nuevo, df_programas, COLUMNAS_EXCEL, logger)
    except Exception as e:
        _registrar_error(logger, f"  No se pudieron enriquecer las referencias con {PROGRAMAS_FILE}", e)
        logger.warning(AVISO_SIN_ENRIQUECIMIENTO)
        return refs
    return refs._replace(snies=snies_nuevo, nombres=nombre_nuevo, snies_fijos={**programas_map, **refs.snies_un},
                         snies_auto=snies_auto, snies_revision=snies_revision - set(refs.snies_un),
                         overrides_programas=tuple(overrides.items()),
                         nombres_ref={s: frozenset(n) for s, n in nombres_ref.items()}, nombre_vigente=nombre_vigente)


def _referencias_desde_programas(refs, divisiones_validas_norm, logger, caches):
    """Programas.xlsx no fue seleccionado: se clasifica en memoria (sin guardar) solo para enriquecer
    las referencias, de modo que los parquets se clasifiquen igual que en una ejecución completa."""
    path = find_file_insensitive(FUENTES_DIR, PROGRAMAS_FILE)
    if not path:
        logger.warning(f"No se encontro {PROGRAMAS_FILE} en Fuentes para completar las referencias.")
        logger.warning(AVISO_SIN_ENRIQUECIMIENTO)
        return refs

    logger.info("", extra=PLAIN)
    logger.info("─" * 60, extra=PLAIN)
    logger.info(f"Preparando referencias: {PROGRAMAS_FILE} no esta en la seleccion; se clasifica en memoria (sin guardar)",
                extra=VISIBLE)
    t_inicio = time.time()
    try:
        if not validar_division_no_existe(path, logger):
            logger.warning(AVISO_SIN_ENRIQUECIMIENTO)
            return refs
        df_programas = _clasificar_programas(path, refs, divisiones_validas_norm, logger, caches)
    except Exception as e:
        _registrar_error(logger, f"  Error al clasificar {PROGRAMAS_FILE} para las referencias", e)
        logger.warning(AVISO_SIN_ENRIQUECIMIENTO)
        return refs

    refs = _enriquecer_referencias(df_programas, refs, logger)
    logger.info(f"  Referencias listas en {time.time() - t_inicio:.1f}s ({len(refs.nombres):,} nombres para la Capa 2)",
                extra=VISIBLE)
    return refs


# Columnas de origen de la tabla final (claves de clasificar_divisiones.ORIGENES)
COLUMNAS_TABLA = [
    ("FIJO_UN", "UN"),
    ("FIJO_PROGRAMAS", "PROGR."),
    ("OVERRIDE", "OVERR."),
    ("VOTO_SNIES", "SNIES"),
    ("VOTO_SIN_SNIES", "NUEVO"),
    ("REGISTRO", "REGIS."),
    ("NOMBRE_NUEVO", "N.NOMB"),
]


def _preparar_parquets(refs, logger):
    """Referencias de los parquets a partir de los nombres de TODOS los parquets de Fuentes (no solo los seleccionados),
    iguales en cualquier selección (inventario_nombres):
    - División de los SNIES que solo están en los parquets: la del override de su nombre más reciente. La toman sus
      registros sin override propio y se guarda en la retroalimentación.
    - División de los nombres distintos al vigente y sin override (resolver_nombres): la registrada en la
      retroalimentación o, si el nombre es nuevo, la de su clasificación.
    Solo con Programas.xlsx clasificado (si no, no se sabe qué SNIES están en él).
    Retorna (referencias, SNIES -> nombre del override para la procedencia, inventario de nombres o None)."""
    if not (refs.snies_fijos.keys() - refs.snies_un.keys()):
        # Ya avisado (AVISO_SIN_ENRIQUECIMIENTO): sin inventario los parquets no se procesan
        logger.debug(f"  Sin la clasificacion de {PROGRAMAS_FILE} no se preparan las referencias de los parquets.")
        return refs, {}, None
    rutas = [p for p in (find_file_insensitive(FUENTES_DIR, f) for f in ARCHIVOS_PARQUET) if p]
    try:
        inventario = inventario_nombres(rutas, COLUMNAS_PARQUET, COLUMNAS_PERIODO_PARQUET)
        division, nombres = division_parquets_por_override(inventario, refs.snies_fijos, logger)
    except Exception as e:
        _registrar_error(logger, "  No se pudieron leer los nombres de los parquets de Fuentes (se necesitan los de todos)", e)
        return refs, {}, None
    cambian = sum(1 for s, d in division.items() if refs.snies.get(s) != d)
    refs.snies.update(division)
    if cambian:
        logger.info(f"  SNIES solo en parquets: {cambian:,} nuevos o actualizados para la retroalimentacion")
    refs = refs._replace(fijos_parquets=division)
    return refs._replace(division_nombres=resolver_nombres(inventario, refs, logger)), nombres, inventario


def imprimir_tabla_final(resumen_archivos, logger, log_path, simular=False):
    """Tabla resumen de la ejecución (consola + log): de dónde sale la división de los registros de cada archivo, y los
    programas por revisar con la indicación para resolverlos."""
    if not resumen_archivos:
        return
    v = "─"
    vacios = "".join(f"  {v:>6}" for _ in COLUMNAS_TABLA)
    cabecera = (f"  {'Archivo':<48} {'Registros':>10}" + "".join(f"  {t:>6}" for _, t in COLUMNAS_TABLA)
                + f"  {'REVISAR':>7}  {'Tiempo':>6}")
    sep = "─" * len(cabecera)
    logger.info("", extra=PLAIN)
    logger.info(sep, extra=PLAIN)
    logger.info(cabecera, extra=PLAIN)
    logger.info(sep, extra=PLAIN)
    for s in resumen_archivos:
        t_s = f"{s['tiempo']:.1f}s"
        if s["error"]:
            logger.info(f"  {s['nombre']:<48} {'ERROR':>10}{vacios}  {v:>7}  {t_s:>6}", extra=PLAIN)
            continue
        revision = s.get("revision")
        revisar = v if not s["guardado"] else "ERROR" if revision is None else f"{revision[1]:,}"
        origen = "".join(f"  {s['origen'][c]:>5.1f}%" for c, _ in COLUMNAS_TABLA) if "origen" in s else vacios
        logger.info(f"  {s['nombre']:<48} {s['registros']:>10,}{origen}  {revisar:>7}  {t_s:>6}", extra=PLAIN)
    logger.info(sep, extra=PLAIN)
    logger.info("  Origen de la division (% de registros; prioridad: UN > PROGR. > OVERR. > votacion, salvo en los parquets los nombres"
                " antiguos del programa (con override propio, REGIS. o N.NOMB), que conservan su division):", extra=PLAIN)
    logger.info("    UN      fija por ProgramasUN", extra=PLAIN)
    logger.info(f"    PROGR.  fija por la clasificacion final de {PROGRAMAS_FILE} para el mismo SNIES (solo parquets)", extra=PLAIN)
    logger.info("    OVERR.  override por nombre (NOMBRE_DIVISION_OVERRIDE; exacto, con caracteres danados o truncado equivalente)", extra=PLAIN)
    logger.info("    SNIES   votacion con SNIES conocido (retroalimentacion.json)", extra=PLAIN)
    logger.info("    NUEVO   votacion sin SNIES conocido: nombre parecido + NBC/CINE (programas nuevos)", extra=PLAIN)
    logger.info("    REGIS.  nombre ya registrado en la retroalimentacion: conserva su division (solo parquets)", extra=PLAIN)
    logger.info("    N.NOMB  nombre nuevo de un SNIES registrado (programa renombrado): votacion sin la Capa 1", extra=PLAIN)
    logger.info("  REVISAR = registros con REQUIERE_REVISION=VERDADERO", extra=PLAIN)

    con_lista = [s for s in resumen_archivos if not s["error"] and s.get("revision")]
    if con_lista:
        n_prog = sum(s["revision"][0] for s in con_lista)
        if simular:
            texto = f"Programas por revisar: {n_prog:,} en {len(con_lista)} archivo(s) (simulacion: no se escribio ninguna lista)"
        else:
            texto = f"Listas de revision ({len(con_lista)} archivo(s), {n_prog:,} programas por revisar) en: {REVISION_DIR}"
        logger.info(texto, extra=AVISO if n_prog else VISIBLE)
        for s in con_lista:
            if s["revision"][2]:
                logger.info(f"  {s['nombre']}:", extra=AVISO)
                for linea in s["revision"][2]:
                    logger.info(linea, extra=AVISO)
        if n_prog:
            logger.info("  Para resolverlos: copie la columna OVERRIDE_SUGERIDO de la lista de revision en "
                        "NOMBRE_DIVISION_OVERRIDE (diccionarios.py), ajuste la division si corresponde y vuelva a ejecutar.",
                        extra=AVISO)

    con_error = [s["nombre"] for s in resumen_archivos if s["error"]]
    no_guardados = [s["nombre"] for s in resumen_archivos if not s["error"] and not s["guardado"]]
    sin_revision = [s["nombre"] for s in resumen_archivos
                    if not s["error"] and s["guardado"] and s.get("revision", ()) is None]
    if con_error:
        logger.warning(f"{len(con_error)} archivo(s) omitidos por error: {', '.join(con_error)}")
    if no_guardados:
        logger.warning(f"{len(no_guardados)} archivo(s) procesados pero no guardados (se conservan su salida y su lista de "
                       f"revision anteriores): {', '.join(no_guardados)}")
    if sin_revision:
        logger.warning(f"{len(sin_revision)} lista(s) de revision no se pudieron guardar: {', '.join(sin_revision)}")
    if (con_error or no_guardados or sin_revision) and log_path:
        logger.warning(f"Detalle de los errores en: {log_path}")


def ejecutar(seleccion, logger, log_path, simular=False):
    """Flujo completo: validaciones, referencias, procesamiento por archivo y retroalimentación. Con simular=True clasifica
    y compara con las salidas actuales sin escribir Salidas, Revision ni la retroalimentación.
    Retorna el código de salida (0 = todo OK, 1 = hubo errores)."""
    t_ejecucion = time.time()
    if not validar_pesos(logger):
        logger.error("Pesos de votacion invalidos (config.py). Abortando.")
        return 1

    if not validar_catalogo_divisiones(logger):
        logger.error("Catalogo de divisiones invalido (diccionarios.DIVISIONES). Abortando.")
        return 1

    if not validar_referencias(FUENTES_DIR, logger):
        logger.error("Validacion de referencias fallida. Abortando.")
        return 1

    if not validar_divisiones_diccionarios(logger):
        logger.error("Validacion de diccionarios fallida. Abortando.")
        return 1
    sha, conteos = huella_diccionarios()
    logger.info(f"Diccionario: huella {sha} ({conteos['overrides']:,} overrides, {conteos['reglas_nbc']:,} reglas NBC, "
                f"{conteos['nbc']} NBC, {conteos['cine']} CINE, {conteos['keywords']} keywords)", extra=VISIBLE)

    divisiones_validas_norm = {normalizar_texto(d): d for d in DIVISIONES_VALIDAS}

    archivos = resolver_archivos(seleccion, ARCHIVOS_PROCESABLES, FUENTES_DIR, logger)
    if not archivos:
        logger.error("No se encontraron archivos para procesar.")
        return 1

    # Los seleccionados que no están en Fuentes figuran como ERROR en el resumen y en el código de salida
    encontrados = {nombre for nombre, _ in archivos}
    resumen_archivos = [_stats_error(ARCHIVOS_PROCESABLES[i]) for i in seleccion
                        if ARCHIVOS_PROCESABLES[i] not in encontrados]
    archivos_legibles = []
    for nombre, path in archivos:
        try:
            sin_division = validar_division_no_existe(path, logger)
        except Exception as e:
            # Archivo ilegible/corrupto: se excluye sin detener el resto
            _registrar_error(logger, f"No se pudo leer '{nombre}'", e)
            logger.error("  Archivo omitido; se continua con el resto.")
            resumen_archivos.append(_stats_error(nombre))
            continue
        if not sin_division:
            logger.error("Abortando por columna DIVISION_UNINORTE existente.")
            return 1
        archivos_legibles.append((nombre, path))

    # Una salida abierta en Excel no se puede reemplazar: se detecta antes de clasificar, no al guardar minutos después.
    # En simulación no se escribe nada, así que no hay nada que bloquear
    bloqueados = [] if simular else archivos_bloqueados(
        [os.path.join(SALIDAS_DIR, n) for n, _ in archivos_legibles]
        + [ruta_revision(n, REVISION_DIR) for n, _ in archivos_legibles]
        + [os.path.join(RETROALIMENTACION_DIR, RETROALIMENTACION_FILE)]
    )
    if bloqueados:
        logger.error(f"{len(bloqueados)} archivo(s) abiertos en otra aplicacion (p. ej. Excel) o de solo lectura; no se "
                     "podrian reemplazar al guardar:")
        for ruta in bloqueados:
            logger.error(f"  {ruta}")
        logger.error("Cierrelos y vuelva a ejecutar. Abortando.")
        return 1

    refs = cargar_referencias(FUENTES_DIR, logger)
    retro_info, retro_meta = {}, {}
    retro_map = cargar_retroalimentacion(RETROALIMENTACION_DIR, logger, divisiones_validas_norm, info=retro_info,
                                         meta_out=retro_meta)
    if retro_map is None:
        logger.error(
            "Abortando para no alterar la clasificacion ni sobrescribir el historial. "
            f"Corrija {RETROALIMENTACION_FILE} o restaure una copia (.bak) de {RETROALIMENTACION_DIR} y vuelva a ejecutar."
        )
        return 1
    fusionar_retroalimentacion(refs.snies, retro_map, divisiones_validas_norm, logger)
    # Registro de nombres de cada SNIES: los ya registrados conservan su división; uno nuevo se clasifica. Hasta que la
    # retroalimentación tenga el registro completo (primera ejecución), ningún nombre cuenta como nuevo
    refs = refs._replace(retro_info=retro_info,
                         registro={s: e["nombres"] for s, e in retro_info.items() if e.get("nombres")},
                         registro_completo=bool(retro_meta.get("registro_nombres")))
    eliminados = overrides_eliminados(retro_info)
    if eliminados:
        logger.warning(f"{len(eliminados)} SNIES de {RETROALIMENTACION_FILE} tienen la division de un override que ya no existe "
                       f"(se revisan en {PROGRAMAS_FILE}): " + ", ".join(f"{s} ('{n}')" for s, n in sorted(eliminados.items())[:10]))
    if retro_map:
        logger.debug(f"SNIES map total tras retroalimentacion: {len(refs.snies):,} entradas")

    if not simular:
        os.makedirs(SALIDAS_DIR, exist_ok=True)

    # Caché de combinaciones ya clasificadas; se vacía cada vez que cambian las referencias
    caches = {}

    archivos_ordenados = sorted(archivos_legibles, key=lambda x: 0 if _es_programas(x[0]) else 1)

    # Sin Programas.xlsx en la selección, los parquets perderían su enriquecimiento de referencias
    hay_parquets = any(_es_parquet(n) for n, _ in archivos_ordenados)
    overrides_retro = {}  # procedencia OVERRIDE de los SNIES que solo están en los parquets: SNIES -> nombre
    inventario = None  # nombres de cada SNIES en todos los parquets (registro de nombres de la retroalimentación)
    if not any(_es_programas(n) for n, _ in archivos_ordenados) and hay_parquets:
        if any(_es_programas(ARCHIVOS_PROCESABLES[i]) for i in seleccion):
            # Seleccionado pero ausente o ilegible (error ya registrado): no se intenta de nuevo
            logger.warning(AVISO_SIN_ENRIQUECIMIENTO)
        else:
            refs = _referencias_desde_programas(refs, divisiones_validas_norm, logger, caches)
            caches.clear()
            refs, overrides_retro, inventario = _preparar_parquets(refs, logger)

    # Un mismo SNIES con un mismo nombre: una sola división en todos los archivos; nombres distintos (programa renombrado)
    # pueden tener divisiones distintas, también dentro de un archivo
    divisiones_archivos = {}  # archivo -> divisiones_por_nombre

    total_archivos = len(archivos_ordenados)
    for file_num, (nombre, path) in enumerate(archivos_ordenados, 1):
        logger.info("", extra=PLAIN)
        logger.info("─" * 60, extra=PLAIN)
        logger.info(f"[{file_num}/{total_archivos}] Procesando: {nombre}", extra=VISIBLE)
        t_inicio = time.time()

        if _es_parquet(nombre) and inventario is None:
            # Sin las referencias de Programas.xlsx (no se pudo clasificar o enriquecer, o no se leyeron los nombres de los
            # parquets) la salida diferiría de una ejecución completa: no se reemplaza la salida verificada anterior
            logger.error(f"  Omitido: faltan las referencias de los parquets ({PROGRAMAS_FILE} clasificado y nombres de todos "
                         "los parquets); se conservan su salida y su lista de revision anteriores.")
            resumen_archivos.append(_stats_error(nombre))
            continue

        # Un error en cualquier fase de un archivo no detiene el pipeline ni impide guardar la retroalimentación
        try:
            df_final, stats = procesar_archivo(nombre, path, refs, divisiones_validas_norm, logger, caches, simular)
        except Exception as e:
            _registrar_error(logger, f"  Error al procesar '{nombre}'", e)
            logger.error("  Archivo omitido; se continua con el siguiente.")
            resumen_archivos.append(_stats_error(nombre, time.time() - t_inicio))
            if _es_programas(nombre):
                logger.warning(AVISO_SIN_ENRIQUECIMIENTO)
            continue

        resumen_archivos.append(stats)
        if stats["guardado"] and stats["revision"] is not None:
            logger.info(_linea_resultado(stats), extra=AVISO if stats["revision"][0] else EXITO)
        else:
            logger.error(_linea_resultado(stats))

        divisiones_archivos[nombre] = divisiones_por_nombre(df_final, _col_map(nombre))
        # Lo decidido aquí para cada nombre (forma canónica) es la referencia de sus equivalentes en los archivos siguientes
        refs = refs._replace(canonicas_ejecucion=canonicas_de_archivo(df_final, _col_map(nombre), refs.canonicas_ejecucion))

        if _es_programas(nombre):
            refs = _enriquecer_referencias(df_final, refs, logger)
            caches.clear()
            if hay_parquets:
                refs, overrides_retro, inventario = _preparar_parquets(refs, logger)

        del df_final

    entre_archivos = verificar_division_por_snies(divisiones_archivos, logger)
    equivalentes = verificar_division_por_forma_canonica(divisiones_archivos, logger)

    overrides_retro = {**dict(refs.overrides_programas), **overrides_retro}
    # Nombre con el que se verificó cada división (Programas.xlsx/ProgramasUN; en los SNIES que solo están en los
    # parquets, el del override): permite detectar renombres en ejecuciones futuras
    nombres_retro = {**{s: n for s, n in overrides_retro.items() if s not in refs.snies_fijos}, **(refs.nombre_vigente or {})}
    # Override eliminado y división confirmada por la votación sin revisión: su procedencia pasa a VOTACION
    reclasificados = frozenset(s for s in eliminados if s in refs.snies_fijos and s not in refs.snies_revision
                               and s not in overrides_retro)
    # Registro de nombres (fuente, años y división de cada nombre de cada SNIES): solo si se leyeron los parquets
    registro = construir_registro(inventario, refs, refs.division_nombres) if inventario is not None else None
    if simular:
        retro_ok = True
    else:
        retro_ok = guardar_retroalimentacion(refs.snies, RETROALIMENTACION_DIR, divisiones_validas_norm, logger, refs.snies_auto,
                                             snies_un=refs.snies_un, overrides=overrides_retro, nombres=nombres_retro,
                                             reclasificados=reclasificados, registro=registro)

    imprimir_tabla_final(resumen_archivos, logger, log_path, simular)
    logger.info("", extra=PLAIN)
    if simular:
        logger.info("Simulacion: no se escribio ninguna salida, lista de revision ni retroalimentacion.", extra=AVISO)
    else:
        logger.info(f"Archivos de salida en: {SALIDAS_DIR}", extra=VISIBLE)
    if log_path:
        logger.info(f"Log de la ejecucion: {log_path}", extra=VISIBLE)

    # Parquets omitidos por falta de sus referencias (cuentan como ERROR en el resumen)
    if hay_parquets and inventario is None:
        logger.warning("Los parquets no se procesaron por falta de sus referencias: revise los errores anteriores y vuelva "
                       "a ejecutar.")
    return _veredicto_final(resumen_archivos, retro_ok, entre_archivos, equivalentes, time.time() - t_ejecucion, logger, simular)


def _formato_duracion(segundos):
    """'42.3s' o '5m11s'."""
    if segundos < 60:
        return f"{segundos:.1f}s"
    minutos, seg = divmod(int(round(segundos)), 60)
    return f"{minutos}m{seg:02d}s"


def _veredicto_final(resumen_archivos, retro_ok, entre_archivos, equivalentes, segundos, logger, simular=False):
    """Última línea de la ejecución: resultado, archivos OK y tiempo total (más los cambios frente a las salidas anteriores).
    Retorna el código de salida (0 = todo OK, 1 = hubo errores)."""
    total = len(resumen_archivos)
    ok = [s for s in resumen_archivos if not s["error"] and s["guardado"] and s.get("revision", ()) is not None]
    motivos = []
    if len(ok) < total:
        motivos.append(f"{total - len(ok)} de {total} archivo(s) con error")
    if not retro_ok:
        motivos.append("retroalimentacion no guardada")
    if entre_archivos:
        motivos.append("divisiones distintas para un mismo SNIES y nombre")
    if equivalentes:
        motivos.append("divisiones distintas para nombres equivalentes")

    tiempo = _formato_duracion(segundos)
    if motivos:
        logger.error(f"TERMINADO CON ERRORES en {tiempo}: {'; '.join(motivos)}. Revise los mensajes anteriores.")
        return 1

    comparados = [s["cambios"] for s in ok if s.get("cambios") is not None]
    cambiaron = sum(c[0] for c in comparados)
    if simular:
        if not comparados:
            extra = ""
        elif cambiaron:
            extra = f"; {cambiaron:,} registros cambiarian de division respecto a las salidas actuales"
        else:
            extra = "; sin cambios de division respecto a las salidas actuales"
        logger.info(f"SIMULACION COMPLETADA en {tiempo}: {len(ok)}/{total} archivos clasificados{extra}; no se escribio ningun archivo",
                    extra=EXITO)
        return 0
    if not comparados:
        extra = ""
    elif cambiaron:
        extra = f"; {cambiaron:,} registros cambiaron de division respecto a las salidas anteriores"
    else:
        extra = "; sin cambios de division respecto a las salidas anteriores"
    logger.info(f"COMPLETADO en {tiempo}: {len(ok)}/{total} archivos OK{extra}", extra=EXITO)
    return 0


def main():
    args = parse_args()

    if args.archivos is not None:
        seleccion, error = _parsear_seleccion(args.archivos)
        if error:
            print(f"--archivos: {error}")
            return 1
    else:
        seleccion = menu_seleccion()
        if seleccion is None:
            return 1

    logger, log_path = setup_logger(verbose=args.verbose, logs_dir=LOGS_DIR, max_logs=MAX_LOGS)

    nombres_sel = [ARCHIVOS_PROCESABLES[i] for i in seleccion]
    modo = "SIMULACION (sin escribir archivos)" if args.simular else "pipeline"
    logger.info(f"Iniciando {modo} ({len(nombres_sel)} archivo(s)): {', '.join(nombres_sel)}", extra=VISIBLE)

    try:
        return ejecutar(seleccion, logger, log_path, args.simular)
    except Exception as e:
        _registrar_error(logger, "Error inesperado; pipeline detenido", e)
        if log_path:
            logger.error(f"Detalle completo en: {log_path}")
        return 1


def _mtime(ruta):
    """Fecha de modificación de ruta (ns) o None si no existe."""
    try:
        return os.stat(ruta).st_mtime_ns
    except OSError:
        return None


if __name__ == "__main__":
    ruta_retro = os.path.join(RETROALIMENTACION_DIR, RETROALIMENTACION_FILE)
    mtime_retro = _mtime(ruta_retro)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # La retroalimentación se guarda casi al final (después solo se imprime el resumen): el mensaje depende de si
        # llegó a reemplazarse el archivo
        if _mtime(ruta_retro) != mtime_retro:
            estado = "La retroalimentacion ya se habia guardado en esta ejecucion."
        else:
            estado = "La retroalimentacion no se actualizo en esta ejecucion."
        logging.getLogger("clasificacion").warning(f"Ejecucion interrumpida por el usuario. {estado}")
        sys.exit(130)
