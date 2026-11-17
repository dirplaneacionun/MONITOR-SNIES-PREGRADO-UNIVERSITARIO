"""Validaciones previas a la ejecución del pipeline y verificación de integridad de las salidas."""

import ast
import itertools
import os
import re
from collections import defaultdict
import pandas as pd
import pyarrow.parquet as pq

from config import (
    COLUMNAS_PARQUET, COLUMNAS_EXCEL, COLUMNAS_PROGRAMAS_UN, COLUMNAS_REQUERIDAS, PROGRAMAS_UN_FILE,
    PESO_CAPA1, PESO_CAPA2_NOMBRE, PESO_CAPA3_CINE, PESO_CAPA3_NBC, UMBRAL_MEDIA,
)
from diccionarios import DIVISIONES, DIVISIONES_VALIDAS
from utils import find_file_insensitive, VISIBLE
from normalizar import normalizar_texto, normalizar_para_keywords, CARACTERES_DANADOS, MIN_TRUNCADO, forma_canonica


def validar_pesos(logger):
    """Verifica las reglas de las que depende la votación (pesos y umbrales de config.py)."""
    tol = 1e-9
    capa2 = PESO_CAPA2_NOMBRE
    resto = capa2 + PESO_CAPA3_CINE + PESO_CAPA3_NBC

    errores = []
    if abs(PESO_CAPA1 + resto - 1.0) > tol:
        errores.append(f"Los pesos de las 3 capas deben sumar 1.00, pero suman {PESO_CAPA1 + resto:.4f}.")
    if PESO_CAPA1 < resto - tol:
        # Con menos peso, las demás capas juntas podrían cambiar la división de un SNIES ya verificado
        errores.append(
            f"PESO_CAPA1 ({PESO_CAPA1:.2f}) debe ser mayor o igual que la suma de las demas capas ({resto:.2f}): "
            "de lo contrario un programa con SNIES conocido podria cambiar de division."
        )
    if errores:
        for e in errores:
            logger.error(f"  {e}")
        return False

    if UMBRAL_MEDIA <= capa2 + tol:
        # El nombre es la señal menos precisa: si alcanza MEDIA por sí solo, esos casos no van a revisión
        logger.warning(
            f"UMBRAL_MEDIA ({UMBRAL_MEDIA:.2f}) no es mayor que el peso de la Capa 2 ({capa2:.2f}): un programa sin SNIES "
            "conocido quedara sin revision solo por el parecido de su nombre. Se recomienda subir UMBRAL_MEDIA en config.py."
        )
    logger.debug(
        f"Pesos: Capa 1 {PESO_CAPA1:.2f} | Capa 2 {capa2:.2f} | Capa 3 {PESO_CAPA3_CINE + PESO_CAPA3_NBC:.2f} "
        f"| UMBRAL_MEDIA {UMBRAL_MEDIA:.2f}"
    )
    return True


def validar_catalogo_divisiones(logger):
    """Verifica la integridad del catálogo embebido DIVISIONES (sin vacíos ni duplicados tras normalizar)."""
    errores = []
    vistos = {}
    for div in DIVISIONES_VALIDAS:
        div_norm = normalizar_texto(div)
        if not div_norm:
            errores.append(f"Division vacia en el catalogo: {div!r}")
        elif div_norm in vistos:
            errores.append(f"Division duplicada en el catalogo: '{div}' (equivale a '{vistos[div_norm]}')")
        else:
            vistos[div_norm] = div
    if "OTRO" in DIVISIONES:
        errores.append("'OTRO' no debe estar en DIVISIONES (se agrega automaticamente en DIVISIONES_VALIDAS)")

    if errores:
        for e in errores:
            logger.error(f"  {e}")
        return False
    logger.debug(f"Catalogo de divisiones: {len(DIVISIONES)} divisiones + OTRO")
    return True


def validar_division_no_existe(filepath, logger):
    """Verifica que DIVISION_UNINORTE no exista ya en el archivo."""
    nombre = os.path.basename(filepath)
    if filepath.lower().endswith(".parquet"):
        pf = pq.ParquetFile(filepath)
        columnas = [pf.schema[i].name for i in range(len(pf.schema))]
    else:
        df = pd.read_excel(filepath, nrows=0)
        columnas = list(df.columns)

    if "DIVISION_UNINORTE" in columnas:
        logger.error(
            f"El archivo '{nombre}' ya contiene la columna DIVISION_UNINORTE. "
            "Eliminela manualmente o use un archivo limpio antes de continuar."
        )
        return False
    return True


