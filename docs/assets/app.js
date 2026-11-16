/* Monitor SNIES Pregrado · Universitario — componentes compartidos del Dashboard */
(function () {
  "use strict";
  const S = window.SNIES || {};
  const C = S.comun;
  const CAT = C.catalogos;

  // ── Colores ───────────────────────────────────────────────────────────────
  const ESTADO = { nuevos: "#1E8E5A", modificados: "#E69F00", inactivos: "#BE0000" };  // validados para daltonismo (ΔE ≥ 8)
  // Paleta categórica validada (orden fijo; el color sigue a la entidad, nunca al rango)
  const CATEGORICA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const GRIS = "#9E9890";
  const SECTOR_COLOR = { PRIVADO: "#BE0000", OFICIAL: "#E69F00" };
  const TINTA = "#1A140C", TINTA2 = "#57504A", REJILLA = "#ECE8E3";

  // ── Utilidades ────────────────────────────────────────────────────────────
  const fmt = (n) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString("es-CO"));
  const fmtFecha = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "");
  const MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];
  const normalizar = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function grupoUniversidad(codigo) {
    const c = Number(codigo);
    if (c === CAT.uninorte) return CAT.grupos_universidad[0];
    if (CAT.mayor_interes.includes(c)) return CAT.grupos_universidad[1];
    return CAT.grupos_universidad[2];
  }
  function grupoModalidad(m) {
    if (m == null || m === "") return "SIN INFORMACIÓN";
    return CAT.modalidad_a_grupo[normalizar(m).trim()] || "SIN INFORMACIÓN";
  }

  /** Tabla columnar (codificada en el generador) → arreglo de registros con campos derivados. */
  function decodificar(t) {
    if (!t) return [];
    const cols = t.columnas;
    const arrs = cols.map((c) => { const d = t.datos[c]; return Array.isArray(d) ? d : d.i.map((i) => d.d[i]); });
    const filas = new Array(t.n);
    for (let r = 0; r < t.n; r++) {
      const o = {};
      for (let j = 0; j < cols.length; j++) o[cols[j]] = arrs[j][r];
      o._GU = grupoUniversidad(o.CODIGO_INSTITUCION);
      o._MG = grupoModalidad(o.MODALIDAD);
      filas[r] = o;
    }
    filas.columnas = cols;
    return filas;
  }

  /** Color estable por entidad: orden fijo del catálogo; 9.ª en adelante punteada; OTRO/SIN INFORMACIÓN en gris. */
  function asignarColores(valores) {
    const mapa = {};
    let k = 0;
    valores.forEach((v) => {
      if (v === "OTRO" || v === "SIN INFORMACIÓN") { mapa[v] = { color: GRIS, dash: "solid" }; return; }
      mapa[v] = { color: CATEGORICA[k % CATEGORICA.length], dash: k < CATEGORICA.length ? "solid" : "dash" };
      k++;
    });
    return mapa;
  }

  // ── Filtros ───────────────────────────────────────────────────────────────
  const DEF_FILTROS = {
    universidades: { etiqueta: "TODAS LAS UNIVERSIDADES", campo: "_GU", plural: "UNIVERSIDADES" },
    divisiones: { etiqueta: "TODAS LAS DIVISIONES", campo: "DIVISION_UNINORTE", plural: "DIVISIONES" },
    cine: { etiqueta: "TODOS LOS CAMPOS DE CINE DETALLADO", campo: "CINE_F_2013_AC_CAMPO_DETALLADO", plural: "CAMPOS CINE", buscar: true },
    fechas: { etiqueta: "TODAS LAS FECHAS", tipo: "fecha" },
    departamentos: { etiqueta: "TODOS LOS DEPARTAMENTOS", campo: "DEPARTAMENTO_OFERTA_PROGRAMA", plural: "DEPARTAMENTOS", buscar: true },
    modalidades: { etiqueta: "TODAS LAS MODALIDADES", campo: "_MG", plural: "MODALIDADES" },
    sectores: { etiqueta: "TODOS LOS SECTORES", campo: "SECTOR", plural: "SECTORES" },
    cambios: { etiqueta: "TODOS LOS CAMBIOS", campo: "TIPO_CAMBIO_DETECTADO", plural: "CAMBIOS" },
  };

  class Multiselect {
    constructor(def, opciones, etiquetas, alCambiar) {
      this.def = def; this.sel = new Set(); this.alCambiar = alCambiar; this.etiquetas = etiquetas || {};
      const el = (this.el = document.createElement("div"));
      el.className = "ms";
      el.innerHTML = `<button type="button" class="ms-btn" aria-haspopup="listbox"><span></span></button>
        <div class="ms-panel" role="listbox" aria-multiselectable="true">
          ${def.buscar ? '<input type="search" placeholder="Buscar..." aria-label="Buscar opción">' : ""}
          <label class="ms-op todos"><input type="checkbox" data-todos checked> ${esc(def.etiqueta)}</label>
          <div class="ms-ops">${opciones.map((o) => `<label class="ms-op"><input type="checkbox" value="${esc(o)}"> ${esc(this.etiquetas[o] || o)}</label>`).join("")}</div>
        </div>`;
      this.btn = el.querySelector(".ms-btn");
      this.btn.addEventListener("click", (e) => { e.stopPropagation(); Multiselect.cerrarTodos(this); el.classList.toggle("abierto"); });
      el.querySelector(".ms-panel").addEventListener("click", (e) => e.stopPropagation());
      el.querySelectorAll(".ms-ops input").forEach((cb) => cb.addEventListener("change", () => {
        cb.checked ? this.sel.add(cb.value) : this.sel.delete(cb.value);
        this.refrescar(); this.alCambiar();
      }));
      el.querySelector("[data-todos]").addEventListener("change", () => { this.limpiar(); this.alCambiar(); });
      const busq = el.querySelector("input[type=search]");
      if (busq) busq.addEventListener("input", () => {
        const q = normalizar(busq.value);
        el.querySelectorAll(".ms-ops .ms-op").forEach((l) => { l.style.display = normalizar(l.textContent).includes(q) ? "" : "none"; });
      });
      Multiselect.todos.push(this);
      this.refrescar();
    }
    static cerrarTodos(excepto) { Multiselect.todos.forEach((m) => m !== excepto && m.el.classList.remove("abierto")); }
    limpiar() { this.sel.clear(); this.el.querySelectorAll(".ms-ops input").forEach((cb) => (cb.checked = false)); this.refrescar(); }
    refrescar() {
      const n = this.sel.size;
      const txt = n === 0 ? this.def.etiqueta : n === 1 ? (this.etiquetas[[...this.sel][0]] || [...this.sel][0]) : `${n} ${this.def.plural}`;
      this.btn.querySelector("span").textContent = txt;
      this.btn.title = n ? [...this.sel].join(" · ") : this.def.etiqueta;
      this.btn.classList.toggle("con-valor", n > 0);
      this.el.querySelector("[data-todos]").checked = n === 0;
    }
  }
  Multiselect.todos = [];
  document.addEventListener("click", () => Multiselect.cerrarTodos(null));

  /**
   * Barra de filtros. config = { buscar: placeholder, claves: [...], registros: [...] (para opciones),
   * camposBusqueda: [...], fechas: [...ISO], alCambiar }
   */
  class Filtros {
    constructor(cont, config) {
      this.cfg = config; this.texto = ""; this.desde = null; this.hasta = null; this.ms = {};
      const barra = document.createElement("div");
      barra.className = "filtros";
      const input = document.createElement("input");
      input.type = "search"; input.className = "buscar"; input.placeholder = config.buscar; input.setAttribute("aria-label", config.buscar);
      let t;
      input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { this.texto = normalizar(input.value).trim(); config.alCambiar(); }, 200); });
      barra.appendChild(input);
      this.input = input;
      config.claves.forEach((clave) => {
        const def = DEF_FILTROS[clave];
        if (def.tipo === "fecha") { this.crearFecha(def, barra); return; }
        const opciones = (config.opciones && config.opciones[clave]) || this.opcionesDe(def.campo);
        const ms = new Multiselect(def, opciones, (config.etiquetas || {})[clave], config.alCambiar);
        this.ms[clave] = ms;
        barra.appendChild(ms.el);
      });
      const limpiar = document.createElement("button");
      limpiar.type = "button"; limpiar.className = "btn btn-limpiar"; limpiar.textContent = "LIMPIAR FILTROS";
      limpiar.addEventListener("click", () => this.limpiar());
      barra.appendChild(limpiar);
      this.conteo = document.createElement("span");
      this.conteo.className = "conteo-filtro";
      barra.appendChild(this.conteo);
      cont.appendChild(barra);
    }
    opcionesDe(campo) {
      const set = new Set();
      this.cfg.registros.forEach((r) => { const v = r[campo]; if (v != null && v !== "") set.add(v); });
      return [...set].sort((a, b) => String(a).localeCompare(String(b), "es"));
    }
    crearFecha(def, barra) {
      const input = document.createElement("input");
      input.type = "text"; input.className = "fecha-input"; input.placeholder = def.etiqueta; input.readOnly = true;
      input.setAttribute("aria-label", "Rango de fechas de ejecución");
      barra.appendChild(input);  // flatpickr inserta su campo visible junto a este: debe estar ya en la página
      const fechas = this.cfg.fechas;
      this.fp = flatpickr(input, {
        mode: "range", locale: "es", dateFormat: "Y-m-d", altInput: true, altFormat: "d/m/Y", altInputClass: "fecha-input",
        enable: fechas, minDate: fechas[0], maxDate: fechas[fechas.length - 1], defaultDate: null,
        onChange: (sel) => {
          if (sel.length === 2) {
            const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
            this.desde = iso(sel[0]); this.hasta = iso(sel[1]);
            this.fp.altInput.classList.add("con-valor");
            this.cfg.alCambiar();
          }
        },
      });
      this.fp.altInput.placeholder = def.etiqueta;
      this.fp.altInput.setAttribute("aria-label", "Rango de fechas de ejecución");
    }
    limpiar() {
      this.texto = ""; this.input.value = "";
      Object.values(this.ms).forEach((m) => m.limpiar());
      if (this.fp) { this.fp.clear(); this.fp.altInput.classList.remove("con-valor"); }
      this.desde = this.hasta = null;
      this.cfg.alCambiar();
    }
    /** ¿El registro cumple los filtros? opciones.sinFecha ignora el rango de fechas. */
    pasa(r, opciones = {}) {
      for (const [clave, ms] of Object.entries(this.ms)) {
        if (ms.sel.size && !ms.sel.has(String(r[DEF_FILTROS[clave].campo]))) return false;
      }
      if (!opciones.sinFecha && this.desde && r.FECHA_DE_OBTENCION) {
        if (r.FECHA_DE_OBTENCION < this.desde || r.FECHA_DE_OBTENCION > this.hasta) return false;
      }
      if (this.texto) {
        const campos = this.cfg.camposBusqueda;
        let ok = false;
        for (const c of campos) { if (normalizar(r[c]).includes(this.texto)) { ok = true; break; } }
        if (!ok) return false;
      }
      return true;
    }
    /** Fechas (de `lista`) dentro del rango seleccionado. */
    enRango(lista) { return this.desde ? lista.filter((f) => f >= this.desde && f <= this.hasta) : lista.slice(); }
  }

  // ── Gráficas (Plotly) ─────────────────────────────────────────────────────
  const CONFIG_PLOTLY = {
    displaylogo: false, responsive: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"],
    toImageButtonOptions: { format: "png", scale: 2 },
  };
  function baseLayout(extra) {
    return Object.assign({
      font: { family: "Montserrat, Segoe UI, Arial, sans-serif", size: 11, color: TINTA2 },
      paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF",
      margin: { l: 56, r: 16, t: 12, b: 48 },
      separators: ",.",
      hoverlabel: { bgcolor: "#FFFFFF", bordercolor: "#D9D3CC", font: { color: TINTA, size: 12 } },
      showlegend: false,
    }, extra || {});
  }
  /** Con conteos pequeños, marcas del eje solo en enteros (evita 0, 0, 1, 1...). */
  function ejeEntero(valores) {
    const max = Math.max(0, ...valores.map((v) => Math.abs(v)));
    return max <= 8 ? { dtick: max <= 4 ? 1 : 2 } : {};
  }
  /** Quita el mensaje "Sin datos" (si lo hay) antes de dibujar; no toca un gráfico existente. */
  function limpiarVacio(div) { if (div.querySelector(".vacio")) div.innerHTML = ""; }
  function vacio(div, msg) {
    Plotly.purge(div);
    div.innerHTML = `<div class="vacio">${esc(msg || "Sin datos para los filtros seleccionados")}</div>`;
  }
  /** Eje X por meses: etiquetas MES AÑO; cada punto es una ejecución. */
  function ejeMeses(fechas) {
    const ini = new Date(fechas[0] + "T00:00:00"), fin = new Date(fechas[fechas.length - 1] + "T00:00:00");
    const meses = (fin.getFullYear() - ini.getFullYear()) * 12 + fin.getMonth() - ini.getMonth() + 1;
    const paso = meses <= 12 ? 1 : meses <= 24 ? 2 : meses <= 36 ? 3 : 6;
    const vals = [], txt = [];
    for (let y = ini.getFullYear(), m = ini.getMonth(); y < fin.getFullYear() || (y === fin.getFullYear() && m <= fin.getMonth()); m += paso) {
      while (m > 11) { m -= 12; y++; }
      if (y > fin.getFullYear() || (y === fin.getFullYear() && m > fin.getMonth())) break;
      vals.push(`${y}-${String(m + 1).padStart(2, "0")}-01`);
      txt.push(`${MESES[m]}<br>${y}`);
    }
    const rango = fechas.length === 1 ? undefined : [fechas[0], fechas[fechas.length - 1]];
    return { type: "date", tickmode: "array", tickvals: vals, ticktext: txt, range: rango, hoverformat: "%d/%m/%Y", showgrid: false, linecolor: "#CFC9C2", ticks: "outside", tickcolor: "#CFC9C2" };
  }

  /**
   * Gráfico de líneas estándar: sin marcadores, eje X por meses y TOTAL arriba a la derecha.
   * series: [{nombre, valores, color, dash}] · modo: "flujo" (TOTAL = suma) | "stock" (TOTAL = último valor).
   */
  function lineas(div, fechas, series, opciones = {}) {
    if (!fechas.length || !series.length) return vacio(div);
    const total = (s) => (opciones.modo === "stock" ? s.valores[s.valores.length - 1] : s.valores.reduce((a, b) => a + b, 0));
    const varias = series.length > 1;
    const trazas = series.map((s) => ({
      type: "scatter", mode: "lines", x: fechas, y: s.valores,
      name: varias ? `${s.nombre} · ${fmt(total(s))}` : s.nombre,
      line: { color: s.color, width: 2, dash: s.dash || "solid", shape: "linear" },
      hovertemplate: `${esc(s.nombre)}: <b>%{y:,}</b><extra></extra>`,
    }));
    const layout = baseLayout({
      xaxis: ejeMeses(fechas),
      yaxis: Object.assign({ rangemode: opciones.modo === "stock" && !varias ? "normal" : "tozero", gridcolor: REJILLA, zeroline: false, tickformat: ",d", automargin: true }, ejeEntero(series.flatMap((s) => s.valores))),
      hovermode: "x unified",
      margin: { l: 56, r: varias ? 12 : 16, t: varias ? 12 : 40, b: 48 },
    });
    if (varias) {
      Object.assign(layout, {
        showlegend: true,
        legend: { title: { text: "<b>TOTAL</b>", font: { color: TINTA, size: 11 } }, x: 1.01, xanchor: "left", y: 1, yanchor: "top", font: { size: 10, color: TINTA }, bgcolor: "rgba(255,255,255,0)", itemclick: "toggle", itemdoubleclick: "toggleothers" },
      });
    } else {
      layout.annotations = [{ xref: "paper", yref: "paper", x: 1, y: 1.02, xanchor: "right", yanchor: "bottom", showarrow: false, text: `<b>TOTAL</b> ${fmt(total(series[0]))}`, font: { size: 12, color: TINTA } }];
    }
    limpiarVacio(div);
    Plotly.react(div, trazas, layout, CONFIG_PLOTLY);
  }

  function barrasH(div, etiquetas, valores, color, opciones = {}) {
    if (!valores.length) return vacio(div);
    const corta = (s) => (s.length > 34 ? s.slice(0, 33) + "…" : s);
    const traza = {
      type: "bar", orientation: "h", y: etiquetas.map(corta).reverse(), x: valores.slice().reverse(),
      customdata: etiquetas.slice().reverse(), marker: { color: Array.isArray(color) ? color.slice().reverse() : color },
      text: valores.slice().reverse().map(fmt), textposition: "outside", cliponaxis: false,
      textfont: { color: TINTA, size: 10 },
      hovertemplate: `%{customdata}: <b>%{x:,}</b>${opciones.unidad ? " " + opciones.unidad : ""}<extra></extra>`,
    };
    const layout = baseLayout({
      margin: { l: 10, r: 40, t: 8, b: 30 }, bargap: 0.35,
      xaxis: Object.assign({ gridcolor: REJILLA, zeroline: true, zerolinecolor: "#CFC9C2", tickformat: ",d" }, ejeEntero(valores)),
      yaxis: { automargin: true, tickfont: { size: 10, color: TINTA } },
    });
    limpiarVacio(div);
    Plotly.react(div, [traza], layout, CONFIG_PLOTLY);
  }

  function barrasV(div, x, y, color, opciones = {}) {
    if (!y.length || !y.some((v) => v > 0)) return vacio(div);
    const traza = {
      type: "bar", x: x.map(String), y, marker: { color }, text: y.map(fmt), textposition: "outside", cliponaxis: false,
      textfont: { color: TINTA, size: 10 },
      hovertemplate: `${opciones.prefijo || ""}%{x}${opciones.sufijo || ""}: <b>%{y:,}</b><extra></extra>`,
    };
    const layout = baseLayout({
      margin: { l: 56, r: 16, t: 20, b: 48 }, bargap: 0.3,
      xaxis: { type: "category", title: { text: opciones.tituloX || "", font: { size: 11 } }, tickfont: { color: TINTA } },
      yaxis: Object.assign({ gridcolor: REJILLA, zeroline: false, tickformat: ",d", rangemode: "tozero" }, ejeEntero(y)),
    });
    limpiarVacio(div);
    Plotly.react(div, [traza], layout, CONFIG_PLOTLY);
  }

  function torta(div, etiquetas, valores, colores) {
    if (!valores.some((v) => v > 0)) return vacio(div);
    const traza = {
      type: "pie", hole: 0.55, labels: etiquetas, values: valores, sort: false, direction: "clockwise",
      marker: { colors: colores, line: { color: "#FFFFFF", width: 2 } },
      texttemplate: "<b>%{label}</b><br>%{percent:.1%}", textposition: "outside", textfont: { color: TINTA, size: 11 },
      hovertemplate: "%{label}: <b>%{value:,}</b> (%{percent:.1%})<extra></extra>",
    };
    const total = valores.reduce((a, b) => a + b, 0);
    const layout = baseLayout({
      margin: { l: 30, r: 30, t: 30, b: 30 },
      annotations: [{ text: `<b>${fmt(total)}</b><br><span style="font-size:10px">TOTAL</span>`, showarrow: false, font: { size: 18, color: TINTA }, x: 0.5, y: 0.5 }],
    });
    limpiarVacio(div);
    Plotly.react(div, [traza], layout, CONFIG_PLOTLY);
  }

  function plotly(div, trazas, layout) { limpiarVacio(div); Plotly.react(div, trazas, baseLayout(layout), CONFIG_PLOTLY); }

  // ── Selector de campos CINE para la gráfica de evolución ──────────────────
  class SelectorCine {
    constructor(cont, opcionesOrdenadas, porDefecto, alCambiar) {
      this.sel = [...porDefecto]; this.alCambiar = alCambiar; this.max = CATEGORICA.length;
      this.colores = {};
      this.sel.forEach((v, i) => (this.colores[v] = CATEGORICA[i]));
      cont.innerHTML = `<div class="cine-ctrl"><select aria-label="Buscar Campo De CINE Detallado"><option value="">Buscar Campo De CINE Detallado</option>
        ${opcionesOrdenadas.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("")}</select>
        <button type="button" class="btn btn-primario">AGREGAR</button></div><div class="chips"></div>`;
      this.select = cont.querySelector("select"); this.chips = cont.querySelector(".chips");
      cont.querySelector("button").addEventListener("click", () => {
        const v = this.select.value;
        if (!v || this.sel.includes(v)) return;
        if (this.sel.length >= this.max) { alert(`Máximo ${this.max} campos a la vez. Quite uno para agregar otro.`); return; }
        const usados = new Set(Object.values(this.colores));
        this.colores[v] = CATEGORICA.find((c) => !usados.has(c)) || CATEGORICA[0];
        this.sel.push(v); this.select.value = ""; this.pintar(); this.alCambiar();
      });
      this.pintar();
    }
    pintar() {
      this.chips.innerHTML = this.sel.map((v) => `<span class="chip"><i style="background:${this.colores[v]}"></i>${esc(v)}<button type="button" data-v="${esc(v)}" aria-label="Quitar ${esc(v)}">×</button></span>`).join("");
      this.chips.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        this.sel = this.sel.filter((x) => x !== b.dataset.v); delete this.colores[b.dataset.v]; this.pintar(); this.alCambiar();
      }));
    }
  }

  // ── Tabla con orden, paginación y exportación a Excel ─────────────────────
  const esFecha = (c) => c.startsWith("FECHA_") || c === "VIGENCIA_TRANSITORIA" || c === "REACTIVADO_POSTERIORMENTE";
  const encabezado = (c) => c.replace(/_/g, " ");
  function celda(c, v) {
    if (v == null || v === "") return "";
    if (esFecha(c)) return fmtFecha(String(v));
    if (typeof v === "number") {
      if (c.startsWith("COSTO_")) return Math.round(v).toLocaleString("es-CO");
      if (c === "PUNTAJE_TOTAL") return v.toLocaleString("es-CO", { maximumFractionDigits: 4 });
      return Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
    }
    return String(v);
  }
  class Tabla {
    constructor(cont, { columnas, archivo, porPagina = 25 }) {
      this.columnas = columnas; this.archivo = archivo; this.porPagina = porPagina;
      this.orden = { col: "FECHA_DE_OBTENCION", asc: false }; this.pagina = 0; this.filas = [];
      cont.innerHTML = `<div class="tabla-cab"><span class="kpi-s" data-n></span><button type="button" class="btn btn-primario" data-xls>DESCARGAR EXCEL</button></div>
        <div class="tabla-env"><table class="datos"><thead><tr>${columnas.map((c) => `<th data-c="${c}" scope="col">${encabezado(c)}<span class="ord"></span></th>`).join("")}</tr></thead><tbody></tbody></table></div>
        <div class="paginacion"><button type="button" class="btn" data-p="-1">ANTERIOR</button><span data-pag></span><button type="button" class="btn" data-p="1">SIGUIENTE</button></div>`;
      this.cont = cont; this.tbody = cont.querySelector("tbody");
      cont.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
        const c = th.dataset.c;
        this.orden = this.orden.col === c ? { col: c, asc: !this.orden.asc } : { col: c, asc: !esFecha(c) };
        this.ordenar(); this.pagina = 0; this.pintar();
      }));
      cont.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => {
        const max = Math.max(0, Math.ceil(this.filas.length / this.porPagina) - 1);
        this.pagina = Math.min(max, Math.max(0, this.pagina + Number(b.dataset.p))); this.pintar();
      }));
      cont.querySelector("[data-xls]").addEventListener("click", () => this.exportar());
    }
    actualizar(filas) { this.filas = filas.slice(); this.ordenar(); this.pagina = 0; this.pintar(); }
    ordenar() {
      const { col, asc } = this.orden, s = asc ? 1 : -1;
      this.filas.sort((a, b) => {
        const x = a[col], y = b[col];
        if (x == null || x === "") return 1;
        if (y == null || y === "") return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "es")) * s;
      });
    }
    pintar() {
      const ini = this.pagina * this.porPagina, pag = this.filas.slice(ini, ini + this.porPagina);
      this.tbody.innerHTML = pag.map((r) => `<tr>${this.columnas.map((c) => {
        const v = r[c];
        return `<td${typeof v === "number" ? ' class="num"' : ""}>${esc(celda(c, v))}</td>`;
      }).join("")}</tr>`).join("") || `<tr><td colspan="${this.columnas.length}">Sin registros para los filtros seleccionados</td></tr>`;
      this.cont.querySelectorAll("th").forEach((th) => {
        th.querySelector(".ord").textContent = th.dataset.c === this.orden.col ? (this.orden.asc ? "▲" : "▼") : "";
        th.setAttribute("aria-sort", th.dataset.c === this.orden.col ? (this.orden.asc ? "ascending" : "descending") : "none");
      });
      const paginas = Math.max(1, Math.ceil(this.filas.length / this.porPagina));
      this.cont.querySelector("[data-pag]").textContent = `PÁGINA ${this.pagina + 1} DE ${paginas}`;
      this.cont.querySelector("[data-n]").textContent = `${fmt(this.filas.length)} registros`;
    }
    exportar() {
      const datos = this.filas.map((r) => {
        const o = {};
        this.columnas.forEach((c) => {
          const v = r[c];
          o[encabezado(c)] = v == null ? "" : esFecha(c) && v ? new Date(String(v) + "T00:00:00") : v;
        });
        return o;
      });
      const hoja = XLSX.utils.json_to_sheet(datos, { cellDates: true, dateNF: "dd/mm/yyyy" });
      hoja["!cols"] = this.columnas.map((c) => ({ wch: Math.min(40, Math.max(12, encabezado(c).length + 2)) }));
      const libro = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(libro, hoja, "Datos");
      XLSX.writeFile(libro, `${this.archivo} ${C.ultima_ejecucion}.xlsx`);
    }
  }

  // ── Agregaciones ──────────────────────────────────────────────────────────
  /** Conteo por fecha (y opcionalmente por categoría) de registros con FECHA_DE_OBTENCION. */
  function contarPorFecha(registros, fechas, campo) {
    const idx = new Map(fechas.map((f, i) => [f, i]));
    if (!campo) {
      const v = new Array(fechas.length).fill(0);
      registros.forEach((r) => { const i = idx.get(r.FECHA_DE_OBTENCION); if (i != null) v[i]++; });
      return v;
    }
    const m = {};
    registros.forEach((r) => {
      const i = idx.get(r.FECHA_DE_OBTENCION); if (i == null) return;
      const k = r[campo] ?? "SIN INFORMACIÓN";
      (m[k] = m[k] || new Array(fechas.length).fill(0))[i]++;
    });
    return m;
  }
  function contar(registros, campo) {
    const m = new Map();
    registros.forEach((r) => { const k = r[campo]; if (k == null || k === "") return; m.set(k, (m.get(k) || 0) + 1); });
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }
  /** Series por categoría con colores estables según `orden` (lista completa de valores posibles). */
  function seriesPorCategoria(mapa, orden, colores) {
    return orden.filter((k) => mapa[k] && mapa[k].some((v) => v > 0)).map((k) => ({ nombre: k, valores: mapa[k], color: colores[k].color, dash: colores[k].dash }));
  }

  window.App = {
    C, CAT, ESTADO, CATEGORICA, GRIS, SECTOR_COLOR, fmt, fmtFecha, esc, normalizar,
    decodificar, asignarColores, Filtros, SelectorCine, Tabla, lineas, barrasH, barrasV, torta, plotly, vacio,
    contarPorFecha, contar, seriesPorCategoria, ejeMeses, ejeEntero, grupoModalidad,
  };
})();
