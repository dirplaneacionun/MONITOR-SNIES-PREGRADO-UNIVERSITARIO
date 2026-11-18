/* Página de Inicio: programas académicos activos y resumen de novedades */
(function () {
  "use strict";
  const A = window.App, C = A.C, S = window.SNIES;
  const $ = (id) => document.getElementById(id);
  const textos = (sel, t) => document.querySelectorAll(sel).forEach((e) => (e.textContent = t));

  // Datos
  const versiones = A.decodificar(S.activos.versiones);
  S.activos.rangos.forEach((r, i) => (versiones[i]._R = r));
  const nov = { nuevos: A.decodificar(S.nuevos), modificados: A.decodificar(S.modificados), inactivos: A.decodificar(S.inactivos) };
  const fechasActivos = C.fechas_activos;
  $("ultima").textContent = A.fmtFecha(C.ultima_ejecucion);

  // Colores estables por entidad
  const todas = (campo) => [...new Set(versiones.map((v) => v[campo]).filter((x) => x != null && x !== ""))].sort((a, b) => String(a).localeCompare(String(b), "es"));
  const divisiones = todas("DIVISION_UNINORTE");
  const colDiv = A.asignarColores(divisiones);
  const modalidades = C.catalogos.grupos_modalidad;
  const colMod = A.asignarColores(modalidades);

  const filtros = new A.Filtros($("filtros"), {
    buscar: "Buscar Por Programa Académico, Institución, Modalidad, Código SNIES ...",
    claves: ["universidades", "divisiones", "cine", "fechas", "departamentos", "modalidades", "sectores"],
    registros: versiones,
    opciones: { universidades: C.catalogos.grupos_universidad, modalidades, cine: [...C.cine_orden].sort((a, b) => a.localeCompare(b, "es")) },
    camposBusqueda: ["NOMBRE_DEL_PROGRAMA", "NOMBRE_INSTITUCION", "MODALIDAD", "CODIGO_SNIES_DEL_PROGRAMA"],
    fechas: C.fechas_ejecucion,
    alCambiar: () => actualizar(),
  });
  const selectorCine = new A.SelectorCine($("cine-ctrl"), C.cine_orden, C.cine_por_defecto, () => actualizar());

  /** Conteos por fecha de activos para las versiones que pasan los filtros. */
  function evolucion(vs, n, campo) {
    const total = new Array(n).fill(0), por = {};
    vs.forEach((v) => {
      const k = v[campo] ?? "SIN INFORMACIÓN";
      const arr = campo ? (por[k] = por[k] || new Array(n).fill(0)) : null;
      v._R.forEach(([a, b]) => { for (let i = a; i <= b; i++) { total[i]++; if (arr) arr[i]++; } });
    });
    return campo ? por : total;
  }
  const recortar = (serie, ia, ib) => serie.slice(ia, ib + 1);

  function actualizar() {
    const vs = versiones.filter((v) => filtros.pasa(v, { sinFecha: true }));
    const n = fechasActivos.length;
    const fechasR = filtros.enRango(fechasActivos);
    const ia = fechasActivos.indexOf(fechasR[0]), ib = fechasActivos.indexOf(fechasR[fechasR.length - 1]);

    // Evolución de los programas activos (nivel en cada ejecución)
    if (!fechasR.length) {
      ["g-activos", "g-modalidad", "g-division", "g-cine", "g-sector-evol"].forEach((id) => A.vacio($(id), "Sin descargas de programas activos en el periodo seleccionado"));
    } else {
      A.lineas($("g-activos"), fechasR, [{ nombre: "PROGRAMAS ACTIVOS", valores: recortar(evolucion(vs, n), ia, ib), color: A.ROJO }], { modo: "stock" });
      const porMod = evolucion(vs, n, "_MG");
      A.lineas($("g-modalidad"), fechasR, modalidades.filter((m) => porMod[m]).map((m) => ({ nombre: m, valores: recortar(porMod[m], ia, ib), color: colMod[m].color, dash: colMod[m].dash })), { modo: "stock" });
      const porDiv = evolucion(vs, n, "DIVISION_UNINORTE");
      A.lineas($("g-division"), fechasR, divisiones.filter((d) => porDiv[d]).map((d) => ({ nombre: d, valores: recortar(porDiv[d], ia, ib), color: colDiv[d].color, dash: colDiv[d].dash })), { modo: "stock" });
      const porCine = evolucion(vs, n, "CINE_F_2013_AC_CAMPO_DETALLADO");
      A.lineas($("g-cine"), fechasR, selectorCine.sel.map((c) => ({ nombre: c, valores: recortar(porCine[c] || new Array(n).fill(0), ia, ib), color: selectorCine.colores[c].color, dash: selectorCine.colores[c].dash })), { modo: "stock" });
      const porSec = evolucion(vs, n, "SECTOR");
      A.lineas($("g-sector-evol"), fechasR, Object.keys(A.SECTOR_COLOR).filter((s) => porSec[s]).map((s) => ({ nombre: s, valores: recortar(porSec[s], ia, ib), color: A.SECTOR_COLOR[s] })), { modo: "stock" });
    }

    // Estado en la última ejecución del periodo: KPI de activos, torta de sector, top 10 y periodos
    const corte = ib >= 0 ? ib : n - 1;
    const enCorte = ib >= 0 ? vs.filter((v) => v._R.some(([a, b]) => a <= corte && corte <= b)) : [];
    textos("[data-fecha-corte]", A.fmtFecha(fechasActivos[corte]));
    const sec = A.contar(enCorte, "SECTOR");
    A.torta($("g-sector"), sec.map((s) => s[0]), sec.map((s) => s[1]), sec.map((s) => A.SECTOR_COLOR[s[0]] || A.GRIS));
    const dep = A.contar(enCorte, "DEPARTAMENTO_OFERTA_PROGRAMA").slice(0, 10);
    A.barrasH($("g-deptos"), dep.map((d) => d[0]), dep.map((d) => d[1]), A.ROJO, { unidad: "programas" });
    const uni = A.contar(enCorte, "NOMBRE_INSTITUCION").slice(0, 10);
    A.barrasH($("g-univ"), uni.map((d) => d[0]), uni.map((d) => d[1]), A.NEGRO, { unidad: "programas" });
    const per = A.contar(enCorte, "NUMERO_PERIODOS_DE_DURACION").sort((a, b) => a[0] - b[0]);
    A.barrasV($("g-periodos"), per.map((p) => p[0]), per.map((p) => p[1]), A.ROJO, { tituloX: "NÚMERO DE PERIODOS", sufijo: " periodos" });

    // Novedades: evolución acumulada y KPI de la última ejecución del periodo
    const fechasE = filtros.enRango(C.fechas_ejecucion);
    const ultimaE = fechasE[fechasE.length - 1];
    const filtradas = Object.fromEntries(Object.entries(nov).map(([t, regs]) => [t, regs.filter((r) => filtros.pasa(r))]));
    A.lineas($("g-novedades"), fechasE, [
      { nombre: "NUEVOS", valores: A.contarPorFecha(filtradas.nuevos, fechasE), color: A.ESTADO.nuevos },
      { nombre: "MODIFICADOS", valores: A.contarPorFecha(filtradas.modificados, fechasE), color: A.ESTADO.modificados },
      { nombre: "INACTIVOS", valores: A.contarPorFecha(filtradas.inactivos, fechasE), color: A.ESTADO.inactivos },
    ], { modo: "acumulado" });

    const enUltima = (t) => filtradas[t].filter((r) => r.FECHA_DE_OBTENCION === ultimaE);
    const modU = enUltima("modificados");
    $("k-activos").textContent = A.fmt(enCorte.length);
    $("k-activos-s").textContent = ib >= 0 ? `Programas activos al ${A.fmtFecha(fechasActivos[corte])}` : "Sin descargas en el periodo";
    $("k-nuevos").textContent = A.fmt(enUltima("nuevos").length);
    $("k-modificados").textContent = A.fmt(modU.length);
    $("k-modificados-s").textContent = `Cambios en ${A.fmt(new Set(modU.map((r) => r.CODIGO_SNIES_DEL_PROGRAMA)).size)} programa(s) · ${A.fmtFecha(ultimaE)}`;
    $("k-inactivos").textContent = A.fmt(enUltima("inactivos").length);
    textos("[data-fecha-ultima]", ultimaE ? A.fmtFecha(ultimaE) : "–");
    filtros.conteo.textContent = `${A.fmt(enCorte.length)} programas activos`;
  }

  actualizar();
})();
