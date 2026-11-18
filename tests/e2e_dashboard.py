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
    RESULTADOS.append((nombre, bool(ok), detalle))
    if not ok:
        print(f"  FALLA · {nombre} · {detalle}", flush=True)
    return ok


# ── Datos de referencia ───────────────────────────────────────────────────────

def leer_js(nombre):
    texto = (DOCS / "data" / nombre).read_text(encoding="utf-8")
    return json.loads(texto[texto.index("]=") + 2:].rstrip().rstrip(";"))


def decodificar(t):
    datos = {}
    for c in t["columnas"]:
        d = t["datos"][c]
        datos[c] = d if isinstance(d, list) else [d["d"][i] for i in d["i"]]
    df = pd.DataFrame(datos)
    return derivar(df)


def derivar(df):
    df["_GU"] = df["CODIGO_INSTITUCION"].map(catalogos.grupo_universidad)
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


@functools.lru_cache(maxsize=None)
def historico(fecha):
    df = pd.read_parquet(DOCS / "data" / "historico" / f"{fecha}.parquet")
    return derivar(df)


CAMPOS = {
    "divisiones": "DIVISION_UNINORTE", "cine": "CINE_F_2013_AC_CAMPO_DETALLADO",
    "departamentos": "DEPARTAMENTO_OFERTA_PROGRAMA", "modalidades": "_MG", "sectores": "SECTOR",
    "cambios": "TIPO_CAMBIO_DETECTADO",
}


def filtrar(df, filtro, busqueda=None, campos_busqueda=()):
    """Aplica la misma regla que el Dashboard: {'clave': valor} con la semántica del filtro de universidades."""
    m = pd.Series(True, index=df.index)
    for clave, valor in filtro.items():
        if clave == "universidades":
            tipo, v = valor
            if tipo == "grupo":
                m &= df["_GU"] == v
            else:  # universidad de mayor interés por código
                m &= df["CODIGO_INSTITUCION"].astype(float) == float(v)
        else:
            m &= df[CAMPOS[clave]].astype(str) == str(valor)
    if busqueda:
        q = norm_js(busqueda)
        hit = pd.Series(False, index=df.index)
        for c in campos_busqueda:
            hit |= df[c].map(norm_js).str.contains(q, regex=False)
        m &= hit
    return df[m]


# ── Navegador ─────────────────────────────────────────────────────────────────

def servidor():
    manejador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS))
    manejador.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def navegador():
    o = webdriver.ChromeOptions()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1400,1000")
    o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=o)


class Pagina:
    def __init__(self, d, base, nombre):
        self.d, self.nombre = d, nombre
        t0 = time.time()
        d.get(f"{base}/{nombre}")
        self.esperar_listo()
        self.carga = time.time() - t0

    def js(self, codigo, *args):
        return self.d.execute_script(codigo, *args)

    def esperar_listo(self, limite=60):
        fin = time.time() + limite
        while time.time() < fin:
            # Inicio carga su tabla al acercarse a la vista: se desplaza hasta ella para validar también sus registros
            self.js("const t = document.getElementById('tabla'); if (t && t.dataset.diferida) t.scrollIntoView({block: 'center'});")
            if self.js("return !!document.querySelector('.filtros') && !(document.getElementById('tabla') || {dataset: {}}).dataset.diferida "
                       "&& !(document.getElementById('tabla-estado')||{}).textContent "
                       "&& !!document.querySelector('#tabla [data-n]') && document.querySelector('#tabla [data-n]').textContent !== ''"):
                time.sleep(0.15)
                return
            time.sleep(0.1)
        raise TimeoutError(f"{self.nombre}: la página no terminó de cargar")

    def boton(self, indice):
        return self.d.find_elements(By.CSS_SELECTOR, ".filtros .ms > .ms-btn")[indice]

    def abrir(self, indice):
        self.cerrar()
        self.boton(indice).click()

    def cerrar(self):
        self.d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)

    def errores(self):
        return [l["message"] for l in self.d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]

    def estado(self):
        return self.js("""
            const t = (id) => (document.getElementById(id) || {}).textContent || null;
            const num = (s) => s == null ? null : Number(String(s).replace(/\\./g, '').replace(/[^0-9-]/g, ''));
            const g = (id) => { const e = document.getElementById(id); return e && e.data ? e.data : null; };
            const ultimo = (tr) => tr.y[tr.y.length - 1];
            const out = {
              total: num(t('total')), activos: num(t('k-activos')), nuevos: num(t('k-nuevos')), modificados: num(t('k-modificados')),
              inactivos: num(t('k-inactivos')), tabla: num(document.querySelector('#tabla [data-n]').textContent),
              conteo: document.querySelector('.conteo-filtro').textContent };
            const ev = g('g-evolucion'); if (ev) out.evolucion = ultimo(ev[0]);
            const act = g('g-activos'); if (act) out.g_activos = ultimo(act[0]);
            const nov = g('g-novedades'); if (nov) out.g_novedades = nov.map(ultimo);
            const div = g('g-division'); if (div && div[0].y) out.suma_division = div.reduce((a, tr) => a + ultimo(tr), 0);
            const sec = g('g-sector'); if (sec) out.suma_sector = sec[0].values.reduce((a, b) => a + b, 0);
            const per = g('g-periodos'); if (per) out.suma_periodos = per[0].y.reduce((a, b) => a + b, 0);
            out.vacias = [...document.querySelectorAll('.grafica')].filter(e => !e.data).map(e => e.id);
            return out;""")


