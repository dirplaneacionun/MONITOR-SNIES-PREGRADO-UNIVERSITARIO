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

import hashlib
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def v(rel):
    """Sufijo de versión según el contenido: el navegador descarga de nuevo el archivo solo si cambió."""
    return f"{rel}?v={hashlib.sha256((DOCS / rel).read_bytes()).hexdigest()[:10]}"
SUB = "Programas Académicos Universitarios de Pregrado en Colombia"

HEAD = '''<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{desc}">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <link rel="icon" href="data:,">
  <link rel="preload" href="assets/fuentes/montserrat-latin.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.css" integrity="sha384-RkASv+6KfBMW9eknReJIJ6b3UnjKOKC5bOUaNgIY778NFbQ8MtWq9Lr/khUgqtTt" crossorigin="anonymous">
  <link rel="stylesheet" href="{css}">
  <script type="importmap">
    {{ "imports": {{
      "hyparquet": "./assets/vendor/hyparquet/src/index.js",
      "hyparquet/": "./assets/vendor/hyparquet/",
      "hyparquet-writer": "./assets/vendor/hyparquet-writer/src/index.js"
    }} }}
  </script>
</head>
<body{body}>
  <header class="encabezado">
    <div class="encabezado-int">
      <div class="enc-fila">
        <div>
          <h1 class="enc-titulo">{h1}</h1>{sub}
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
FOOT = '''
    <p class="pie-pagina">Fuente: SNIES — Sistema Nacional de Información de la Educación Superior, Ministerio de Educación Nacional · Dirección de Planeación, Universidad del Norte</p>
  </main>

  <script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js" integrity="sha384-cCVCZkAjYNxaYKbM8lsArLznDF/SvMFr1jcZrvOpSTCa0W40ZAdLzHCEulnUa5i7" crossorigin="anonymous"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.js" integrity="sha384-5JqMv4L/Xa0hfvtF06qboNdhvuYXUku9ZrhZh3bSk8VXF0A/RuSLHpLsSV9Zqhl6" crossorigin="anonymous"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/l10n/es.min.js" integrity="sha384-Kw6LvVIC7DwgsJQKl9iQVoSEKfjX0hE8/JbVEVnkQNHLI0x2njGmBcqt2VxcPEKC" crossorigin="anonymous"></script>
{scripts}
</body>
</html>
'''


def nav(activa):
    items = [("index.html", "INICIO"), ("nuevos.html", "NUEVOS"), ("modificados.html", "MODIFICADOS"), ("inactivos.html", "INACTIVOS")]
    out = []
    for h, t in items:
        attr = ' class="activa" aria-current="page"' if h == activa else ""
        out.append(f'        <a href="{h}"{attr}>{t}</a>')
    return "\n".join(out)


def tarjeta(tit, gid, desc, clase="", extra=""):
    return (f'      <section class="tarjeta" aria-labelledby="t-{gid}">\n'
            f'        <h2 class="tarjeta-tit" id="t-{gid}">{tit}</h2>\n'
            f'        <p class="tarjeta-sub">{desc}</p>{extra}\n'
            f'        <div class="grafica {clase}" id="{gid}" role="img" aria-label="{tit.capitalize()}"></div>\n'
            f'      </section>')


def ancha(*a, **k):
    return f'    <div class="fila">\n{tarjeta(*a, **k)}\n    </div>'


def par(a, b, cls="dos"):
    return f'    <div class="fila {cls}">\n{a}\n{b}\n    </div>'


def scripts(datos, js):
    lineas = [f'  <script src="{v(f"data/{d}.js")}"></script>' for d in datos]
    lineas += [f'  <script src="{v("assets/app.js")}"></script>', f'  <script src="{v(f"assets/{js}.js")}"></script>']
    return "\n".join(lineas)


def main() -> None:
    # ── Inicio ────────────────────────────────────────────────────────────────────
    KPIS = '''
        <section class="kpis" aria-label="Indicadores de la última ejecución del periodo filtrado">
          <div class="kpi"><div class="kpi-et">PROGRAMAS ACTIVOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-activos">–</div><div class="kpi-s" id="k-activos-s"></div></div>
          <a class="kpi k-nuevos" href="nuevos.html"><div class="kpi-et">NUEVOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-nuevos">–</div><div class="kpi-s">Ejecución del <span data-fecha-ultima></span></div></a>
          <a class="kpi k-modificados" href="modificados.html"><div class="kpi-et">MODIFICADOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-modificados">–</div><div class="kpi-s" id="k-modificados-s"></div></a>
          <a class="kpi k-inactivos" href="inactivos.html"><div class="kpi-et">INACTIVOS (ÚLTIMA EJECUCIÓN)</div><div class="kpi-v" id="k-inactivos">–</div><div class="kpi-s">Ejecución del <span data-fecha-ultima></span></div></a>
        </section>
    '''
    TABLA_INICIO = '''    <section class="tarjeta" aria-labelledby="t-tabla">
          <h2 class="tarjeta-tit" id="t-tabla">PROGRAMAS ACADÉMICOS ACTIVOS (HISTÓRICO)</h2>
          <p class="tarjeta-sub">Consulta del histórico de programas activos: <b data-tabla-periodo></b>. Sin filtro de fecha se muestra la última ejecución; con una fecha, rango, mes o año se incluyen todas las ejecuciones del periodo (un registro por programa y ejecución). Haga clic en un encabezado para ordenar según el tipo de dato; DESCARGAR PARQUET exporta los registros filtrados.</p>
          <div id="tabla-estado" aria-live="polite"></div>
          <div id="tabla"></div>
        </section>'''
    cuerpo = [
        KPIS,
        par(tarjeta("EVOLUCIÓN DE LOS PROGRAMAS ACADÉMICOS ACTIVOS", "g-activos",
                    "Número de programas universitarios de pregrado activos en el SNIES en cada ejecución de la herramienta. Cada punto corresponde a una descarga."),
            tarjeta("EVOLUCIÓN ACUMULADA DE NUEVOS, MODIFICADOS E INACTIVOS", "g-novedades",
                    "Novedades acumuladas desde el inicio del periodo seleccionado. La pendiente de cada línea muestra el ritmo de aperturas, cambios y cierres.")),
        ancha("EVOLUCIÓN DE PROGRAMAS ACTIVOS: UNINORTE FRENTE A LAS UNIVERSIDADES DE MAYOR INTERÉS", "g-mayor-interes",
              "Programas activos en cada ejecución de la Universidad del Norte (rojo) y de cada universidad de mayor interés (gris), "
              "como líneas independientes: no se suman. Pase el cursor sobre una línea para resaltarla; haga clic en la leyenda para "
              "ocultar o aislar universidades. Responde a todos los filtros; el filtro de universidades limita las instituciones mostradas.", "alta"),
        ancha("EVOLUCIÓN DE LOS PROGRAMAS ACTIVOS POR MODALIDAD", "g-modalidad",
              "Programas activos en cada ejecución según la modalidad agrupada: presencial, virtual, a distancia, híbrida y dual."),
        ancha("EVOLUCIÓN DE LOS PROGRAMAS ACTIVOS POR DIVISIÓN DE UNINORTE", "g-division",
              "Programas activos en cada ejecución según la división académica de la Universidad del Norte con la que se relacionan. Haga clic en la leyenda para ocultar o aislar divisiones.", "alta"),
        ancha("EVOLUCIÓN DE LOS PROGRAMAS ACTIVOS POR CAMPO DE CINE DETALLADO", "g-cine",
              "Por defecto se muestran los campos con más programas activos. Use el desplegable y AGREGAR para comparar otros campos (máximo 8).", extra='\n        <div id="cine-ctrl"></div>'),
        par(tarjeta("PROGRAMAS ACTIVOS POR SECTOR", "g-sector", "Distribución entre instituciones oficiales y privadas en la ejecución del <span data-fecha-corte></span>."),
            tarjeta("EVOLUCIÓN DE LOS PROGRAMAS ACTIVOS POR SECTOR", "g-sector-evol", "Programas activos de instituciones oficiales y privadas en cada ejecución."), "tercio"),
        par(tarjeta("TOP 10 DEPARTAMENTOS CON MÁS PROGRAMAS ACTIVOS", "g-deptos", "Departamentos de oferta con más programas activos en la ejecución del <span data-fecha-corte></span>."),
            tarjeta("TOP 10 UNIVERSIDADES CON MÁS PROGRAMAS ACTIVOS", "g-univ", "Instituciones con más programas activos en la ejecución del <span data-fecha-corte></span>.")),
        ancha("PROGRAMAS ACTIVOS SEGÚN NÚMERO DE PERIODOS DE DURACIÓN", "g-periodos",
              "Cantidad de programas activos según su duración en periodos académicos (semestres o equivalentes) en la ejecución del <span data-fecha-corte></span>.", "baja"),
        TABLA_INICIO,
    ]
    html = HEAD.format(css=v("assets/estilos.css"), title="Monitor SNIES Pregrado", desc="Monitor de programas académicos universitarios de pregrado en Colombia (SNIES)",
                       body="", h1="MONITOR SNIES PREGRADO · UNIVERSITARIO", sub=f'\n          <p class="enc-sub">{SUB}</p>',
                       total='<div class="enc-total"><div class="et">ÚLTIMA EJECUCIÓN</div><div class="ev" id="ultima">–</div></div>', nav=nav("index.html"))
    html += "\n".join(cuerpo) + FOOT.format(scripts=scripts(["comun", "activos", "nuevos", "modificados", "inactivos"], "inicio"))
    (DOCS / "index.html").write_text(html, encoding="utf-8")

    # ── Detalle ───────────────────────────────────────────────────────────────────
    PAG = {
        "nuevos": dict(t="PROGRAMAS ACADÉMICOS NUEVOS", sub=SUB, ht="Programas Nuevos · Monitor SNIES", s="NUEVOS",
                       estado="Programas que aparecen en el SNIES y no estaban en la ejecución anterior (incluye los reactivados), acumulados desde el inicio del periodo seleccionado."),
        "modificados": dict(t="PROGRAMAS ACADÉMICOS MODIFICADOS", sub=SUB, ht="Programas Modificados · Monitor SNIES", s="MODIFICADOS",
                            estado="Cambios detectados en créditos, municipio de oferta, nombre, número de periodos o modalidad, acumulados desde el inicio del periodo seleccionado (un registro por cambio)."),
        "inactivos": dict(t="PROGRAMAS ACADÉMICOS INACTIVOS", sub=None, ht="Programas Inactivos · Monitor SNIES", s="INACTIVOS",
                          estado="Programas que estaban activos en la ejecución anterior y ya no figuran en el SNIES, acumulados desde el inicio del periodo seleccionado."),
    }
    for tipo, p in PAG.items():
        N, n = p["s"], p["s"].lower()
        c = [ancha(f"EVOLUCIÓN ACUMULADA DE PROGRAMAS ACADÉMICOS {N}", "g-evolucion", p["estado"])]
        if tipo == "modificados":
            c.append(ancha("EVOLUCIÓN ACUMULADA DE LA MODIFICACIÓN DETECTADA POR TIPO DE CAMBIO", "g-tipos",
                           "Cambios acumulados por tipo: número de créditos, municipio de oferta, nombre del programa, número de periodos y modalidad."))
        c.append(ancha("EVOLUCIÓN ACUMULADA POR DIVISIÓN DE UNINORTE", "g-division",
                       f"Programas {n} acumulados según la división académica de la Universidad del Norte con la que se relacionan.", "alta"))
        c.append(ancha("EVOLUCIÓN ACUMULADA POR MODALIDAD", "g-modalidad",
                       f"Programas {n} acumulados según la modalidad agrupada: presencial, virtual, a distancia, híbrida y dual."))
        c.append(par(tarjeta(f"PROGRAMAS {N} POR SECTOR", "g-sector", "Distribución de los registros del periodo filtrado entre instituciones oficiales y privadas."),
                     tarjeta("EVOLUCIÓN ACUMULADA POR SECTOR", "g-sector-evol", f"Programas {n} acumulados de instituciones oficiales y privadas."), "tercio"))
        c.append(ancha("EVOLUCIÓN ACUMULADA POR CAMPO DE CINE DETALLADO", "g-cine",
                       "Por defecto se muestran los campos con más programas activos. Use el desplegable y AGREGAR para comparar otros campos (máximo 8).",
                       extra='\n        <div id="cine-ctrl"></div>'))
        c.append(par(tarjeta("TOP 10 DEPARTAMENTOS", "g-deptos", "Departamentos de oferta con más registros en el periodo filtrado."),
                     tarjeta("TOP 10 INSTITUCIONES", "g-instituciones", "Instituciones con más registros en el periodo filtrado.")))
        c.append(ancha("REGISTROS SEGÚN NÚMERO DE PERIODOS DE DURACIÓN", "g-periodos",
                       "Cantidad de registros del periodo filtrado según la duración del programa en periodos académicos.", "baja"))
        if tipo == "modificados":
            c.append(par(
                tarjeta("AUMENTOS Y REDUCCIONES DE CRÉDITOS POR EJECUCIÓN", "g-creditos",
                        "Programas que aumentaron (barras negras, hacia arriba) o redujeron (barras rojas, hacia abajo) su número de créditos en cada ejecución.",
                        extra='\n        <p class="nota" id="g-creditos-nota"></p>'),
                tarjeta("AUMENTOS Y REDUCCIONES DE PERIODOS POR EJECUCIÓN", "g-periodos-dir",
                        "Programas que alargaron (barras negras, hacia arriba) o acortaron (barras rojas, hacia abajo) su duración en cada ejecución.",
                        extra='\n        <p class="nota" id="g-periodos-dir-nota"></p>')))
            c.append(ancha("TRANSICIONES DE MODALIDAD: ANTERIOR → NUEVA", "g-transiciones",
                           "Número de cambios de modalidad agrupada en el periodo filtrado: cada fila es la modalidad anterior y cada columna, la nueva."))
        if tipo == "inactivos":
            c.append(par(
                tarjeta("ANTIGÜEDAD DE LOS PROGRAMAS AL INACTIVARSE", "g-antiguedad", "Años transcurridos entre el registro del programa en el SNIES y su inactivación."),
                tarjeta("BALANCE NETO POR DIVISIÓN DE UNINORTE", "g-balance",
                        "Programas nuevos menos programas inactivos del periodo filtrado, por división: verde indica crecimiento y rojo, contracción.")))
        nota = (f"Todos los registros de programas {n} que cumplen los filtros, con todas las columnas, ordenados desde la fecha más reciente. "
                "Haga clic en un encabezado para ordenar según el tipo de dato; DESCARGAR PARQUET exporta los registros filtrados.")
        if tipo == "inactivos":
            nota += " REACTIVADO POSTERIORMENTE indica la fecha en que el programa volvió a estar activo."
        c.append('    <section class="tarjeta" aria-labelledby="t-tabla">\n'
                 f'      <h2 class="tarjeta-tit" id="t-tabla">REGISTROS DE PROGRAMAS {N}</h2>\n'
                 f'      <p class="tarjeta-sub">{nota}</p>\n'
                 '      <div id="tabla"></div>\n'
                 '    </section>')
        html = HEAD.format(css=v("assets/estilos.css"), title=p["ht"], desc=f'{p["t"].capitalize()} — Monitor SNIES de programas universitarios de pregrado',
                           body=f' data-tipo="{tipo}"', h1=p["t"], sub=f'\n          <p class="enc-sub">{p["sub"]}</p>' if p["sub"] else "",
                           total='<div class="enc-total" aria-live="polite"><div class="et">TOTAL</div><div class="ev" id="total">–</div><div class="es" id="total-s"></div></div>',
                           nav=nav(f"{tipo}.html"))
        datos = ["comun", tipo] + (["nuevos"] if tipo == "inactivos" else [])
        html += "\n" + "\n".join(c) + FOOT.format(scripts=scripts(datos, "detalle"))
        (DOCS / f"{tipo}.html").write_text(html, encoding="utf-8")
    print("  Páginas del Dashboard: index, nuevos, modificados e inactivos")


if __name__ == "__main__":
    main()
