/* Páginas de detalle: Nuevos, Modificados e Inactivos */
(function () {
  "use strict";
  const A = window.App, C = A.C, S = window.SNIES;
  const $ = (id) => document.getElementById(id);
  const tipo = document.body.dataset.tipo;                 // nuevos | modificados | inactivos
  const NOMBRE = { nuevos: "NUEVOS", modificados: "MODIFICADOS", inactivos: "INACTIVOS" }[tipo];
  const CAMPO_CINE = "CINE_F_2013_AC_CAMPO_ESPECIFIC";
  // Color del apartado: verde (nuevos), amarillo (modificados; ocre en líneas) o rojo (inactivos). Es el de las series, el sector privado
  // y las barras de las gráficas del apartado, incluidas las de universidades.
  const color = A.ESTADO[tipo];
  const colorUniversidades = color;
  const colorSector = A.colorSector(color);
  const registros = A.decodificar(S[tipo]);
  const tiposCambio = C.catalogos.tipos_cambio, etqCambio = C.catalogos.tipos_cambio_etiqueta;

  const todas = (campo) => [...new Set(registros.map((v) => v[campo]).filter((x) => x != null && x !== ""))].sort((a, b) => String(a).localeCompare(String(b), "es"));
  const divisiones = todas("DIVISION_UNINORTE");
  const colDiv = A.asignarColores(C.catalogos.divisiones);   // color fijo por división, igual en todas las páginas
  const modalidades = C.catalogos.grupos_modalidad;
  const colMod = A.asignarColores(modalidades);
  const colCambio = A.asignarColores(tiposCambio);
  // Programas: una opción por programa y universidad, con el nombre más reciente de cada código SNIES
  const programas = A.opcionesProgramas([registros]);

  // Rango temporal: cada gráfica empieza en la primera ejecución con datos de su métrica (antes no hay información)
  const inicioPagina = A.primeraFecha(registros);
  const inicioDe = (cambio) => A.primeraFecha(registros.filter((r) => r.TIPO_CAMBIO_DETECTADO === cambio && numeros(r.CAMBIO_DETECTADO)));

  const claves = ["universidades", "programas", "departamentos", "municipios", "sectores", "modalidades", "cine", "divisiones"];
  if (tipo === "modificados") claves.push("cambios");
  claves.push("fechas");
  const filtros = new A.Filtros($("filtros"), {
    buscar: "Buscar por Código de Programa o Institución",
    claves, fuentes: [registros],
    opciones: { modalidades, cambios: tiposCambio, programas: programas.opciones },
    etiquetas: { cambios: etqCambio, modalidades: A.etiquetasModalidad(modalidades) },
    valorDe: { programas: programas.valorDe },
    camposBusqueda: ["CODIGO_INSTITUCION_PADRE", "CODIGO_SNIES_DEL_PROGRAMA"],
    fechas: C.fechas_ejecucion, fechasActivos: [],
    alCambiar: () => actualizar(),
  });
  const selectorCine = new A.SelectorCine($("cine-ctrl"), filtros);
  const columnas = registros.columnas.filter((c) => !c.startsWith("_"));
  const tablaUI = new A.Tabla($("tabla"), { columnas, archivo: `Programas Academicos ${NOMBRE.charAt(0)}${NOMBRE.slice(1).toLowerCase()}` });

  const numeros = (cambio) => {
    const [a, b] = String(cambio).split(" → ").map(Number);
    return Number.isFinite(a) && Number.isFinite(b) ? [a, b] : null;
  };

  /** Programas que aumentaron (barras negras) o redujeron (barras del color del apartado) un valor numérico en cada ejecución. */
  function direccion(div, filas, tipoCambio) {
    // La gráfica empieza en el primer cambio válido de esta métrica, no en la primera ejecución del apartado
    const fechas = filtros.enRango(A.desdeFecha(C.fechas_ejecucion, inicioDe(tipoCambio)));
    const sel = filas.filter((r) => r.TIPO_CAMBIO_DETECTADO === tipoCambio);
    const idx = new Map(fechas.map((f, i) => [f, i]));
    const sube = new Array(fechas.length).fill(0), baja = new Array(fechas.length).fill(0);
    let validos = 0;
    sel.forEach((r) => {
      const i = idx.get(r.FECHA_DE_OBTENCION), n = numeros(r.CAMBIO_DETECTADO);
      if (i == null || !n) return;
      validos++;
      if (n[1] > n[0]) sube[i]++; else if (n[1] < n[0]) baja[i]--;
    });
    const tot = (v) => Math.abs(v.reduce((a, b) => a + b, 0));
    if (!validos) return A.vacio(div);
    A.plotly(div, [
      { type: "bar", x: fechas, y: sube, name: `AUMENTOS · ${A.fmt(tot(sube))}`, marker: { color: A.NEGRO }, hovertemplate: "Aumentos: <b>%{y:,}</b> registros<extra></extra>" },
      { type: "bar", x: fechas, y: baja, name: `REDUCCIONES · ${A.fmt(tot(baja))}`, marker: A.marcador(A.ESTADO.modificados), customdata: baja.map(Math.abs), hovertemplate: "Reducciones: <b>%{customdata:,}</b> registros<extra></extra>" },
    ], {
      barmode: "relative", bargap: 0.15, hovermode: "x unified",
      xaxis: A.ejeMeses(fechas, div.clientWidth - 80),
      yaxis: Object.assign({ gridcolor: "#E5E5E5", zeroline: true, zerolinecolor: A.TINTA2, tickformat: ",d", title: { text: "REGISTROS", font: { size: 10 } } }, A.ejeEntero([...sube, ...baja])),
      showlegend: true, margin: { l: 56, r: 12, t: 36, b: 48 },
      legend: { title: { text: "<b>TOTAL</b>" }, orientation: "h", x: 1, xanchor: "right", y: 1.02, yanchor: "bottom", font: { size: 10, color: A.TINTA } },
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
    const etq = filasUsadas.map((i) => A.etqModalidad(g[i]));
    const zz = filasUsadas.map((i) => filasUsadas.map((j) => z[i][j]));
    A.plotly(div, [{
      type: "heatmap", x: etq, y: etq, z: zz, xgap: 2, ygap: 2,
      colorscale: [[0, "#F5F5F5"], [0.0001, "#FFF5CC"], [1, A.ESTADO.modificados]], showscale: false,
      text: zz.map((f) => f.map((v) => (v ? A.fmt(v) : ""))), texttemplate: "%{text}", textfont: { size: 12 },
      hovertemplate: "DE %{y} A %{x}: <b>%{z:,}</b> registros<extra></extra>",
    }], {
      margin: { l: 110, r: 12, t: 12, b: 70 },
      xaxis: { title: { text: "MODALIDAD NUEVA", font: { size: 10 } }, tickfont: { color: A.TINTA }, side: "bottom" },
      yaxis: { title: { text: "MODALIDAD ANTERIOR", font: { size: 10 } }, tickfont: { color: A.TINTA }, autorange: "reversed" },
    });
  }

  function actualizar() {
    const filas = registros.filter((r) => filtros.pasa(r));
    // Las gráficas temporales empiezan en la primera ejecución con registros del apartado
    const fechas = filtros.enRango(A.desdeFecha(C.fechas_ejecucion, inicioPagina));

    // Total del encabezado y conteo: una sola unidad, "registros"
    $("total").textContent = A.fmt(filas.length);
    $("total-s").textContent = filas.length === 1 ? "registro" : "registros";
    // En Modificados un programa puede tener varios registros de cambio: se informa también el número de programas distintos
    const distintos = new Set(filas.map((r) => r.CODIGO_SNIES_DEL_PROGRAMA)).size;
    filtros.conteo.textContent = A.plural(filas.length, "registro", "registros") + (tipo === "modificados" ? ` · ${A.plural(distintos, "programa distinto", "programas distintos")}` : "");

    A.lineas($("g-evolucion"), fechas, [{ nombre: NOMBRE, valores: A.contarPorFecha(filas, fechas), color }], { modo: "acumulado" });
    if (tipo === "modificados") {
      const porTipo = A.contarPorFecha(filas, fechas, "TIPO_CAMBIO_DETECTADO");
      A.lineas($("g-tipos"), fechas, tiposCambio.filter((t) => porTipo[t]).map((t) => ({ nombre: etqCambio[t], valores: porTipo[t], color: colCambio[t].color })), { modo: "acumulado" });
    }
    const porDiv = A.contarPorFecha(filas, fechas, "DIVISION_UNINORTE");
    A.lineas($("g-division"), fechas, A.seriesPorCategoria(porDiv, divisiones, colDiv), { modo: "acumulado" });
    const porMod = A.contarPorFecha(filas, fechas, "_MG");
    A.lineas($("g-modalidad"), fechas, A.seriesPorCategoria(porMod, modalidades, colMod, A.etiquetasModalidad(modalidades)), { modo: "acumulado" });

    const sec = A.contar(filas, "SECTOR");
    A.torta($("g-sector"), sec.map((s) => s[0]), sec.map((s) => s[1]), sec.map((s) => colorSector[s[0]] || A.GRIS));
    const porSec = A.contarPorFecha(filas, fechas, "SECTOR");
    A.lineas($("g-sector-evol"), fechas, Object.keys(colorSector).filter((s) => porSec[s]).map((s) => ({ nombre: s, valores: porSec[s], color: colorSector[s] })), { modo: "acumulado" });

    // Campos de CINE de la gráfica: sincronizados con el filtro superior (TODOS: los 10 con más registros en este apartado)
    selectorCine.fijar(new Map(A.contar(filas, CAMPO_CINE)));
    const porCine = A.contarPorFecha(filas, fechas, CAMPO_CINE);
    A.lineas($("g-cine"), fechas, selectorCine.sel.map((c) => ({ nombre: c, valores: porCine[c] || new Array(fechas.length).fill(0), color: selectorCine.colorDe(c) })), { modo: "acumulado" });

    const dep = A.contar(filas, "DEPARTAMENTO_OFERTA_PROGRAMA").slice(0, 10);
    A.barrasH($("g-deptos"), dep.map((d) => d[0]), dep.map((d) => d[1]), color);
    const ins = A.contar(filas, "NOMBRE_INSTITUCION").slice(0, 10);
    A.barrasH($("g-instituciones"), ins.map((d) => d[0]), ins.map((d) => d[1]), colorUniversidades);
    const per = A.contar(filas, "NUMERO_PERIODOS_DE_DURACION").sort((a, b) => a[0] - b[0]);
    A.barrasV($("g-periodos"), per.map((p) => p[0]), per.map((p) => p[1]), color, { tituloX: "NÚMERO DE PERIODOS", sufijo: " periodos" });

    if (tipo === "modificados") {
      direccion($("g-creditos"), filas, "NUMERO DE CREDITOS");
      direccion($("g-periodos-dir"), filas, "NUMERO DE PERIODOS");
      transiciones($("g-transiciones"), filas);
    }
    tablaUI.actualizar(filas);
  }

  actualizar();
})();
