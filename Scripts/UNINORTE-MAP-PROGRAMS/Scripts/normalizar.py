"""Funciones de normalización de texto para el pipeline de clasificación."""

import re
import unicodedata
import pandas as pd

# Patrones compartidos por la versión de un valor (re de Python) y la de Series (pandas con Arrow, motor RE2),
# para que ambas den exactamente lo mismo: en RE2 \s no incluye los espacios Unicode (p. ej. el espacio duro
# \xa0 de algunos nombres de la fuente), por eso se listan aparte. Los caracteres Unicode van literales en el
# patrón (no como escapes \uXXXX, que RE2 no admite).
_PUNTUACION = r"[,;:.\-/()]+"
_ESPACIOS = "[\\s   -     　]+"
_DIACRITICOS = "[̀-ͯ]"

# Caracteres con que la fuente reemplaza una letra con tilde o una ñ ('GESTI¿N', 'DISE�O'): en los nombres valen por
# una letra cualquiera (clasificar_divisiones._match_comodin)
CARACTERES_DANADOS = ("¿", "�")

# Largo mínimo (normalizado) de un nombre truncado por la fuente para contar como el mismo nombre que el completo: la
# fuente corta los nombres largos hacia los 85-100 caracteres. Un prefijo más corto es otro nombre ('tecnologia en
# gestion' frente a 'tecnologia en gestion agropecuaria', 'comunicacion social' frente a '... social periodismo')
MIN_TRUNCADO = 80

# Conectores que no distinguen un nombre de programa de otro con las mismas palabras en otro orden
# ('gestion e innovacion de empresas' = 'innovacion y gestion de empresas')
CONECTORES = frozenset({"de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "con", "a", "al", "por", "o", "u"})


def _equivalencias():
    """diccionarios.TERMINOS_EQUIVALENTES normalizado (forma → término base); vacío si no existe."""
    import diccionarios
    return {normalizar_para_keywords(k): normalizar_para_keywords(v)
            for k, v in getattr(diccionarios, "TERMINOS_EQUIVALENTES", {}).items()}


_EQUIVALENCIAS = None  # se carga en el primer uso (normalizar_para_keywords se define más abajo)


def forma_canonica(nombre):
    """Palabras de un nombre normalizado (normalizar_para_keywords), sin conectores, con cada término equivalente en su forma
    base (TERMINOS_EQUIVALENTES: 'empresarial' → 'empresa') y en orden alfabético: dos nombres con la misma forma canónica
    son el mismo programa con otro orden de palabras o una variante estricta de sus términos."""
    global _EQUIVALENCIAS
    if _EQUIVALENCIAS is None:
        _EQUIVALENCIAS = _equivalencias()
    return " ".join(sorted(_EQUIVALENCIAS.get(t, t) for t in nombre.split() if t not in CONECTORES))


def normalizar_texto(texto):
    """Normaliza texto: estandariza a mayúsculas y limpia puntuación."""
    if pd.isna(texto):
        return None
    texto = str(texto).strip()
    if not texto:
        return None
    texto = texto.upper()
    texto = re.sub(_PUNTUACION, " ", texto)
    texto = re.sub(_ESPACIOS, " ", texto).strip()
    # Solo puntuación (p. ej. ".", "-"): ausente, igual que normalizar_serie
    return texto or None


def normalizar_para_keywords(texto):
    """Normaliza texto para búsqueda de keywords (minúsculas, sin diacríticos: á→a, ñ→n, ü→u)."""
    if pd.isna(texto):
        return ""
    texto = str(texto).strip()
    if not texto:
        return ""
    texto = re.sub(_DIACRITICOS, "", unicodedata.normalize("NFD", texto.lower()))
    texto = re.sub(_PUNTUACION, " ", texto)
    texto = re.sub(_ESPACIOS, " ", texto).strip()
    return texto


def normalizar_serie(s):
    """Aplica normalización (mayúsculas) a toda una Series de pandas.
    Igual que normalizar_texto: un valor vacío tras limpiar se trata como ausente (None)."""
    res = s.where(s.isna(), s.astype(str).str.strip()
        .str.upper()
        .str.replace(_PUNTUACION, " ", regex=True)
        .str.replace(_ESPACIOS, " ", regex=True)
        .str.strip()
    )
    return res.where(s.notna() & (res != ""), None)


def normalizar_serie_keywords(s):
    """Normaliza para búsqueda de keywords (minúsculas, sin diacríticos) en toda una Series."""
    result = s.where(s.isna(), s.astype(str).str.strip()
        .str.lower()
        .str.normalize("NFD")
        .str.replace(_DIACRITICOS, "", regex=True)
        .str.replace(_PUNTUACION, " ", regex=True)
        .str.replace(_ESPACIOS, " ", regex=True)
        .str.strip()
    ).fillna("")
    return result
