"""
generar_paginas.py
------------------
Genera las páginas del Dashboard (docs/index.html, nuevos.html, modificados.html, inactivos.html) desde una
plantilla común, para mantener posiciones, títulos, descripciones y estilos coherentes entre apartados.
Cada archivo propio (estilos y scripts) se enlaza con un sufijo de versión según su contenido, para que el
navegador no use una copia anterior en caché. generar_dashboard.py la ejecuta en cada corrida.

Ejecución:
    python Scripts/generar_paginas.py
"""

import base64
import hashlib
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def v(rel):
    """Sufijo de versión según el contenido: el navegador descarga de nuevo el archivo solo si cambió."""
    return f"{rel}?v={hashlib.sha256((DOCS / rel).read_bytes()).hexdigest()[:10]}"
SUB = "Programas Académicos Universitarios de Pregrado en Colombia"

# Mapa de importación de los módulos de la exportación a Excel (write-excel-file y fflate): script en línea, autorizado en la política de seguridad
# por su huella SHA-256 (si el texto cambia, la huella se recalcula al generar las páginas).
IMPORTMAP = """
    { "imports": {
      "write-excel-file/browser": "./assets/vendor/write-excel-file/browser/index.js",
      "fflate": "./assets/vendor/fflate/esm/browser.js"
    } }
  """
# Política de seguridad de contenido: solo se ejecutan scripts propios del sitio (y el mapa de importación autorizado),
# sin objetos incrustados ni formularios, y ninguna solicitud sale del origen. Los estilos en línea los usan Plotly
# y flatpickr; las imágenes data: y blob: son las de las gráficas y de la exportación.
CSP = ("default-src 'self'; script-src 'self' 'sha256-" + base64.b64encode(hashlib.sha256(IMPORTMAP.encode()).digest()).decode() + "'; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
       "object-src 'none'; base-uri 'self'; form-action 'none'")

HEAD = '''<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="@CSP@">
  <title>{title}</title>
  <meta name="description" content="{desc}">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <link rel="icon" href="assets/img/favicon-uninorte.ico" sizes="32x32">
  <link rel="preload" href="assets/fuentes/montserrat-latin.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="{css_fp}">
  <link rel="stylesheet" href="{css}">
  <script type="importmap">@IMPORTMAP@</script>
</head>
<body{body}>
  <header class="encabezado">
    <div class="encabezado-int">
      <div class="enc-fila">
        <div class="enc-marca">
          <img class="enc-logo" src="assets/img/logo-uninorte-direccion-blanco.png" width="307" height="52" alt="Universidad del Norte">
          <div class="enc-textos">
            <h1 class="enc-titulo">{h1}</h1>{sub}
          </div>
        </div>
        {total}
      </div>
      <nav class="nav" aria-label="Apartados">
{nav}
      </nav>
    </div>
  </header>

  <main class="contenido">
    <div id="filtros"></div>
'''
HEAD = HEAD.replace("@CSP@", CSP).replace("@IMPORTMAP@", IMPORTMAP.replace("{", "{{").replace("}", "}}"))
FOOT = '''
    <img class="pie-logo" src="assets/img/logo-uninorte-direccion-principal.png" width="237" height="40" alt="Universidad del Norte">
    <p class="pie-pagina">Fuente: Sistema Nacional de Información de la Educación Superior (SNIES), Ministerio de Educación Nacional · Dirección de Planeación, Universidad del Norte</p>
  </main>

{scripts}
</body>
</html>
'''


def nav(activa):
    """Menú de apartados (HTML); marca como activo el de la página `activa`."""
    items = [("index.html", "INICIO"), ("nuevos.html", "NUEVOS"), ("modificados.html", "MODIFICADOS"), ("inactivos.html", "INACTIVOS")]
    out = []
    for h, t in items:
        attr = ' class="activa" aria-current="page"' if h == activa else ""
        out.append(f'        <a href="{h}"{attr}>{t}</a>')
    return "\n".join(out)


def tarjeta(tit, gid, desc, clase="", extra=""):
    """Tarjeta con título, descripción y el contenedor de una gráfica (`gid`); `extra` agrega controles bajo la descripción."""
    return (f'      <section class="tarjeta" aria-labelledby="t-{gid}">\n'
            f'        <h2 class="tarjeta-tit" id="t-{gid}">{tit}</h2>\n'
            f'        <p class="tarjeta-sub">{desc}</p>{extra}\n'
            f'        <div class="grafica {clase}" id="{gid}" role="img" aria-label="{tit.capitalize()}"></div>\n'
            f'      </section>')