def validar_referencias(fuentes_dir, logger):
    """Valida que ProgramasUN.xlsx exista, tenga las columnas necesarias y solo use divisiones del catálogo."""
    programas_un_path = find_file_insensitive(fuentes_dir, PROGRAMAS_UN_FILE)
    if not programas_un_path:
        logger.error(f"No se encontro {PROGRAMAS_UN_FILE} en la carpeta Fuentes.")
        return False

    df = pd.read_excel(programas_un_path)
    # Mismas columnas que usa cargar_referencias (fuente única: config.COLUMNAS_PROGRAMAS_UN)
    cols_requeridas = list(COLUMNAS_PROGRAMAS_UN.values())
    faltantes = [c for c in cols_requeridas if c not in df.columns]
    if faltantes:
        logger.error(f"{PROGRAMAS_UN_FILE} no tiene las columnas: {faltantes}")
        return False

    # ProgramasUN alimenta la Capa 1 (peso 0.50): una división fuera del catálogo llegaría sin validar a la salida
    divisiones_norm = {normalizar_texto(d) for d in DIVISIONES_VALIDAS}
    divs_archivo = df[COLUMNAS_PROGRAMAS_UN["division"]].dropna().astype(str)
    invalidas = divs_archivo[~divs_archivo.map(normalizar_texto).isin(divisiones_norm)]
    if not invalidas.empty:
        conteo = invalidas.value_counts()
        logger.error(f"{PROGRAMAS_UN_FILE} tiene {len(invalidas)} fila(s) con division fuera del catalogo:")
        for div, n in conteo.items():
            logger.error(f"  '{div}' ({n} fila(s))")
        return False

    # Un SNIES repetido con divisiones distintas tomaría en silencio la última fila (Capa 1, peso 0.50,
    # y división fija); un nombre repetido, la primera (referencia de la Capa 2)
    col_div = COLUMNAS_PROGRAMAS_UN["division"]
    con_div = df[df[col_div].notna()]
    div_norm = con_div[col_div].astype(str).map(normalizar_texto)
    snies = pd.to_numeric(con_div[COLUMNAS_PROGRAMAS_UN["snies"]], errors="coerce")
    conflictos_snies = div_norm.groupby(snies).nunique()
    conflictos_snies = conflictos_snies[conflictos_snies > 1]
    if not conflictos_snies.empty:
        logger.error(f"{PROGRAMAS_UN_FILE} tiene {len(conflictos_snies)} SNIES repetido(s) con divisiones distintas:")
        for s_ in conflictos_snies.index:
            divs = sorted(set(con_div.loc[snies == s_, col_div].astype(str)))
            logger.error(f"  SNIES {int(s_)}: {', '.join(divs)}")
        return False
    nombres = con_div[COLUMNAS_PROGRAMAS_UN["nombre"]].map(normalizar_para_keywords)
    conflictos_nombre = div_norm[nombres != ""].groupby(nombres[nombres != ""]).nunique()
    conflictos_nombre = conflictos_nombre[conflictos_nombre > 1]
    if not conflictos_nombre.empty:
        logger.warning(
            f"{PROGRAMAS_UN_FILE}: {len(conflictos_nombre)} nombre(s) de programa con divisiones distintas "
            "(la Capa 2 usa la primera fila; cada SNIES conserva la suya):"
        )
        for n_ in conflictos_nombre.index:
            divs = sorted(set(con_div.loc[nombres == n_, col_div].astype(str)))
            logger.warning(f"  '{n_}': {', '.join(divs)}")

    logger.info("Validacion de referencias: OK", extra=VISIBLE)
    return True