# ── Pruebas ───────────────────────────────────────────────────────────────────

def opciones_filtro(p, indice):
    return p.js("""const ms = document.querySelectorAll('.filtros .ms')[arguments[0]];
                   return [...ms.querySelectorAll('.ms-ops input[value]')].map(i => i.value);""", indice)


def clic_opcion(p, indice, selector):
    p.abrir(indice)
    el = p.d.find_elements(By.CSS_SELECTOR, ".filtros .ms")[indice].find_element(By.CSS_SELECTOR, selector)
    p.js("arguments[0].scrollIntoView({block: 'center'});", el)
    el.click()
    p.esperar_listo()


def reset_filtros(p):
    p.cerrar()
    p.d.find_element(By.CSS_SELECTOR, ".filtros .btn-limpiar").click()
    p.esperar_listo()


def esperado_inicio(filtro, busqueda=None, desde=None, hasta=None):
    campos = ["NOMBRE_DEL_PROGRAMA", "NOMBRE_INSTITUCION", "MODALIDAD", "CODIGO_SNIES_DEL_PROGRAMA"]
    fa = [f for f in C["fechas_activos"] if not desde or desde <= f <= hasta]
    fe = [f for f in C["fechas_ejecucion"] if not desde or desde <= f <= hasta]
    activos = len(filtrar(historico(fa[-1]), filtro, busqueda, campos)) if fa else 0
    out = {"activos": activos}
    for t in NOV:
        df = NOV[t]
        if desde:
            df = df[(df.FECHA_DE_OBTENCION >= desde) & (df.FECHA_DE_OBTENCION <= hasta)]
        df = filtrar(df, filtro, busqueda, campos)
        out[f"{t}_periodo"] = len(df)
        out[t] = int((df.FECHA_DE_OBTENCION == (fe[-1] if fe else "")).sum())
    # Tabla: sin filtro de fecha, todas las ejecuciones del histórico; con filtro, las del periodo
    fh = [f for f in C["historico_fechas"] if not desde or desde <= f <= hasta]
    out["tabla"] = sum(len(filtrar(historico(f), filtro, busqueda, campos)) for f in fh)
    return out


def esperado_detalle(tipo, filtro, busqueda=None, desde=None, hasta=None):
    df = NOV[tipo]
    if desde:
        df = df[(df.FECHA_DE_OBTENCION >= desde) & (df.FECHA_DE_OBTENCION <= hasta)]
    return len(filtrar(df, filtro, busqueda, ["NOMBRE_DEL_PROGRAMA", "NOMBRE_INSTITUCION", "CODIGO_SNIES_DEL_PROGRAMA"]))