def ancha(*a, **k):
    """Fila con una sola tarjeta de ancho completo."""
    return f'    <div class="fila">\n{tarjeta(*a, **k)}\n    </div>'


def par(a, b, cls="dos"):
    """Fila con dos tarjetas; `cls` define su distribución (dos columnas iguales o un tercio y dos tercios)."""
    return f'    <div class="fila {cls}">\n{a}\n{b}\n    </div>'


def scripts(datos, js):
    """Etiquetas <script> de una página: librerías, archivos de datos `datos`, componentes compartidos y el script `js` propio."""
    # Librerías incluidas en el proyecto (docs/assets/vendor, licencia MIT): el Dashboard no depende de servicios
    # externos para funcionar ni comparte la dirección IP de los visitantes con terceros.
    lineas = [f'  <script src="{v(f"assets/vendor/{lib}")}"></script>'
              for lib in ("plotly/plotly.min.js", "flatpickr/flatpickr.min.js", "flatpickr/l10n/es.min.js")]
    lineas += [f'  <script src="{v(f"data/{d}.js")}"></script>' for d in datos]
    lineas += [f'  <script src="{v("assets/app.js")}"></script>', f'  <script src="{v(f"assets/{js}.js")}"></script>']
    return "\n".join(lineas)


def _control_cine(maximo: int, ambito: str) -> tuple[str, str]:
    """Descripción y control (selector de campos) de la gráfica de evolución por campo de CINE específico."""
    desc = (f"Campos de CINE específico elegidos en el filtro superior, que comparte su selección con el desplegable de esta gráfica. Con todos los campos seleccionados se muestran "
            f"los {maximo} campos con más {ambito}; con una selección, los campos marcados (hasta {maximo}: los que tengan más {ambito}).")
    return desc, '\n        <div id="cine-ctrl"></div>'