def validar_divisiones_diccionarios(logger):
    """Verifica los diccionarios antes de clasificar: divisiones del catálogo DIVISIONES, campos y patrones de las reglas,
    claves repetidas, overrides con caracteres dañados o truncados que contradicen a su nombre completo (con una errata
    en el nivel: aviso) y reglas de NBC_AMBIGUO que nunca se aplican por su orden. Retorna True si no hay errores."""
    import diccionarios as _dic

    divisiones_norm = {normalizar_texto(d) for d in DIVISIONES_VALIDAS}

    errores = []

    for nbc, div in _dic.NBC_EXCLUSIVO.items():
        div_norm = normalizar_texto(div)
        if div_norm not in divisiones_norm:
            errores.append(f"NBC_EXCLUSIVO['{nbc}'] -> '{div}' no es una division valida")

    for nbc, info in _dic.NBC_AMBIGUO.items():
        if info["default"] is not None:
            default_norm = normalizar_texto(info["default"])
            if default_norm not in divisiones_norm:
                errores.append(f"NBC_AMBIGUO['{nbc}'].default -> '{info['default']}' no es una division valida")
        for campo, patron, div in info["reglas"]:
            div_norm = normalizar_texto(div)
            if div_norm not in divisiones_norm:
                errores.append(f"NBC_AMBIGUO['{nbc}'].regla({patron}) -> '{div}' no es una division valida")
            # Un campo desconocido busca en texto vacío (la regla nunca aplica);
            # un patrón vacío está contenido en cualquier texto (la regla aplica siempre)
            if campo not in _dic.CAMPOS_REGLA_NBC:
                errores.append(
                    f"NBC_AMBIGUO['{nbc}'].regla({patron}) -> campo '{campo}' no reconocido "
                    f"(validos: {', '.join(_dic.CAMPOS_REGLA_NBC)})"
                )
            # Misma normalización que aplica clasificar_divisiones._normalizar_diccionarios
            patron_norm = normalizar_para_keywords(patron) if campo == "nombre" else normalizar_texto(patron)
            if not patron_norm:
                errores.append(f"NBC_AMBIGUO['{nbc}'].regla({patron!r}) -> patron vacio: aplicaria a todo el NBC")

    for cine, div in _dic.CINE_EXCLUSIVO.items():
        div_norm = normalizar_texto(div)
        if div_norm not in divisiones_norm:
            errores.append(f"CINE_EXCLUSIVO['{cine}'] -> '{div}' no es una division valida")

    for div_name in _dic.KEYWORDS:
        div_norm = normalizar_texto(div_name)
        if div_norm not in divisiones_norm:
            errores.append(f"KEYWORDS['{div_name}'] no es una division valida")

    # Equivalencias de NBC: el destino debe ser una clave del catálogo y el origen no (si lo fuera, la clave quedaría oculta)
    claves_nbc = {normalizar_texto(k) for k in (*_dic.NBC_EXCLUSIVO, *_dic.NBC_AMBIGUO)}
    for origen, destino in getattr(_dic, "NBC_ALIAS", {}).items():
        if normalizar_texto(destino) not in claves_nbc:
            errores.append(f"NBC_ALIAS['{origen}'] -> '{destino}' no es una clave de NBC_EXCLUSIVO ni de NBC_AMBIGUO")
        if normalizar_texto(origen) in claves_nbc:
            errores.append(f"NBC_ALIAS['{origen}'] ya es una clave de NBC_EXCLUSIVO/NBC_AMBIGUO: la equivalencia la ocultaria")

    errores += _claves_repetidas_diccionarios(_dic, logger)
    _avisar_nivel_con_errata(getattr(_dic, "NOMBRE_DIVISION_OVERRIDE", {}), logger)
    errores += _comodines_contradictorios(getattr(_dic, "NOMBRE_DIVISION_OVERRIDE", {}))
    errores += _truncados_contradictorios(getattr(_dic, "NOMBRE_DIVISION_OVERRIDE", {}))
    errores += _permutaciones_contradictorias(getattr(_dic, "NOMBRE_DIVISION_OVERRIDE", {}))
    errores += _terminos_equivalentes_invalidos(getattr(_dic, "TERMINOS_EQUIVALENTES", {}))
    errores += _reglas_inalcanzables(_dic.NBC_AMBIGUO, logger)

    # Claves: solo nombres de programa (la división de un SNIES concreto la fijan ProgramasUN y la retroalimentación)
    for clave, div in getattr(_dic, "NOMBRE_DIVISION_OVERRIDE", {}).items():
        if not isinstance(clave, str) or clave.strip().isdigit():
            errores.append(
                f"NOMBRE_DIVISION_OVERRIDE[{clave!r}]: la clave debe ser un nombre de programa. Para fijar la "
                f"division de un SNIES use {PROGRAMAS_UN_FILE} (programas UniNorte) o corrija el nombre del programa"
            )
        elif not normalizar_para_keywords(clave):
            # Un nombre vacío coincidiría con todos los registros sin nombre
            errores.append(f"NOMBRE_DIVISION_OVERRIDE[{clave!r}]: nombre vacio")
        if normalizar_texto(div) not in divisiones_norm:
            errores.append(f"NOMBRE_DIVISION_OVERRIDE[{clave!r}] -> '{div}' no es una division valida")

    if errores:
        logger.error(f"Errores en diccionarios ({len(errores)}):")
        for e in errores:
            logger.error(f"  {e}")
        return False

    logger.info("Validacion de diccionarios: OK", extra=VISIBLE)
    return True


