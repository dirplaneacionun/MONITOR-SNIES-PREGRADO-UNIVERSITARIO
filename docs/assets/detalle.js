/* Páginas de detalle: Nuevos, Modificados e Inactivos */
(function () {
  "use strict";
  const A = window.App, C = A.C, S = window.SNIES;
  const $ = (id) => document.getElementById(id);
  const tipo = document.body.dataset.tipo;                 // nuevos | modificados | inactivos
  const NOMBRE = { nuevos: "NUEVOS", modificados: "MODIFICADOS", inactivos: "INACTIVOS" }[tipo];
  const color = A.ESTADO[tipo];
  const registros = A.decodificar(S[tipo]);
  const nuevosRef = tipo === "inactivos" ? A.decodificar(S.nuevos) : null;
  const tiposCambio = C.catalogos.tipos_cambio, etqCambio = C.catalogos.tipos_cambio_etiqueta;

  const todas = (campo) => [...new Set(registros.map((v) => v[campo]).filter((x) => x != null && x !== ""))].sort((a, b) => String(a).localeCompare(String(b), "es"));
  const divisiones = todas("DIVISION_UNINORTE");
  const colDiv = A.asignarColores(divisiones);
  const modalidades = C.catalogos.grupos_modalidad;
  const colMod = A.asignarColores(modalidades);
  const colCambio = A.asignarColores(tiposCambio);

  const claves = ["universidades", "divisiones", "cine", "fechas", "departamentos", "modalidades", "sectores"];
  if (tipo === "modificados") claves.push("cambios");
  const filtros = new A.Filtros($("filtros"), {
    buscar: "BUSCAR POR NOMBRE, INSTITUCIÓN, CÓDIGO SNIES...",
    claves, registros,
    opciones: {
      universidades: C.catalogos.grupos_universidad, modalidades, cambios: tiposCambio,
      cine: [...new Set([...C.cine_orden, ...todas("CINE_F_2013_AC_CAMPO_DETALLADO")])].sort((a, b) => a.localeCompare(b, "es")),
    },
    etiquetas: { cambios: etqCambio },
    camposBusqueda: ["NOMBRE_DEL_PROGRAMA", "NOMBRE_INSTITUCION", "CODIGO_SNIES_DEL_PROGRAMA"],
    fechas: C.fechas_ejecucion,
    alCambiar: () => actualizar(),
  });
  const cineOrden = [...new Set([...C.cine_orden, ...todas("CINE_F_2013_AC_CAMPO_DETALLADO")])];
  const selectorCine = new A.SelectorCine($("cine-ctrl"), cineOrden, C.cine_por_defecto, () => actualizar());
  const columnas = registros.columnas.filter((c) => !c.startsWith("_"));
  const tablaUI = new A.Tabla($("tabla"), { columnas, archivo: `Programas Academicos ${NOMBRE.charAt(0)}${NOMBRE.slice(1).toLowerCase()}` });

  const numeros = (cambio) => {
    const [a, b] = String(cambio).split(" → ").map(Number);
    return Number.isFinite(a) && Number.isFinite(b) ? [a, b] : null;
  };

  function direccion(div, filas, tipoCambio, fechas, unidad) {
    const sel = filas.filter((r) => r.TIPO_CAMBIO_DETECTADO === tipoCambio);
    const idx = new Map(fechas.map((f, i) => [f, i]));
    const sube = new Array(fechas.length).fill(0), baja = new Array(fechas.length).fill(0);
    let difs = [];
    sel.forEach((r) => {
      const i = idx.get(r.FECHA_DE_OBTENCION), n = numeros(r.CAMBIO_DETECTADO);
      if (i == null || !n) return;
      if (n[1] > n[0]) sube[i]++; else if (n[1] < n[0]) baja[i]--;
      difs.push(n[1] - n[0]);
    });
    const tot = (v) => Math.abs(v.reduce((a, b) => a + b, 0));
    if (!difs.length) return A.vacio(div);
    const prom = difs.reduce((a, b) => a + b, 0) / difs.length;
    $(div.id + "-nota").textContent = `Variación promedio: ${prom > 0 ? "+" : ""}${prom.toLocaleString("es-CO", { maximumFractionDigits: 1 })} ${unidad} por cambio`;
    A.plotly(div, [
      { type: "bar", x: fechas, y: sube, name: `AUMENTOS · ${A.fmt(tot(sube))}`, marker: { color: "#2a78d6" }, hovertemplate: "Aumentos: <b>%{y:,}</b><extra></extra>" },
      { type: "bar", x: fechas, y: baja, name: `REDUCCIONES · ${A.fmt(tot(baja))}`, marker: { color: "#eb6834" }, customdata: baja.map(Math.abs), hovertemplate: "Reducciones: <b>%{customdata:,}</b><extra></extra>" },
    ], {
      barmode: "relative", bargap: 0.15, hovermode: "x unified",
      xaxis: A.ejeMeses(fechas),
      yaxis: Object.assign({ gridcolor: "#ECE8E3", zeroline: true, zerolinecolor: "#57504A", tickformat: ",d", title: { text: "PROGRAMAS", font: { size: 10 } } }, A.ejeEntero([...sube, ...baja])),
      showlegend: true, margin: { l: 56, r: 12, t: 36, b: 48 },
      legend: { title: { text: "<b>TOTAL</b>" }, orientation: "h", x: 1, xanchor: "right", y: 1.02, yanchor: "bottom", font: { size: 10, color: "#1A140C" } },
    });
  }

  function transiciones(div, filas) {
    const sel = filas.filter((r) => r.TIPO_CAMBIO_DETECTADO === "MODALIDAD");
    if (!sel.length) return A.vacio(div);
    const g = modalidades;
    const z = g.map(() => g.map(() => 0));
    sel.forEach((r) => {
      const [a, b] = String(r.CAMBIO_DETECTADO).split(" → ");
      const i = g.indexOf(A.grupoModalidad(a)), j = g.indexOf(A.grupoModalidad(b));
      if (i >= 0 && j >= 0) z[i][j]++;
    });
    const filasUsadas = g.map((_, i) => i).filter((i) => z[i].some((v) => v) || z.some((f) => f[i]));
    const etq = filasUsadas.map((i) => g[i]);
    const zz = filasUsadas.map((i) => filasUsadas.map((j) => z[i][j]));
    A.plotly(div, [{
      type: "heatmap", x: etq, y: etq, z: zz, xgap: 2, ygap: 2,
      colorscale: [[0, "#F7F4F1"], [0.0001, "#FBE3D6"], [1, "#BE0000"]], showscale: false,
      text: zz.map((f) => f.map((v) => (v ? A.fmt(v) : ""))), texttemplate: "%{text}", textfont: { size: 12 },
      hovertemplate: "DE %{y} A %{x}: <b>%{z:,}</b> cambios<extra></extra>",
    }], {
      margin: { l: 110, r: 12, t: 12, b: 70 },
      xaxis: { title: { text: "MODALIDAD NUEVA", font: { size: 10 } }, tickfont: { color: "#1A140C" }, side: "bottom" },
      yaxis: { title: { text: "MODALIDAD ANTERIOR", font: { size: 10 } }, tickfont: { color: "#1A140C" }, autorange: "reversed" },
    });
  }

  function antiguedad(div, filas) {
    const tramos = ["0 A 2 AÑOS", "3 A 5 AÑOS", "6 A 10 AÑOS", "MÁS DE 10 AÑOS", "SIN FECHA DE REGISTRO"];
    const c = new Array(tramos.length).fill(0);
    filas.forEach((r) => {
      const reg = r.FECHA_DE_REGISTRO_EN_SNIES, obt = r.FECHA_DE_OBTENCION;
      if (!reg) { c[4]++; return; }
      const anios = (new Date(obt) - new Date(reg)) / (365.25 * 864e5);
      c[anios <= 2 ? 0 : anios <= 5 ? 1 : anios <= 10 ? 2 : 3]++;
    });
    const usados = tramos.map((t, i) => [t, c[i]]).filter(([t, v], i) => i < 4 || v > 0);
    A.barrasV(div, usados.map((u) => u[0]), usados.map((u) => u[1]), color, { tituloX: "AÑOS ENTRE EL REGISTRO EN SNIES Y LA INACTIVACIÓN" });
  }

  function balance(div, filasIna) {
    const nue = nuevosRef.filter((r) => filtros.pasa(r));
    const m = {};
    nue.forEach((r) => { const d = r.DIVISION_UNINORTE || "OTRO"; m[d] = (m[d] || 0) + 1; });
    filasIna.forEach((r) => { const d = r.DIVISION_UNINORTE || "OTRO"; m[d] = (m[d] || 0) - 1; });
    const orden = Object.entries(m).sort((a, b) => b[1] - a[1]);
    if (!orden.length) return A.vacio(div);
    const corta = (s) => (s.length > 34 ? s.slice(0, 33) + "…" : s);
    A.plotly(div, [{
      type: "bar", orientation: "h", y: orden.map((o) => corta(o[0])).reverse(), x: orden.map((o) => o[1]).reverse(),
      customdata: orden.map((o) => o[0]).reverse(),
      marker: { color: orden.map((o) => (o[1] >= 0 ? A.ESTADO.nuevos : A.ESTADO.inactivos)).reverse() },
      text: orden.map((o) => (o[1] > 0 ? "+" : "") + A.fmt(o[1])).reverse(), textposition: "outside", cliponaxis: false,
      textfont: { color: "#1A140C", size: 10 },
      hovertemplate: "%{customdata}: <b>%{x:+,}</b> (nuevos − inactivos)<extra></extra>",
    }], {
      margin: { l: 10, r: 40, t: 8, b: 30 }, bargap: 0.35,
      xaxis: Object.assign({ gridcolor: "#ECE8E3", zeroline: true, zerolinecolor: "#57504A", tickformat: "+,d" }, A.ejeEntero(orden.map((o) => o[1]))),
      yaxis: { automargin: true, tickfont: { size: 10, color: "#1A140C" } },
    });
  }

  function actualizar() {
    const filas = registros.filter((r) => filtros.pasa(r));
    const fechas = filtros.enRango(C.fechas_ejecucion);

    // Total del encabezado y conteo
    const programas = new Set(filas.map((r) => r.CODIGO_SNIES_DEL_PROGRAMA)).size;
    $("total").textContent = A.fmt(filas.length);
    $("total-s").textContent = tipo === "modificados" ? `${A.fmt(programas)} programas` : "registros";
    filtros.conteo.textContent = tipo === "modificados" ? `${A.fmt(filas.length)} cambios en ${A.fmt(programas)} programas` : `${A.fmt(filas.length)} programas`;

    A.lineas($("g-evolucion"), fechas, [{ nombre: NOMBRE, valores: A.contarPorFecha(filas, fechas), color }], { modo: "flujo" });
    if (tipo === "modificados") {
      const porTipo = A.contarPorFecha(filas, fechas, "TIPO_CAMBIO_DETECTADO");
      A.lineas($("g-tipos"), fechas, tiposCambio.filter((t) => porTipo[t]).map((t) => ({ nombre: etqCambio[t], valores: porTipo[t], color: colCambio[t].color })), { modo: "flujo" });
    }
    const porDiv = A.contarPorFecha(filas, fechas, "DIVISION_UNINORTE");
    A.lineas($("g-division"), fechas, A.seriesPorCategoria(porDiv, divisiones, colDiv), { modo: "flujo" });
    const porMod = A.contarPorFecha(filas, fechas, "_MG");
    A.lineas($("g-modalidad"), fechas, A.seriesPorCategoria(porMod, modalidades, colMod), { modo: "flujo" });

    const sec = A.contar(filas, "SECTOR");
    A.torta($("g-sector"), sec.map((s) => s[0]), sec.map((s) => s[1]), sec.map((s) => A.SECTOR_COLOR[s[0]] || A.GRIS));
    const porSec = A.contarPorFecha(filas, fechas, "SECTOR");
    A.lineas($("g-sector-evol"), fechas, Object.keys(A.SECTOR_COLOR).filter((s) => porSec[s]).map((s) => ({ nombre: s, valores: porSec[s], color: A.SECTOR_COLOR[s] })), { modo: "flujo" });

    const porCine = A.contarPorFecha(filas, fechas, "CINE_F_2013_AC_CAMPO_DETALLADO");
    A.lineas($("g-cine"), fechas, selectorCine.sel.map((c) => ({ nombre: c, valores: porCine[c] || new Array(fechas.length).fill(0), color: selectorCine.colores[c] })), { modo: "flujo" });

    const dep = A.contar(filas, "DEPARTAMENTO_OFERTA_PROGRAMA").slice(0, 10);
    A.barrasH($("g-deptos"), dep.map((d) => d[0]), dep.map((d) => d[1]), color, { unidad: "registros" });
    const ins = A.contar(filas, "NOMBRE_INSTITUCION").slice(0, 10);
    A.barrasH($("g-instituciones"), ins.map((d) => d[0]), ins.map((d) => d[1]), "#1A140C", { unidad: "registros" });
    const per = A.contar(filas, "NUMERO_PERIODOS_DE_DURACION").sort((a, b) => a[0] - b[0]);
    A.barrasV($("g-periodos"), per.map((p) => p[0]), per.map((p) => p[1]), color, { tituloX: "PERIODOS", sufijo: " periodos" });

    if (tipo === "modificados") {
      direccion($("g-creditos"), filas, "NUMERO DE CREDITOS", fechas, "créditos");
      direccion($("g-periodos-dir"), filas, "NUMERO DE PERIODOS", fechas, "periodos");
      transiciones($("g-transiciones"), filas);
    }
    if (tipo === "inactivos") {
      antiguedad($("g-antiguedad"), filas);
      balance($("g-balance"), filas);
    }
    tablaUI.actualizar(filas);
  }

  actualizar();
})();