def comparar_inicio(p, nombre, filtro, busqueda=None, desde=None, hasta=None):
    e, s = esperado_inicio(filtro, busqueda, desde, hasta), p.estado()
    ok = verificar(f"inicio · {nombre} · KPI activos", s["activos"] == e["activos"], f"{s['activos']} vs {e['activos']}")
    for t in NOV:
        ok &= verificar(f"inicio · {nombre} · KPI {t}", s[t] == e[t], f"{s[t]} vs {e[t]}")
    ok &= verificar(f"inicio · {nombre} · tabla", s["tabla"] == e["tabla"], f"{s['tabla']} vs {e['tabla']}")
    if s.get("g_novedades"):
        ok &= verificar(f"inicio · {nombre} · gráfica novedades acumuladas",
                        s["g_novedades"] == [e["nuevos_periodo"], e["modificados_periodo"], e["inactivos_periodo"]],
                        f"{s['g_novedades']} vs {[e['nuevos_periodo'], e['modificados_periodo'], e['inactivos_periodo']]}")
    if e["activos"]:
        ok &= verificar(f"inicio · {nombre} · coherencia activos (línea, torta, división)",
                        s.get("g_activos") == s.get("suma_sector") == s.get("suma_division") == e["activos"],
                        f"línea {s.get('g_activos')} · torta {s.get('suma_sector')} · división {s.get('suma_division')}")
    return ok


def comparar_detalle(p, tipo, nombre, filtro, busqueda=None, desde=None, hasta=None):
    e, s = esperado_detalle(tipo, filtro, busqueda, desde, hasta), p.estado()
    ok = verificar(f"{tipo} · {nombre} · TOTAL", s["total"] == e, f"{s['total']} vs {e}")
    ok &= verificar(f"{tipo} · {nombre} · tabla", s["tabla"] == e, f"{s['tabla']} vs {e}")
    if e:
        ok &= verificar(f"{tipo} · {nombre} · coherencia (evolución, división, torta)",
                        s.get("evolucion") == s.get("suma_division") == s.get("suma_sector") == e,
                        f"evolución {s.get('evolucion')} · división {s.get('suma_division')} · torta {s.get('suma_sector')}")
    return ok


def probar_filtros(p, comparar, claves, muestra):
    for indice, clave in enumerate(claves):
        if clave == "fechas":
            continue
        if clave == "universidades":
            for g, sel in zip(catalogos.GRUPOS_UNIVERSIDAD, ('[data-g="U"]', '[data-g="M"]', '[data-g="O"]')):
                clic_opcion(p, indice, sel)
                comparar(f"universidades={g}", {"universidades": ("grupo", g)})
                reset_filtros(p)
            codigos = sorted(C["catalogos"]["mayor_interes"])
            for c in (codigos[:3] if muestra else codigos):
                p.abrir(indice)
                ms = p.d.find_elements(By.CSS_SELECTOR, ".filtros .ms")[indice]
                if p.js("return arguments[0].querySelector('.ms-hijos').hidden", ms):
                    ms.find_element(By.CSS_SELECTOR, ".ms-expandir").click()
                clic_opcion(p, indice, f'[data-c="{c}"]')
                comparar(f"universidad={c}", {"universidades": ("codigo", c)})
                reset_filtros(p)
            continue
        valores = opciones_filtro(p, indice)
        if muestra:
            valores = valores[:2] + valores[-1:]
        for v in valores:
            clic_opcion(p, indice, f'.ms-ops input[value="{v}"]')
            comparar(f"{clave}={v}", {clave: v})
            # deseleccionar con el mismo control (no con LIMPIAR) para probar el retiro de una opción
            clic_opcion(p, indice, f'.ms-ops input[value="{v}"]')
        verificar(f"{p.nombre} · filtro {clave} probado", True, f"{len(valores)} opciones")
        reset_filtros(p)