def _comodines_contradictorios(overrides):
    """Un override con caracteres dañados ('maestr¿a en educaci¿n') y el de su nombre limpio equivalente ('maestria en
    educacion') deben fijar la misma división: si no, el mismo programa cambia de división según cómo lo escriba la
    fuente. Retorna la lista de errores."""
    limpios = defaultdict(list)
    danados = []
    for clave, div in overrides.items():
        if not isinstance(clave, str):
            continue
        nombre = normalizar_para_keywords(clave)
        if any(c in nombre for c in CARACTERES_DANADOS):
            danados.append((nombre, clave, div))
        else:
            limpios[len(nombre)].append((nombre, clave, div))
    errores = []
    for nombre, clave, div in danados:
        patron = re.compile("".join("." if c in CARACTERES_DANADOS else re.escape(c) for c in nombre))
        for nombre_l, clave_l, div_l in limpios.get(len(nombre), ()):
            if normalizar_texto(div_l) != normalizar_texto(div) and patron.fullmatch(nombre_l):
                errores.append(f"NOMBRE_DIVISION_OVERRIDE[{clave!r}] -> '{div}' contradice a su nombre limpio "
                               f"equivalente {clave_l!r} -> '{div_l}'")
    return errores


def _terminos_equivalentes_invalidos(equivalentes):
    """TERMINOS_EQUIVALENTES: cada forma y su término base son una sola palabra normalizada, distintas entre sí, y el término
    base no es a su vez una forma de otro término (sin cadenas). Retorna la lista de errores."""
    errores = []
    formas = {normalizar_para_keywords(k) for k in equivalentes}
    for forma, base in equivalentes.items():
        f, b = normalizar_para_keywords(forma), normalizar_para_keywords(base)
        if not f or not b or " " in f or " " in b:
            errores.append(f"TERMINOS_EQUIVALENTES[{forma!r}] -> {base!r}: forma y término base deben ser una sola palabra")
        elif f == b:
            errores.append(f"TERMINOS_EQUIVALENTES[{forma!r}]: la forma es igual a su término base")
        elif b in formas:
            errores.append(f"TERMINOS_EQUIVALENTES[{forma!r}] -> {base!r}: el término base es a su vez una forma de otro término")
    return errores


def _permutaciones_contradictorias(overrides):
    """Dos overrides que son el mismo nombre con otro orden de palabras, conectores o términos equivalentes ('gestion e
    innovacion de empresas' / 'innovacion y gestion empresarial', forma_canonica) deben fijar la misma división. Retorna
    la lista de errores."""
    grupos = defaultdict(list)
    for clave, div in overrides.items():
        if isinstance(clave, str):
            grupos[forma_canonica(normalizar_para_keywords(clave))].append((clave, div))
    errores = []
    for nombres in grupos.values():
        if len({normalizar_texto(d) for _, d in nombres}) > 1:
            errores.append("NOMBRE_DIVISION_OVERRIDE: el mismo nombre con otro orden de palabras tiene divisiones distintas: "
                           + "; ".join(f"{k!r} -> '{d}'" for k, d in nombres))
    return errores


def _distancia_edicion(a, b):
    """Número mínimo de letras insertadas, borradas o cambiadas para pasar de a a b (Levenshtein)."""
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (ca != cb)))
        previa = actual
    return previa[-1]


