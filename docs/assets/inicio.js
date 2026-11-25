/* Página de Inicio: programas académicos activos y resumen de novedades */
(function () {
  "use strict";
  const A = window.App, C = A.C, S = window.SNIES;
  const $ = (id) => document.getElementById(id);
  const textos = (sel, t) => document.querySelectorAll(sel).forEach((e) => (e.textContent = t));
  const CAMPO_CINE = "CINE_F_2013_AC_CAMPO_ESPECIFIC";
  const TIPOS = ["nuevos", "modificados", "inactivos"];

  // Datos
  const versiones = A.decodificar(S.activos.versiones);
  S.activos.rangos.forEach((r, i) => (versiones[i]._R = r));
  const nov = Object.fromEntries(TIPOS.map((t) => [t, A.decodificar(S[t])]));
  const inicioNovedades = TIPOS.map((t) => A.primeraFecha(nov[t])).filter(Boolean).sort()[0] || null;   // primera ejecución con novedades
  const fechasActivos = C.fechas_activos;
  $("ultima").textContent = A.fmtFecha(C.ultima_ejecucion);

  // Colores estables por entidad
  const todas = (campo) => [...new Set(versiones.map((v) => v[campo]).filter((x) => x != null && x !== ""))].sort((a, b) => String(a).localeCompare(String(b), "es"));
  const divisiones = todas("DIVISION_UNINORTE");
  const colDiv = A.asignarColores(C.catalogos.divisiones);   // color fijo por división, igual en todas las páginas
  const modalidades = C.catalogos.grupos_modalidad;
  const colMod = A.asignarColores(modalidades);
  // Fuentes de los filtros: las versiones de programas activos y las tres tablas de novedades
  const fuentes = [versiones, ...TIPOS.map((t) => nov[t])];
  // Programas: una opción por programa y universidad, con el nombre más reciente de cada código SNIES
  const programas = A.opcionesProgramas(fuentes, (r) => (r._R ? fechasActivos[r._R[r._R.length - 1][1]] : r.FECHA_DE_OBTENCION) || "");

  const filtros = new A.Filtros($("filtros"), {
    buscar: "Buscar por Programa, Institución o Código",
    claves: ["universidades", "programas", "departamentos", "sectores", "modalidades", "cine", "divisiones", "fechas"],
    fuentes,
    opciones: { modalidades, programas: programas.opciones },
    valorDe: { programas: programas.valorDe },
    fechas: C.fechas_ejecucion, fechasActivos: C.fechas_activos,
    alCambiar: () => actualizar(),
  });
  const selectorCine = new A.SelectorCine($("cine-ctrl"), filtros);

  // Tablas de novedades (una por tipo): se actualizan siempre las tres, aunque solo una esté visible
  const tablas = Object.fromEntries(TIPOS.map((t) => [t, new A.Tabla($(`tabla-${t}`), {
    columnas: (nov[t].columnas || []).filter((c) => !c.startsWith("_")),
    archivo: `Programas Academicos ${t.charAt(0).toUpperCase()}${t.slice(1)}`,
  })]));
  const pestanas = [...document.querySelectorAll("[data-tab]")];
  function mostrarTabla(tipo, foco) {
    pestanas.forEach((b) => {
      const activa = b.dataset.tab === tipo;
      b.setAttribute("aria-selected", activa); b.tabIndex = activa ? 0 : -1;
      $(`panel-${b.dataset.tab}`).hidden = !activa;
      if (activa && foco) b.focus();
    });
  }
  pestanas.forEach((b, i) => {
    b.addEventListener("click", () => mostrarTabla(b.dataset.tab));
    b.addEventListener("keydown", (e) => {
      const paso = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (paso) { e.preventDefault(); mostrarTabla(pestanas[(i + paso + pestanas.length) % pestanas.length].dataset.tab, true); }
    });
  });

  /** Conteos por fecha de activos para las versiones que pasan los filtros. */
  function evolucion(vs, n, campo) {
    const total = new Array(n).fill(0), por = {};
    vs.forEach((v) => {
      const k = v[campo] ?? "SIN INFORMACION";
      const arr = campo ? (por[k] = por[k] || new Array(n).fill(0)) : null;
      v._R.forEach(([a, b]) => { for (let i = a; i <= b; i++) { total[i]++; if (arr) arr[i]++; } });
    });
    return campo ? por : total;
  }

  function actualizar() {
    const vs = versiones.filter((v) => filtros.pasa(v, { sinFecha: true }));
    const n = fechasActivos.length;
    // Ejecuciones con descarga de activos dentro de la selección de fechas (no necesariamente contiguas)
    const fechasR = filtros.enRango(fechasActivos);
    const posiciones = fechasR.map((f) => fechasActivos.indexOf(f));
    const serie = (arr) => posiciones.map((i) => arr[i]);
    const hayCorte = posiciones.length > 0;
    const totales = evolucion(vs, n);

    // Estado en la última ejecución de la selección: KPI de activos, torta de sector, top 10 y periodos
    const corte = hayCorte ? posiciones[posiciones.length - 1] : n - 1;
    const enCorte = hayCorte ? vs.filter((v) => v._R.some(([a, b]) => a <= corte && corte <= b)) : [];

    // Campos de CINE de la gráfica: sincronizados con el filtro superior (TODOS: los 10 con más registros)
    selectorCine.fijar(new Map(A.contar(enCorte, CAMPO_CINE)));

    // Evolución de los programas activos (nivel en cada ejecución)
    if (!hayCorte) {
      ["g-activos", "g-modalidad", "g-division", "g-cine", "g-sector-evol"].forEach((id) => A.vacio($(id), "Sin ejecuciones con programas activos en el periodo seleccionado"));
    } else {
      A.lineas($("g-activos"), fechasR, [{ nombre: "PROGRAMAS ACTIVOS", valores: serie(totales), color: A.NEGRO }], { modo: "stock" });
      const porMod = evolucion(vs, n, "_MG");
      A.lineas($("g-modalidad"), fechasR, modalidades.filter((m) => porMod[m]).map((m) => ({ nombre: m, valores: serie(porMod[m]), color: colMod[m].color })), { modo: "stock" });
      const porDiv = evolucion(vs, n, "DIVISION_UNINORTE");
      A.lineas($("g-division"), fechasR, divisiones.filter((d) => porDiv[d]).map((d) => ({ nombre: d, valores: serie(porDiv[d]), color: colDiv[d].color })), { modo: "stock" });
      const porCine = evolucion(vs, n, CAMPO_CINE);
      A.lineas($("g-cine"), fechasR, selectorCine.sel.map((c) => ({ nombre: c, valores: serie(porCine[c] || new Array(n).fill(0)), color: selectorCine.colorDe(c) })), { modo: "stock" });
      const porSec = evolucion(vs, n, "SECTOR");
      A.lineas($("g-sector-evol"), fechasR, Object.keys(A.SECTOR_COLOR).filter((s) => porSec[s]).map((s) => ({ nombre: s, valores: serie(porSec[s]), color: A.SECTOR_COLOR[s] })), { modo: "stock" });
    }

    textos("[data-fecha-corte]", hayCorte ? A.fmtFecha(fechasActivos[corte]) : "–");
    const sec = A.contar(enCorte, "SECTOR");
    A.torta($("g-sector"), sec.map((s) => s[0]), sec.map((s) => s[1]), sec.map((s) => A.SECTOR_COLOR[s[0]] || A.GRIS));
    const dep = A.contar(enCorte, "DEPARTAMENTO_OFERTA_PROGRAMA").slice(0, 10);
    A.barrasH($("g-deptos"), dep.map((d) => d[0]), dep.map((d) => d[1]), A.NEGRO);
    const uni = A.contar(enCorte, "NOMBRE_INSTITUCION").slice(0, 10);
    A.barrasH($("g-univ"), uni.map((d) => d[0]), uni.map((d) => d[1]), A.NEGRO);
    const per = A.contar(enCorte, "NUMERO_PERIODOS_DE_DURACION").sort((a, b) => a[0] - b[0]);
    A.barrasV($("g-periodos"), per.map((p) => p[0]), per.map((p) => p[1]), A.NEGRO, { tituloX: "NÚMERO DE PERIODOS", sufijo: " periodos" });

    // Novedades: evolución acumulada, KPI de la última ejecución de la selección y tablas
    const fechasE = filtros.enRango(C.fechas_ejecucion);
    const ultimaE = fechasE[fechasE.length - 1];
    // La evolución de novedades empieza en la primera ejecución con registros (antes no hay datos de la métrica)
    const fechasGraf = filtros.enRango(A.desdeFecha(C.fechas_ejecucion, inicioNovedades));
    const filtradas = Object.fromEntries(TIPOS.map((t) => [t, nov[t].filter((r) => filtros.pasa(r))]));
    A.lineas($("g-novedades"), fechasGraf, [
      { nombre: "NUEVOS", valores: A.contarPorFecha(filtradas.nuevos, fechasGraf), color: A.ESTADO.nuevos },
      { nombre: "MODIFICADOS", valores: A.contarPorFecha(filtradas.modificados, fechasGraf), color: A.ESTADO.modificados },
      { nombre: "INACTIVOS", valores: A.contarPorFecha(filtradas.inactivos, fechasGraf), color: A.ESTADO.inactivos },
    ], { modo: "acumulado" });
    TIPOS.forEach((t) => { tablas[t].actualizar(filtradas[t]); textos(`[data-n-tab="${t}"]`, A.fmt(filtradas[t].length)); });

    const enUltima = (t) => filtradas[t].filter((r) => r.FECHA_DE_OBTENCION === ultimaE);
    const modU = enUltima("modificados");
    $("k-activos").textContent = A.fmt(enCorte.length);
    // Con una ejecución anterior (aunque quede fuera de la selección) se muestra la variación, como el correo
    $("k-activos-s").textContent = !hayCorte ? "Sin ejecuciones en el periodo"
      : corte > 0 ? `${A.variacion(enCorte.length, totales[corte - 1])} frente al ${A.fmtFecha(fechasActivos[corte - 1])}`
      : `Programas activos al ${A.fmtFecha(fechasActivos[corte])}`;
    $("k-nuevos").textContent = A.fmt(enUltima("nuevos").length);
    $("k-modificados").textContent = A.fmt(modU.length);
    $("k-modificados-s").textContent = `${A.plural(modU.reduce((s, r) => s + (r._TC ? r._TC.length : 0), 0), "cambio", "cambios")}${ultimaE ? ` · ${A.fmtFecha(ultimaE)}` : ""}`;
    $("k-inactivos").textContent = A.fmt(enUltima("inactivos").length);
    textos("[data-fecha-ultima]", ultimaE ? A.fmtFecha(ultimaE) : "–");
    filtros.conteo.textContent = A.plural(enCorte.length, "registro", "registros");
  }

  actualizar();
})();