def seleccionar_fecha(p, modo, valor):
    p.cerrar()
    p.d.find_element(By.CSS_SELECTOR, ".fs-btn").click()
    p.d.find_element(By.CSS_SELECTOR, f'[data-modo="{modo}"]').click()
    if modo in ("mes", "anio"):
        p.d.find_element(By.CSS_SELECTOR, f'[data-{"mes" if modo == "mes" else "anio"}="{valor}"]').click()
    else:
        p.js("document.querySelector('.fs-cuerpo > div')._flatpickr.setDate(arguments[0], true);", valor)
    p.esperar_listo()


def probar_fechas(p, comparar):
    casos = [
        ("fecha", "2026-11-03", "2026-11-03", "2026-11-03"),
        ("rango", ["2026-09-01", "2026-09-29"], "2026-09-01", "2026-09-29"),
        ("mes", "2026-10", "2026-10-01", "2026-10-31"),
        ("anio", "2025", "2025-01-01", "2025-12-31"),
    ]
    for modo, valor, desde, hasta in casos:
        seleccionar_fecha(p, modo, valor)
        etiqueta = p.js("return document.querySelector('.fs-btn span').textContent")
        verificar(f"{p.nombre} · fecha {modo} etiqueta", etiqueta != "TODAS LAS FECHAS", etiqueta)
        comparar(f"fecha {modo} {valor}", {}, desde=desde, hasta=hasta)
    # Restablecer con la × y con el botón del panel
    p.d.find_element(By.CSS_SELECTOR, ".fs-quitar").click(); p.esperar_listo()
    verificar(f"{p.nombre} · restablecer fechas con ×", p.js("return document.querySelector('.fs-btn span').textContent") == "TODAS LAS FECHAS")
    comparar("tras restablecer fechas", {})
    seleccionar_fecha(p, "anio", "2024")
    p.d.find_element(By.CSS_SELECTOR, ".fs-btn").click(); p.d.find_element(By.CSS_SELECTOR, ".btn-reset").click(); p.esperar_listo()
    verificar(f"{p.nombre} · restablecer fechas con TODAS LAS FECHAS", p.js("return document.querySelector('.fs-btn span').textContent") == "TODAS LAS FECHAS")
    # Una fecha sin ejecución no debe poder seleccionarse
    p.d.find_element(By.CSS_SELECTOR, ".fs-btn").click(); p.d.find_element(By.CSS_SELECTOR, '[data-modo="fecha"]').click()
    n = p.js("const f = document.querySelector('.fs-cuerpo > div')._flatpickr; f.setDate('2026-11-12', false); return f.selectedDates.length;")
    verificar(f"{p.nombre} · fecha sin ejecución rechazada", n == 0, str(n))
    p.cerrar()


def probar_combinaciones(p, comparar, es_inicio):
    p.js("""const i = document.querySelector('.buscar'); i.value = 'ingeniería de sistemas'; i.dispatchEvent(new Event('input'));""")
    time.sleep(0.5); p.esperar_listo()
    comparar("búsqueda 'ingeniería de sistemas'", {}, busqueda="ingeniería de sistemas")
    # combinación: grupo de mayor interés + sector privado + periodo 2026
    clic_opcion(p, 0, '[data-g="M"]')
    sectores = [i for i, c in enumerate(p.claves) if c == "sectores"][0]
    clic_opcion(p, sectores, '.ms-ops input[value="PRIVADO"]')
    seleccionar_fecha(p, "anio", "2026")
    comparar("combinación búsqueda + mayor interés + privado + 2026",
             {"universidades": ("grupo", catalogos.GRUPO_MAYOR_INTERES), "sectores": "PRIVADO"},
             busqueda="ingeniería de sistemas", desde="2026-01-01", hasta="2026-12-31")
    reset_filtros(p)
    verificar(f"{p.nombre} · LIMPIAR FILTROS restablece todo",
              p.js("return document.querySelector('.buscar').value === '' && !document.querySelector('.ms-btn.con-valor')"))
    comparar("tras LIMPIAR FILTROS", {})