# Diferencia máxima (letras) entre dos palabras de nivel para considerarlas el mismo nivel con una errata de la fuente
_MAX_ERRATA_NIVEL = 2


def _avisar_nivel_con_errata(overrides, logger):
    """Aviso para dos overrides iguales salvo una errata o variante en la primera palabra (el nivel: 'especilizacion' /
    'especializacion', 'maestra' / 'maestria', 'tecnico' / 'tecnica') con divisiones distintas: al corregir una familia
    quedó atrás una de sus variantes. Las palabras de nivel distintas ('maestria' / 'doctorado') o los nombres con caracteres dañados (los
    revisa _comodines_contradictorios) no cuentan. Solo avisa: no detiene el pipeline."""
    por_resto = defaultdict(list)
    for clave, div in overrides.items():
        if not isinstance(clave, str):
            continue
        nombre = normalizar_para_keywords(clave)
        partes = nombre.split(" ", 1)
        if len(partes) == 2 and not any(c in nombre for c in CARACTERES_DANADOS):
            por_resto[partes[1]].append((partes[0], clave, normalizar_texto(div), div))
    for resto, nombres in por_resto.items():
        for i, (nivel_a, clave_a, div_a, txt_a) in enumerate(nombres):
            for nivel_b, clave_b, div_b, txt_b in nombres[i + 1:]:
                if (nivel_a != nivel_b and div_a != div_b
                        and _distancia_edicion(nivel_a, nivel_b) <= _MAX_ERRATA_NIVEL):
                    logger.warning(f"  NOMBRE_DIVISION_OVERRIDE: {clave_a!r} -> '{txt_a}' y {clave_b!r} -> '{txt_b}' solo "
                                   f"difieren en el nivel ('{nivel_a}' / '{nivel_b}': errata o variante): revise si deben "
                                   "tener la misma division")


def _truncados_contradictorios(overrides):
    """Un override y otro que es su mismo nombre truncado por la fuente (uno empieza con el otro, de al menos
    MIN_TRUNCADO caracteres) deben fijar la misma división. Retorna la lista de errores."""
    nombres = sorted((normalizar_para_keywords(k), k, v) for k, v in overrides.items() if isinstance(k, str))
    errores = []
    for i, (corto, clave, div) in enumerate(nombres):
        if len(corto) < MIN_TRUNCADO:
            continue
        for largo, clave_l, div_l in nombres[i + 1:]:
            if not largo.startswith(corto):
                break
            if normalizar_texto(div_l) != normalizar_texto(div):
                errores.append(f"NOMBRE_DIVISION_OVERRIDE[{clave!r}] -> '{div}' contradice a su nombre completo "
                               f"{clave_l!r} -> '{div_l}' (la fuente trunca los nombres largos)")
    return errores


def _reglas_inalcanzables(nbc_ambiguo, logger):
    """En NBC_AMBIGUO gana la primera regla cuyo patrón aparece en el texto. Una regla cuyo patrón contiene el de una
    regla anterior del mismo campo nunca se aplica: si fija otra división es un error (orden equivocado); si fija la
    misma, es redundante (aviso). Retorna la lista de errores."""
    errores = []
    for nbc, info in nbc_ambiguo.items():
        reglas = [(campo, normalizar_para_keywords(p) if campo == "nombre" else normalizar_texto(p) or "", div)
                  for campo, p, div in info["reglas"]]
        for j, (campo_j, patron_j, div_j) in enumerate(reglas):
            for campo_i, patron_i, div_i in reglas[:j]:
                if campo_i == campo_j and patron_i and patron_i in patron_j:
                    msg = (f"NBC_AMBIGUO['{nbc}']: la regla '{patron_j}' -> '{div_j}' nunca se aplica porque antes esta "
                           f"'{patron_i}' -> '{div_i}'")
                    if normalizar_texto(div_i) != normalizar_texto(div_j):
                        errores.append(msg + " (muevala antes de esa regla)")
                    else:
                        logger.warning(f"  {msg} (misma division: redundante)")
                    break
    return errores


# Diccionarios de diccionarios.py cuyas claves se comparan normalizadas (como las usa clasificar_divisiones)
_NORMALIZACION_CLAVES = {
    "NBC_EXCLUSIVO": normalizar_texto,
    "NBC_AMBIGUO": normalizar_texto,
    "CINE_EXCLUSIVO": normalizar_texto,
    "KEYWORDS": normalizar_texto,
    "NBC_ALIAS": normalizar_texto,
    "NOMBRE_DIVISION_OVERRIDE": normalizar_para_keywords,
}


