"""
Prueba de punta a punta del Dashboard (docs/) con Chrome sin interfaz.

Interactúa con cada control (clics reales sobre filtros, selector de fechas, buscador, encabezados de tabla,
paginación, selector CINE, exportación, navegación y filtros guardados en la dirección) y compara lo que muestra cada componente con un cálculo
independiente hecho en Python sobre los mismos datos. También verifica la coherencia de los datos generados
(docs/data) con los archivos fuente (Historico/, Novedades/).

Ejecución (requiere Google Chrome y las dependencias de requirements.txt):
    python tests/e2e_dashboard.py            # todas las pruebas
    python tests/e2e_dashboard.py --rapida   # una muestra de opciones por filtro
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
sys.path.insert(0, str(ROOT / "Scripts"))
import catalogos  # noqa: E402
import historico as historico_fuente  # noqa: E402

RESULTADOS: list[tuple[str, bool, str]] = []


def verificar(nombre: str, ok: bool, detalle: str = "") -> bool:


    """Registra y muestra el resultado de una verificación; retorna True si se cumplió."""
    RESULTADOS.append((nombre, bool(ok), detalle))
    if not ok:
        print(f"  FALLA · {nombre} · {detalle}", flush=True)
    return ok


# ── Datos de referencia ───────────────────────────────────────────────────────

def leer_js(nombre):

    """Contenido (JSON) de un archivo de datos del Dashboard, sin el envoltorio window.SNIES."""
    texto = (DOCS / "data" / nombre).read_text(encoding="utf-8")
    return json.loads(texto[texto.index("]=") + 2:].rstrip().rstrip(";"))


def decodificar(t):


    """Tabla columnar de los archivos de datos → DataFrame, con las columnas derivadas del Dashboard."""
    datos = {}
    for c in t["columnas"]:
        d = t["datos"][c]
        datos[c] = d if isinstance(d, list) else [d["d"][i] for i in d["i"]]
    df = pd.DataFrame(datos)
    return derivar(df)


def derivar(df):


    """Agrega el grupo de universidades (_GU) y la modalidad agrupada (_MG), igual que el Dashboard."""
    df["_GU"] = df["CODIGO_INSTITUCION_PADRE"].map(catalogos.grupo_universidad)   # grupos por institución padre
    df["_MG"] = df["MODALIDAD"].map(catalogos.modalidad_grupo)
    return df


def norm_js(s):
    """Igual que normalizar() del Dashboard: quita tildes y pasa a mayúsculas (conserva la Ñ como N)."""
    s = "" if s is None or (isinstance(s, float) and pd.isna(s)) else str(s)
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch)).upper()


C = leer_js("comun.js")
NOV = {t: decodificar(leer_js(f"{t}.js")) for t in ("nuevos", "modificados", "inactivos")}
for _t, _df in NOV.items():
    _df["FECHA_DE_OBTENCION"] = _df["FECHA_DE_OBTENCION"].astype(str)


@functools.lru_cache(maxsize=1)
def _historico_completo():
    """Histórico de Historico/ con la misma unificación de variantes de departamento y municipio del Dashboard."""
    h = historico_fuente.leer()
    h["FECHA_DE_OBTENCION"] = pd.to_datetime(h["FECHA_DE_OBTENCION"]).dt.strftime("%Y-%m-%d")
    for col in ("DEPARTAMENTO_OFERTA_PROGRAMA", "MUNICIPIO_OFERTA_PROGRAMA"):
        conteo = pd.concat([h[col]] + [NOV[t][col] for t in NOV if col in NOV[t]]).value_counts()
        canonica = {}
        for valor in conteo.index:   # de más a menos frecuente
            canonica.setdefault(re.sub(r"[^A-Z0-9Ñ]", "", str(valor)), valor)
        h[col] = h[col].map(lambda v: v if v is None or (isinstance(v, float) and pd.isna(v))
                            else canonica.get(re.sub(r"[^A-Z0-9Ñ]", "", str(v)), v))
    return derivar(h)


@functools.lru_cache(maxsize=None)
def historico(fecha):
    """Programas activos del histórico en la ejecución `fecha` (AAAA-MM-DD)."""
    h = _historico_completo()
    return h[h["FECHA_DE_OBTENCION"] == fecha]


CAMPOS = {
    "divisiones": "DIVISION_UNINORTE", "cine": "CINE_F_2013_AC_CAMPO_ESPECIFIC", "programas": "CODIGO_SNIES_DEL_PROGRAMA",
    "departamentos": "DEPARTAMENTO_OFERTA_PROGRAMA", "modalidades": "_MG", "sectores": "SECTOR",
    "cambios": "TIPO_CAMBIO_DETECTADO",
}
CAMPOS_BUSQUEDA = ["CODIGO_SNIES_DEL_PROGRAMA", "CODIGO_INSTITUCION"]
BUSCADOR = "Buscar por Código de Programa o Institución"
MAX_SERIES = 15
CINE_DEFECTO = 10


def texto_busqueda(v):
    """Texto de un código como lo ve el Dashboard (enteros sin decimales), normalizado como la búsqueda."""
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return norm_js(v)


def filtrar(df, filtro, busqueda=None, campos_busqueda=()):
    """
    Aplica la misma regla que el Dashboard. `filtro` = {'clave': valor}; valor: una opción (solo esa), ("excepto", v),
    ("varios", [v...]); para universidades: ("grupo", g), ("codigo", c), ("codigos", [c...]), ("excepto_codigos", [c...]).
    Los registros sin valor en el campo no cumplen un filtro con selección (igual que el Dashboard).
    """
    m = pd.Series(True, index=df.index)
    for clave, valor in filtro.items():
        if clave == "universidades":
            tipo, v = valor
            codigos = df["CODIGO_INSTITUCION_PADRE"].astype(float)
            if tipo == "grupo":
                m &= df["_GU"] == v
            elif tipo == "codigo":
                m &= codigos == float(v)
            elif tipo == "codigos":
                m &= codigos.isin([float(c) for c in v])
            else:   # excepto_codigos
                m &= ~codigos.isin([float(c) for c in v])
        else:
            col = df[CAMPOS[clave]]
            con_valor = col.notna() & (col.astype(str) != "")
            if isinstance(valor, tuple):
                tipo, v = valor
                m &= con_valor & ((col.astype(str) != str(v)) if tipo == "excepto" else col.astype(str).isin([str(x) for x in v]))
            else:
                m &= col.astype(str) == str(valor)
    if busqueda:
        q = norm_js(busqueda)
        hit = pd.Series(False, index=df.index)
        for c in campos_busqueda:
            hit |= df[c].map(texto_busqueda).str.contains(q, regex=False)
        m &= hit
    return df[m]


# ── Navegador ─────────────────────────────────────────────────────────────────

class _ManejadorSilencioso(http.server.SimpleHTTPRequestHandler):
    """Sirve docs/ sin escribir cada solicitud en la consola (solo se informan las fallas de las pruebas)."""

    def log_message(self, *args, **kwargs):

        """Sin registro de solicitudes en la consola."""
        pass

    def handle(self):

        """Ignora las conexiones que el navegador cierra antes de recibir la respuesta."""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):   # Chrome cierra conexiones abiertas al navegar: no es una falla
            pass


def servidor():


    """Servidor HTTP local de docs/ en un puerto libre."""
    manejador = functools.partial(_ManejadorSilencioso, directory=str(DOCS))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def navegador():


    """Chrome sin interfaz con registro de la consola del navegador."""
    o = webdriver.ChromeOptions()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1400,1000")
    o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=o)


class Pagina:
    """Página del Dashboard abierta en el navegador de pruebas: ejecuta JavaScript, abre filtros y lee su estado."""
    def __init__(self, d, base, nombre):
        self.d, self.nombre = d, nombre
        t0 = time.time()
        d.get(f"{base}/{nombre}")
        self.esperar_listo()
        self.carga = time.time() - t0

    def js(self, codigo, *args):
        """Ejecuta JavaScript en la página y retorna su resultado."""
        return self.d.execute_script(codigo, *args)

    def esperar_listo(self, limite=60):
        """Espera a que la página termine de cargar (filtros y tabla listos) o lanza TimeoutError."""
        fin = time.time() + limite
        while time.time() < fin:
            # Páginas con tabla (Nuevos, Modificados, Inactivos): lista la tabla. Inicio: lista la barra de filtros.
            if self.js("return !!document.querySelector('.filtros') && (document.getElementById('tabla') "
                       "? !!document.querySelector('#tabla [data-n]') && document.querySelector('#tabla [data-n]').textContent !== '' "
                       ": !!(document.querySelector('.conteo-filtro') || {}).textContent)"):
                time.sleep(0.15)
                return
            time.sleep(0.1)
        raise TimeoutError(f"{self.nombre}: la página no terminó de cargar")

    def boton(self, indice):
        """Botón del filtro desplegable en la posición `indice` de la barra de filtros."""
        return self.d.find_elements(By.CSS_SELECTOR, ".filtros .ms > .ms-btn")[indice]

    def abrir(self, indice):
        """Abre el filtro desplegable `indice` (cierra los demás)."""
        self.cerrar()
        self.boton(indice).click()

    def cerrar(self):
        """Cierra los desplegables abiertos con la tecla Escape."""
        self.d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)

    def errores(self):
        """Errores graves de la consola del navegador (se ignora el favicon)."""
        return [l["message"] for l in self.d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]

    def estado(self):
        """Valores visibles de la página: indicadores, tablas, conteos y datos de las gráficas."""
        return self.js("""
            const t = (id) => (document.getElementById(id) || {}).textContent || null;
            const num = (s) => s == null ? null : Number(String(s).replace(/\\./g, '').replace(/[^0-9-]/g, ''));
            const g = (id) => { const e = document.getElementById(id); return e && e.data ? e.data : null; };
            const ultimo = (tr) => tr.y[tr.y.length - 1];
            const out = {
              total: num(t('total')), activos: num(t('k-activos')), activos_sub: t('k-activos-s'), nuevos: num(t('k-nuevos')), modificados: num(t('k-modificados')),
              inactivos: num(t('k-inactivos')), tabla: num((document.querySelector('#tabla [data-n]') || {}).textContent),
              conteo: document.querySelector('.conteo-filtro').textContent };
            out.tabs = Object.fromEntries([...document.querySelectorAll('[data-n-tab]')].map(e => [e.dataset.nTab, num(e.textContent)]));
            out.tablas = Object.fromEntries(['nuevos', 'modificados', 'inactivos'].map(x => { const e = document.querySelector('#tabla-' + x + ' [data-n]'); return [x, e ? num(e.textContent) : null]; }));
            const ev = g('g-evolucion'); if (ev) out.evolucion = ultimo(ev[0]);
            const act = g('g-activos'); if (act) out.g_activos = ultimo(act[0]);
            const nov = g('g-novedades'); if (nov) out.g_novedades = Object.fromEntries(nov.map(tr => [tr.name.split(' · ')[0], ultimo(tr)]));
            const div = g('g-division'); if (div && div[0].y) out.suma_division = div.reduce((a, tr) => a + ultimo(tr), 0);
            const sec = g('g-sector'); if (sec) out.suma_sector = sec[0].values.reduce((a, b) => a + b, 0);
            const per = g('g-periodos'); if (per) out.suma_periodos = per[0].y.reduce((a, b) => a + b, 0);
            out.vacias = [...document.querySelectorAll('.grafica')].filter(e => !e.data).map(e => e.id);
            return out;""")


# ── Pruebas ───────────────────────────────────────────────────────────────────

def opciones_filtro(p, indice):

    """Valores de las opciones de un filtro de lista plana (en el orden mostrado)."""
    return p.js("""const ms = document.querySelectorAll('.filtros .ms')[arguments[0]];
                   return [...ms.querySelectorAll('.ms-ops input[value]')].map(i => i.value);""", indice)


def clic_opcion(p, indice, selector):


    """Abre el filtro `indice` y hace clic real sobre el control que cumple `selector`."""
    p.abrir(indice)
    el = p.d.find_elements(By.CSS_SELECTOR, ".filtros .ms")[indice].find_element(By.CSS_SELECTOR, selector)
    p.js("arguments[0].scrollIntoView({block: 'center'});", el)
    el.click()
    p.esperar_listo()


def todos_marcado(p, indice):


    """¿Está marcada la opción TODOS/TODAS del filtro `indice`?"""
    return p.js("return document.querySelectorAll('.filtros .ms')[arguments[0]].querySelector('[data-todos]').checked", indice)


def reset_filtros(p):


    """Restablece todos los filtros con el botón LIMPIAR FILTROS."""
    p.cerrar()
    p.d.find_element(By.CSS_SELECTOR, ".filtros .btn-limpiar").click()
    p.esperar_listo()


def rango(desde, hasta):
    """Fechas de ejecución entre `desde` y `hasta` (inclusive)."""
    return [f for f in C["fechas_ejecucion"] if desde <= f <= hasta]


def subtitulo_activos(fa, filtro, busqueda):
    """
    Texto esperado bajo el KPI de programas activos, calculado desde el histórico sin usar el código del Dashboard:
    variación frente a la ejecución inmediatamente anterior (aunque quede fuera del periodo filtrado) con el mismo
    formato del correo, o la fecha del corte si no existe una ejecución anterior.
    """
    if not fa:
        return "Sin ejecuciones en el periodo"
    todas = C["fechas_activos"]
    i = todas.index(fa[-1])
    fmt = lambda iso: f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"  # noqa: E731
    if i == 0:
        return f"Programas activos al {fmt(fa[-1])}"
    hoy = len(filtrar(historico(todas[i]), filtro, busqueda, CAMPOS_BUSQUEDA))
    antes = len(filtrar(historico(todas[i - 1]), filtro, busqueda, CAMPOS_BUSQUEDA))
    d = hoy - antes
    cifra = "Sin variación" if d == 0 else f"{'+' if d > 0 else '−'}{abs(d):,}".replace(",", ".")
    return f"{cifra} frente al {fmt(todas[i - 1])}"


def esperado_inicio(filtro, busqueda=None, fechas=None):
    """Valores esperados en Inicio; `fechas` = ejecuciones seleccionadas (None: todas)."""
    permitida = (lambda f: True) if fechas is None else set(fechas).__contains__
    fa = [f for f in C["fechas_activos"] if permitida(f)]
    fe = [f for f in C["fechas_ejecucion"] if permitida(f)]
    activos = len(filtrar(historico(fa[-1]), filtro, busqueda, CAMPOS_BUSQUEDA)) if fa else 0
    out = {"activos": activos, "subtitulo_activos": subtitulo_activos(fa, filtro, busqueda)}
    for t in NOV:
        df = NOV[t]
        if fechas is not None:
            df = df[df.FECHA_DE_OBTENCION.isin(list(fechas))]
        df = filtrar(df, filtro, busqueda, CAMPOS_BUSQUEDA)
        out[f"{t}_periodo"] = len(df)
        out[t] = int((df.FECHA_DE_OBTENCION == (fe[-1] if fe else "")).sum())
    return out


def esperado_detalle(tipo, filtro, busqueda=None, fechas=None):


    """Registros esperados en una página de detalle, calculados en Python desde Novedades/."""
    df = NOV[tipo]
    if fechas is not None:
        df = df[df.FECHA_DE_OBTENCION.isin(list(fechas))]
    return len(filtrar(df, filtro, busqueda, CAMPOS_BUSQUEDA))


def comparar_inicio(p, nombre, filtro, busqueda=None, fechas=None):


    """Compara KPIs, pestañas, tablas y gráficas de Inicio con el cálculo independiente en Python."""
    e, s = esperado_inicio(filtro, busqueda, fechas), p.estado()
    ok = verificar(f"inicio · {nombre} · KPI activos", s["activos"] == e["activos"], f"{s['activos']} vs {e['activos']}")
    ok &= verificar(f"inicio · {nombre} · variación del KPI activos", s["activos_sub"] == e["subtitulo_activos"],
                    f"{s['activos_sub']!r} vs {e['subtitulo_activos']!r}")
    for t in NOV:
        ok &= verificar(f"inicio · {nombre} · KPI {t}", s[t] == e[t], f"{s[t]} vs {e[t]}")
        ok &= verificar(f"inicio · {nombre} · tabla de {t} (pestaña y registros)",
                        s["tabs"][t] == e[f"{t}_periodo"] == s["tablas"][t], f"pestaña {s['tabs'][t]} · tabla {s['tablas'][t]} vs {e[f'{t}_periodo']}")
    if s.get("g_novedades"):
        ok &= verificar(f"inicio · {nombre} · gráfica novedades acumuladas",
                        [s["g_novedades"].get(k) for k in ("NUEVOS", "MODIFICADOS", "INACTIVOS")]
                        == [e["nuevos_periodo"], e["modificados_periodo"], e["inactivos_periodo"]],
                        f"{s['g_novedades']} vs {[e['nuevos_periodo'], e['modificados_periodo'], e['inactivos_periodo']]}")
    if e["activos"]:
        ok &= verificar(f"inicio · {nombre} · coherencia activos (línea, torta, división)",
                        s.get("g_activos") == s.get("suma_sector") == s.get("suma_division") == e["activos"],
                        f"línea {s.get('g_activos')} · torta {s.get('suma_sector')} · división {s.get('suma_division')}")
    return ok


def comparar_detalle(p, tipo, nombre, filtro, busqueda=None, fechas=None):


    """Compara total, tabla y gráficas de una página de detalle con el cálculo independiente en Python."""
    e, s = esperado_detalle(tipo, filtro, busqueda, fechas), p.estado()
    ok = verificar(f"{tipo} · {nombre} · TOTAL", s["total"] == e, f"{s['total']} vs {e}")
    ok &= verificar(f"{tipo} · {nombre} · tabla", s["tabla"] == e, f"{s['tabla']} vs {e}")
    if e:
        ok &= verificar(f"{tipo} · {nombre} · coherencia (evolución, división, torta)",
                        s.get("evolucion") == s.get("suma_division") == s.get("suma_sector") == e,
                        f"evolución {s.get('evolucion')} · división {s.get('suma_division')} · torta {s.get('suma_sector')}")
    return ok


def grupo_expandido(p, indice, gid):
    """Expande el grupo `gid` de un filtro jerárquico (si no lo está) y retorna su elemento."""
    ms = p.d.find_elements(By.CSS_SELECTOR, ".filtros .ms")[indice]
    p.abrir(indice)
    grupo = ms.find_element(By.CSS_SELECTOR, f'[data-g="{gid}"]').find_element(By.XPATH, "./ancestor::div[contains(@class,'ms-grupo')]")
    if p.js("const h = arguments[0].querySelector('.ms-hijos'); return !!h && h.hidden", grupo):
        grupo.find_element(By.CSS_SELECTOR, ".ms-expandir").click()
    return grupo


def probar_filtro_plano(p, comparar, indice, clave, muestra):
    """Lista plana con TODOS/TODAS sincronizada: solo una opción, exclusión de una opción y retorno a TODOS."""
    valores = opciones_filtro(p, indice)
    ordenadas = p.js("""const ms = document.querySelectorAll('.filtros .ms')[arguments[0]];
        const et = [...ms.querySelectorAll('.ms-ops .ms-op')].map(l => l.textContent.trim());
        const primero = ms.querySelector('.ms-panel > .ms-op, .ms-panel > input + .ms-op');
        return { alfabetico: et.every((v, i) => i === 0 || et[i - 1].localeCompare(v, 'es') <= 0),
                 todosPrimero: !!primero && primero.classList.contains('todos'), todasMarcadas: [...ms.querySelectorAll('.ms-ops input')].every(i => i.checked) };""", indice)
    verificar(f"{p.nombre} · filtro {clave} en orden alfabético con TODOS primero", ordenadas["alfabetico"] and ordenadas["todosPrimero"], str(ordenadas))
    verificar(f"{p.nombre} · filtro {clave} inicia con TODOS y todas las opciones marcadas", todos_marcado(p, indice) and ordenadas["todasMarcadas"], str(ordenadas))
    muestra_valores = valores[:2] + valores[-1:] if muestra else valores
    for v in muestra_valores:
        clic_opcion(p, indice, "[data-todos]")                      # TODOS desmarcado: ninguna opción
        sin = p.js("return [...document.querySelectorAll('.filtros .ms')[arguments[0]].querySelectorAll('.ms-ops input')].filter(i => i.checked).length", indice)
        clic_opcion(p, indice, f'.ms-ops input[value="{v}"]')
        comparar(f"{clave}={v}", {clave: v})
        verificar(f"{p.nombre} · {clave}={v} · sin TODOS y con una opción", sin == 0 and not todos_marcado(p, indice))
        clic_opcion(p, indice, "[data-todos]")                      # TODOS marcado: todas las opciones
        verificar(f"{p.nombre} · {clave} · marcar TODOS selecciona todas",
                  todos_marcado(p, indice) and p.js("return [...document.querySelectorAll('.filtros .ms')[arguments[0]].querySelectorAll('.ms-ops input')].every(i => i.checked)", indice))
    # Excluir una opción partiendo de TODOS
    v = valores[0]
    clic_opcion(p, indice, f'.ms-ops input[value="{v}"]')
    verificar(f"{p.nombre} · {clave} · desmarcar una opción desactiva TODOS", not todos_marcado(p, indice))
    comparar(f"{clave} excepto {v}", {clave: ("excepto", v)})
    clic_opcion(p, indice, f'.ms-ops input[value="{v}"]')
    verificar(f"{p.nombre} · {clave} · marcar de nuevo la última opción activa TODOS", todos_marcado(p, indice))
    comparar(f"{clave} tras volver a marcar", {})
    # Sin ninguna opción marcada no hay resultados
    clic_opcion(p, indice, "[data-todos]")
    verificar(f"{p.nombre} · {clave} · sin opciones marcadas", p.js("return document.querySelector('.conteo-filtro').textContent").startswith("0 "))
    reset_filtros(p)


def probar_filtro_universidades(p, comparar, indice, muestra):
    """Universidades: grupos, instituciones de cada grupo, búsqueda y sincronización de TODAS LAS UNIVERSIDADES."""
    cat = C["catalogos"]
    uninorte, mayor = str(cat["uninorte"]), [str(c) for c in cat["mayor_interes"]]
    otras = sorted(c for c in cat["instituciones"] if c != uninorte and c not in mayor)
    grupos = {"uninorte": catalogos.GRUPO_UNINORTE, "mayor": catalogos.GRUPO_MAYOR_INTERES, "otras": catalogos.GRUPO_OTRAS}
    verificar(f"{p.nombre} · universidades · grupos y búsqueda", p.js(
        "const ms = document.querySelectorAll('.filtros .ms')[arguments[0]]; return !!ms.querySelector('input[type=search]') && ms.querySelectorAll('.ms-grupo').length === 3", indice))
    for gid, g in grupos.items():
        clic_opcion(p, indice, "[data-todos]")
        clic_opcion(p, indice, f'[data-g="{gid}"]')
        comparar(f"universidades={g}", {"universidades": ("grupo", g)})
        verificar(f"{p.nombre} · universidades · grupo {gid} marcado y TODAS desmarcada",
                  p.js("const ms = document.querySelectorAll('.filtros .ms')[arguments[0]]; return ms.querySelector(`[data-g='${arguments[1]}']`).checked && !ms.querySelector('[data-todos]').checked", indice, gid))
        reset_filtros(p)
    # Instituciones individuales: universidades de mayor interés y una muestra de otras
    for gid, codigos in (("mayor", mayor[:3] if muestra else mayor), ("otras", otras[:2] + otras[len(otras) // 2: len(otras) // 2 + 1] + otras[-1:] if muestra else otras[::18])):
        for c in codigos:
            clic_opcion(p, indice, "[data-todos]")
            grupo_expandido(p, indice, gid)
            clic_opcion(p, indice, f'[data-v="{c}"]')
            estado = p.js("const ms = document.querySelectorAll('.filtros .ms')[arguments[0]]; const g = ms.querySelector(`[data-g='${arguments[1]}']`); return { indet: g.indeterminate, marcado: g.checked };", indice, gid)
            comparar(f"universidad={c}", {"universidades": ("codigo", c)})
            verificar(f"{p.nombre} · universidad {c} · el grupo queda parcial", estado["indet"] or estado["marcado"], str(estado))
            reset_filtros(p)
    # Excluir una universidad partiendo de TODAS y volver a marcarla
    clic_opcion(p, indice, '[data-g="uninorte"]')
    verificar(f"{p.nombre} · universidades · desmarcar Uninorte desactiva TODAS", not todos_marcado(p, indice))
    comparar("universidades excepto Uninorte", {"universidades": ("excepto_codigos", [uninorte])})
    clic_opcion(p, indice, '[data-g="uninorte"]')
    verificar(f"{p.nombre} · universidades · al completar todas se activa TODAS", todos_marcado(p, indice))
    comparar("universidades tras volver a marcar", {})
    # Selección de todos los grupos de a uno: TODAS se activa sola
    clic_opcion(p, indice, "[data-todos]")
    for gid in grupos:
        clic_opcion(p, indice, f'[data-g="{gid}"]')
    verificar(f"{p.nombre} · universidades · los tres grupos marcados activan TODAS", todos_marcado(p, indice))
    reset_filtros(p)
    # Búsqueda dentro del filtro
    p.abrir(indice)
    ms = p.d.find_elements(By.CSS_SELECTOR, ".filtros .ms")[indice]
    caja = ms.find_element(By.CSS_SELECTOR, "input[type=search]")
    caja.send_keys("norte")
    visibles = p.js("""const ms = document.querySelectorAll('.filtros .ms')[arguments[0]];
        return [...ms.querySelectorAll('.ms-op.hijo')].filter(l => l.style.display !== 'none').map(l => l.textContent.trim());""", indice)
    verificar(f"{p.nombre} · universidades · búsqueda 'norte'", bool(visibles) and all("NORTE" in v.upper() or "NORTE" in v.upper().replace("Ó", "O") for v in visibles), str(visibles[:5]))
    for _ in range(5):   # borra el texto con eventos de teclado (clear() no dispara el evento de búsqueda)
        caja.send_keys(Keys.BACKSPACE)
    p.cerrar()


def probar_filtro_cine(p, comparar, indice, muestra):
    """CINE jerárquico: campo amplio con sus campos específicos, mismo comportamiento que universidades."""
    jerarquia = C["catalogos"]["cine_jerarquia"]
    verificar(f"{p.nombre} · CINE · primer nivel = campos amplios", p.js(
        "const ms = document.querySelectorAll('.filtros .ms')[arguments[0]]; return [...ms.querySelectorAll('.ms-grupo > .ms-padre label')].map(l => l.textContent.trim());", indice)
        == [a for a, _ in jerarquia], "")
    amplios = list(enumerate(jerarquia))
    for i, (amplio, detallados) in (amplios[:2] if muestra else amplios):
        clic_opcion(p, indice, "[data-todos]")
        clic_opcion(p, indice, f'[data-g="a{i}"]')
        comparar(f"CINE amplio {amplio}", {"cine": ("varios", detallados)})
        for d in (detallados[:1] if muestra else detallados[::3]):
            reset_filtros(p)                                     # TODOS marcado: se desmarca para partir sin selección
            clic_opcion(p, indice, "[data-todos]")
            grupo_expandido(p, indice, f"a{i}")
            clic_opcion(p, indice, f'.ms-hijos [data-v="{d}"]')
            comparar(f"CINE detallado {d}", {"cine": d})
        reset_filtros(p)
    # Excluir un campo específico partiendo de TODOS: el campo amplio queda parcial
    amplio, detallados = jerarquia[0]
    grupo_expandido(p, indice, "a0")
    clic_opcion(p, indice, f'.ms-hijos [data-v="{detallados[0]}"]')
    estado = p.js("const ms = document.querySelectorAll('.filtros .ms')[arguments[0]]; const g = ms.querySelector(\"[data-g='a0']\"); return { indet: g.indeterminate, todos: ms.querySelector('[data-todos]').checked };", indice)
    verificar(f"{p.nombre} · CINE · excluir un detallado deja el amplio parcial y desactiva TODOS", estado["indet"] and not estado["todos"], str(estado))
    todos_detallados = [d for _, ds in jerarquia for d in ds]
    comparar(f"CINE excepto {detallados[0]}", {"cine": ("varios", [d for d in dict.fromkeys(todos_detallados) if d != detallados[0]])})
    clic_opcion(p, indice, f'.ms-hijos [data-v="{detallados[0]}"]')
    verificar(f"{p.nombre} · CINE · al volver a marcarlo se activa TODOS", todos_marcado(p, indice))
    reset_filtros(p)


def probar_filtro_programas(p, comparar, indice):
    """PROGRAMA ACADÉMICO: lista plana por CODIGO_SNIES_DEL_PROGRAMA con búsqueda, TODOS sincronizado y dirección por exclusión."""
    probar_filtro_plano(p, comparar, indice, "programas", True)
    opciones = p.js("""const ms = document.querySelectorAll('.filtros .ms')[arguments[0]];
        return [...ms.querySelectorAll('.ms-ops .ms-op')].map(l => [l.querySelector('input').value, l.textContent.trim()]);""", indice)
    base = NOV[p.tipo] if getattr(p, "tipo", None) else _historico_completo()
    esperados = base["CODIGO_SNIES_DEL_PROGRAMA"].dropna().astype(str).nunique()
    verificar(f"{p.nombre} · programas · una opción por CODIGO_SNIES_DEL_PROGRAMA", len(opciones) == esperados and len({o[0] for o in opciones}) == esperados,
              f"{len(opciones)} vs {esperados}")
    verificar(f"{p.nombre} · programas · etiqueta con programa, institución y código", all(o[1].endswith(f"({o[0]})") and " · " in o[1] for o in opciones), str(opciones[:2]))
    # Búsqueda por código y por nombre
    codigo = opciones[len(opciones) // 2][0]
    p.abrir(indice)
    ms = p.d.find_elements(By.CSS_SELECTOR, ".filtros .ms")[indice]
    caja = ms.find_element(By.CSS_SELECTOR, "input[type=search]")
    caja.send_keys(codigo)
    visibles = p.js("""const ms = document.querySelectorAll('.filtros .ms')[arguments[0]];
        return [...ms.querySelectorAll('.ms-ops .ms-op')].filter(l => l.style.display !== 'none').map(l => l.querySelector('input').value);""", indice)
    verificar(f"{p.nombre} · programas · búsqueda por código", codigo in visibles and all(codigo in v or True for v in visibles) and len(visibles) < len(opciones) // 10, f"{len(visibles)} visibles")
    for _ in range(len(codigo)):
        caja.send_keys(Keys.BACKSPACE)
    nombre = opciones[len(opciones) // 3][1].split(" · ")[0]
    caja.send_keys(nombre[:12])
    visibles = p.js("""const ms = document.querySelectorAll('.filtros .ms')[arguments[0]];
        return [...ms.querySelectorAll('.ms-ops .ms-op')].filter(l => l.style.display !== 'none').map(l => l.textContent.trim());""", indice)
    verificar(f"{p.nombre} · programas · búsqueda por nombre", bool(visibles) and all(norm_js(nombre[:12]) in norm_js(v) for v in visibles), f"{len(visibles)} visibles")
    for _ in range(12):
        caja.send_keys(Keys.BACKSPACE)
    p.cerrar()
    # Dirección: con casi todo marcado se escriben solo las excluidas ("!") y el estado se restaura al recargar
    reset_filtros(p)
    clic_opcion(p, indice, f'.ms-ops input[value="{codigo}"]')
    comparar("programas excepto uno", {"programas": ("excepto", codigo)})
    url = p.d.current_url
    verificar(f"{p.nombre} · programas · dirección por exclusión", "programa=%21" in url and len(url) < 300, url[-120:])
    antes = p.js("return document.querySelector('.conteo-filtro').textContent")
    p.d.refresh(); p.esperar_listo()
    verificar(f"{p.nombre} · programas · estado restaurado al recargar", p.js("return document.querySelector('.conteo-filtro').textContent") == antes
              and not todos_marcado(p, indice), antes)
    reset_filtros(p)


def probar_filtros(p, comparar, claves, muestra):


    """Prueba cada filtro con clics reales: opciones, exclusiones, TODOS/TODAS y filtros jerárquicos."""
    for indice, clave in enumerate(claves):
        if clave == "fechas":
            continue
        if clave == "universidades":
            probar_filtro_universidades(p, comparar, indice, muestra)
        elif clave == "cine":
            probar_filtro_cine(p, comparar, indice, muestra)
        elif clave == "programas":
            probar_filtro_programas(p, comparar, indice)
        else:
            probar_filtro_plano(p, comparar, indice, clave, muestra)
        verificar(f"{p.nombre} · filtro {clave} probado", True)


def clic_dia(p, iso):
    """Clic real sobre el día `iso` del calendario abierto (muestra primero su mes)."""
    dia = p.js("""const fp = document.querySelector('.fs-cuerpo > div')._flatpickr; fp.jumpToDate(arguments[0]);
        return [...fp.daysContainer.querySelectorAll('.flatpickr-day')].find(d => !d.classList.contains('prevMonthDay')
            && !d.classList.contains('nextMonthDay') && fp.formatDate(d.dateObj, 'Y-m-d') === arguments[0]) || null;""", iso)
    assert dia is not None, f"el calendario no muestra el día {iso}"
    p.js("arguments[0].scrollIntoView({block: 'center'});", dia)
    dia.click()
    p.esperar_listo()


def abrir_fechas(p, modo):


    """Abre el selector de fechas en el modo indicado (fecha, rango, mes o anio)."""
    p.cerrar()
    p.d.find_element(By.CSS_SELECTOR, ".fs-btn").click()
    p.d.find_element(By.CSS_SELECTOR, f'[data-modo="{modo}"]').click()


def clic_valor_fecha(p, selector):


    """Clic real sobre un mes o año del selector de fechas."""
    el = p.d.find_element(By.CSS_SELECTOR, selector)
    p.js("arguments[0].scrollIntoView({block: 'center'});", el)
    el.click()
    p.esperar_listo()


def etiqueta_fechas(p):


    """Texto del botón del selector de fechas."""
    return p.js("return document.querySelector('.fs-btn span').textContent")


def probar_fechas(p, comparar):
    """Fecha puntual, rango, mes y año con selección múltiple no contigua (clics reales sobre el selector)."""
    fe = C["fechas_ejecucion"]
    # FECHA: varias ejecuciones sin incluir las intermedias
    a, b, c = fe[-1], fe[-3], fe[-2]
    abrir_fechas(p, "fecha")
    clic_dia(p, a); clic_dia(p, b)
    comparar("fechas puntuales múltiples", {}, fechas=[a, b])
    verificar(f"{p.nombre} · fechas · etiqueta con varias fechas y sin la intermedia", etiqueta_fechas(p) == "2 FECHAS" and c not in [a, b], etiqueta_fechas(p))
    clic_dia(p, a)
    comparar("fecha puntual tras quitar una", {}, fechas=[b])
    verificar(f"{p.nombre} · fechas · quitar una fecha deja la otra", etiqueta_fechas(p) == f"{b[8:10]}/{b[5:7]}/{b[:4]}", etiqueta_fechas(p))
    clic_dia(p, b)
    comparar("fechas puntuales sin selección", {})
    verificar(f"{p.nombre} · fechas · sin fechas marcadas vuelve a TODAS LAS FECHAS", etiqueta_fechas(p) == "TODAS LAS FECHAS", etiqueta_fechas(p))
    # RANGO: dos clics sobre ejecuciones con días sin ejecución entre ambas (regresión del rango que no se completaba)
    ini, fin = fe[-6], fe[-2]
    abrir_fechas(p, "rango")
    clic_dia(p, ini); clic_dia(p, fin)
    verificar(f"{p.nombre} · rango · se completa con dos clics", " A " in etiqueta_fechas(p), etiqueta_fechas(p))
    comparar("rango entre dos ejecuciones", {}, fechas=rango(ini, fin))
    # RANGO con extremos que no son ejecuciones: incluye las ejecuciones intermedias
    p.d.find_element(By.CSS_SELECTOR, ".fs-quitar").click(); p.esperar_listo()
    dia_sin = next(f for f in (pd.Timestamp(fe[-6]) + pd.Timedelta(days=k) for k in range(1, 6)) if f.strftime("%Y-%m-%d") not in fe).strftime("%Y-%m-%d")
    abrir_fechas(p, "rango")
    clic_dia(p, dia_sin); clic_dia(p, fe[-1])
    comparar("rango con inicio sin ejecución", {}, fechas=rango(dia_sin, fe[-1]))
    p.d.find_element(By.CSS_SELECTOR, ".fs-quitar").click(); p.esperar_listo()
    # MES: dos meses sin el intermedio
    meses = sorted({f[:7] for f in fe})
    m1, m2 = meses[-5], meses[-3]
    abrir_fechas(p, "mes")
    clic_valor_fecha(p, f'[data-mes="{m1}"]'); clic_valor_fecha(p, f'[data-mes="{m2}"]')
    comparar("meses múltiples", {}, fechas=[f for f in fe if f[:7] in (m1, m2)])
    verificar(f"{p.nombre} · meses · etiqueta con varios meses", etiqueta_fechas(p) == "2 MESES", etiqueta_fechas(p))
    clic_valor_fecha(p, f'[data-mes="{m1}"]')
    comparar("mes tras quitar uno", {}, fechas=[f for f in fe if f[:7] == m2])
    p.d.find_element(By.CSS_SELECTOR, ".fs-quitar").click(); p.esperar_listo()
    # AÑO: dos años sin el intermedio
    anios = sorted({f[:4] for f in fe})
    abrir_fechas(p, "anio")
    clic_valor_fecha(p, f'[data-anio="{anios[0]}"]'); clic_valor_fecha(p, f'[data-anio="{anios[-1]}"]')
    comparar("años múltiples", {}, fechas=[f for f in fe if f[:4] in (anios[0], anios[-1])])
    verificar(f"{p.nombre} · años · etiqueta con varios años", etiqueta_fechas(p) == "2 AÑOS", etiqueta_fechas(p))
    # Un tipo de selección reemplaza al anterior
    abrir_fechas(p, "mes")
    clic_valor_fecha(p, f'[data-mes="{m2}"]')
    comparar("mes tras año (reemplaza la selección)", {}, fechas=[f for f in fe if f[:7] == m2])
    # Restablecer con la × y con el botón del panel
    p.d.find_element(By.CSS_SELECTOR, ".fs-quitar").click(); p.esperar_listo()
    verificar(f"{p.nombre} · restablecer fechas con ×", etiqueta_fechas(p) == "TODAS LAS FECHAS")
    comparar("tras restablecer fechas", {})
    abrir_fechas(p, "anio")
    clic_valor_fecha(p, f'[data-anio="{anios[0]}"]')
    p.d.find_element(By.CSS_SELECTOR, ".btn-reset").click(); p.esperar_listo()
    verificar(f"{p.nombre} · restablecer fechas con TODAS LAS FECHAS", etiqueta_fechas(p) == "TODAS LAS FECHAS")
    # Una fecha sin ejecución no debe poder seleccionarse
    abrir_fechas(p, "fecha")
    n = p.js(f"const f = document.querySelector('.fs-cuerpo > div')._flatpickr; f.setDate('{dia_sin}', false); return f.selectedDates.length;")
    verificar(f"{p.nombre} · fecha sin ejecución rechazada", n == 0, str(n))
    p.cerrar()


def probar_combinaciones(p, comparar, es_inicio):


    """Búsqueda por códigos combinada con otros filtros, y restablecimiento con LIMPIAR FILTROS."""
    # Búsqueda por código de institución (la de Uninorte) y por código SNIES de un programa
    codigo_prog = str(NOV["nuevos"]["CODIGO_SNIES_DEL_PROGRAMA"].iloc[0])
    for texto in (str(catalogos.UNINORTE), codigo_prog, codigo_prog[:3]):
        p.js("const i = document.querySelector('.buscar'); i.value = arguments[0]; i.dispatchEvent(new Event('input'));", texto)
        time.sleep(0.5); p.esperar_listo()
        comparar(f"búsqueda '{texto}'", {}, busqueda=texto)
    # La búsqueda ya no usa nombres ni modalidad: un nombre de programa no devuelve resultados
    p.js("const i = document.querySelector('.buscar'); i.value = 'ingenieria de sistemas'; i.dispatchEvent(new Event('input'));")
    time.sleep(0.5); p.esperar_listo()
    verificar(f"{p.nombre} · el buscador solo usa códigos de programa e institución",
              p.js("return document.querySelector('.conteo-filtro').textContent").startswith("0 "), p.js("return document.querySelector('.conteo-filtro').textContent"))
    p.js("const i = document.querySelector('.buscar'); i.value = arguments[0]; i.dispatchEvent(new Event('input'));", "17")
    time.sleep(0.5); p.esperar_listo()
    # combinación: grupo de mayor interés + sector privado + año 2026 + búsqueda
    clic_opcion(p, 0, "[data-todos]")
    clic_opcion(p, 0, '[data-g="mayor"]')
    sectores = [i for i, c in enumerate(p.claves) if c == "sectores"][0]
    clic_opcion(p, sectores, "[data-todos]")
    clic_opcion(p, sectores, '.ms-ops input[value="PRIVADO"]')
    abrir_fechas(p, "anio")
    clic_valor_fecha(p, '[data-anio="2026"]')
    comparar("combinación búsqueda + mayor interés + privado + 2026",
             {"universidades": ("grupo", catalogos.GRUPO_MAYOR_INTERES), "sectores": "PRIVADO"},
             busqueda="17", fechas=[f for f in C["fechas_ejecucion"] if f.startswith("2026")])
    reset_filtros(p)
    verificar(f"{p.nombre} · LIMPIAR FILTROS restablece todo",
              p.js("return document.querySelector('.buscar').value === '' && !document.querySelector('.ms-btn.con-valor') "
                   "&& [...document.querySelectorAll('.filtros .ms:not(.fs)')].every(m => m.querySelector('[data-todos]').checked)"))
    comparar("tras LIMPIAR FILTROS", {})


def probar_tabla(p, filas_ref):


    """Orden por cada columna según su tipo de dato y paginación de la tabla."""
    cols = p.js("return [...document.querySelectorAll('#tabla th')].map(t => t.dataset.c)")
    for c in cols:
        serie = filas_ref[c] if c in filas_ref else pd.Series(dtype=object)
        vals = serie.dropna()
        vals = vals[vals.astype(str) != ""]
        if vals.empty:
            continue
        es_fecha = c.startswith("FECHA_") or c in ("VIGENCIA_TRANSITORIA", "REACTIVADO_POSTERIORMENTE")
        p.js("document.querySelector(`#tabla th[data-c='${arguments[0]}']`).click()", c); time.sleep(0.25)
        primero_1 = p.js("const j=[...document.querySelectorAll('#tabla th')].map(t=>t.dataset.c).indexOf(arguments[0]); return document.querySelector('#tabla tbody tr').children[j].textContent", c)
        p.js("document.querySelector(`#tabla th[data-c='${arguments[0]}']`).click()", c); time.sleep(0.25)
        primero_2 = p.js("const j=[...document.querySelectorAll('#tabla th')].map(t=>t.dataset.c).indexOf(arguments[0]); return document.querySelector('#tabla tbody tr').children[j].textContent", c)
        if es_fecha:
            fechas = pd.to_datetime(vals.astype(str).str[:10], errors="coerce").dropna()
            fmt = lambda x: x.strftime("%d/%m/%Y")  # noqa: E731
            esperado = {fmt(fechas.max()), fmt(fechas.min())}
        else:
            texto = vals.astype(str).str.strip()
            if texto.str.fullmatch(r"\d{16,}").all():
                enteros = texto.map(int)  # códigos largos: comparación exacta (sin pérdida de precisión)
                esperado = {str(enteros.max()), str(enteros.min())}
            else:
                num = pd.to_numeric(texto.str.replace(",", "."), errors="coerce")
                largos = texto.str.replace(r"\D", "", regex=True).str.len() > 15
                if not (num.notna().all() and not texto.str.match(r"^0\d").any() and not largos.any()):
                    continue  # texto: el orden alfabético se valida aparte

                def f(x, c=c):
                    if c.startswith("COSTO_"):
                        return f"{round(x):,}".replace(",", ".")
                    if float(x).is_integer():
                        return str(int(x))
                    return f"{float(x):.4f}".rstrip("0").rstrip(".").replace(".", ",")
                esperado = {f(num.max()), f(num.min())}
        verificar(f"{p.nombre} · orden {c}", {primero_1, primero_2} == esperado, f"{primero_1}/{primero_2} vs {sorted(esperado)}")
    # Paginación
    p.js("document.querySelector('[data-p=\"1\"]').click()"); time.sleep(0.2)
    pag = p.js("return document.querySelector('[data-pag]').textContent")
    p.js("document.querySelector('[data-p=\"-1\"]').click()"); time.sleep(0.2)
    pag2 = p.js("return document.querySelector('[data-pag]').textContent")
    verificar(f"{p.nombre} · paginación", pag.startswith("PÁGINA 2") and pag2.startswith("PÁGINA 1"), f"{pag} / {pag2}")


def probar_exportacion(p):


    """El archivo que descarga la tabla tiene las mismas filas y columnas que la tabla."""
    r = p.d.execute_async_script("""const listo = arguments[arguments.length - 1];
      (async () => {
        let blob = null; const o1 = URL.createObjectURL, o2 = HTMLAnchorElement.prototype.click;
        URL.createObjectURL = (b) => { blob = b; return 'blob:x'; }; HTMLAnchorElement.prototype.click = function () {};
        document.querySelector('#tabla [data-pq]').click();
        for (let i = 0; i < 150 && !blob; i++) await new Promise(r => setTimeout(r, 100));
        URL.createObjectURL = o1; HTMLAnchorElement.prototype.click = o2;
        const hp = await import('hyparquet'); const buf = await blob.arrayBuffer();
        const filas = await hp.parquetReadObjects({ file: buf });
        const f = hp.parquetMetadata(buf).schema.find((s) => s.name === 'FECHA_DE_OBTENCION');
        listo({ filas: filas.length, columnas: Object.keys(filas[0] || {}).length,
                tipoFecha: f ? f.converted_type : null, esFecha: filas[0].FECHA_DE_OBTENCION instanceof Date && filas[0].FECHA_DE_OBTENCION.getUTCHours() === 0,
                tabla: Number(document.querySelector('#tabla [data-n]').textContent.replace(/\\./g, '').replace(/\\D/g, '')),
                cols_tabla: document.querySelectorAll('#tabla th').length });
      })().catch(e => listo({ error: String(e) }));""")
    verificar(f"{p.nombre} · exportación Parquet", r.get("filas") == r.get("tabla") and r.get("columnas") == r.get("cols_tabla"), json.dumps(r))
    verificar(f"{p.nombre} · exportación · FECHA_DE_OBTENCION como fecha sin hora (DATE)", r.get("tipoFecha") == "DATE" and r.get("esFecha") is True, json.dumps(r))


def probar_orden_leyendas(p):
    """Gráficas de líneas con varias series: leyenda de mayor a menor TOTAL (valor final de cada serie)."""
    info = p.js("""return [...document.querySelectorAll('.grafica')].filter(e => e.data && e.data.length > 1
                    && e.data.every(t => t.type === 'scatter' && t.mode === 'lines'))
                  .map(e => ({ id: e.id, totales: e.data.map(t => t.y[t.y.length - 1]) }));""")
    mal = [f'{i["id"]}:{i["totales"]}' for i in info if any(a < b for a, b in zip(i["totales"], i["totales"][1:]))]
    verificar(f"{p.nombre} · leyendas de mayor a menor ({len(info)} gráficas de líneas)", info and not mal, str(mal[:3]))


def probar_sin_punteadas(p):
    """Todas las líneas de las gráficas son continuas."""
    estilos = p.js("""return [...document.querySelectorAll('.grafica')].flatMap(e => (e.data || []).filter(t => t.type === 'scatter')
        .map(t => ({ id: e.id, dash: (t.line || {}).dash })));""")
    mal = [e for e in estilos if e["dash"] not in (None, "solid")]
    verificar(f"{p.nombre} · gráficas de líneas sin líneas punteadas ({len(estilos)} series)", bool(estilos) and not mal, str(mal[:3]))
    chips = p.js("return [...document.querySelectorAll('.chip i')].filter(i => i.style.border.includes('dashed')).length")
    verificar(f"{p.nombre} · los campos de CINE no usan muestras punteadas", chips == 0, str(chips))


def nombres_cine(p):


    """Nombres de las series de la gráfica de evolución por campo de CINE específico."""
    return p.js("return (document.getElementById('g-cine').data || []).map(t => t.name.split(' · ')[0])")


def chips_cine(p):


    """Campos de CINE mostrados como etiquetas bajo el control de la gráfica."""
    return p.js("return [...document.querySelectorAll('.chips .chip')].map(c => c.textContent.replace('×', '').trim())")


def volumen_cine(df):
    """Campos de CINE específico por volumen de registros (de mayor a menor, empates por nombre)."""
    conteo = df["CINE_F_2013_AC_CAMPO_ESPECIFIC"].value_counts()
    return sorted(conteo.index, key=lambda c: (-conteo[c], c)), conteo


def es_top(nombres, df, n):
    """¿`nombres` son n campos de los de mayor volumen? (los empates en el límite pueden resolverse de cualquier forma)."""
    orden, conteo = volumen_cine(df)
    corte = conteo[orden[min(n, len(orden)) - 1]]
    seguros = {c for c in orden if conteo[c] > corte}
    return len(nombres) == min(n, len(orden)) and seguros <= set(nombres) and all(conteo[c] >= corte for c in nombres)


def marcados(p, selector):
    """Campos de CINE marcados en un desplegable (el del filtro superior o el de la gráfica)."""
    return set(p.js("""const ms = document.querySelector(arguments[0]);
        return [...ms.querySelectorAll('[data-v]')].filter(c => c.checked).map(c => c.dataset.v);""", selector))


def texto_boton(p, selector):
    """Texto del botón de un desplegable."""
    return p.js("return document.querySelector(arguments[0] + ' > .ms-btn').textContent.trim()", selector)


def clic_cine_grafica(p, selector):
    """Clic real sobre un control del desplegable de CINE de la gráfica (lo abre si hace falta)."""
    ms = p.d.find_element(By.CSS_SELECTOR, "#cine-ctrl .ms")
    if "abierto" not in ms.get_attribute("class"):
        p.cerrar()
        ms.find_element(By.CSS_SELECTOR, ".ms-btn").click()
    el = ms.find_element(By.CSS_SELECTOR, selector)
    p.js("arguments[0].scrollIntoView({block: 'center'});", el)
    el.click()
    p.esperar_listo()


def expandir_cine_grafica(p, gid):
    """Expande un campo amplio del desplegable de la gráfica."""
    ms = p.d.find_element(By.CSS_SELECTOR, "#cine-ctrl .ms")
    if "abierto" not in ms.get_attribute("class"):
        p.cerrar()
        ms.find_element(By.CSS_SELECTOR, ".ms-btn").click()
    grupo = ms.find_element(By.CSS_SELECTOR, f'[data-g="{gid}"]').find_element(By.XPATH, "./ancestor::div[contains(@class,'ms-grupo')]")
    if p.js("const h = arguments[0].querySelector('.ms-hijos'); return !!h && h.hidden", grupo):
        grupo.find_element(By.CSS_SELECTOR, ".ms-expandir").click()
    return grupo


def total_pagina(p, tipo, filtro):
    """(esperado, mostrado) de la métrica principal de la página para un filtro."""
    if tipo is None:
        return esperado_inicio(filtro)["activos"], p.estado()["activos"]
    return esperado_detalle(tipo, filtro), p.estado()["total"]


def probar_cine_sync(p, tipo):
    """
    La gráfica de evolución por campo de CINE específico y el filtro superior comparten la misma selección (sincronización
    bidireccional, jerarquía amplio → específico, búsqueda y multiselección): con TODOS la gráfica muestra los 10 campos
    con más registros; con una selección, esos campos (máximo 15); todas las líneas continuas.
    """
    cine = [i for i, c in enumerate(p.claves) if c == "cine"][0]
    jerarquia = C["catalogos"]["cine_jerarquia"]
    todos_los_campos = {d for _, ds in jerarquia for d in ds}
    base = historico(C["fechas_activos"][-1]) if tipo is None else NOV[tipo]
    orden, conteo = volumen_cine(base)
    sup = lambda: set(p.js("""return [...document.querySelectorAll('.filtros .ms')[arguments[0]].querySelectorAll('[data-v]')]
        .filter(c => c.checked).map(c => c.dataset.v);""", cine))  # noqa: E731
    graf = lambda: marcados(p, "#cine-ctrl .ms")  # noqa: E731
    boton_sup = lambda: p.js("return document.querySelectorAll('.filtros .ms')[arguments[0]].querySelector('.ms-btn').textContent.trim()", cine)  # noqa: E731
    boton_graf = lambda: texto_boton(p, "#cine-ctrl .ms")  # noqa: E731
    # 1. TODOS: los 10 con más registros; ambos desplegables con todo marcado
    nombres = nombres_cine(p)
    verificar(f"{p.nombre} · CINE · TODOS muestra los {CINE_DEFECTO} campos con más registros", es_top(nombres, base, CINE_DEFECTO), f"{len(nombres)} series")
    chips = chips_cine(p)
    verificar(f"{p.nombre} · CINE · campos de la gráfica en orden alfabético",
              len(chips) == CINE_DEFECTO and p.js("const c = arguments[0]; return c.every((v, i) => i === 0 || c[i - 1].localeCompare(v, 'es') <= 0)", chips), str(chips[:3]))
    verificar(f"{p.nombre} · CINE · el desplegable de la gráfica es jerárquico y parte con todo marcado",
              graf() == todos_los_campos and p.js("return document.querySelectorAll('#cine-ctrl .ms-grupo').length") == len(jerarquia)
              and p.js("return !!document.querySelector('#cine-ctrl input[type=search]') && !document.querySelector('#cine-ctrl select')"), f"{len(graf())} marcados")
    verificar(f"{p.nombre} · CINE · ambos desplegables con el mismo texto", boton_sup() == boton_graf() == "TODOS LOS CAMPOS DE CINE ESPECÍFICO", f"{boton_sup()!r} · {boton_graf()!r}")
    # 2. Del filtro superior a la gráfica
    elegidos = [orden[0], orden[len(orden) // 2], orden[-1]]
    clic_opcion(p, cine, "[data-todos]")
    for d in elegidos:
        gid = f"a{[a for a, ds in jerarquia if d in ds] and [a for a, _ in jerarquia].index([a for a, ds in jerarquia if d in ds][0])}"
        grupo_expandido(p, cine, gid)
        clic_opcion(p, cine, f'.ms-hijos [data-v="{d}"]')
    verificar(f"{p.nombre} · CINE · filtro superior → gráfica: líneas y etiquetas", set(nombres_cine(p)) == set(elegidos) and set(chips_cine(p)) == set(elegidos),
              f"{nombres_cine(p)} vs {elegidos}")
    verificar(f"{p.nombre} · CINE · filtro superior → desplegable de la gráfica", graf() == set(elegidos) and boton_graf() == "3 CAMPOS CINE", f"{graf()} · {boton_graf()!r}")
    reset_filtros(p)
    # 3. De la gráfica al filtro superior y a toda la página (multiselección)
    elegidos2 = [orden[1], orden[2]]
    clic_cine_grafica(p, "[data-todos]")
    verificar(f"{p.nombre} · CINE · TODOS desmarcado en la gráfica quita todo del filtro superior y de la página",
              sup() == set() and graf() == set() and nombres_cine(p) == [] and boton_sup() == "SIN SELECCIÓN", f"{boton_sup()!r}")
    for d in elegidos2:
        gid = f"a{[a for a, _ in jerarquia].index([a for a, ds in jerarquia if d in ds][0])}"
        expandir_cine_grafica(p, gid)
        clic_cine_grafica(p, f'.ms-hijos [data-v="{d}"]')
    verificar(f"{p.nombre} · CINE · gráfica → filtro superior (multiselección)", sup() == set(elegidos2) == graf() and boton_sup() == "2 CAMPOS CINE", f"{sup()} · {boton_sup()!r}")
    verificar(f"{p.nombre} · CINE · gráfica → líneas y etiquetas", set(nombres_cine(p)) == set(elegidos2) == set(chips_cine(p)), str(nombres_cine(p)))
    esperado, mostrado = total_pagina(p, tipo, {"cine": ("varios", elegidos2)})
    verificar(f"{p.nombre} · CINE · la selección hecha en la gráfica filtra toda la página", esperado == mostrado, f"{mostrado} vs {esperado}")
    # La dirección de la página guarda la selección y la restaura al recargar
    p.d.refresh(); p.esperar_listo()
    verificar(f"{p.nombre} · CINE · selección restaurada al recargar en ambos desplegables", sup() == graf() == set(elegidos2), f"{sup()} · {graf()}")
    # 4. Expandir, contraer y búsqueda en campo amplio y específico
    expandir_cine_grafica(p, "a0")
    amplio, especificos = jerarquia[0]
    abierto = p.js("return !document.querySelector('#cine-ctrl .ms-grupo .ms-hijos').hidden")
    p.d.find_element(By.CSS_SELECTOR, "#cine-ctrl .ms-grupo .ms-expandir").click()
    cerrado = p.js("return document.querySelector('#cine-ctrl .ms-grupo .ms-hijos').hidden")
    verificar(f"{p.nombre} · CINE · expandir y contraer categorías en la gráfica", abierto and cerrado, f"{abierto} · {cerrado}")
    caja = p.d.find_element(By.CSS_SELECTOR, "#cine-ctrl input[type=search]")
    caja.send_keys(amplio[:8])
    grupos_visibles = p.js("return [...document.querySelectorAll('#cine-ctrl .ms-grupo')].filter(g => g.style.display !== 'none').map(g => g.querySelector('.ms-padre label, label').textContent.trim())")
    verificar(f"{p.nombre} · CINE · búsqueda por campo amplio", amplio in grupos_visibles and all(norm_js(amplio[:8]) in norm_js(g) or True for g in grupos_visibles), str(grupos_visibles[:3]))
    for _ in range(8):
        caja.send_keys(Keys.BACKSPACE)
    especifico = especificos[-1]
    caja.send_keys(especifico[:10])
    hijos = p.js("return [...document.querySelectorAll('#cine-ctrl .ms-op.hijo')].filter(l => l.style.display !== 'none' && !l.closest('.ms-hijos').hidden).map(l => l.textContent.trim())")
    verificar(f"{p.nombre} · CINE · búsqueda por campo específico", especifico in hijos and all(norm_js(especifico[:10]) in norm_js(h) for h in hijos), str(hijos[:3]))
    for _ in range(10):
        caja.send_keys(Keys.BACKSPACE)
    p.cerrar()
    # 5. La × de una etiqueta quita el campo de la selección compartida
    antes = set(nombres_cine(p))
    quitado = sorted(antes)[0]
    p.js("document.querySelector('.chips .chip button').click()"); time.sleep(0.4); p.esperar_listo()
    verificar(f"{p.nombre} · CINE · la × de la etiqueta quita el campo en el filtro y en la gráfica", sup() == antes - {quitado} == graf() and set(nombres_cine(p)) == antes - {quitado},
              f"{sup()} · {nombres_cine(p)}")
    reset_filtros(p)
    # 6. Con TODOS desmarcado y menos de 15 campos se muestran todos; con más de 15, los 15 con más registros
    clic_opcion(p, cine, "[data-todos]")
    posibles = []
    for i, (_, ds) in enumerate(jerarquia):
        if len(set(posibles)) > MAX_SERIES:
            break
        clic_opcion(p, cine, f'[data-g="a{i}"]')
        posibles += ds
    nombres = nombres_cine(p)
    verificar(f"{p.nombre} · CINE · con {len(set(posibles))} campos filtrados la gráfica muestra como máximo {MAX_SERIES}",
              len(set(posibles)) > MAX_SERIES and len(nombres) == MAX_SERIES and set(nombres) <= set(posibles), f"{len(nombres)} series de {len(set(posibles))}")
    reset_filtros(p)
    verificar(f"{p.nombre} · CINE · al restablecer vuelven los {CINE_DEFECTO} campos con más registros", es_top(nombres_cine(p), base, CINE_DEFECTO), str(len(nombres_cine(p))))


def probar_rendimiento(p):


    """Tiempo de carga y de aplicación de un filtro."""
    t0 = time.time(); clic_opcion(p, 0, '[data-g="otras"]'); dt_ = time.time() - t0
    reset_filtros(p)
    verificar(f"{p.nombre} · rendimiento", p.carga < 15 and dt_ < 5, f"carga inicial {p.carga:.1f}s · filtro {dt_:.2f}s")


def probar_inicio_sin_tabla(d, base):
    """Inicio no tiene tabla de programas activos ni descarga copias del histórico."""
    d.get(f"{base}/index.html?sin_tabla=1")
    fin = time.time() + 30
    while time.time() < fin and not d.execute_script("return !!(document.getElementById('g-activos') || {}).data"):
        time.sleep(0.1)
    time.sleep(1.5)
    sin_tabla = d.execute_script("return !document.getElementById('tabla') && !document.getElementById('t-tabla')")
    descargas = d.execute_script("return performance.getEntriesByType('resource').filter(e => e.name.includes('data/historico/')).length")
    verificar("inicio · sin tabla de programas activos ni descargas del histórico", sin_tabla and descargas == 0, f"tabla ausente {sin_tabla} · {descargas} descargas")
    # Librerías, tipografía y datos se sirven desde el propio sitio: ninguna solicitud a terceros
    for pagina in ("index.html", "modificados.html"):
        if pagina != "index.html":
            d.get(f"{base}/{pagina}"); time.sleep(2)
        externos = d.execute_script("return performance.getEntriesByType('resource').map(e => e.name).filter(u => !u.startsWith(location.origin))")
        listo = d.execute_script("return typeof Plotly !== 'undefined' && typeof flatpickr !== 'undefined' && !!document.querySelector('.grafica .main-svg')")
        verificar(f"{pagina} · sin solicitudes a terceros (Plotly y flatpickr locales)", not externos and listo, f"{externos[:3]} · librerías {listo}")


def consistencia_datos():


    """Los datos publicados en docs/data coinciden con los archivos fuente (Historico/ y Novedades/)."""
    h = historico_fuente.leer(["FECHA_DE_OBTENCION"])
    por_fecha = h["FECHA_DE_OBTENCION"].dt.strftime("%Y-%m-%d").value_counts().to_dict()
    verificar("datos · ejecuciones del histórico en el Dashboard", sorted(por_fecha) == C["fechas_activos"], f"{len(por_fecha)} vs {len(C['fechas_activos'])}")
    verificar("datos · sin copias del histórico publicadas (docs/data/historico)", not (DOCS / "data" / "historico").exists()
              and "historico_fechas" not in C)
    act = leer_js("activos.js")
    n = len(C["fechas_activos"]); conteo = [0] * n
    for rangos in act["rangos"]:
        for a, b in rangos:
            for i in range(a, b + 1):
                conteo[i] += 1
    verificar("datos · versiones de activos vs histórico", conteo == [por_fecha[f] for f in C["fechas_activos"]], "")
    for t in NOV:
        fuente = pd.read_parquet(ROOT / "Novedades" / f"{t.capitalize()}.parquet")
        verificar(f"datos · {t} (Novedades vs Dashboard)", len(fuente) == len(NOV[t]), f"{len(fuente)} vs {len(NOV[t])}")
    k = C["kpis"]; u = C["ultima_ejecucion"]
    verificar("datos · KPIs de la última ejecución",
              k["nuevos"] == int((NOV["nuevos"].FECHA_DE_OBTENCION == u).sum()) and k["inactivos"] == int((NOV["inactivos"].FECHA_DE_OBTENCION == u).sum())
              and k["modificados"] == int((NOV["modificados"].FECHA_DE_OBTENCION == u).sum()) and k["activos"] == por_fecha[C["fechas_activos"][-1]], json.dumps(k))
    # Catálogos del filtro de universidades y del filtro jerárquico de CINE: cubren todo lo que aparece en los datos
    cat = C["catalogos"]
    todo = historico_fuente.leer(["CODIGO_INSTITUCION", "CODIGO_INSTITUCION_PADRE", "CINE_F_2013_AC_CAMPO_ESPECIFIC"])
    codigos = set(todo["CODIGO_INSTITUCION_PADRE"].dropna().astype(int).astype(str))
    verificar("datos · catálogo de instituciones (por institución padre) cubre las del histórico", codigos <= set(cat["instituciones"]), str(sorted(codigos - set(cat["instituciones"]))[:5]))
    verificar("datos · el catálogo no lista las seccionales aparte", not (set(todo["CODIGO_INSTITUCION"].dropna().astype(int).astype(str)) - codigos) & set(cat["instituciones"]), "")
    especificos = {x for _, ds in cat["cine_jerarquia"] for x in ds}
    verificar("datos · jerarquía de CINE cubre los campos específicos del histórico y las novedades",
              set(todo["CINE_F_2013_AC_CAMPO_ESPECIFIC"].dropna()) | {x for t in NOV for x in NOV[t]["CINE_F_2013_AC_CAMPO_ESPECIFIC"].dropna()} <= especificos, "")
    verificar("datos · columnas de las tablas sin REACTIVADO_POSTERIORMENTE", all("REACTIVADO_POSTERIORMENTE" not in NOV[t].columns for t in NOV), "")


def probar_url(d, base):
    """Filtros en la dirección: carga, normalización, recarga, cambio de apartado, edición manual y limpieza."""
    ultima = C["ultima_ejecucion"]
    mes = ultima[:7]
    desde_mes, hasta_mes = f"{mes}-01", str((pd.Timestamp(f"{mes}-01") + pd.offsets.MonthEnd(0)).date())
    otras = ("grupo", catalogos.GRUPO_OTRAS)
    mod_mes = NOV["modificados"][NOV["modificados"].FECHA_DE_OBTENCION.str.startswith(mes)]
    cambio = mod_mes["TIPO_CAMBIO_DETECTADO"].value_counts().index[0]

    # Carga directa con filtros (incluido un valor inexistente, que debe descartarse)
    d.get("about:blank")
    q = Pagina(d, base, f"modificados.html#universidad=otras&sector=PRIVADO&mes={mes}&cambio={cambio.replace(' ', '+')}&division=NO_EXISTE")
    q.nombre = "url"
    filtro = {"universidades": otras, "sectores": "PRIVADO", "cambios": cambio}
    comparar_detalle(q, "modificados", "url · carga directa", filtro, fechas=rango(desde_mes, hasta_mes))
    verificar("url · valor inexistente descartado", "NO_EXISTE" not in d.current_url, d.current_url)
    verificar("url · selector de fechas restaurado", q.js("return document.querySelector('.fs-btn span').textContent").endswith(mes[:4]),
              q.js("return document.querySelector('.fs-btn span').textContent"))
    d.refresh(); q.esperar_listo()
    comparar_detalle(q, "modificados", "url · tras recargar", filtro, fechas=rango(desde_mes, hasta_mes))
    # Cambio de apartado: se conservan los filtros comunes, no el tipo de cambio (propio de Modificados)
    enlace = d.find_element(By.CSS_SELECTOR, '.nav a[href^="nuevos.html"]')
    href = enlace.get_attribute("href")
    verificar("url · menú conserva filtros comunes", "universidad=otras" in href and "sector=PRIVADO" in href and f"mes={mes}" in href, href)
    verificar("url · menú no arrastra el tipo de cambio", "cambio=" not in href, href)
    enlace.click(); q.esperar_listo()
    comparar_detalle(q, "nuevos", "url · tras cambiar de apartado", {"universidades": otras, "sectores": "PRIVADO"}, fechas=rango(desde_mes, hasta_mes))
    # Edición manual de la dirección en la misma página (evento hashchange)
    d.get(f"{base}/nuevos.html#fecha={ultima}"); time.sleep(0.5); q.esperar_listo()
    comparar_detalle(q, "nuevos", "url · edición manual", {}, fechas=[ultima])
    d.get(f"{base}/nuevos.html#fecha=2099-01-01&sector=INVALIDO"); time.sleep(0.5); q.esperar_listo()
    verificar("url · parámetros inválidos ignorados", d.current_url.endswith("nuevos.html"), d.current_url)
    comparar_detalle(q, "nuevos", "url · tras parámetros inválidos", {})
    # Inicio: rango + universidad, enlaces de las tarjetas, búsqueda y LIMPIAR FILTROS
    fe = C["fechas_ejecucion"]
    d.get("about:blank")
    p = Pagina(d, base, f"index.html#desde={fe[-5]}&hasta={fe[-1]}&universidad=uninorte")
    p.nombre = "url"
    uni = {"universidades": ("grupo", catalogos.GRUPO_UNINORTE)}
    comparar_inicio(p, "url · inicio con rango", uni, fechas=rango(fe[-5], fe[-1]))
    href = d.find_element(By.CSS_SELECTOR, ".kpi.k-nuevos").get_attribute("href")
    verificar("url · tarjeta KPI conserva filtros", "universidad=uninorte" in href and f"desde={fe[-5]}" in href, href)
    p.js("""const i = document.querySelector('.buscar'); i.value = 'derecho'; i.dispatchEvent(new Event('input'));""")
    time.sleep(0.5); p.esperar_listo()
    verificar("url · búsqueda guardada", "buscar=derecho" in d.current_url, d.current_url)
    reset_filtros(p)
    verificar("url · LIMPIAR FILTROS deja la dirección sin filtros", d.current_url.endswith("index.html"), d.current_url)
    # Selección múltiple en la dirección: varias fechas, meses y años, y un filtro con exclusiones
    a, b = fe[-1], fe[-3]
    d.get("about:blank")
    p = Pagina(d, base, f"index.html#fecha={a}&fecha={b}")
    p.nombre = "url"
    comparar_inicio(p, "url · varias fechas", {}, fechas=[a, b])
    meses = sorted({f[:7] for f in fe})
    d.get("about:blank")
    p = Pagina(d, base, f"index.html#mes={meses[-5]}&mes={meses[-3]}&anio=1999")
    p.nombre = "url"
    comparar_inicio(p, "url · varios meses", {}, fechas=[f for f in fe if f[:7] in (meses[-5], meses[-3])])
    anios = sorted({f[:4] for f in fe})
    d.get("about:blank")
    p = Pagina(d, base, f"index.html#anio={anios[0]}&anio={anios[-1]}")
    p.nombre = "url"
    comparar_inicio(p, "url · varios años", {}, fechas=[f for f in fe if f[:4] in (anios[0], anios[-1])])
    d.get("about:blank")
    p = Pagina(d, base, "index.html#universidad=uninorte&universidad=mayor")
    p.nombre = "url"
    comparar_inicio(p, "url · grupos de universidades",
                    {"universidades": ("codigos", [catalogos.UNINORTE, *catalogos.MAYOR_INTERES])})
    verificar("url · errores de consola", not p.errores(), str(p.errores()[:3]))


def probar_fecha_unica(d, base):
    """
    Una sola fecha (enlaces del correo: #fecha=AAAA-MM-DD): las gráficas de línea muestran el punto y el eje indica
    esa fecha. En Inicio, un periodo sin descargas de activos no debe mostrar la fecha de la última descarga.
    """
    ultima = C["ultima_ejecucion"]
    for pagina, grafica in (("nuevos.html", "g-evolucion"), ("modificados.html", "g-evolucion"), ("index.html", "g-activos")):
        d.get("about:blank")
        q = Pagina(d, base, f"{pagina}#fecha={ultima}")
        info = q.js("const g = document.getElementById(arguments[0]); return g && g.data ? "
                    "{modo: g.data[0].mode, puntos: g.data[0].x.length, ticks: g.layout.xaxis.ticktext} : null", grafica)
        verificar(f"fecha única · {pagina} · el punto se dibuja",
                  bool(info) and info["puntos"] == 1 and "markers" in info["modo"], json.dumps(info))
        verificar(f"fecha única · {pagina} · el eje muestra la fecha",
                  bool(info) and info["ticks"] == [f"{ultima[8:10]}/{ultima[5:7]}/{ultima[:4]}"], json.dumps(info))
        verificar(f"fecha única · {pagina} · errores de consola", not q.errores(), str(q.errores()[:3]))
    solo_novedades = sorted(set(C["fechas_ejecucion"]) - set(C["fechas_activos"]))
    if solo_novedades:
        d.get("about:blank")
        q = Pagina(d, base, f"index.html#fecha={solo_novedades[0]}")
        fechas = q.js("return [...document.querySelectorAll('[data-fecha-corte]')].map(e => e.textContent)")
        verificar("periodo sin descargas de activos · no se muestra la fecha de otra descarga",
                  fechas and all(f == "–" for f in fechas), str(fechas))


def probar_consistencia_visual(d, base):
    """Etiquetas y estados interactivos homogéneos: la tabla usa las mismas etiquetas que el filtro y el correo; todos los botones reaccionan al cursor."""
    from selenium.webdriver.common.action_chains import ActionChains
    d.get("about:blank")
    q = Pagina(d, base, "modificados.html")
    etiquetas = set(C["catalogos"]["tipos_cambio_etiqueta"].values())
    celdas = q.js("const j = [...document.querySelectorAll('#tabla th')].findIndex(t => t.dataset.c === 'TIPO_CAMBIO_DETECTADO');"
                  "return [...new Set([...document.querySelectorAll('#tabla tbody tr')].map(tr => tr.children[j].textContent))];")
    verificar("consistencia · tipo de cambio de la tabla con la etiqueta del filtro y del correo", bool(celdas) and set(celdas) <= etiquetas, str(celdas))
    for nombre, selector in (("filtro desplegable", ".filtros .ms-btn"), ("botón", ".filtros .btn-limpiar"), ("descarga", "#tabla [data-pq]")):
        el = q.d.find_element(By.CSS_SELECTOR, selector)
        q.js("arguments[0].scrollIntoView({block: 'center'});", el)
        antes = q.js("return getComputedStyle(arguments[0]).backgroundColor", el)
        ActionChains(d).move_to_element(el).perform(); time.sleep(0.15)
        despues = q.js("return getComputedStyle(arguments[0]).backgroundColor", el)
        verificar(f"consistencia · {nombre} cambia de aspecto al pasar el cursor", antes != despues, f"{antes} → {despues}")
    verificar("consistencia · errores de consola", not q.errores(), str(q.errores()[:3]))


def probar_movil(d, base):
    """Pantalla angosta (390 px): leyendas debajo del gráfico y etiquetas del eje X sin superposición."""
    d.set_window_size(390, 900)
    try:
        for pagina in ("index.html", "modificados.html"):
            d.get("about:blank")
            q = Pagina(d, base, pagina)
            info = q.js("""return [...document.querySelectorAll('.grafica')].filter(e => e.data && e.layout && e.layout.xaxis && e.layout.xaxis.tickvals)
                .map(e => ({ id: e.id, varias: e.data.length > 1 && e.layout.showlegend, orientacion: (e.layout.legend || {}).orientation,
                             etiquetas: e.layout.xaxis.tickvals.length, ancho: e._fullLayout._size.w,
                             desborde: document.documentElement.scrollWidth > document.documentElement.clientWidth }));""")
            mal_leyenda = [i["id"] for i in info if i["varias"] and i["orientacion"] != "h"]
            mal_eje = [f'{i["id"]}:{i["etiquetas"]}' for i in info if i["etiquetas"] > max(2, i["ancho"] // 40)]
            verificar(f"móvil · {pagina} · leyendas debajo del gráfico", not mal_leyenda, str(mal_leyenda))
            verificar(f"móvil · {pagina} · etiquetas del eje sin superposición", not mal_eje, str(mal_eje))
            verificar(f"móvil · {pagina} · sin desplazamiento horizontal", not any(i["desborde"] for i in info))
            verificar(f"móvil · {pagina} · errores de consola", not q.errores(), str(q.errores()[:3]))
    finally:
        d.set_window_size(1400, 1000)


def probar_filtros_plegables(d, base):
    """Pantalla angosta: los filtros se pliegan tras el botón FILTROS (con el número de filtros activos); en escritorio no hay botón."""
    estado = """const t = document.querySelector('.filtros-toggle'), ms = [...document.querySelectorAll('.filtros .ms')];
        const visible = (e) => getComputedStyle(e).display !== 'none';
        return { toggle: visible(t), texto: t.textContent, expandido: t.getAttribute('aria-expanded'), ms: ms.filter(visible).length, total: ms.length,
                 buscador: visible(document.querySelector('.buscar')), conteo: visible(document.querySelector('.conteo-filtro')),
                 limpiar: visible(document.querySelector('.btn-limpiar')) };"""
    for pagina in ("index.html", "modificados.html"):
        d.set_window_size(1400, 1000)
        d.get("about:blank")
        q = Pagina(d, base, pagina)
        e = q.js(estado)
        verificar(f"filtros plegables · {pagina} · escritorio: sin botón y con todos los filtros", not e["toggle"] and e["ms"] == e["total"] and e["limpiar"], json.dumps(e))
        d.set_window_size(390, 900)
        try:
            time.sleep(0.4)
            e = q.js(estado)
            verificar(f"filtros plegables · {pagina} · móvil: plegados, con botón, buscador y conteo visibles",
                      e["toggle"] and e["ms"] == 0 and not e["limpiar"] and e["buscador"] and e["conteo"] and e["expandido"] == "false" and e["texto"] == "FILTROS", json.dumps(e))
            q.d.find_element(By.CSS_SELECTOR, ".filtros-toggle").click(); time.sleep(0.3)
            e = q.js(estado)
            verificar(f"filtros plegables · {pagina} · móvil: el botón despliega todos los filtros", e["ms"] == e["total"] and e["limpiar"] and e["expandido"] == "true", json.dumps(e))
            # Un filtro aplicado desde el panel desplegado: el botón informa 1 filtro activo y la página se actualiza
            sec = q.js("return [...document.querySelectorAll('.filtros .ms > .ms-btn')].findIndex(b => b.textContent.includes('SECTORES'))")
            antes = q.js("return document.querySelector('.conteo-filtro').textContent")
            q.d.find_elements(By.CSS_SELECTOR, ".filtros .ms > .ms-btn")[sec].click(); time.sleep(0.2)
            op = q.d.find_element(By.CSS_SELECTOR, '.filtros .ms.abierto .ms-ops input[value="PRIVADO"]')
            q.js("arguments[0].scrollIntoView({block: 'center'});", op)
            q.js("document.querySelector('.filtros .ms.abierto [data-todos]').click()"); time.sleep(0.2)
            op.click(); q.esperar_listo()
            despues = q.js("return document.querySelector('.conteo-filtro').textContent")
            e = q.js(estado)
            verificar(f"filtros plegables · {pagina} · móvil: el botón muestra el número de filtros activos", e["texto"] == "FILTROS · 1" and despues != antes, f"{e['texto']!r} · {antes!r} → {despues!r}")
            q.d.find_element(By.CSS_SELECTOR, ".filtros-toggle").click(); time.sleep(0.2)        # plegar de nuevo
            e = q.js(estado)
            verificar(f"filtros plegables · {pagina} · móvil: al plegar conserva el filtro y el conteo", e["ms"] == 0 and e["conteo"] and e["texto"] == "FILTROS · 1", json.dumps(e))
            q.d.find_element(By.CSS_SELECTOR, ".filtros-toggle").click(); time.sleep(0.2)
            q.d.find_element(By.CSS_SELECTOR, ".filtros .btn-limpiar").click(); q.esperar_listo()
            verificar(f"filtros plegables · {pagina} · móvil: LIMPIAR FILTROS restablece el botón y el conteo",
                      q.js(estado)["texto"] == "FILTROS" and q.js("return document.querySelector('.conteo-filtro').textContent") == antes, q.js(estado)["texto"])
            verificar(f"filtros plegables · {pagina} · errores de consola", not q.errores(), str(q.errores()[:3]))
        finally:
            d.set_window_size(1400, 1000)


def probar_politica_de_contenido(d, base):
    """La política de seguridad está activa: bloquea scripts en línea y externos, y el Dashboard funciona sin violaciones."""
    for pagina in ("index.html", "modificados.html"):
        d.get("about:blank")
        q = Pagina(d, base, pagina)
        registro = d.get_log("browser")   # antes de provocar las violaciones de la prueba
        carga = [l["message"] for l in registro if "Content Security Policy" in l["message"]]
        errores = [l["message"] for l in registro if l["level"] == "SEVERE" and "favicon" not in l["message"]]
        res = d.execute_async_script("""const listo = arguments[arguments.length - 1]; let n = 0;
            document.addEventListener('securitypolicyviolation', () => n++);
            const a = document.createElement('script'); a.textContent = 'window.__csp = 1'; document.body.appendChild(a);
            const b = document.createElement('script'); b.src = 'https://example.com/x.js'; document.body.appendChild(b);
            setTimeout(() => listo({ violaciones: n, ejecuto: window.__csp === 1 }), 600);""")
        verificar(f"política de contenido · {pagina} · sin violaciones al cargar ni al usar el Dashboard", not carga and not errores, str((carga + errores)[:2]))
        verificar(f"política de contenido · {pagina} · bloquea un script en línea y uno externo", res == {"violaciones": 2, "ejecuto": False}, json.dumps(res))
        d.get_log("browser")   # descarta los registros de las violaciones provocadas, para que no pasen a la página siguiente


def tipografia_local():
    """El Dashboard no carga recursos de Google Fonts: la tipografía se sirve desde docs/assets/fuentes."""
    paginas = [(DOCS / f"{n}.html").read_text(encoding="utf-8") for n in ("index", "nuevos", "modificados", "inactivos")]
    css = (DOCS / "assets" / "estilos.css").read_text(encoding="utf-8")
    verificar("tipografía · sin Google Fonts", not any("googleapis" in t or "gstatic" in t for t in paginas + [css]))
    fuentes = [DOCS / "assets" / "fuentes" / n for n in ("montserrat-latin.woff2", "montserrat-latin-ext.woff2", "OFL.txt")]
    verificar("tipografía · archivos y licencia presentes", all(f.exists() for f in fuentes))
    verificar("tipografía · formato woff2", all(f.read_bytes()[:4] == b"wOF2" for f in fuentes[:2]))


COLUMNAS_INICIALES = {
    "nuevos": ["FECHA_DE_OBTENCION", "NOMBRE_INSTITUCION", "NOMBRE_DEL_PROGRAMA", "DEPARTAMENTO_OFERTA_PROGRAMA", "MUNICIPIO_OFERTA_PROGRAMA",
               "MODALIDAD", "TIPO_NOVEDAD", "NUMERO_CREDITOS", "NUMERO_PERIODOS_DE_DURACION", "PERIODICIDAD", "COSTO_MATRICULA_ESTUD_NUEVOS", "DIVISION_UNINORTE"],
    "modificados": ["FECHA_DE_OBTENCION", "NOMBRE_INSTITUCION", "NOMBRE_DEL_PROGRAMA", "DEPARTAMENTO_OFERTA_PROGRAMA", "MUNICIPIO_OFERTA_PROGRAMA",
                    "MODALIDAD", "TIPO_CAMBIO_DETECTADO", "CAMBIO_DETECTADO", "NUMERO_CREDITOS", "NUMERO_PERIODOS_DE_DURACION", "PERIODICIDAD",
                    "COSTO_MATRICULA_ESTUD_NUEVOS", "DIVISION_UNINORTE"],
    "inactivos": ["FECHA_DE_OBTENCION", "NOMBRE_INSTITUCION", "NOMBRE_DEL_PROGRAMA", "DEPARTAMENTO_OFERTA_PROGRAMA", "MUNICIPIO_OFERTA_PROGRAMA",
                  "MODALIDAD", "NUMERO_CREDITOS", "NUMERO_PERIODOS_DE_DURACION", "PERIODICIDAD", "COSTO_MATRICULA_ESTUD_NUEVOS", "DIVISION_UNINORTE"],
}


def titulos_esperados(pagina):
    """Títulos de las gráficas y tablas de cada página, en el orden de presentación acordado."""
    if pagina == "index":
        return ["EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS",
                "EVOLUCIÓN DE PROGRAMAS ACADÉMICOS NUEVOS, MODIFICADOS E INACTIVOS",
                "PROGRAMAS ACADÉMICOS ACTIVOS POR SECTOR",
                "EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR SECTOR",
                "DEPARTAMENTOS CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS ACTIVOS",
                "UNIVERSIDADES CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS ACTIVOS",
                "EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR MODALIDAD",
                "PROGRAMAS ACADÉMICOS ACTIVOS POR NÚMERO DE PERIODOS DE DURACIÓN",
                "EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR CAMPO DE CINE ESPECÍFICO",
                "EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR DIVISIÓN DE UNINORTE",
                "REGISTROS DE PROGRAMAS ACADÉMICOS NUEVOS, MODIFICADOS E INACTIVOS"]
    N = pagina.upper()
    t = [f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N}"]
    if pagina == "modificados":
        t.append("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS MODIFICADOS POR TIPO DE CAMBIO")
    t += [f"PROGRAMAS ACADÉMICOS {N} POR SECTOR", f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR SECTOR",
          f"DEPARTAMENTOS CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS {N}", f"UNIVERSIDADES CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS {N}",
          f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR MODALIDAD", f"PROGRAMAS ACADÉMICOS {N} POR NÚMERO DE PERIODOS DE DURACIÓN",
          f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR CAMPO DE CINE ESPECÍFICO", f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR DIVISIÓN DE UNINORTE"]
    if pagina == "modificados":
        t += ["TRANSICIONES DE MODALIDAD: ANTERIOR → NUEVA", "AUMENTOS Y REDUCCIONES DE NÚMERO DE CRÉDITOS EN PROGRAMAS ACADÉMICOS",
              "AUMENTOS Y REDUCCIONES DE NÚMERO DE PERIODOS DE DURACIÓN EN PROGRAMAS ACADÉMICOS"]
    t.append(f"REGISTROS DE PROGRAMAS ACADÉMICOS {N}")
    return t


NEGRO, VERDE, AMARILLO, OCRE, ROJO = "#151515", "#1E8E5A", "#FFCC00", "#B78B1E", "#960A11"


def probar_estructura(d, base):
    """Títulos y orden de las tarjetas, subtítulo, buscador, botón DESCARGAR, columnas de las tablas, colores y gráficas retiradas."""
    for pagina in ("index", "nuevos", "modificados", "inactivos"):
        d.get("about:blank")
        q = Pagina(d, base, f"{pagina}.html")
        info = q.js("""const g = (id) => document.getElementById(id);
            return { titulos: [...document.querySelectorAll('.tarjeta-tit')].map(t => t.textContent),
              sub: (document.querySelector('.enc-sub') || {}).textContent, buscador: document.querySelector('.buscar').placeholder,
              descargas: [...document.querySelectorAll('[data-pq]')].map(b => b.textContent.trim()),
              texto: document.body.innerText, ids: [...document.querySelectorAll('.grafica')].map(e => e.id),
              logos: [...document.querySelectorAll('img')].map(i => [i.className, i.alt, i.complete && i.naturalWidth > 0, i.getAttribute('src'), Math.round(i.getBoundingClientRect().height)]) };""")
        verificar(f"estructura · {pagina} · títulos y orden de las tarjetas", info["titulos"] == titulos_esperados(pagina),
                  str([t for t in info["titulos"] if t not in titulos_esperados(pagina)][:3]) + str([t for t in titulos_esperados(pagina) if t not in info["titulos"]][:3]))
        verificar(f"estructura · {pagina} · subtítulo bajo el título principal", info["sub"] == "Programas Académicos Universitarios de Pregrado en Colombia", str(info["sub"]))
        verificar(f"estructura · {pagina} · texto del buscador", info["buscador"] == BUSCADOR, info["buscador"])
        verificar(f"estructura · {pagina} · botón DESCARGAR", bool(info["descargas"]) and all(t == "DESCARGAR" for t in info["descargas"]) and "PARQUET" not in info["texto"].upper(), str(info["descargas"]))
        verificar(f"estructura · {pagina} · sin 'Variación promedio' ni gráficas retiradas",
                  "Variación promedio" not in info["texto"] and not {"g-antiguedad", "g-balance"} & set(info["ids"]) and "ANTIGÜEDAD" not in info["texto"].upper()
                  and "BALANCE NETO" not in info["texto"].upper(), "")
        if pagina != "index":
            cols = q.js("return [...document.querySelectorAll('#tabla th')].map(t => t.dataset.c)")
            esperadas = COLUMNAS_INICIALES[pagina]
            verificar(f"estructura · {pagina} · orden de columnas de la tabla", cols[:len(esperadas)] == esperadas and "REACTIVADO_POSTERIORMENTE" not in cols, str(cols[:14]))
            fuente = [c for c in pd.read_parquet(ROOT / "Novedades" / f"{pagina.capitalize()}.parquet").columns if c not in esperadas]
            verificar(f"estructura · {pagina} · las demás columnas conservan el orden del archivo", cols[len(esperadas):] == fuente, str(cols[len(esperadas):][:3]))
        logos = {l[0]: l for l in info["logos"]}
        verificar(f"marca · {pagina} · logotipo blanco en el encabezado y principal en el pie, con texto alternativo y carga correcta",
                  set(logos) == {"enc-logo", "pie-logo"} and all(l[1] == "Universidad del Norte" and l[2] for l in logos.values())
                  and logos["enc-logo"][3].endswith("logo-uninorte-blanco.png") and logos["pie-logo"][3].endswith("logo-uninorte-principal.png"), str(info["logos"]))
        icono = q.js("const l = document.querySelector('link[rel=icon]'); return l ? l.getAttribute('href') : null")
        estado = q.js("const l = document.querySelector('link[rel=icon]'); const x = new XMLHttpRequest(); x.open('GET', l.href, false); x.send(); return [x.status, x.responseText.length]") if icono else None
        verificar(f"marca · {pagina} · favicon institucional enlazado y disponible", icono == "assets/img/favicon-uninorte.ico" and estado and estado[0] == 200, f"{icono} {estado}")
        verificar(f"marca · {pagina} · el logotipo del encabezado no altera la altura del encabezado", logos["enc-logo"][4] == 68, str(logos["enc-logo"]))
        verificar(f"estructura · {pagina} · errores de consola", not q.errores(), str(q.errores()[:3]))

    # Colores por apartado
    def colores(pagina, js):
        d.get("about:blank")
        q = Pagina(d, base, f"{pagina}.html")
        return q.js(js)
    g = "const g = (id) => document.getElementById(id).data;"
    c = colores("index", g + "return { activos: g('g-activos')[0].line.color, deptos: g('g-deptos')[0].marker.color, periodos: g('g-periodos')[0].marker.color, sector: g('g-sector')[0].marker.colors, sectorEvol: g('g-sector-evol').map(t => t.line.color) };")
    verificar("colores · inicio · activos, departamentos y periodos en negro", c["activos"] == c["deptos"] == c["periodos"] == NEGRO, str(c))
    verificar("colores · inicio · sector en rojo y negro", set(c["sector"]) == {ROJO, NEGRO} and set(c["sectorEvol"]) == {ROJO, NEGRO}, str(c))
    for pagina, color in (("nuevos", VERDE), ("modificados", AMARILLO)):
        c = colores(pagina, g + "return { sector: g('g-sector')[0].marker.colors, sectorEvol: g('g-sector-evol').map(t => t.line.color), univ: g('g-instituciones')[0].marker.color, deptos: g('g-deptos')[0].marker.color };")
        linea = OCRE if color == AMARILLO else color   # en líneas, Modificados usa el ocre (el amarillo casi no se ve sobre blanco)
        verificar(f"colores · {pagina} · sector y universidades en {color}", set(c["sector"]) == {color, NEGRO} and set(c["sectorEvol"]) == {linea, NEGRO} and c["univ"] == color == c["deptos"], str(c))
    c = colores("modificados", g + "return { evol: g('g-evolucion').map(t => t.line.color), tipos: g('g-tipos').map(t => t.line.color) };")
    verificar("colores · modificados · la línea de evolución usa ocre, no amarillo", c["evol"] == [OCRE] and AMARILLO not in c["tipos"] and OCRE not in c["tipos"], str(c))
    c = colores("modificados", g + "return { h: g('g-deptos')[0].marker.line, v: g('g-periodos')[0].marker.line, pie: g('g-sector')[0].marker.line.color, red: g('g-creditos')[1].marker.line };")
    verificar("colores · modificados · las barras y el sector amarillos llevan contorno ocre", c["h"] == {"color": OCRE, "width": 1} == c["v"] == c["red"]
              and sorted(c["pie"]) == sorted([OCRE, "#FFFFFF"]), str(c))
    c = colores("nuevos", g + "return { h: g('g-deptos')[0].marker.line.width, v: g('g-periodos')[0].marker.line.width };")
    verificar("colores · nuevos · las barras de otros colores no llevan contorno", c["h"] == 0 == c["v"], str(c))
    c = colores("index", g + "return g('g-novedades').map(t => t.line.color);")
    verificar("colores · inicio · novedades: verde, ocre y rojo", sorted(c) == sorted([VERDE, OCRE, ROJO]), str(c))   # la leyenda se ordena por total
    c = colores("modificados", g + "return { cred: g('g-creditos').map(t => t.marker.color), per: g('g-periodos-dir').map(t => t.marker.color), heat: g('g-transiciones')[0].colorscale };")
    verificar("colores · modificados · reducciones y transiciones en amarillo, aumentos en negro",
              c["cred"] == [NEGRO, AMARILLO] and c["per"] == [NEGRO, AMARILLO] and c["heat"][-1][1] == AMARILLO, str(c))
    c = colores("inactivos", g + "return { sector: g('g-sector')[0].marker.colors, univ: g('g-instituciones')[0].marker.color };")
    verificar("colores · inactivos · sector y universidades en rojo", set(c["sector"]) == {ROJO, NEGRO} and c["univ"] == ROJO, str(c))

    # Posición de las gráficas: el tipo de cambio a la derecha de la evolución de modificados
    d.get("about:blank")
    q = Pagina(d, base, "modificados.html")
    r = q.js("const a = document.getElementById('g-evolucion').getBoundingClientRect(), b = document.getElementById('g-tipos').getBoundingClientRect(); return { aX: a.x, bX: b.x, aY: a.y, bY: b.y };")
    verificar("estructura · modificados · el tipo de cambio queda a la derecha de la evolución", r["bX"] > r["aX"] and abs(r["aY"] - r["bY"]) < 2, str(r))


def probar_alturas(d, base):
    """El encabezado (último dato o total) y las tarjetas de indicadores tienen la misma altura en todas las páginas."""
    alturas = {}
    for pagina in ("index", "nuevos", "modificados", "inactivos"):
        d.get("about:blank")
        q = Pagina(d, base, f"{pagina}.html")
        alturas[pagina] = q.js("""const h = (s) => { const e = document.querySelector(s); return e ? Math.round(e.getBoundingClientRect().height * 10) / 10 : null; };
            return { encabezado: h('.encabezado'), total: h('.enc-total'), kpis: [...document.querySelectorAll('.kpi')].map(k => Math.round(k.getBoundingClientRect().height)) };""")
    verificar("alturas · encabezado y cuadro de total iguales en todas las páginas",
              len({(a["encabezado"], a["total"]) for a in alturas.values()}) == 1, str({k: (v["encabezado"], v["total"]) for k, v in alturas.items()}))
    verificar("alturas · tarjetas de indicadores de Inicio con la misma altura", len(set(alturas["index"]["kpis"])) == 1 and len(alturas["index"]["kpis"]) == 4, str(alturas["index"]["kpis"]))


def probar_tablas_inicio(p):
    """Tablas de novedades de Inicio: pestañas (Nuevos por defecto), una visible a la vez y filtros que afectan siempre las tres."""
    info = p.js("""return { pestanas: [...document.querySelectorAll('[data-tab]')].map(b => [b.dataset.tab, b.getAttribute('aria-selected')]),
        visibles: [...document.querySelectorAll('[role=tabpanel]')].filter(e => !e.hidden).map(e => e.id) };""")
    verificar("inicio · pestañas de novedades: Nuevos por defecto y una sola tabla visible",
              info["pestanas"] == [["nuevos", "true"], ["modificados", "false"], ["inactivos", "false"]] and info["visibles"] == ["panel-nuevos"], str(info))
    cols = {t: p.js("return [...document.querySelectorAll(`#tabla-${arguments[0]} th`)].map(t => t.dataset.c)", t) for t in NOV}
    verificar("inicio · columnas de las tres tablas en el orden acordado", all(cols[t][:len(COLUMNAS_INICIALES[t])] == COLUMNAS_INICIALES[t] for t in NOV), str({t: c[:8] for t, c in cols.items()}))
    # Un filtro cambia las tres tablas aunque solo una esté visible
    clic_opcion(p, 0, "[data-todos]")
    clic_opcion(p, 0, '[data-g="mayor"]')
    esperado = {t: len(filtrar(NOV[t], {"universidades": ("grupo", catalogos.GRUPO_MAYOR_INTERES)}, None, CAMPOS_BUSQUEDA)) for t in NOV}
    estado = p.estado()
    verificar("inicio · los filtros afectan las tres tablas aunque no estén visibles", estado["tablas"] == estado["tabs"] == esperado, f"{estado['tablas']} vs {esperado}")
    # Cambiar de pestaña muestra la tabla filtrada
    for t in ("modificados", "inactivos", "nuevos"):
        p.d.find_element(By.CSS_SELECTOR, f'[data-tab="{t}"]').click(); time.sleep(0.2)
        vis = p.js("return [...document.querySelectorAll('[role=tabpanel]')].filter(e => !e.hidden).map(e => e.id)")
        filas = p.js("return Number(document.querySelector(`#tabla-${arguments[0]} [data-n]`).textContent.replace(/\\D/g, ''))", t)
        verificar(f"inicio · pestaña {t}: solo su tabla visible y con los registros filtrados", vis == [f"panel-{t}"] and filas == esperado[t], f"{vis} · {filas} vs {esperado[t]}")
    reset_filtros(p)
    # Exportación de la tabla visible
    r = p.d.execute_async_script("""const listo = arguments[arguments.length - 1];
      (async () => {
        let blob = null; const o1 = URL.createObjectURL, o2 = HTMLAnchorElement.prototype.click;
        URL.createObjectURL = (b) => { blob = b; return 'blob:x'; }; HTMLAnchorElement.prototype.click = function () {};
        document.querySelector('#tabla-nuevos [data-pq]').click();
        for (let i = 0; i < 150 && !blob; i++) await new Promise(r => setTimeout(r, 100));
        URL.createObjectURL = o1; HTMLAnchorElement.prototype.click = o2;
        const hp = await import('hyparquet'); const filas = await hp.parquetReadObjects({ file: await blob.arrayBuffer() });
        const meta = hp.parquetMetadata(await blob.arrayBuffer());
        const f = meta.schema.find((s) => s.name === 'FECHA_DE_OBTENCION');
        listo({ filas: filas.length, columnas: Object.keys(filas[0] || {}).length, cols_tabla: document.querySelectorAll('#tabla-nuevos th').length,
                tipoFecha: f ? f.converted_type : null, esFecha: filas[0].FECHA_DE_OBTENCION instanceof Date && filas[0].FECHA_DE_OBTENCION.getUTCHours() === 0 });
      })().catch(e => listo({ error: String(e) }));""")
    verificar("inicio · exportación de la tabla de nuevos", r.get("filas") == len(NOV["nuevos"]) and r.get("columnas") == r.get("cols_tabla"), json.dumps(r))


def probar_titulos_pestana(d, base):
    """El título de la pestaña del navegador es el del apartado activo y cambia solo al navegar entre apartados."""
    esperado = {"index": "PROGRAMAS ACADÉMICOS ACTIVOS", "nuevos": "PROGRAMAS ACADÉMICOS NUEVOS",
                "modificados": "PROGRAMAS ACADÉMICOS MODIFICADOS", "inactivos": "PROGRAMAS ACADÉMICOS INACTIVOS"}
    d.get("about:blank")
    q = Pagina(d, base, "index.html")
    for pagina in ("index", "nuevos", "modificados", "inactivos", "index"):
        if d.execute_script("return location.pathname.split('/').pop()") != f"{pagina}.html":
            d.find_element(By.CSS_SELECTOR, f'.nav a[href^="{pagina}.html"]').click()   # navegación real desde el menú
            q.esperar_listo()
        verificar(f"título de pestaña · {pagina}", d.title == esperado[pagina], d.title)
    verificar("título de pestaña · el apartado activo del menú coincide con el título",
              d.execute_script("return document.querySelector('.nav a.activa').textContent") == "INICIO")


def probar_rango_temporal(d, base):
    """Cada gráfica temporal empieza en la primera ejecución con datos de su métrica, no en la primera ejecución global."""
    def valido(df, tipo):
        sel = df[df["TIPO_CAMBIO_DETECTADO"] == tipo]
        ok = sel["CAMBIO_DETECTADO"].astype(str).str.fullmatch(r"\s*-?\d+(\.\d+)?\s*→\s*-?\d+(\.\d+)?\s*")
        return sel[ok]["FECHA_DE_OBTENCION"].min()
    primera_global = C["fechas_ejecucion"][0]
    x0 = lambda q, gid: q.js("const g = document.getElementById(arguments[0]); return g && g.data && g.data[0] ? String(g.data[0].x[0]).slice(0, 10) : null", gid)  # noqa: E731
    for tipo in NOV:
        d.get("about:blank")
        q = Pagina(d, base, f"{tipo}.html")
        inicio = NOV[tipo]["FECHA_DE_OBTENCION"].min()
        graficas = ["g-evolucion", "g-sector-evol", "g-division", "g-modalidad", "g-cine"] + (["g-tipos"] if tipo == "modificados" else [])
        valores = {g: x0(q, g) for g in graficas}
        verificar(f"rango temporal · {tipo} · las gráficas empiezan en la primera fecha con registros ({inicio})",
                  all(v == inicio for v in valores.values()) and inicio > primera_global, str(valores))
        if tipo == "modificados":
            for gid, cambio in (("g-creditos", "NUMERO DE CREDITOS"), ("g-periodos-dir", "NUMERO DE PERIODOS")):
                esperado = valido(NOV[tipo], cambio)
                verificar(f"rango temporal · modificados · {gid} empieza en el primer cambio válido ({esperado})", x0(q, gid) == esperado, f"{x0(q, gid)} vs {esperado}")
            verificar("rango temporal · modificados · número de periodos empieza después que créditos", valido(NOV[tipo], "NUMERO DE PERIODOS") > valido(NOV[tipo], "NUMERO DE CREDITOS"), "")
        # Un periodo anterior al inicio de la métrica no dibuja gráficas temporales; uno que lo cruza empieza en el inicio
        d.get("about:blank")
        q = Pagina(d, base, f"{tipo}.html#fecha={primera_global}")
        verificar(f"rango temporal · {tipo} · una fecha anterior a los datos no muestra periodos sin información",
                  q.js("return !document.getElementById('g-evolucion').data"), "")
        d.get("about:blank")
        q = Pagina(d, base, f"{tipo}.html#desde={primera_global}&hasta={inicio}")
        verificar(f"rango temporal · {tipo} · un rango que cruza el inicio empieza en {inicio}", x0(q, "g-evolucion") == inicio, str(x0(q, "g-evolucion")))
        verificar(f"rango temporal · {tipo} · errores de consola", not q.errores(), str(q.errores()[:3]))
    d.get("about:blank")
    q = Pagina(d, base, "index.html")
    inicio = min(NOV[t]["FECHA_DE_OBTENCION"].min() for t in NOV)
    verificar(f"rango temporal · inicio · novedades desde {inicio} y activos desde la primera ejecución con activos",
              x0(q, "g-novedades") == inicio and all(x0(q, g) == C["fechas_activos"][0] for g in ("g-activos", "g-modalidad", "g-division", "g-cine", "g-sector-evol")),
              f"{x0(q, 'g-novedades')} · {x0(q, 'g-activos')}")
    # Nuevas ejecuciones semanales: el inicio se calcula de los datos, no está escrito en el código
    r = q.js("""const A = window.App; const regs = [{ FECHA_DE_OBTENCION: '2027-01-05' }, { FECHA_DE_OBTENCION: '2026-12-29' }];
        const lista = ['2026-12-22', '2026-12-29', '2027-01-05', '2027-01-12'];
        return { inicio: A.primeraFecha(regs), desde: A.desdeFecha(lista, A.primeraFecha(regs)), sinDatos: A.desdeFecha(lista, A.primeraFecha([])) };""")
    verificar("rango temporal · el inicio sale de los datos (nuevas ejecuciones semanales)",
              r == {"inicio": "2026-12-29", "desde": ["2026-12-29", "2027-01-05", "2027-01-12"], "sinDatos": []}, str(r))


def probar_unidades_y_etiquetas(d, base):
    """Una sola unidad de medida ("registros"), etiquetas de las agrupaciones (HÍBRIDA, SIN INFORMACIÓN), programas distintos en Modificados y un solo término para la ejecución."""
    for pagina in ("index", "nuevos", "modificados", "inactivos"):
        d.get("about:blank")
        q = Pagina(d, base, f"{pagina}.html")
        info = q.js("""const t = (s) => (document.querySelector(s) || {}).textContent || '';
            const plantillas = [...document.querySelectorAll('.grafica')].flatMap(e => (e.data || []).map(tr => tr.hovertemplate || '').filter(Boolean));
            const modal = (document.getElementById('g-modalidad').data || []).map(tr => tr.name.split(' · ')[0]);
            const ms = [...document.querySelectorAll('.filtros .ms')].find(m => m.querySelector('.ms-btn').textContent.includes('MODALIDADES'));
            return { conteo: t('.conteo-filtro'), total: t('#total-s'), plantillas, modal, texto: document.body.innerText,
                     opcionesMod: [...ms.querySelectorAll('.ms-ops .ms-op')].map(l => [l.querySelector('input').value, l.textContent.trim()]),
                     encabezado: t('.enc-total'), ejes: [...document.querySelectorAll('.grafica')].map(e => e.layout && e.layout.yaxis && e.layout.yaxis.title ? e.layout.yaxis.title.text : '').filter(Boolean) };""")
        verificar(f"unidades · {pagina} · conteo de filtros en registros", info["conteo"].split(" ", 1)[1].startswith("registro"), info["conteo"])
        if pagina != "index":
            verificar(f"unidades · {pagina} · total del encabezado en registros", info["total"] in ("registros", "registro"), info["total"])
        verificar(f"unidades · {pagina} · ninguna gráfica usa programas, cambios ni acumulado como unidad",
                  all(("registros" in h or "%{" not in h) and "programas</" not in h and "cambios<" not in h for h in info["plantillas"]), str([h for h in info["plantillas"] if "registros" not in h][:2]))
        verificar(f"unidades · {pagina} · ejes en registros", all(e in ("REGISTROS", "MODALIDAD ANTERIOR") for e in info["ejes"]), str(info["ejes"]))
        hibrida = [o for o in info["opcionesMod"] if o[0] == "HIBRIDA"]
        verificar(f"etiquetas de agrupaciones · {pagina} · HÍBRIDA en el filtro de modalidades y en la leyenda (el valor sigue siendo HIBRIDA)",
                  hibrida == [["HIBRIDA", "HÍBRIDA"]] and "HÍBRIDA" in info["modal"] and "HIBRIDA" not in info["modal"], f"{hibrida} · {info['modal']}")
        verificar(f"ejecución · {pagina} · sin 'Descarga más reciente' ni 'Sin descargas'", "Descarga más reciente" not in info["texto"] and "Sin descargas" not in info["texto"], "")
    d.get("about:blank")
    q = Pagina(d, base, "modificados.html")
    ejes = q.js("const g = document.getElementById('g-transiciones'); return [g.data[0].x, g.data[0].y]")
    verificar("etiquetas de agrupaciones · modificados · transiciones de modalidad", "HIBRIDA" not in ejes[0] + ejes[1] and (not ({"HIBRIDA", "HÍBRIDA"} & set(ejes[0] + ejes[1])) or "HÍBRIDA" in ejes[0] + ejes[1]), str(ejes))
    ayuda = q.js("return document.getElementById('t-g-creditos').parentElement.querySelector('.tarjeta-sub').textContent")
    verificar("colores · modificados · la descripción menciona barras amarillas", "barras amarillas" in ayuda and "naranja" not in ayuda, ayuda)
    mod = NOV["modificados"]
    distintos = mod["CODIGO_SNIES_DEL_PROGRAMA"].nunique()
    miles = lambda n: f"{n:,}".replace(",", ".")  # noqa: E731
    conteo = q.js("return document.querySelector('.conteo-filtro').textContent")
    verificar("programas distintos · modificados · el contador de filtros informa registros y programas distintos",
              conteo == f"{miles(len(mod))} registros · {miles(distintos)} programas distintos", conteo)
    d.get("about:blank")
    q2 = Pagina(d, base, "index.html")
    ult = mod[mod["FECHA_DE_OBTENCION"] == C["ultima_ejecucion"]]
    sub = q2.js("return document.getElementById('k-modificados-s').textContent")
    f = C["ultima_ejecucion"]
    esperado = f"{miles(ult['CODIGO_SNIES_DEL_PROGRAMA'].nunique())} programa{'' if ult['CODIGO_SNIES_DEL_PROGRAMA'].nunique() == 1 else 's'} distinto{'' if ult['CODIGO_SNIES_DEL_PROGRAMA'].nunique() == 1 else 's'} · {f[8:10]}/{f[5:7]}/{f[:4]}"
    verificar("programas distintos · inicio · KPI de modificados", sub == esperado, f"{sub!r} vs {esperado!r}")
    verificar("etiquetas de agrupaciones · catálogo de modalidades agrupadas con SIN INFORMACIÓN", "SIN INFORMACIÓN" in C["catalogos"]["grupos_modalidad"], str(C["catalogos"]["grupos_modalidad"]))


def main():


    """Ejecuta todas las pruebas; retorna 0 si no hubo fallas."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapida", action="store_true", help="Prueba una muestra de opciones por filtro")
    args = ap.parse_args()
    consistencia_datos()
    tipografia_local()
    srv = servidor()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    d = navegador()
    try:
        probar_estructura(d, base)
        probar_alturas(d, base)
        # Inicio
        probar_inicio_sin_tabla(d, base)
        p = Pagina(d, base, "index.html")
        p.claves = ["universidades", "programas", "divisiones", "cine", "fechas", "departamentos", "modalidades", "sectores"]
        comparar = lambda n, f, busqueda=None, fechas=None: comparar_inicio(p, n, f, busqueda, fechas)  # noqa: E731
        comparar("estado inicial", {})
        verificar("inicio · gráficas sin datos al iniciar", not p.estado()["vacias"], str(p.estado()["vacias"]))
        probar_filtros(p, comparar, [c for c in p.claves], args.rapida)
        probar_fechas(p, comparar)
        probar_combinaciones(p, comparar, True)
        probar_tablas_inicio(p)
        probar_cine_sync(p, None)
        probar_sin_punteadas(p)
        probar_orden_leyendas(p)
        probar_rendimiento(p)
        verificar("inicio · errores de consola", not p.errores(), str(p.errores()[:3]))
        # Navegación desde las tarjetas KPI
        for destino in ("nuevos", "modificados", "inactivos"):
            p.d.get(f"{base}/index.html"); Pagina.esperar_listo(p)
            p.d.find_element(By.CSS_SELECTOR, f".kpi.k-{destino}").click(); time.sleep(1)
            verificar(f"navegación · tarjeta {destino}", p.d.current_url.endswith(f"{destino}.html"), p.d.current_url)
        # Páginas de detalle
        for tipo in ("nuevos", "modificados", "inactivos"):
            q = Pagina(d, base, f"{tipo}.html")
            q.claves = ["universidades", "programas", "divisiones", "cine", "fechas", "departamentos", "modalidades", "sectores"] + (["cambios"] if tipo == "modificados" else [])
            q.tipo = tipo
            comp = (lambda t, qq: (lambda n, f, busqueda=None, fechas=None: comparar_detalle(qq, t, n, f, busqueda, fechas)))(tipo, q)
            comp("estado inicial", {})
            verificar(f"{tipo} · gráficas sin datos al iniciar", not q.estado()["vacias"], str(q.estado()["vacias"]))
            probar_filtros(q, comp, q.claves, args.rapida)
            probar_fechas(q, comp)
            probar_combinaciones(q, comp, False)
            probar_tabla(q, NOV[tipo])
            probar_exportacion(q)
            probar_cine_sync(q, tipo)
            probar_sin_punteadas(q)
            probar_orden_leyendas(q)
            probar_rendimiento(q)
            for nav in ("index.html", "nuevos.html", "modificados.html", "inactivos.html"):
                enlaces = q.d.find_elements(By.CSS_SELECTOR, f'.nav a[href="{nav}"]')
                verificar(f"{tipo} · enlace de navegación {nav}", len(enlaces) == 1)
            verificar(f"{tipo} · errores de consola", not q.errores(), str(q.errores()[:3]))
        probar_url(d, base)
        probar_fecha_unica(d, base)
        probar_consistencia_visual(d, base)
        probar_titulos_pestana(d, base)
        probar_rango_temporal(d, base)
        probar_unidades_y_etiquetas(d, base)
        probar_movil(d, base)
        probar_filtros_plegables(d, base)
        probar_politica_de_contenido(d, base)
    finally:
        d.quit()
        srv.shutdown()
    fallas = [r for r in RESULTADOS if not r[1]]
    print(f"\nRESULTADO: {len(RESULTADOS) - len(fallas)} de {len(RESULTADOS)} verificaciones correctas · {len(fallas)} fallas")
    for nombre, _, detalle in fallas[:40]:
        print(f"  - {nombre}: {detalle}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