def probar_tabla(p, filas_ref):
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
    r = p.d.execute_async_script("""const listo = arguments[arguments.length - 1];
      (async () => {
        let blob = null; const o1 = URL.createObjectURL, o2 = HTMLAnchorElement.prototype.click;
        URL.createObjectURL = (b) => { blob = b; return 'blob:x'; }; HTMLAnchorElement.prototype.click = function () {};
        document.querySelector('#tabla [data-pq]').click();
        for (let i = 0; i < 150 && !blob; i++) await new Promise(r => setTimeout(r, 100));
        URL.createObjectURL = o1; HTMLAnchorElement.prototype.click = o2;
        const hp = await import('hyparquet'); const buf = await blob.arrayBuffer();
        const filas = await hp.parquetReadObjects({ file: buf });
        listo({ filas: filas.length, columnas: Object.keys(filas[0] || {}).length,
                tabla: Number(document.querySelector('#tabla [data-n]').textContent.replace(/\\./g, '').replace(/\\D/g, '')),
                cols_tabla: document.querySelectorAll('#tabla th').length });
      })().catch(e => listo({ error: String(e) }));""")
    verificar(f"{p.nombre} · exportación Parquet", r.get("filas") == r.get("tabla") and r.get("columnas") == r.get("cols_tabla"), json.dumps(r))


def probar_cine(p):
    antes = p.js("return document.getElementById('g-cine').data.length")
    p.js("const s = document.querySelector('.cine-ctrl select'); s.value = s.options[s.options.length - 1].value;")
    p.d.find_element(By.CSS_SELECTOR, ".cine-ctrl button").click(); time.sleep(0.4)
    despues = p.js("return document.getElementById('g-cine').data.length")
    p.d.find_elements(By.CSS_SELECTOR, ".chips .chip button")[-1].click(); time.sleep(0.4)
    final = p.js("return document.getElementById('g-cine').data.length")
    verificar(f"{p.nombre} · selector CINE agregar/quitar", despues == antes + 1 and final == antes, f"{antes}→{despues}→{final}")


def probar_rendimiento(p):
    t0 = time.time(); clic_opcion(p, 0, '[data-g="O"]'); dt_ = time.time() - t0
    reset_filtros(p)
    verificar(f"{p.nombre} · rendimiento", p.carga < 15 and dt_ < 5, f"carga inicial {p.carga:.1f}s · filtro {dt_:.2f}s")


def probar_carga_diferida(d, base):
    """La tabla de Inicio no descarga el histórico hasta que el visitante se acerca a ella."""
    d.get(f"{base}/index.html?diferida=1")
    fin = time.time() + 30
    while time.time() < fin and not d.execute_script("return !!(document.getElementById('g-activos') || {}).data"):
        time.sleep(0.1)
    time.sleep(1.5)
    js_hist = "return performance.getEntriesByType('resource').filter(e => e.name.includes('data/historico/')).length"
    antes = d.execute_script(js_hist)
    estado = d.execute_script("return [document.getElementById('tabla').dataset.diferida || '', document.querySelector('#tabla [data-n]').textContent]")
    verificar("inicio · carga diferida: sin descargar el histórico al abrir", antes == 0 and estado[0] == "pendiente", f"{antes} archivos · {estado}")
    d.execute_script("document.getElementById('tabla').scrollIntoView({block: 'center'})")
    fin = time.time() + 60
    while time.time() < fin and not d.execute_script("return !document.getElementById('tabla').dataset.diferida && /registros/.test(document.querySelector('#tabla [data-n]').textContent) && !document.getElementById('tabla-estado').textContent"):
        time.sleep(0.1)
    despues = d.execute_script(js_hist)
    n = d.execute_script("return document.querySelector('#tabla [data-n]').textContent")
    verificar("inicio · carga diferida: descarga al llegar a la tabla", despues == len(C["historico_fechas"]), f"{despues} archivos · {n}")
    # Librerías, tipografía y datos se sirven desde el propio sitio: ninguna solicitud a terceros
    for pagina in ("index.html", "modificados.html"):
        if pagina != "index.html":
            d.get(f"{base}/{pagina}"); time.sleep(2)
        externos = d.execute_script("return performance.getEntriesByType('resource').map(e => e.name).filter(u => !u.startsWith(location.origin))")
        listo = d.execute_script("return typeof Plotly !== 'undefined' && typeof flatpickr !== 'undefined' && !!document.querySelector('.grafica .main-svg')")
        verificar(f"{pagina} · sin solicitudes a terceros (Plotly y flatpickr locales)", not externos and listo, f"{externos[:3]} · librerías {listo}")