def _claves_repetidas_diccionarios(dic_mod, logger):
    """Claves repetidas en los diccionarios de diccionarios.py, que Python acepta en silencio (gana la última):
    - Literal repetida o dos claves iguales tras normalizar ("Gestión" / "gestion") con distinto valor: error
      (una de las decisiones se pierde sin aviso).
    - Mismo caso con el mismo valor: aviso (entrada redundante).
    Las keywords repetidas dentro de una división cuentan doble en los puntajes: aviso.
    Se lee el código fuente porque el diccionario ya cargado conserva solo la última clave."""
    with open(dic_mod.__file__, encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    errores = []
    for nodo in arbol.body:
        if not (isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Dict)):
            continue
        nombre = next((t.id for t in nodo.targets if isinstance(t, ast.Name) and t.id in _NORMALIZACION_CLAVES), None)
        if nombre is None:
            continue
        normalizar = _NORMALIZACION_CLAVES[nombre]
        vistos = {}
        for k, v in zip(nodo.value.keys, nodo.value.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            try:
                valor = ast.literal_eval(v)
            except ValueError:
                # Valor no literal (p. ej. reglas con *REGLAS_AGRO): se compara su estructura; si se omitiera, una clave
                # repetida de esos bloques pasaría sin aviso
                valor = ast.dump(v)
            clave = normalizar(k.value)
            if clave in vistos:
                linea0, k0, valor0 = vistos[clave]
                desc = (f"{nombre}: {k0!r} (linea {linea0}) y {k.value!r} (linea {k.lineno})"
                        + (" son la misma clave" if k0 == k.value else " quedan iguales al normalizar"))
                if valor != valor0:
                    errores.append(f"{desc} con valores distintos: solo se aplica el de la linea {k.lineno}")
                else:
                    logger.warning(f"  {desc} (mismo valor; entrada redundante)")
            vistos[clave] = (k.lineno, k.value, valor)
            if nombre == "KEYWORDS" and isinstance(valor, list):
                repetidas = sorted({kw for kw in valor if [normalizar_para_keywords(x) for x in valor].count(normalizar_para_keywords(kw)) > 1})
                if repetidas:
                    logger.warning(f"  KEYWORDS[{k.value!r}]: keywords repetidas (cuentan doble): {repetidas}")
    return errores


class ErrorIntegridad(Exception):
    """Una salida no conserva exactamente los datos originales de su archivo de Fuentes."""


_MAX_EJEMPLOS = 5


def verificar_integridad(fuente_path, salida_path):
    """Comprueba que la salida conserve, en el mismo orden, todas las filas y columnas de la fuente con exactamente
    los mismos valores y tipos (las columnas que agrega el pipeline van al final). Lanza ErrorIntegridad si no."""
    if fuente_path.lower().endswith(".parquet"):
        _verificar_parquet(fuente_path, salida_path)
    else:
        _verificar_excel(fuente_path, salida_path)


def _verificar_parquet(fuente_path, salida_path):
    fuente, salida = pq.ParquetFile(fuente_path), pq.ParquetFile(salida_path)
    cols = fuente.schema_arrow.names
    if salida.schema_arrow.names[:len(cols)] != cols:
        raise ErrorIntegridad("las columnas originales no estan completas o cambiaron de orden")
    if salida.metadata.num_rows != fuente.metadata.num_rows:
        raise ErrorIntegridad(f"filas: {fuente.metadata.num_rows:,} en la fuente, {salida.metadata.num_rows:,} en la salida")
    # Columna por columna: no se cargan los dos archivos completos a la vez
    alteradas = []
    for c in cols:
        a = pq.read_table(fuente_path, columns=[c]).column(0)
        b = pq.read_table(salida_path, columns=[c]).column(0)
        if a.type != b.type:
            alteradas.append(f"{c} (tipo {a.type} -> {b.type})")
        elif not a.equals(b):
            alteradas.append(f"{c} (valores)")
    if alteradas:
        raise ErrorIntegridad(f"{len(alteradas)} columna(s) originales alteradas: {', '.join(alteradas[:_MAX_EJEMPLOS])}")


def _verificar_excel(fuente_path, salida_path):
    import openpyxl

    wa = openpyxl.load_workbook(fuente_path, read_only=True)
    wb = openpyxl.load_workbook(salida_path, read_only=True)
    try:
        # Primera hoja, la que lee pd.read_excel (la activa puede ser otra)
        filas_a, filas_b = wa.worksheets[0].iter_rows(values_only=True), wb.worksheets[0].iter_rows(values_only=True)
        cab_a, cab_b = next(filas_a, ()), next(filas_b, ())
        # Celdas vacías al final del encabezado (con formato): pandas tampoco las lee
        n = len(cab_a)
        while n and cab_a[n - 1] is None:
            n -= 1
        cab_a = tuple(cab_a[:n])
        if tuple(cab_b[:n]) != cab_a:
            raise ErrorIntegridad("los encabezados originales no estan completos o cambiaron de orden")
        distintas, ejemplos, fila = 0, [], 1
        for fila, (x, y) in enumerate(itertools.zip_longest(filas_a, filas_b), 2):
            if y is None and x is not None and all(v is None for v in x):
                # Fila vacía (con formato) al final de la fuente: pandas descarta las filas vacías finales
                continue
            if x is None or y is None:
                raise ErrorIntegridad(f"la salida tiene {'menos' if y is None else 'mas'} filas que la fuente (desde la fila {fila})")
            for j in range(n):
                # Mismo valor y mismo tipo: '106341' (texto) no es 106341 (numero), ni 5 es 5.0
                if x[j] != y[j] or type(x[j]) is not type(y[j]):
                    distintas += 1
                    if len(ejemplos) < _MAX_EJEMPLOS:
                        ejemplos.append(f"fila {fila}, {cab_a[j]}: {x[j]!r} -> {y[j]!r}")
        if distintas:
            raise ErrorIntegridad(f"{distintas:,} celda(s) originales alteradas; p. ej. {'; '.join(ejemplos)}")
    finally:
        wa.close()
        wb.close()


def validar_columnas_clasificacion(filepath, es_parquet, logger):
    """Verifica que las columnas de clasificación existan. Retorna dict de disponibilidad."""
    nombre = os.path.basename(filepath)
    col_map = COLUMNAS_PARQUET if es_parquet else COLUMNAS_EXCEL

    if es_parquet:
        pf = pq.ParquetFile(filepath)
        columnas_archivo = {pf.schema[i].name for i in range(len(pf.schema))}
    else:
        df = pd.read_excel(filepath, nrows=0)
        columnas_archivo = set(df.columns)

    disponibilidad = {clave: col is not None and col in columnas_archivo for clave, col in col_map.items()}

    # Se verifica aquí, antes de cargar el archivo completo
    faltantes = [f"'{col_map[c]}' ({c})" for c in COLUMNAS_REQUERIDAS if not disponibilidad.get(c)]
    if faltantes:
        raise ValueError(f"[{nombre}] Faltan columnas requeridas: {', '.join(faltantes)}")
    for clave, disponible in disponibilidad.items():
        if not disponible:
            logger.warning(f"  [{nombre}] Columna '{col_map[clave]}' ({clave}) no encontrada.")

    return disponibilidad


def archivos_bloqueados(rutas):
    """Rutas existentes que no se pueden abrir para escritura (abiertas en otra aplicación, p. ej. Excel, o de solo
    lectura): al guardar no se podrían reemplazar. Se abren sin modificarlas. Retorna la lista de rutas bloqueadas."""
    bloqueados = []
    for ruta in rutas:
        if not os.path.exists(ruta):
            continue
        try:
            with open(ruta, "r+b"):
                pass
        except PermissionError:
            bloqueados.append(ruta)
    return bloqueados


def resolver_archivos(seleccion, archivos_procesables, fuentes_dir, logger):
    """Dado una lista de índices seleccionados, resuelve las rutas reales."""
    archivos = []
    for idx in seleccion:
        nombre = archivos_procesables[idx]
        path = find_file_insensitive(fuentes_dir, nombre)
        if not path:
            logger.error(f"Archivo '{nombre}' no encontrado en {fuentes_dir}")
            continue
        archivos.append((nombre, path))
    return archivos