def main() -> None:
    """Escribe las cuatro páginas del Dashboard (Inicio, Nuevos, Modificados e Inactivos) en docs/."""
    # ── Inicio ────────────────────────────────────────────────────────────────────
    KPIS = '''
        <section class="kpis" aria-label="Indicadores de la última ejecución del periodo filtrado">
          <div class="kpi"><div class="kpi-et">PROGRAMAS ACTIVOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-activos">–</div><div class="kpi-s" id="k-activos-s"></div></div>
          <a class="kpi k-nuevos" href="nuevos.html"><div class="kpi-et">NUEVOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-nuevos">–</div><div class="kpi-s">Ejecución del <span data-fecha-ultima></span></div></a>
          <a class="kpi k-modificados" href="modificados.html"><div class="kpi-et">MODIFICADOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-modificados">–</div><div class="kpi-s" id="k-modificados-s"></div></a>
          <a class="kpi k-inactivos" href="inactivos.html"><div class="kpi-et">INACTIVOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-inactivos">–</div><div class="kpi-s">Ejecución del <span data-fecha-ultima></span></div></a>
        </section>
    '''
    desc_cine, extra_cine = _control_cine(10, "registros activos")
    tabs = "".join(
        f'<button type="button" role="tab" class="tab t-{t}" id="tab-{t}" data-tab="{t}" aria-controls="panel-{t}" '
        f'aria-selected="{"true" if t == "nuevos" else "false"}" tabindex="{0 if t == "nuevos" else -1}">'
        f'{t.upper()} · <span data-n-tab="{t}">–</span></button>' for t in ("nuevos", "modificados", "inactivos"))
    paneles = "\n".join(
        f'      <div role="tabpanel" id="panel-{t}" aria-labelledby="tab-{t}"{"" if t == "nuevos" else " hidden"}><div id="tabla-{t}"></div></div>'
        for t in ("nuevos", "modificados", "inactivos"))
    tabla_novedades = (
        '    <section class="tarjeta" aria-labelledby="t-novedades">\n'
        '      <h2 class="tarjeta-tit" id="t-novedades">REGISTROS DE PROGRAMAS ACADÉMICOS NUEVOS, MODIFICADOS E INACTIVOS</h2>\n'
        '      <p class="tarjeta-sub">Novedades del periodo filtrado, con todas las columnas y ordenadas desde la fecha más reciente. '
        'Seleccione la pestaña de cada tipo de novedad: los filtros de esta página afectan siempre las tres tablas. '
        'Haga clic en un encabezado para ordenar; el botón de descarga exporta a Excel los registros filtrados de la tabla visible.</p>\n'
        f'      <div class="tabs" role="tablist" aria-label="Tipo de novedad">{tabs}</div>\n{paneles}\n'
        '    </section>')
    cuerpo = [
        KPIS,
        par(tarjeta("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS", "g-activos",
                    "Número de programas universitarios de pregrado activos en el SNIES en cada ejecución de la herramienta. Cada punto corresponde a una ejecución."),
            tarjeta("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS NUEVOS, MODIFICADOS E INACTIVOS", "g-novedades",
                    "Novedades acumuladas desde el inicio del periodo seleccionado. La pendiente de cada línea muestra el ritmo de aperturas, cambios y cierres.")),
        par(tarjeta("PROGRAMAS ACADÉMICOS ACTIVOS POR SECTOR", "g-sector", "Distribución entre instituciones oficiales y privadas en la ejecución del <span data-fecha-corte></span>."),
            tarjeta("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR SECTOR", "g-sector-evol", "Programas activos de instituciones oficiales y privadas en cada ejecución."), "tercio"),
        par(tarjeta("DEPARTAMENTOS CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS ACTIVOS", "g-deptos", "Departamentos de oferta con mayor número de registros en la ejecución del <span data-fecha-corte></span>, de mayor a menor."),
            tarjeta("UNIVERSIDADES CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS ACTIVOS", "g-univ", "Instituciones con mayor número de registros en la ejecución del <span data-fecha-corte></span>, de mayor a menor.")),
        ancha("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR MODALIDAD", "g-modalidad",
              "Programas activos en cada ejecución según la modalidad agrupada: presencial, virtual, a distancia, híbrida y dual."),
        ancha("PROGRAMAS ACADÉMICOS ACTIVOS POR NÚMERO DE PERIODOS DE DURACIÓN", "g-periodos",
              "Cantidad de registros según la duración del programa en periodos académicos (semestres o equivalentes) en la ejecución del <span data-fecha-corte></span>.", "baja"),
        ancha("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR CAMPO DE CINE ESPECÍFICO", "g-cine", desc_cine, extra=extra_cine),
        ancha("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS ACTIVOS POR DIVISIÓN DE UNINORTE", "g-division",
              "Programas activos en cada ejecución según la división académica de la Universidad del Norte con la que se relacionan. Haga clic en la leyenda para ocultar o aislar divisiones.", "alta"),
        tabla_novedades,
    ]
    html = HEAD.format(css=v("assets/estilos.css"), css_fp=v("assets/vendor/flatpickr/flatpickr.min.css"), title="PROGRAMAS ACADÉMICOS ACTIVOS", desc="Monitor de programas académicos universitarios de pregrado en Colombia (SNIES)",
                       body="", h1="MONITOR SNIES PREGRADO · UNIVERSITARIO", sub=f'\n          <p class="enc-sub">{SUB}</p>',
                       total='<div class="enc-total"><div class="et">ÚLTIMA EJECUCIÓN</div><div class="ev" id="ultima">–</div><div class="es"></div></div>', nav=nav("index.html"))
    html += "\n".join(cuerpo) + FOOT.format(scripts=scripts(["comun", "activos", "nuevos", "modificados", "inactivos"], "inicio"))
    (DOCS / "index.html").write_text(html, encoding="utf-8")

    # ── Detalle ───────────────────────────────────────────────────────────────────
    PAG = {
        "nuevos": dict(t="PROGRAMAS ACADÉMICOS NUEVOS", ht="PROGRAMAS ACADÉMICOS NUEVOS", s="NUEVOS",
                       estado="Programas que aparecen en el SNIES y no estaban en la ejecución anterior (incluye los reactivados), acumulados desde el inicio del periodo seleccionado."),
        "modificados": dict(t="PROGRAMAS ACADÉMICOS MODIFICADOS", ht="PROGRAMAS ACADÉMICOS MODIFICADOS", s="MODIFICADOS",
                            estado="Cambios detectados en créditos, municipio de oferta, nombre, número de periodos o modalidad, acumulados desde el inicio del periodo seleccionado."),
        "inactivos": dict(t="PROGRAMAS ACADÉMICOS INACTIVOS", ht="PROGRAMAS ACADÉMICOS INACTIVOS", s="INACTIVOS",
                          estado="Programas que estaban activos en la ejecución anterior y ya no figuran en el SNIES, acumulados desde el inicio del periodo seleccionado."),
    }
    for tipo, p in PAG.items():
        N, n = p["s"], p["s"].lower()
        evol = tarjeta(f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N}", "g-evolucion", p["estado"])
        if tipo == "modificados":
            # El tipo de cambio se muestra a la derecha de la evolución de los modificados
            c = [par(evol, tarjeta("EVOLUCIÓN DE PROGRAMAS ACADÉMICOS MODIFICADOS POR TIPO DE CAMBIO", "g-tipos",
                                   "Cambios acumulados por tipo: número de créditos, municipio de oferta, nombre del programa, número de periodos y modalidad. Un registro con varios tipos de cambio cuenta una vez en cada tipo."))]
        else:
            c = ['    <div class="fila">\n' + evol + "\n    </div>"]
        desc_cine, extra_cine = _control_cine(10, f"registros {n}")
        c.append(par(tarjeta(f"PROGRAMAS ACADÉMICOS {N} POR SECTOR", "g-sector", "Distribución de los registros del periodo filtrado entre instituciones oficiales y privadas."),
                     tarjeta(f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR SECTOR", "g-sector-evol", f"Programas {n} acumulados de instituciones oficiales y privadas."), "tercio"))
        c.append(par(tarjeta(f"DEPARTAMENTOS CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS {N}", "g-deptos", "Departamentos de oferta con mayor número de registros en el periodo filtrado, de mayor a menor."),
                     tarjeta(f"UNIVERSIDADES CON MAYOR NÚMERO DE PROGRAMAS ACADÉMICOS {N}", "g-instituciones", "Instituciones con mayor número de registros en el periodo filtrado, de mayor a menor.")))
        c.append(ancha(f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR MODALIDAD", "g-modalidad",
                       f"Programas {n} acumulados según la modalidad agrupada: presencial, virtual, a distancia, híbrida y dual."))
        c.append(ancha(f"PROGRAMAS ACADÉMICOS {N} POR NÚMERO DE PERIODOS DE DURACIÓN", "g-periodos",
                       "Cantidad de registros del periodo filtrado según la duración del programa en periodos académicos.", "baja"))
        c.append(ancha(f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR CAMPO DE CINE ESPECÍFICO", "g-cine", desc_cine, extra=extra_cine))
        c.append(ancha(f"EVOLUCIÓN DE PROGRAMAS ACADÉMICOS {N} POR DIVISIÓN DE UNINORTE", "g-division",
                       f"Programas {n} acumulados según la división académica de la Universidad del Norte con la que se relacionan.", "alta"))
        if tipo == "modificados":
            c.append(ancha("TRANSICIONES DE MODALIDAD: ANTERIOR → NUEVA", "g-transiciones",
                           "Número de cambios de modalidad agrupada en el periodo filtrado: cada fila es la modalidad anterior y cada columna, la nueva."))
            c.append(par(
                tarjeta("AUMENTOS Y REDUCCIONES DE NÚMERO DE CRÉDITOS EN PROGRAMAS ACADÉMICOS", "g-creditos",
                        "Programas que aumentaron (barras negras, hacia arriba) o redujeron (barras amarillas, hacia abajo) su número de créditos en cada ejecución."),
                tarjeta("AUMENTOS Y REDUCCIONES DE NÚMERO DE PERIODOS DE DURACIÓN EN PROGRAMAS ACADÉMICOS", "g-periodos-dir",
                        "Programas que alargaron (barras negras, hacia arriba) o acortaron (barras amarillas, hacia abajo) su duración en cada ejecución.")))
        nota = (f"Todos los registros de programas {n} que cumplen los filtros, con todas las columnas, ordenados desde la fecha más reciente. "
                "Haga clic en un encabezado para ordenar según el tipo de dato; el botón de descarga exporta a Excel los registros filtrados.")
        c.append('    <section class="tarjeta" aria-labelledby="t-tabla">\n'
                 f'      <h2 class="tarjeta-tit" id="t-tabla">REGISTROS DE PROGRAMAS ACADÉMICOS {N}</h2>\n'
                 f'      <p class="tarjeta-sub">{nota}</p>\n'
                 '      <div id="tabla"></div>\n'
                 '    </section>')
        html = HEAD.format(css=v("assets/estilos.css"), css_fp=v("assets/vendor/flatpickr/flatpickr.min.css"), title=p["ht"], desc=f'{p["t"].capitalize()} · Monitor SNIES de programas universitarios de pregrado',
                           body=f' data-tipo="{tipo}"', h1=p["t"], sub=f'\n          <p class="enc-sub">{SUB}</p>',
                           total='<div class="enc-total" aria-live="polite"><div class="et">TOTAL</div><div class="ev" id="total">–</div><div class="es" id="total-s"></div></div>',
                           nav=nav(f"{tipo}.html"))
        html += "\n" + "\n".join(c) + FOOT.format(scripts=scripts(["comun", tipo], "detalle"))
        (DOCS / f"{tipo}.html").write_text(html, encoding="utf-8")
    print("  Páginas del Dashboard: index, nuevos, modificados e inactivos")


if __name__ == "__main__":
    main()