def consistencia_datos():
    h = historico_fuente.leer(["FECHA_DE_OBTENCION"])
    por_fecha = h["FECHA_DE_OBTENCION"].dt.strftime("%Y-%m-%d").value_counts().to_dict()
    verificar("datos · fechas del histórico publicadas", sorted(por_fecha) == C["historico_fechas"], f"{len(por_fecha)} vs {len(C['historico_fechas'])}")
    errores = [f for f in C["historico_fechas"] if len(pd.read_parquet(DOCS / "data" / "historico" / f"{f}.parquet", columns=["CODIGO_SNIES_DEL_PROGRAMA"])) != por_fecha.get(f)]
    verificar("datos · filas por fecha (histórico vs Dashboard)", not errores, str(errores[:5]))
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
    comparar_detalle(q, "modificados", "url · carga directa", filtro, desde=desde_mes, hasta=hasta_mes)
    verificar("url · valor inexistente descartado", "NO_EXISTE" not in d.current_url, d.current_url)
    verificar("url · selector de fechas restaurado", q.js("return document.querySelector('.fs-btn span').textContent").endswith(mes[:4]),
              q.js("return document.querySelector('.fs-btn span').textContent"))
    d.refresh(); q.esperar_listo()
    comparar_detalle(q, "modificados", "url · tras recargar", filtro, desde=desde_mes, hasta=hasta_mes)
    # Cambio de apartado: se conservan los filtros comunes, no el tipo de cambio (propio de Modificados)
    enlace = d.find_element(By.CSS_SELECTOR, '.nav a[href^="nuevos.html"]')
    href = enlace.get_attribute("href")
    verificar("url · menú conserva filtros comunes", "universidad=otras" in href and "sector=PRIVADO" in href and f"mes={mes}" in href, href)
    verificar("url · menú no arrastra el tipo de cambio", "cambio=" not in href, href)
    enlace.click(); q.esperar_listo()
    comparar_detalle(q, "nuevos", "url · tras cambiar de apartado", {"universidades": otras, "sectores": "PRIVADO"}, desde=desde_mes, hasta=hasta_mes)
    # Edición manual de la dirección en la misma página (evento hashchange)
    d.get(f"{base}/nuevos.html#fecha={ultima}"); time.sleep(0.5); q.esperar_listo()
    comparar_detalle(q, "nuevos", "url · edición manual", {}, desde=ultima, hasta=ultima)
    d.get(f"{base}/nuevos.html#fecha=2099-01-01&sector=INVALIDO"); time.sleep(0.5); q.esperar_listo()
    verificar("url · parámetros inválidos ignorados", d.current_url.endswith("nuevos.html"), d.current_url)
    comparar_detalle(q, "nuevos", "url · tras parámetros inválidos", {})
    # Inicio: rango + universidad, enlaces de las tarjetas, búsqueda y LIMPIAR FILTROS
    fe = C["fechas_ejecucion"]
    d.get("about:blank")
    p = Pagina(d, base, f"index.html#desde={fe[-5]}&hasta={fe[-1]}&universidad=uninorte")
    p.nombre = "url"
    uni = {"universidades": ("grupo", catalogos.GRUPO_UNINORTE)}
    comparar_inicio(p, "url · inicio con rango", uni, desde=fe[-5], hasta=fe[-1])
    href = d.find_element(By.CSS_SELECTOR, ".kpi.k-nuevos").get_attribute("href")
    verificar("url · tarjeta KPI conserva filtros", "universidad=uninorte" in href and f"desde={fe[-5]}" in href, href)
    p.js("""const i = document.querySelector('.buscar'); i.value = 'derecho'; i.dispatchEvent(new Event('input'));""")
    time.sleep(0.5); p.esperar_listo()
    verificar("url · búsqueda guardada", "buscar=derecho" in d.current_url, d.current_url)
    reset_filtros(p)
    verificar("url · LIMPIAR FILTROS deja la dirección sin filtros", d.current_url.endswith("index.html"), d.current_url)
    verificar("url · errores de consola", not p.errores(), str(p.errores()[:3]))


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


def tipografia_local():
    """El Dashboard no carga recursos de Google Fonts: la tipografía se sirve desde docs/assets/fuentes."""
    paginas = [(DOCS / f"{n}.html").read_text(encoding="utf-8") for n in ("index", "nuevos", "modificados", "inactivos")]
    css = (DOCS / "assets" / "estilos.css").read_text(encoding="utf-8")
    verificar("tipografía · sin Google Fonts", not any("googleapis" in t or "gstatic" in t for t in paginas + [css]))
    fuentes = [DOCS / "assets" / "fuentes" / n for n in ("montserrat-latin.woff2", "montserrat-latin-ext.woff2", "OFL.txt")]
    verificar("tipografía · archivos y licencia presentes", all(f.exists() for f in fuentes))
    verificar("tipografía · formato woff2", all(f.read_bytes()[:4] == b"wOF2" for f in fuentes[:2]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapida", action="store_true", help="Prueba una muestra de opciones por filtro")
    args = ap.parse_args()
    consistencia_datos()
    tipografia_local()
    srv = servidor()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    d = navegador()
    try:
        # Inicio
        probar_carga_diferida(d, base)
        p = Pagina(d, base, "index.html")
        p.claves = ["universidades", "divisiones", "cine", "fechas", "departamentos", "modalidades", "sectores"]
        comparar = lambda n, f, busqueda=None, desde=None, hasta=None: comparar_inicio(p, n, f, busqueda, desde, hasta)  # noqa: E731
        comparar("estado inicial", {})
        verificar("inicio · gráficas sin datos al iniciar", not p.estado()["vacias"], str(p.estado()["vacias"]))
        probar_filtros(p, comparar, [c for c in p.claves], args.rapida)
        probar_fechas(p, comparar)
        probar_combinaciones(p, comparar, True)
        probar_tabla(p, pd.concat([historico(f) for f in C["historico_fechas"]], ignore_index=True))
        seleccionar_fecha(p, "mes", "2026-10"); probar_exportacion(p); p.d.find_element(By.CSS_SELECTOR, ".fs-quitar").click(); p.esperar_listo()
        probar_cine(p)
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
            q.claves = ["universidades", "divisiones", "cine", "fechas", "departamentos", "modalidades", "sectores"] + (["cambios"] if tipo == "modificados" else [])
            comp = (lambda t, qq: (lambda n, f, busqueda=None, desde=None, hasta=None: comparar_detalle(qq, t, n, f, busqueda, desde, hasta)))(tipo, q)
            comp("estado inicial", {})
            verificar(f"{tipo} · gráficas sin datos al iniciar", not q.estado()["vacias"], str(q.estado()["vacias"]))
            probar_filtros(q, comp, q.claves, args.rapida)
            probar_fechas(q, comp)
            probar_combinaciones(q, comp, False)
            probar_tabla(q, NOV[tipo])
            probar_exportacion(q)
            probar_cine(q)
            probar_rendimiento(q)
            for nav in ("index.html", "nuevos.html", "modificados.html", "inactivos.html"):
                enlaces = q.d.find_elements(By.CSS_SELECTOR, f'.nav a[href="{nav}"]')
                verificar(f"{tipo} · enlace de navegación {nav}", len(enlaces) == 1)
            verificar(f"{tipo} · errores de consola", not q.errores(), str(q.errores()[:3]))
        probar_url(d, base)
        probar_movil(d, base)
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
