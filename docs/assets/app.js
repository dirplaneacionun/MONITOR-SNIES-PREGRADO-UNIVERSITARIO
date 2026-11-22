/* Monitor SNIES Pregrado · Universitario: componentes compartidos del Dashboard */
(function () {
  "use strict";
  const S = window.SNIES || {};
  const C = S.comun;
  const CAT = C.catalogos;

  // ── Colores (paleta institucional Universidad del Norte, BRANDBOOK_UNINORTE: códigos HEX impresos) ──
  // Rojo Pantone 7427 C, negro Pantone Black 6 C, amarillo Pantone 116 C, ocre Pantone 7556 C y gris K70.
  const ROJO = "#960A11", AMARILLO = "#FFCC00", NEGRO = "#151515", OCRE = "#B78B1E";
  // Estados: verde (funcional, fuera de la marca), amarillo y rojo institucionales.
  // El amarillo rellena (barras, tarjetas, pestañas); en líneas y textos Modificados usa el ocre, porque el amarillo
  // sobre blanco tiene un contraste de solo 1,5:1 y una línea fina casi no se vería.
  const ESTADO = { nuevos: "#1E8E5A", modificados: AMARILLO, inactivos: ROJO };
  /** Color del trazo de una serie: el de relleno, salvo el amarillo de Modificados, que en líneas pasa a ocre. */
  const colorLinea = (c) => (c === ESTADO.modificados ? OCRE : c);
  /** Marcador de barras: el amarillo sobre blanco tiene un contraste de 1,5:1, así que lo delimita un contorno ocre de 1 px
      (con el ocre el borde alcanza 3,1:1). Los demás colores no llevan contorno. Acepta un color o una lista de colores. */
  const marcador = (color) => {
    const trazo = (c) => (c === AMARILLO ? OCRE : "rgba(0,0,0,0)");
    return Array.isArray(color) ? { color, line: { color: color.map(trazo), width: 1 } } : { color, line: { color: trazo(color), width: color === AMARILLO ? 1 : 0 } };
  };
  // Paleta categórica complementaria para las series de las gráficas de líneas (hasta 15, todas con línea continua).
  // Las 8 primeras están validadas para daltonismo (separación ≥ 8 entre colores vecinos); las 7 siguientes amplían
  // la paleta con tonos de matiz y luminosidad distintos. Orden fijo: el color sigue a la categoría, no a su posición.
  const CATEGORICA = ["#960A11", "#2563B0", "#D4A100", "#0E9384", "#7B4FB0", "#E8743B", "#C2408A", "#6B8E23",
    "#5B6770", "#8C5A2B", "#3AA6D0", "#3FA34D", "#1F2A5C", "#E58AB0", "#333333"];
  const MAX_SERIES = CATEGORICA.length;
  const GRIS = "#9E9E9E";
  /** Colores del sector: oficial en negro y privado en el color de acento de la página (rojo por defecto). */
  const colorSector = (acento = ROJO) => ({ PRIVADO: acento, OFICIAL: NEGRO });
  const SECTOR_COLOR = colorSector();
  const TINTA = NEGRO, TINTA2 = "#3E4751", REJILLA = "#E5E5E5";

  // ── Utilidades ────────────────────────────────────────────────────────────
  const fmt = (n) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString("es-CO"));
  const fmtFecha = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "");
  /** Cantidad con su unidad en singular o plural (1 programa, 3 programas), igual que el correo. */
  const plural = (n, singular, plural_) => `${fmt(n)} ${n === 1 ? singular : plural_}`;
  /** Variación frente a la ejecución anterior con signo (+3, −2) o "Sin variación"; mismo formato que el correo. */
  const variacion = (hoy, antes) => {
    const d = hoy - antes;
    return d === 0 ? "Sin variación" : `${d > 0 ? "+" : "−"}${fmt(Math.abs(d))}`;
  };
  /** Etiquetas visibles de las modalidades agrupadas (los datos y la dirección conservan su valor sin tilde: HIBRIDA). */
  const ETIQUETA_MODALIDAD = { HIBRIDA: "HÍBRIDA" };
  const etqModalidad = (m) => ETIQUETA_MODALIDAD[m] || m;
  const etiquetasModalidad = (lista) => Object.fromEntries(lista.map((m) => [m, etqModalidad(m)]));
  const MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];
  const normalizar = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isoDe = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

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
  function derivar(o) {
    o._GU = grupoUniversidad(o.CODIGO_INSTITUCION_PADRE ?? o.CODIGO_INSTITUCION);   // por institución padre, como el correo
    o._MG = grupoModalidad(o.MODALIDAD);
    return o;
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
      filas[r] = derivar(o);
    }
    filas.columnas = cols;
    return filas;
  }

  /** Color estable por entidad: orden fijo de la paleta de 15 colores; OTRO/SIN INFORMACIÓN en gris. Siempre línea continua. */
  function asignarColores(valores) {
    const mapa = {};
    let k = 0;
    valores.forEach((v) => {
      if (v === "OTRO" || v === "SIN INFORMACIÓN") { mapa[v] = { color: GRIS }; return; }
      mapa[v] = { color: CATEGORICA[k % CATEGORICA.length] };
      k++;
    });
    return mapa;
  }

  // ── Filtros desplegables (selección múltiple) ─────────────────────────────
  // `param`: nombre del parámetro en la dirección de la página (#universidad=...&division=...)
  const DEF_FILTROS = {
    universidades: { etiqueta: "TODAS LAS UNIVERSIDADES", plural: "INSTITUCIONES", param: "universidad", buscar: true },
    divisiones: { etiqueta: "TODAS LAS DIVISIONES", campo: "DIVISION_UNINORTE", plural: "DIVISIONES", param: "division" },
    programas: { etiqueta: "TODOS LOS PROGRAMAS ACADÉMICOS", campo: "CODIGO_SNIES_DEL_PROGRAMA", plural: "PROGRAMAS", param: "programa", buscar: true, complemento: true },
    cine: { etiqueta: "TODOS LOS CAMPOS DE CINE ESPECÍFICO", campo: "CINE_F_2013_AC_CAMPO_ESPECIFIC", plural: "CAMPOS CINE", buscar: true, param: "cine" },
    fechas: { etiqueta: "TODAS LAS FECHAS", tipo: "fecha" },
    departamentos: { etiqueta: "TODOS LOS DEPARTAMENTOS", campo: "DEPARTAMENTO_OFERTA_PROGRAMA", plural: "DEPARTAMENTOS", buscar: true, param: "departamento" },
    modalidades: { etiqueta: "TODAS LAS MODALIDADES", campo: "_MG", plural: "MODALIDADES", param: "modalidad" },
    sectores: { etiqueta: "TODOS LOS SECTORES", campo: "SECTOR", plural: "SECTORES", param: "sector" },
    cambios: { etiqueta: "TODOS LOS CAMBIOS", campo: "TIPO_CAMBIO_DETECTADO", plural: "CAMBIOS", param: "cambio", propio: true },
  };
  const SIN_SELECCION = "SIN SELECCIÓN";
  const alfabetico = (a, b) => String(a).localeCompare(String(b), "es");

  const desplegables = [];
  function cerrarDesplegables(excepto) { desplegables.forEach((d) => d !== excepto && d.cerrar()); }
  document.addEventListener("click", () => cerrarDesplegables(null));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarDesplegables(null); });

  /** Alinea el panel desplegable a la derecha del botón si no cabe en la pantalla. */
  function ajustarPanel(el) {
    const panel = el.querySelector(".ms-panel");
    panel.classList.remove("a-derecha");
    if (panel.getBoundingClientRect().right > document.documentElement.clientWidth - 8) panel.classList.add("a-derecha");
  }

  /** Comportamiento común de los filtros desplegables: botón que abre el panel, cierre al elegir otro y accesibilidad. */
  class Desplegable {
    /** Registra el desplegable; `this.el` y `this.btn` ya deben existir. */
    enlazar() {
      this.btn.addEventListener("click", (e) => { e.stopPropagation(); cerrarDesplegables(this); this.alternar(); });
      this.el.querySelector(".ms-panel").addEventListener("click", (e) => e.stopPropagation());
      desplegables.push(this);
    }
    alternar() { const a = this.el.classList.toggle("abierto"); this.btn.setAttribute("aria-expanded", a); if (a) ajustarPanel(this.el); }
    cerrar() { this.el.classList.remove("abierto"); this.btn.setAttribute("aria-expanded", "false"); }
  }

  /** Texto del botón: todas las opciones, sin selección, una opción o la cantidad seleccionada. */
  function textoSeleccion(def, n, total, unica) {
    return n === total ? def.etiqueta : n === 0 ? SIN_SELECCION : n === 1 ? unica : `${n} ${def.plural}`;
  }

  /**
   * Filtro de selección múltiple con la opción TODOS/TODAS sincronizada: TODOS marcada equivale a tener todas las
   * opciones marcadas. Marcar TODOS selecciona todas; desmarcarla las quita todas; marcar o desmarcar una opción
   * actualiza TODOS. Las opciones se muestran en orden alfabético.
   * Estado: `sel` = opciones marcadas. Todas marcadas = sin filtro; ninguna marcada = sin resultados.
   */
  class Multiselect extends Desplegable {
    constructor(def, opciones, etiquetas, alCambiar) {
      super();
      this.def = def; this.alCambiar = alCambiar; this.etiquetas = etiquetas || {};
      const etq = (o) => this.etiquetas[o] || o;
      this.lista = [...new Set(opciones.map(String))].sort((a, b) => alfabetico(etq(a), etq(b)));
      this.opciones = new Set(this.lista); this.sel = new Set(this.lista);
      const el = (this.el = document.createElement("div"));
      el.className = "ms";
      el.innerHTML = `<button type="button" class="ms-btn" aria-haspopup="listbox" aria-expanded="false"><span></span></button>
        <div class="ms-panel" role="listbox" aria-multiselectable="true">
          ${def.buscar ? '<input type="search" placeholder="Buscar..." aria-label="Buscar opción">' : ""}
          <label class="ms-op todos"><input type="checkbox" data-todos checked> ${esc(def.etiqueta)}</label>
          <div class="ms-ops">${this.lista.map((o) => `<label class="ms-op" data-b="${esc(normalizar(`${etq(o)} ${o}`))}"><input type="checkbox" value="${esc(o)}" checked> ${esc(etq(o))}</label>`).join("")}</div>
        </div>`;
      this.btn = el.querySelector(".ms-btn");
      this.enlazar();
      el.querySelectorAll(".ms-ops input").forEach((cb) => cb.addEventListener("change", () => {
        cb.checked ? this.sel.add(cb.value) : this.sel.delete(cb.value);
        this.refrescar(); this.alCambiar();
      }));
      el.querySelector("[data-todos]").addEventListener("change", (e) => {
        this.sel = new Set(e.target.checked ? this.lista : []);
        this.refrescar(); this.alCambiar();
      });
      const busq = el.querySelector("input[type=search]");
      if (busq) busq.addEventListener("input", () => {
        const q = normalizar(busq.value);
        el.querySelectorAll(".ms-ops .ms-op").forEach((l) => { l.style.display = l.dataset.b.includes(q) ? "" : "none"; });
      });
      this.refrescar();
    }
    /** ¿Hay filtro? (alguna opción sin marcar) */
    get activo() { return this.sel.size < this.opciones.size; }
    /** ¿El registro cumple el filtro? Con todas las opciones marcadas pasan todos, también los valores vacíos. */
    pasa(r) { return !this.activo || this.sel.has(String(r[this.def.campo])); }
    limpiar() { this.sel = new Set(this.lista); this.refrescar(); }
    /**
     * Valores para la dirección de la página: ninguno si están todas, [""] si no hay ninguna y las marcadas en otro caso.
     * En listas muy largas (programas) con más de la mitad marcada, se escriben las excluidas precedidas de "!".
     */
    valores() {
      if (!this.activo) return [];
      if (this.sel.size === 0) return [""];
      if (this.def.complemento && this.sel.size > this.opciones.size / 2) return ["!", ...this.lista.filter((o) => !this.sel.has(o))];
      return this.lista.filter((o) => this.sel.has(o));
    }
    /** Restablece la selección desde la dirección, sin avisar; se descartan los valores que no son opciones. */
    fijar(valores) {
      if (!valores.length) this.sel = new Set(this.lista);
      else if (valores.length === 1 && valores[0] === "") this.sel = new Set();
      else if (valores[0] === "!") { const fuera = new Set(valores.slice(1)); const v = this.lista.filter((o) => !fuera.has(o)); this.sel = new Set(v.length ? v : this.lista); }
      else { const v = valores.filter((x) => this.opciones.has(x)); this.sel = new Set(v.length ? v : this.lista); }
      this.refrescar();
    }
    refrescar() {
      const n = this.sel.size, unica = n === 1 ? (this.etiquetas[[...this.sel][0]] || [...this.sel][0]) : "";
      this.el.querySelectorAll(".ms-ops input").forEach((cb) => (cb.checked = this.sel.has(cb.value)));
      this.btn.querySelector("span").textContent = textoSeleccion(this.def, n, this.opciones.size, unica);
      this.btn.title = this.activo ? (n > 15 ? `${n} opciones seleccionadas` : [...this.sel].join(" · ")) : this.def.etiqueta;
      this.btn.classList.toggle("con-valor", this.activo);
      this.el.querySelector("[data-todos]").checked = !this.activo;
    }
  }

  // ── Filtro jerárquico (universidades y campos de CINE) ────────────────────
  /**
   * Opciones agrupadas: cada grupo se puede expandir para elegir sus elementos y su casilla queda marcada, parcial o
   * vacía según sus elementos. Mismo comportamiento de TODOS/TODAS que Multiselect, con barra de búsqueda: al buscar
   * se muestran los grupos con coincidencias, expandidos.
   * grupos = [{ id, etiqueta, token?, expandible, hijos: [{ valor, etiqueta, busqueda? }] }]; `token` es el nombre
   * corto del grupo completo en la dirección; `valorDe(registro)` da el valor del elemento al que pertenece.
   */
  class SelectorJerarquico extends Desplegable {
    constructor(def, grupos, valorDe, alCambiar) {
      super();
      this.def = def; this.grupos = grupos.filter((g) => g.hijos.length); this.valorDe = valorDe; this.alCambiar = alCambiar;
      this.etiquetaDe = {};
      this.grupos.forEach((g) => { g.abierto = false; g.hijos.forEach((h) => { this.etiquetaDe[h.valor] = h.etiqueta; }); });
      this.hojas = Object.keys(this.etiquetaDe);
      this.sel = new Set(this.hojas);
      const fila = (g) => (g.expandible
        ? `<div class="ms-grupo"><div class="ms-op ms-padre"><label><input type="checkbox" data-g="${esc(g.id)}"> ${esc(g.etiqueta)}</label>
             <button type="button" class="ms-expandir" aria-expanded="false" aria-label="Mostrar u ocultar: ${esc(g.etiqueta)}">▸</button></div>
             <div class="ms-hijos" hidden>${g.hijos.map((h) => `<label class="ms-op hijo" data-b="${esc(normalizar(`${h.etiqueta} ${h.busqueda || ""}`))}"><input type="checkbox" data-v="${esc(h.valor)}"> ${esc(h.etiqueta)}</label>`).join("")}</div></div>`
        : `<div class="ms-grupo"><label class="ms-op"><input type="checkbox" data-g="${esc(g.id)}"> ${esc(g.etiqueta)}</label></div>`);
      const el = (this.el = document.createElement("div"));
      el.className = "ms";
      el.innerHTML = `<button type="button" class="ms-btn" aria-haspopup="listbox" aria-expanded="false"><span></span></button>
        <div class="ms-panel" role="listbox" aria-multiselectable="true">
          <input type="search" placeholder="Buscar..." aria-label="Buscar opción">
          <label class="ms-op todos"><input type="checkbox" data-todos checked> ${esc(def.etiqueta)}</label>
          <div class="ms-ops">${this.grupos.map(fila).join("")}</div>
        </div>`;
      this.btn = el.querySelector(".ms-btn");
      this.enlazar();
      this.panel = el.querySelector(".ms-panel");
      this.filas = [...el.querySelectorAll(".ms-grupo")];
      el.querySelector(".ms-ops").addEventListener("change", (e) => {
        const cb = e.target;
        if (cb.dataset.v !== undefined) cb.checked ? this.sel.add(cb.dataset.v) : this.sel.delete(cb.dataset.v);
        else if (cb.dataset.g !== undefined) this.grupos.find((g) => g.id === cb.dataset.g).hijos.forEach((h) => (cb.checked ? this.sel.add(h.valor) : this.sel.delete(h.valor)));
        this.refrescar(); this.alCambiar();
      });
      el.querySelectorAll(".ms-expandir").forEach((b, i) => b.addEventListener("click", () => {
        const g = this.grupos.filter((x) => x.expandible)[i];
        g.abierto = !g.abierto; this.mostrarHijos();
      }));
      el.querySelector("[data-todos]").addEventListener("change", (e) => {
        this.sel = new Set(e.target.checked ? this.hojas : []);
        this.refrescar(); this.alCambiar();
      });
      const busq = el.querySelector("input[type=search]");
      busq.addEventListener("input", () => this.buscar(normalizar(busq.value).trim()));
      this.q = "";
      this.refrescar();
    }
    /** Muestra u oculta la lista de elementos de cada grupo según su estado de expansión y la búsqueda vigente. */
    mostrarHijos() {
      this.filas.forEach((fila, i) => {
        const g = this.grupos[i], hijos = fila.querySelector(".ms-hijos");
        if (!hijos) return;
        const propios = this.q ? [...hijos.children].some((l) => l.dataset.b.includes(this.q)) : false;
        const abierto = this.q ? propios : g.abierto;
        hijos.hidden = !abierto;
        const boton = fila.querySelector(".ms-expandir");
        boton.textContent = abierto ? "▾" : "▸"; boton.setAttribute("aria-expanded", abierto);
      });
    }
    /** Filtra las opciones por texto (sin tildes ni mayúsculas); un grupo cuyo nombre coincide se muestra con todos sus elementos. */
    buscar(q) {
      this.q = q;
      this.filas.forEach((fila, i) => {
        const g = this.grupos[i], coincideGrupo = !q || normalizar(g.etiqueta).includes(q);
        let visibles = 0;
        fila.querySelectorAll(".ms-op.hijo").forEach((l) => {
          const ok = coincideGrupo || l.dataset.b.includes(q);
          l.style.display = ok ? "" : "none"; if (ok) visibles++;
        });
        fila.style.display = coincideGrupo || visibles ? "" : "none";
      });
      this.mostrarHijos();
    }
    get activo() { return this.sel.size < this.hojas.length; }
    pasa(r) { return !this.activo || this.sel.has(this.valorDe(r)); }
    limpiar() { this.sel = new Set(this.hojas); this.refrescar(); }
    /** Valores para la dirección: ninguno si están todos, [""] si no hay ninguno; grupos completos por su token y el resto uno a uno. */
    valores() {
      if (!this.activo) return [];
      if (!this.sel.size) return [""];
      const out = new Set(), cubiertos = new Set();
      this.grupos.forEach((g) => {
        if (g.token && g.hijos.every((h) => this.sel.has(h.valor))) { out.add(g.token); g.hijos.forEach((h) => cubiertos.add(h.valor)); }
      });
      this.hojas.forEach((v) => { if (this.sel.has(v) && !cubiertos.has(v)) out.add(v); });
      return [...out];
    }
    /** Restablece la selección desde la dirección, sin avisar; se descartan los valores desconocidos. */
    fijar(valores) {
      if (!valores.length) this.sel = new Set(this.hojas);
      else if (valores.length === 1 && valores[0] === "") this.sel = new Set();
      else {
        const sel = new Set();
        valores.forEach((v) => {
          const g = this.grupos.find((x) => x.token === v);
          if (g) g.hijos.forEach((h) => sel.add(h.valor));
          else if (this.etiquetaDe[v] !== undefined) sel.add(v);
        });
        this.sel = sel.size ? sel : new Set(this.hojas);
      }
      this.refrescar();
    }
    refrescar() {
      this.el.querySelectorAll("[data-v]").forEach((cb) => (cb.checked = this.sel.has(cb.dataset.v)));
      this.grupos.forEach((g, i) => {
        const n = g.hijos.filter((h) => this.sel.has(h.valor)).length, cb = this.filas[i].querySelector("[data-g]");
        cb.checked = n === g.hijos.length; cb.indeterminate = n > 0 && n < g.hijos.length;
      });
      const n = this.sel.size, todas = !this.activo;
      const completos = this.grupos.filter((g) => g.hijos.every((h) => this.sel.has(h.valor)));
      const parciales = this.grupos.some((g) => !completos.includes(g) && g.hijos.some((h) => this.sel.has(h.valor)));
      const txt = todas || n === 0 || n === 1 || parciales || completos.length > 3 ? textoSeleccion(this.def, n, this.hojas.length, this.etiquetaDe[[...this.sel][0]])
        : completos.map((g) => g.etiqueta).join(" + ");
      this.btn.querySelector("span").textContent = txt;
      this.btn.title = todas ? this.def.etiqueta : n > 15 ? `${n} opciones seleccionadas` : [...this.sel].map((v) => this.etiquetaDe[v]).join(" · ");
      this.btn.classList.toggle("con-valor", !todas);
      this.el.querySelector("[data-todos]").checked = todas;
    }
  }

  /**
   * Filtro de universidades: UNIVERSIDAD DEL NORTE, UNIVERSIDADES DE MAYOR INTERÉS y OTRAS UNIVERSIDADES (con cada institución).
   * Se agrupa por la institución padre (CODIGO_INSTITUCION_PADRE): las seccionales quedan con su institución principal.
   */
  function selectorUniversidades(alCambiar) {
    const [gU, gM, gO] = CAT.grupos_universidad;
    const nombres = CAT.instituciones || {}, uninorte = String(CAT.uninorte), mayor = new Set(CAT.mayor_interes.map(String));
    const repetidos = {};
    Object.values(nombres).forEach((n) => (repetidos[n] = (repetidos[n] || 0) + 1));
    const hijos = (codigos) => codigos.map((c) => ({ valor: c, etiqueta: repetidos[nombres[c]] > 1 ? `${nombres[c]} (${c})` : nombres[c], busqueda: c }))
      .sort((a, b) => alfabetico(a.etiqueta, b.etiqueta));
    const codigos = Object.keys(nombres);
    return new SelectorJerarquico(DEF_FILTROS.universidades, [
      { id: "uninorte", token: "uninorte", etiqueta: gU, expandible: false, hijos: [{ valor: uninorte, etiqueta: gU }] },
      { id: "mayor", token: "mayor", etiqueta: gM, expandible: true, hijos: hijos(codigos.filter((c) => mayor.has(c))) },
      { id: "otras", token: "otras", etiqueta: gO, expandible: true, hijos: hijos(codigos.filter((c) => c !== uninorte && !mayor.has(c))) },
    ], (r) => String(Number(r.CODIGO_INSTITUCION_PADRE ?? r.CODIGO_INSTITUCION)), alCambiar);
  }

  /** Filtro de campos de CINE: campo amplio (primer nivel) y campos específicos (segundo nivel, expandible). */
  function selectorCineJerarquico(alCambiar) {
    const grupos = [...(CAT.cine_jerarquia || [])].sort((a, b) => alfabetico(a[0], b[0]))
      .map(([amplio, especificos], i) => ({ id: `a${i}`, etiqueta: amplio, expandible: true, hijos: [...especificos].sort(alfabetico).map((d) => ({ valor: d, etiqueta: d })) }));
    return new SelectorJerarquico(DEF_FILTROS.cine, grupos, (r) => String(r[DEF_FILTROS.cine.campo]), alCambiar);
  }

  // ── Selector de fechas de ejecución: fecha, rango, mes o año ──────────────
  /**
   * FECHA, MES y AÑO admiten selección múltiple (no contigua: p. ej. enero y marzo sin febrero); RANGO toma todas
   * las ejecuciones entre dos fechas. Solo hay un tipo de selección vigente; al elegir con otro tipo, reemplaza a la anterior.
   */
  class SelectorFechas extends Desplegable {
    constructor(fechas, alCambiar) {
      super();
      this.fechas = fechas; this.fechasSet = new Set(fechas); this.alCambiar = alCambiar;
      this.modo = "fecha"; this.tipoSel = null;   // tipoSel: null (todas), fecha, rango, mes o anio
      this.vals = new Set(); this.desde = null; this.hasta = null;
      const el = (this.el = document.createElement("div"));
      el.className = "ms fs";
      el.innerHTML = `<button type="button" class="ms-btn fs-btn" aria-haspopup="dialog" aria-expanded="false"><span>TODAS LAS FECHAS</span></button>
        <button type="button" class="fs-quitar" aria-label="Restablecer a todas las fechas" title="Restablecer a TODAS LAS FECHAS" hidden>×</button>
        <div class="ms-panel fs-panel" role="dialog" aria-label="Seleccionar fechas de ejecución">
          <div class="fs-modos" role="tablist">
            ${[["fecha", "FECHA"], ["rango", "RANGO"], ["mes", "MES"], ["anio", "AÑO"]].map(([m, t]) => `<button type="button" role="tab" data-modo="${m}">${t}</button>`).join("")}
          </div>
          <p class="fs-ayuda"></p>
          <div class="fs-cuerpo"></div>
          <div class="fs-pie"><button type="button" class="btn btn-reset">TODAS LAS FECHAS</button><span class="fs-nota"></span></div>
        </div>`;
      this.btn = el.querySelector(".fs-btn"); this.quitar = el.querySelector(".fs-quitar");
      this.cuerpo = el.querySelector(".fs-cuerpo"); this.ayuda = el.querySelector(".fs-ayuda"); this.nota = el.querySelector(".fs-nota");
      this.enlazar();
      el.querySelectorAll("[data-modo]").forEach((b) => b.addEventListener("click", () => this.cambiarModo(b.dataset.modo)));
      el.querySelector(".btn-reset").addEventListener("click", () => { this.limpiar(); this.cerrar(); this.alCambiar(); });
      this.quitar.addEventListener("click", (e) => { e.stopPropagation(); this.limpiar(); this.alCambiar(); });
      this.cambiarModo("fecha");
    }
    get activo() { return this.tipoSel !== null; }
    /** ¿La fecha de ejecución (AAAA-MM-DD) está dentro de la selección? Sin selección, todas. */
    coincide(f) {
      switch (this.tipoSel) {
        case "fecha": return this.vals.has(f);
        case "rango": return f >= this.desde && f <= this.hasta;
        case "mes": return this.vals.has(f.slice(0, 7));
        case "anio": return this.vals.has(f.slice(0, 4));
        default: return true;
      }
    }
    /** Fechas (de `lista`) dentro de la selección, en su orden. */
    enRango(lista) { return this.activo ? lista.filter((f) => this.coincide(f)) : lista.slice(); }
    cambiarModo(modo) {
      this.modo = modo;
      this.el.querySelectorAll("[data-modo]").forEach((b) => { const a = b.dataset.modo === modo; b.classList.toggle("activo", a); b.setAttribute("aria-selected", a); });
      if (this.fp) { this.fp.destroy(); this.fp = null; }
      this.cuerpo.innerHTML = ""; this.cuerpo.classList.remove("fs-con-calendario", "fs-calendario-rango");
      const ayudas = {
        fecha: ["Seleccione una o varias ejecuciones. Haga clic de nuevo en una fecha para quitarla.", "Solo se habilitan los días con ejecución"],
        rango: ["Seleccione la fecha inicial y luego la final: se incluyen todas las ejecuciones entre ambas.", "En negrita, los días con ejecución"],
        mes: ["Seleccione uno o varios meses con ejecuciones registradas.", "Solo se habilitan los meses con ejecución"],
        anio: ["Seleccione uno o varios años con ejecuciones registradas.", "Solo se habilitan los años con ejecución"],
      };
      [this.ayuda.textContent, this.nota.textContent] = ayudas[modo];
      if (modo === "fecha" || modo === "rango") {
        const cont = document.createElement("div");
        this.cuerpo.appendChild(cont);
        const actual = this.tipoSel === modo ? (modo === "rango" ? [this.desde, this.hasta] : [...this.vals]) : null;
        const opciones = {
          inline: true, mode: modo === "rango" ? "range" : "multiple", locale: "es", dateFormat: "Y-m-d", conjunction: ", ",
          minDate: this.fechas[0], maxDate: this.fechas[this.fechas.length - 1], defaultDate: actual,
          onChange: (sel) => {
            if (modo === "fecha") this.definir("fecha", sel.map(isoDe));
            else if (sel.length === 2) this.definirRango(isoDe(sel[0]), isoDe(sel[1]));
          },
        };
        // FECHA: solo días con ejecución. RANGO: cualquier día entre la primera y la última ejecución; flatpickr no permite
        // un rango que incluya días deshabilitados, por lo que los días con ejecución solo se resaltan.
        if (modo === "fecha") opciones.enable = this.fechas;
        else opciones.onDayCreate = (_a, _b, _c, dia) => { if (this.fechasSet.has(isoDe(dia.dateObj))) dia.classList.add("con-ejecucion"); };
        this.fp = flatpickr(cont, opciones);
        this.cuerpo.classList.add("fs-con-calendario"); this.cuerpo.classList.toggle("fs-calendario-rango", modo === "rango");
        return;
      }
      const porAnio = {};
      this.fechas.forEach((f) => { (porAnio[f.slice(0, 4)] = porAnio[f.slice(0, 4)] || new Set()).add(Number(f.slice(5, 7))); });
      const anios = Object.keys(porAnio).sort();
      if (modo === "anio") {
        this.cuerpo.innerHTML = `<div class="fs-rejilla">${anios.map((a) => `<button type="button" class="btn" data-anio="${a}" aria-pressed="false">${a}</button>`).join("")}</div>`;
        this.cuerpo.querySelectorAll("[data-anio]").forEach((b) => b.addEventListener("click", () => this.alternarValor("anio", b.dataset.anio)));
      } else {
        this.cuerpo.innerHTML = anios.map((a) => `<div class="fs-anio">${a}</div><div class="fs-rejilla meses">${MESES.map((m, i) => {
          const hay = porAnio[a].has(i + 1);
          return `<button type="button" class="btn" data-mes="${a}-${String(i + 1).padStart(2, "0")}" aria-pressed="false" ${hay ? "" : "disabled"}>${m}</button>`;
        }).join("")}</div>`).join("");
        this.cuerpo.querySelectorAll("[data-mes]").forEach((b) => b.addEventListener("click", () => this.alternarValor("mes", b.dataset.mes)));
      }
      this.marcar();
    }
    /** Marca en el panel los meses o años seleccionados. */
    marcar() {
      this.cuerpo.querySelectorAll("[data-anio],[data-mes]").forEach((b) => {
        const tipo = b.dataset.anio ? "anio" : "mes", activo = this.tipoSel === tipo && this.vals.has(b.dataset.anio || b.dataset.mes);
        b.classList.toggle("activo", activo); b.setAttribute("aria-pressed", activo);
      });
    }
    /** Reemplaza la selección por `valores` del tipo indicado (fecha, mes o anio); sin valores, vuelve a todas las fechas. */
    definir(tipo, valores) {
      this.vals = new Set(valores); this.desde = this.hasta = null; this.tipoSel = valores.length ? tipo : null;
      this.refrescar(); this.marcar(); this.alCambiar();
    }
    definirRango(desde, hasta) {
      this.vals = new Set(); this.desde = desde; this.hasta = hasta; this.tipoSel = "rango";
      this.refrescar(); this.cerrar(); this.alCambiar();
    }
    /** Agrega o quita un mes o año; si había otro tipo de selección, comienza una nueva. */
    alternarValor(tipo, valor) {
      const base = this.tipoSel === tipo ? new Set(this.vals) : new Set();
      base.has(valor) ? base.delete(valor) : base.add(valor);
      this.definir(tipo, [...base]);
    }
    etiquetaSel() {
      const n = this.vals.size, [primero] = [...this.vals].sort();
      switch (this.tipoSel) {
        case "rango": return `${fmtFecha(this.desde)} A ${fmtFecha(this.hasta)}`;
        case "fecha": return n === 1 ? fmtFecha(primero) : `${n} FECHAS`;
        case "mes": return n === 1 ? `${MESES[Number(primero.slice(5)) - 1]} ${primero.slice(0, 4)}` : `${n} MESES`;
        case "anio": return n === 1 ? `AÑO ${primero}` : `${n} AÑOS`;
        default: return "TODAS LAS FECHAS";
      }
    }
    /** Selección como parámetros: fecha=AAAA-MM-DD · mes=AAAA-MM · anio=AAAA (repetidos si hay varios) · desde=...&hasta=... */
    aParams(p) {
      if (this.tipoSel === "rango") { p.set("desde", this.desde); p.set("hasta", this.hasta); return; }
      const param = { fecha: "fecha", mes: "mes", anio: "anio" }[this.tipoSel];
      if (param) [...this.vals].sort().forEach((v) => p.append(param, v));
    }
    /** Restablece la selección desde la dirección, sin avisar. Solo acepta fechas, meses o años con ejecuciones. */
    desdeParams(p) {
      const hay = (pref) => this.fechas.some((f) => f.startsWith(pref));
      const unicos = (lista) => [...new Set(lista)].sort();
      const fechas = unicos(p.getAll("fecha").filter((f) => this.fechasSet.has(f)));
      const d = p.get("desde"), h = p.get("hasta");
      const meses = unicos(p.getAll("mes").filter((m) => /^\d{4}-\d{2}$/.test(m) && hay(m)));
      const anios = unicos(p.getAll("anio").filter((a) => /^\d{4}$/.test(a) && hay(a)));
      const iso = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || "") && isoDe(new Date(`${s}T00:00:00`)) === s;
      this.vals = new Set(); this.desde = this.hasta = null;
      if (fechas.length) { this.tipoSel = "fecha"; this.vals = new Set(fechas); }
      else if (iso(d) && iso(h) && d <= h) { this.tipoSel = "rango"; this.desde = d; this.hasta = h; }
      else if (meses.length) { this.tipoSel = "mes"; this.vals = new Set(meses); }
      else if (anios.length) { this.tipoSel = "anio"; this.vals = new Set(anios); }
      else this.tipoSel = null;
      this.cambiarModo(this.tipoSel || this.modo);   // muestra el modo de la selección, con las fechas marcadas
      this.refrescar();
    }
    limpiar() {
      this.vals = new Set(); this.desde = this.hasta = null; this.tipoSel = null;
      if (this.fp) this.fp.clear(false);
      this.refrescar(); this.marcar();
    }
    refrescar() {
      this.btn.querySelector("span").textContent = this.etiquetaSel();
      this.btn.title = this.tipoSel === "fecha" ? [...this.vals].sort().map(fmtFecha).join(" · ") : this.etiquetaSel();
      this.btn.classList.toggle("con-valor", this.activo);
      this.quitar.hidden = !this.activo;
    }
  }

  /** Enlaces internos del Dashboard (menú y tarjetas): conservan los filtros al cambiar de apartado. */
  const PAGINAS = /^(index|nuevos|modificados|inactivos)\.html$/;

  /**
   * Barra de filtros. config = { buscar, claves, registros (para opciones), opciones, etiquetas,
   * camposBusqueda, fechas, alCambiar }
   *
   * Los filtros se guardan en la dirección de la página (después de #), de modo que una vista filtrada
   * se conserva al recargar, puede compartirse o guardarse como favorito, y se mantiene al pasar a otro
   * apartado. Se usa replaceState para no agregar una entrada al historial del navegador por cada clic.
   */
  class Filtros {
    constructor(cont, config) {
      this.cfg = config; this.texto = ""; this.ms = {};
      const alCambiar = () => { this.escribirUrl(); config.alCambiar(); };
      this.notificar = alCambiar;   // para los controles que modifican un filtro desde fuera de la barra (selector de CINE de la gráfica)
      const barra = document.createElement("div");
      barra.className = "filtros";
      barra.setAttribute("role", "search");
      // En pantallas angostas los filtros se pliegan tras el botón FILTROS (en escritorio el botón no se muestra)
      const toggle = document.createElement("button");
      toggle.type = "button"; toggle.className = "btn filtros-toggle"; toggle.setAttribute("aria-expanded", "false");
      barra.classList.add("plegada");
      toggle.addEventListener("click", () => toggle.setAttribute("aria-expanded", String(!barra.classList.toggle("plegada"))));
      barra.appendChild(toggle);
      this.toggle = toggle;
      const input = document.createElement("input");
      input.type = "search"; input.className = "buscar"; input.placeholder = config.buscar; input.setAttribute("aria-label", config.buscar);
      let t;
      input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { this.texto = normalizar(input.value).trim(); alCambiar(); }, 200); });
      barra.appendChild(input);
      this.input = input;
      config.claves.forEach((clave) => {
        const def = DEF_FILTROS[clave];
        if (def.tipo === "fecha") {
          this.fechas = new SelectorFechas(config.fechas, alCambiar);
          barra.appendChild(this.fechas.el);
          return;
        }
        // Universidades y campos de CINE son filtros jerárquicos; los demás, listas planas en orden alfabético
        const ms = clave === "universidades" ? selectorUniversidades(alCambiar)
          : clave === "cine" ? selectorCineJerarquico(alCambiar)
          : new Multiselect(def, (config.opciones && config.opciones[clave]) || this.opcionesDe(def.campo), (config.etiquetas || {})[clave], alCambiar);
        this.ms[clave] = ms;
        barra.appendChild(ms.el);
      });
      const limpiar = document.createElement("button");
      limpiar.type = "button"; limpiar.className = "btn btn-limpiar"; limpiar.textContent = "LIMPIAR FILTROS";
      limpiar.addEventListener("click", () => this.limpiar());
      barra.appendChild(limpiar);
      this.conteo = document.createElement("span");
      this.conteo.className = "conteo-filtro";
      this.conteo.setAttribute("aria-live", "polite");
      barra.appendChild(this.conteo);
      cont.appendChild(barra);
      // Estado inicial desde la dirección (sin redibujar: la página dibuja una vez al terminar de cargar)
      this.restaurar(new URLSearchParams(location.hash.slice(1)));
      this.escribirUrl();
      // Dirección modificada a mano o por un enlace a la misma página: se aplican los filtros indicados
      window.addEventListener("hashchange", () => { this.restaurar(new URLSearchParams(location.hash.slice(1))); this.escribirUrl(); config.alCambiar(); });
    }
    /** Filtros vigentes como parámetros de la dirección. */
    estado() {
      const p = new URLSearchParams();
      const texto = this.input.value.trim();
      if (texto) p.set("buscar", texto);
      for (const [clave, ms] of Object.entries(this.ms)) ms.valores().forEach((v) => p.append(DEF_FILTROS[clave].param, v));
      if (this.fechas) this.fechas.aParams(p);
      return p;
    }
    /** Aplica los parámetros de la dirección a los controles, sin avisar del cambio. */
    restaurar(p) {
      const texto = p.get("buscar") || "";
      this.input.value = texto; this.texto = normalizar(texto).trim();
      for (const [clave, ms] of Object.entries(this.ms)) ms.fijar(p.getAll(DEF_FILTROS[clave].param));
      if (this.fechas) this.fechas.desdeParams(p);
    }
    /** Texto del botón FILTROS (pantallas angostas): con la cantidad de filtros activos, si hay alguno. */
    actualizarToggle() {
      const n = Object.values(this.ms).filter((m) => m.activo).length + (this.fechas && this.fechas.activo ? 1 : 0) + (this.texto ? 1 : 0);
      this.toggle.textContent = n ? `FILTROS · ${n}` : "FILTROS";
    }
    /** Actualiza la dirección y los enlaces internos con los filtros vigentes. */
    escribirUrl() {
      this.actualizarToggle();
      const p = this.estado(), h = p.toString();
      const url = location.pathname + location.search + (h ? `#${h}` : "");
      if (url !== location.pathname + location.search + location.hash) history.replaceState(null, "", url);
      // Al cambiar de apartado se conservan los filtros comunes (no los propios de un apartado, como el tipo de cambio)
      Object.values(DEF_FILTROS).filter((d) => d.propio).forEach((d) => p.delete(d.param));
      const comun = p.toString();
      document.querySelectorAll("a[href]").forEach((a) => {
        const base = a.dataset.base || a.getAttribute("href").split("#")[0];
        if (!PAGINAS.test(base)) return;
        a.dataset.base = base;
        a.setAttribute("href", comun ? `${base}#${comun}` : base);
      });
    }
    opcionesDe(campo) {
      const set = new Set();
      this.cfg.registros.forEach((r) => { const v = r[campo]; if (v != null && v !== "") set.add(v); });
      return [...set].sort(alfabetico);
    }
    limpiar() {
      this.texto = ""; this.input.value = "";
      Object.values(this.ms).forEach((m) => m.limpiar());
      if (this.fechas) this.fechas.limpiar();
      this.escribirUrl();
      this.cfg.alCambiar();
    }
    /** ¿El registro cumple los filtros? opciones.sinFecha ignora la selección de fechas. */
    pasa(r, opciones = {}) {
      for (const ms of Object.values(this.ms)) if (!ms.pasa(r)) return false;
      if (!opciones.sinFecha && this.fechas && this.fechas.activo && r.FECHA_DE_OBTENCION && !this.fechas.coincide(r.FECHA_DE_OBTENCION)) return false;
      if (this.texto) {
        let ok = false;
        for (const c of this.cfg.camposBusqueda) { if (normalizar(r[c]).includes(this.texto)) { ok = true; break; } }
        if (!ok) return false;
      }
      return true;
    }
    /** Fechas (de `lista`) dentro de la selección de fechas. */
    enRango(lista) { return this.fechas ? this.fechas.enRango(lista) : lista.slice(); }
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
      hoverlabel: { bgcolor: "#FFFFFF", bordercolor: "#E5E5E5", font: { color: TINTA, size: 12 } },
      showlegend: false,
    }, extra || {});
  }
  /** Con conteos pequeños, marcas del eje solo en enteros (evita 0, 0, 1, 1...). */
  function ejeEntero(valores, desdeCero = true) {
    const abs = valores.map((v) => Math.abs(v));
    const max = Math.max(0, ...abs), min = desdeCero ? 0 : Math.min(...abs);
    const rango = max - min;   // rango visible del eje: con rangos cortos, marcas solo en enteros
    return rango <= 8 ? { dtick: rango <= 4 ? 1 : 2 } : {};
  }
  /** Quita el mensaje "Sin datos" (si lo hay) antes de dibujar; no toca un gráfico existente. */
  function limpiarVacio(div) { if (div.querySelector(".vacio")) div.innerHTML = ""; }
  function vacio(div, msg) {
    Plotly.purge(div);
    div.innerHTML = `<div class="vacio">${esc(msg || "Sin datos para los filtros seleccionados")}</div>`;
  }
  /**
   * Eje X por meses: etiquetas MES AÑO; cada punto es una ejecución. Con `ancho` (píxeles del gráfico) se
   * amplía el paso entre etiquetas para que no se superpongan en pantallas angostas (unos 48 px por etiqueta).
   */
  function ejeMeses(fechas, ancho) {
    const ini = new Date(fechas[0] + "T00:00:00"), fin = new Date(fechas[fechas.length - 1] + "T00:00:00");
    const meses = (fin.getFullYear() - ini.getFullYear()) * 12 + fin.getMonth() - ini.getMonth() + 1;
    let paso = meses <= 12 ? 1 : meses <= 24 ? 2 : meses <= 36 ? 3 : 6;
    if (ancho) { const max = Math.max(2, Math.floor(ancho / 48)); while (Math.ceil(meses / paso) > max && paso < 12) paso = paso < 3 ? paso + 1 : paso * 2; }
    const vals = [], txt = [];
    for (let y = ini.getFullYear(), m = ini.getMonth(); ; m += paso) {
      while (m > 11) { m -= 12; y++; }
      if (y > fin.getFullYear() || (y === fin.getFullYear() && m > fin.getMonth())) break;
      vals.push(`${y}-${String(m + 1).padStart(2, "0")}-01`);
      txt.push(`${MESES[m]}<br>${y}`);
    }
    if (fechas.length === 1) {
      // Una sola ejecución: el eje muestra esa fecha, con unos días de margen a cada lado
      const d = new Date(fechas[0] + "T00:00:00");
      const desplazar = (n) => isoDe(new Date(d.getFullYear(), d.getMonth(), d.getDate() + n));
      return { type: "date", tickmode: "array", tickvals: [fechas[0]], ticktext: [fmtFecha(fechas[0])], range: [desplazar(-3), desplazar(3)], hoverformat: "%d/%m/%Y", showgrid: false, linecolor: "#BDBDBD", ticks: "outside", tickcolor: "#BDBDBD" };
    }
    const rango = [fechas[0], fechas[fechas.length - 1]];
    return { type: "date", tickmode: "array", tickvals: vals, ticktext: txt, range: rango, hoverformat: "%d/%m/%Y", showgrid: false, linecolor: "#BDBDBD", ticks: "outside", tickcolor: "#BDBDBD" };
  }
  const acumular = (v) => { let s = 0; return v.map((x) => (s += x)); };

  /**
   * Gráfico de líneas estándar: sin marcadores, eje X por meses y TOTAL arriba a la derecha.
   * series: [{nombre, valores, color, dash}]
   * modo "acumulado": valores por ejecución que se acumulan (TOTAL = acumulado al final del periodo).
   * modo "stock": valores que ya son un nivel en cada ejecución (TOTAL = valor de la última fecha).
   */
  function lineas(div, fechas, series, opciones = {}) {
    if (!fechas.length || !series.length) return vacio(div);
    const total = (s) => s.valores[s.valores.length - 1];
    const acumuladas = opciones.modo === "acumulado" ? series.map((s) => Object.assign({}, s, { valores: acumular(s.valores) })) : series;
    // Leyenda (y orden de las series) de mayor a menor TOTAL; ante empate se conserva el orden recibido.
    // El color de cada serie no cambia: sigue a la categoría, no a su posición.
    const ser = acumuladas.map((s, i) => [s, i]).sort((a, b) => total(b[0]) - total(a[0]) || a[1] - b[1]).map((x) => x[0]);
    const varias = ser.length > 1;
    const etiquetaHover = opciones.modo === "acumulado" ? "registros acumulados" : "registros";
    // Pantalla angosta: la leyenda pasa debajo del gráfico (a la derecha taparía las líneas) y el contenedor crece
    const angosta = varias && div.clientWidth > 0 && div.clientWidth < 640;
    div.style.height = angosta ? `${300 + ser.length * 22}px` : "";
    const trazas = ser.map((s) => ({
      type: "scatter", mode: fechas.length === 1 ? "lines+markers" : "lines", x: fechas, y: s.valores,   // con una sola fecha una línea no se dibuja: se muestra el punto
      name: varias ? `${angosta && s.nombre.length > 36 ? s.nombre.slice(0, 35) + "…" : s.nombre} · ${fmt(total(s))}` : s.nombre,
      line: { color: colorLinea(s.color), width: 2, dash: "solid", shape: "linear" },   // siempre línea continua
      marker: { color: colorLinea(s.color), size: 9 },
      hovertemplate: `${esc(s.nombre)}: <b>%{y:,}</b> ${etiquetaHover}<extra></extra>`,
    }));
    const layout = baseLayout({
      xaxis: ejeMeses(fechas, div.clientWidth - 80),
      yaxis: Object.assign({ rangemode: opciones.modo === "stock" && !varias ? "normal" : "tozero", gridcolor: REJILLA, zeroline: false, tickformat: ",d", automargin: true },
        ejeEntero(ser.flatMap((s) => s.valores), !(opciones.modo === "stock" && !varias))),
      hovermode: "x unified",
      margin: { l: 56, r: varias ? 12 : 16, t: varias ? 12 : 40, b: 48 },
    });
    if (varias) {
      Object.assign(layout, {
        showlegend: true,
        legend: Object.assign({ title: { text: "<b>TOTAL</b>", font: { color: TINTA, size: 11 } }, font: { size: 10, color: TINTA }, bgcolor: "rgba(255,255,255,0)", itemclick: "toggle", itemdoubleclick: "toggleothers" },
          angosta ? { orientation: "h", x: 0, xanchor: "left", y: -0.12, yanchor: "top" } : { x: 1.01, xanchor: "left", y: 1, yanchor: "top" }),
      });
    } else {
      layout.annotations = [{ xref: "paper", yref: "paper", x: 1, y: 1.02, xanchor: "right", yanchor: "bottom", showarrow: false, text: `<b>TOTAL</b> ${fmt(total(ser[0]))}`, font: { size: 12, color: TINTA } }];
    }
    limpiarVacio(div);
    Plotly.react(div, trazas, layout, CONFIG_PLOTLY);
  }

  function barrasH(div, etiquetas, valores, color, opciones = {}) {
    if (!valores.length) return vacio(div);
    const corta = (s) => (s.length > 34 ? s.slice(0, 33) + "…" : s);
    const traza = {
      type: "bar", orientation: "h", y: etiquetas.map(corta).reverse(), x: valores.slice().reverse(),
      customdata: etiquetas.slice().reverse(), marker: marcador(Array.isArray(color) ? color.slice().reverse() : color),
      text: valores.slice().reverse().map(fmt), textposition: "outside", cliponaxis: false,
      textfont: { color: TINTA, size: 10 },
      hovertemplate: "%{customdata}: <b>%{x:,}</b> registros<extra></extra>",
    };
    const layout = baseLayout({
      margin: { l: 10, r: 40, t: 8, b: 30 }, bargap: 0.35,
      xaxis: Object.assign({ gridcolor: REJILLA, zeroline: true, zerolinecolor: "#BDBDBD", tickformat: ",d" }, ejeEntero(valores)),
      yaxis: { automargin: true, tickfont: { size: 10, color: TINTA } },
    });
    limpiarVacio(div);
    Plotly.react(div, [traza], layout, CONFIG_PLOTLY);
  }

  function barrasV(div, x, y, color, opciones = {}) {
    if (!y.length || !y.some((v) => v > 0)) return vacio(div);
    const traza = {
      type: "bar", x: x.map(String), y, marker: marcador(color), text: y.map(fmt), textposition: "outside", cliponaxis: false,
      textfont: { color: TINTA, size: 10 },
      hovertemplate: `${opciones.prefijo || ""}%{x}${opciones.sufijo || ""}: <b>%{y:,}</b> registros<extra></extra>`,
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
      marker: { colors: colores, line: { color: colores.map((c) => (c === AMARILLO ? OCRE : "#FFFFFF")), width: 2 } },
      texttemplate: "<b>%{label}</b><br>%{percent:.1%}", textposition: "outside", textfont: { color: TINTA, size: 11 },
      hovertemplate: "%{label}: <b>%{value:,}</b> registros (%{percent:.1%})<extra></extra>",
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

  // ── Gráfica de evolución por campo de CINE específico ─────────────────────
  /**
   * Controla las series de la gráfica de evolución por campo de CINE específico (todas con línea continua).
   * Su desplegable jerárquico (campo amplio → campo específico, con búsqueda en ambos niveles) y el del filtro
   * superior comparten la misma selección: marcar o desmarcar en uno se refleja en el otro y en toda la página.
   * Con TODOS marcado la gráfica muestra los CINE_DEFECTO campos con más registros; con una selección, esos campos
   * (los de más registros si son más de MAX_SERIES). Cada campo conserva su color mientras se muestre.
   */
  const CINE_DEFECTO = 10;
  class SelectorCine {
    constructor(cont, filtros) {
      this.filtros = filtros; this.global = filtros.ms.cine; this.sel = []; this.colores = {};
      cont.innerHTML = '<div class="cine-ctrl"></div><div class="chips"></div>';
      this.chips = cont.querySelector(".chips");
      this.selector = selectorCineJerarquico(() => {
        this.global.sel = new Set(this.selector.sel); this.global.refrescar(); this.filtros.notificar();
      });
      cont.querySelector(".cine-ctrl").appendChild(this.selector.el);
    }
    /** Alinea el desplegable de la gráfica con el filtro superior y recalcula las series; `volumen`: Map campo → registros. */
    fijar(volumen) {
      const g = this.global, filtrado = g.activo, v = (c) => volumen.get(c) || 0;
      this.selector.sel = new Set(g.sel); this.selector.refrescar();
      const base = [...g.sel].filter((c) => filtrado || v(c) > 0).sort((a, b) => v(b) - v(a) || alfabetico(a, b));
      this.sel = base.slice(0, filtrado ? MAX_SERIES : CINE_DEFECTO);
      // Colores estables: cada campo conserva el suyo mientras se muestre; los nuevos toman el primero libre
      const usados = new Set();
      Object.keys(this.colores).forEach((c) => { if (this.sel.includes(c)) usados.add(this.colores[c]); else delete this.colores[c]; });
      this.sel.forEach((c) => { if (!this.colores[c]) { this.colores[c] = CATEGORICA.find((k) => !usados.has(k)) || CATEGORICA[0]; usados.add(this.colores[c]); } });
      this.pintar();
    }
    /** Color de línea del campo. */
    colorDe(campo) { return this.colores[campo]; }
    /** Quita un campo de la selección compartida (la × de su etiqueta). */
    quitar(campo) {
      this.global.sel = new Set([...this.global.sel].filter((c) => c !== campo));
      this.global.refrescar(); this.filtros.notificar();
    }
    pintar() {
      this.chips.innerHTML = [...this.sel].sort(alfabetico).map((v) => `<span class="chip"><i style="background:${this.colores[v]}"></i>${esc(v)}<button type="button" data-v="${esc(v)}" aria-label="Quitar ${esc(v)}">×</button></span>`).join("");
      this.chips.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => this.quitar(b.dataset.v)));
    }
  }

  // ── Tabla: orden por tipo de dato, paginación y exportación a Parquet ─────
  const COLS_FECHA = new Set(["FECHA_DE_OBTENCION", "FECHA_DE_RESOLUCION", "FECHA_EJECUTORIA", "FECHA_DE_REGISTRO_EN_SNIES", "VIGENCIA_TRANSITORIA"]);
  const esFecha = (c) => COLS_FECHA.has(c) || c.startsWith("FECHA_");
  const encabezado = (c) => c.replace(/_/g, " ");
  const NUMERO_TEXTO = /^-?\d+(?:[.,]\d+)?$/;

  /** Valor comparable de una fecha: ISO (AAAA-MM-DD[...]) o DD/MM/AAAA → milisegundos. */
  function valorFecha(v) {
    if (v == null || v === "") return null;
    const s = String(v);
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3]);
    m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
    if (m) return Date.UTC(+m[3], +m[2] - 1, +m[1]);
    const t = Date.parse(s);
    return isNaN(t) ? null : t;
  }
  /** Tipo real de la columna según sus valores: fecha, número (también números guardados como texto) o texto. */
  function tipoColumna(col, filas) {
    if (esFecha(col)) return "fecha";
    let hay = false;
    for (const r of filas) {
      const v = r[col];
      if (v == null || v === "") continue;
      hay = true;
      if (typeof v === "number") continue;
      // Número guardado como texto: sin ceros a la izquierda y con hasta 15 dígitos (precisión exacta en JavaScript).
      // Los códigos más largos (ej. CODIGO_ANTERIOR_ICFES) se tratan como texto con orden numérico.
      if (typeof v === "string" && NUMERO_TEXTO.test(v.trim()) && !/^0\d/.test(v.trim()) && v.replace(/\D/g, "").length <= 15) continue;
      return "texto";
    }
    return hay ? "numero" : "texto";
  }
  const valorNumero = (v) => (v == null || v === "" ? null : typeof v === "number" ? v : Number(String(v).replace(",", ".")));
  const COLADOR = new Intl.Collator("es", { numeric: true, sensitivity: "base" });

  function celda(c, v, tipo) {
    if (v == null || v === "") return "";
    if (c === "TIPO_CAMBIO_DETECTADO") return CAT.tipos_cambio_etiqueta[v] || String(v);   // misma etiqueta que el filtro, las gráficas y el correo
    if (esFecha(c)) { const s = String(v); return /^\d{4}-\d{2}-\d{2}/.test(s) ? fmtFecha(s) : s; }
    if (tipo === "numero" && typeof v === "string") v = valorNumero(v);   // números guardados como texto
    if (typeof v === "number") {
      if (c.startsWith("COSTO_")) return Math.round(v).toLocaleString("es-CO");
      if (c === "PUNTAJE_TOTAL") return v.toLocaleString("es-CO", { maximumFractionDigits: 4 });
      return Number.isInteger(v) ? String(v) : v.toLocaleString("es-CO", { maximumFractionDigits: 4, useGrouping: false });
    }
    const s = String(v);
    return /^-?\d+\.0+$/.test(s) ? s.replace(/\.0+$/, "") : s;   // "160.0" → "160"
  }

  class Tabla {
    constructor(cont, { columnas, archivo, porPagina = 25 }) {
      this.archivo = archivo; this.porPagina = porPagina;
      this.orden = { col: "FECHA_DE_OBTENCION", asc: false }; this.pagina = 0; this.filas = []; this.tipos = {};
      this.cont = cont;
      cont.innerHTML = `<div class="tabla-cab"><span class="kpi-s" data-n aria-live="polite"></span><button type="button" class="btn btn-primario" data-pq>DESCARGAR</button></div>
        <div class="tabla-env"><table class="datos"><thead><tr></tr></thead><tbody></tbody></table></div>
        <div class="paginacion"><button type="button" class="btn" data-p="-1">ANTERIOR</button><span data-pag></span><button type="button" class="btn" data-p="1">SIGUIENTE</button></div>`;
      this.thead = cont.querySelector("thead tr"); this.tbody = cont.querySelector("tbody");
      this.ponerColumnas(columnas);
      cont.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => {
        const max = Math.max(0, Math.ceil(this.filas.length / this.porPagina) - 1);
        this.pagina = Math.min(max, Math.max(0, this.pagina + Number(b.dataset.p))); this.pintar();
      }));
      cont.querySelector("[data-pq]").addEventListener("click", () => this.exportar());
    }
    ponerColumnas(columnas) {
      if (this.columnas && this.columnas.join() === columnas.join()) return;
      this.columnas = columnas;
      this.thead.innerHTML = columnas.map((c) => `<th data-c="${c}" scope="col" tabindex="0">${encabezado(c)}<span class="ord" aria-hidden="true"></span></th>`).join("");
      this.thead.querySelectorAll("th").forEach((th) => {
        const ordenar = () => {
          const c = th.dataset.c;
          // Primer clic: fechas y números de mayor a menor (más reciente primero); texto de la A a la Z
          this.orden = this.orden.col === c ? { col: c, asc: !this.orden.asc } : { col: c, asc: this.tipos[c] === "texto" };
          this.ordenar(); this.pagina = 0; this.pintar();
        };
        th.addEventListener("click", ordenar);
        th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ordenar(); } });
      });
    }
    actualizar(filas, columnas) {
      if (columnas) this.ponerColumnas(columnas);
      this.filas = filas.slice();
      this.tipos = Object.fromEntries(this.columnas.map((c) => [c, tipoColumna(c, this.filas)]));
      this.ordenar(); this.pagina = 0; this.pintar();
    }
    ordenar() {
      const { col, asc } = this.orden, s = asc ? 1 : -1, tipo = this.tipos[col] || "texto";
      const clave = tipo === "fecha" ? valorFecha : tipo === "numero" ? valorNumero : (v) => (v == null || v === "" ? null : String(v));
      const llaves = new Map(this.filas.map((r) => [r, clave(r[col])]));
      this.filas.sort((a, b) => {
        const x = llaves.get(a), y = llaves.get(b);
        if (x == null) return y == null ? 0 : 1;   // vacíos siempre al final
        if (y == null) return -1;
        return (tipo === "texto" ? COLADOR.compare(x, y) : x - y) * s;
      });
    }
    pintar() {
      const ini = this.pagina * this.porPagina, pag = this.filas.slice(ini, ini + this.porPagina);
      this.tbody.innerHTML = pag.map((r) => `<tr>${this.columnas.map((c) => {
        const v = r[c];
        return `<td${this.tipos[c] === "numero" ? ' class="num"' : ""}>${esc(celda(c, v, this.tipos[c]))}</td>`;
      }).join("")}</tr>`).join("") || `<tr><td colspan="${this.columnas.length}">Sin registros para los filtros seleccionados</td></tr>`;
      this.thead.querySelectorAll("th").forEach((th) => {
        const activa = th.dataset.c === this.orden.col;
        th.querySelector(".ord").textContent = activa ? (this.orden.asc ? "▲" : "▼") : "";
        th.setAttribute("aria-sort", activa ? (this.orden.asc ? "ascending" : "descending") : "none");
      });
      const paginas = Math.max(1, Math.ceil(this.filas.length / this.porPagina));
      this.cont.querySelector("[data-pag]").textContent = `PÁGINA ${this.pagina + 1} DE ${paginas}`;
      this.cont.querySelector("[data-n]").textContent = `${fmt(this.filas.length)} registros`;
    }
    mensaje(texto) {
      this.filas = [];
      this.tbody.innerHTML = `<tr><td colspan="${this.columnas.length}">${esc(texto)}</td></tr>`;
      this.cont.querySelector("[data-n]").textContent = texto;
      this.cont.querySelector("[data-pag]").textContent = "";
    }
    /** Exporta las filas filtradas (todas las columnas, en el orden actual) a un archivo .parquet. */
    async exportar() {
      const boton = this.cont.querySelector("[data-pq]");
      if (!this.filas.length) { alert("No hay registros para descargar con los filtros seleccionados."); return; }
      boton.disabled = true; boton.textContent = "GENERANDO...";
      try {
        const { parquetWriteBuffer, schemaFromColumnData } = await import("hyparquet-writer");
        const esquema = {};
        const columnData = this.columnas.map((c) => {
          const tipo = this.tipos[c];
          if (tipo === "fecha") {
            // Fechas sin hora: columna de tipo fecha (DATE), igual que FECHA_DE_OBTENCION en los archivos de Historico/ y Novedades/
            esquema[c] = { name: c, type: "INT32", converted_type: "DATE", logical_type: { type: "DATE" }, repetition_type: "OPTIONAL" };
            return { name: c, data: this.filas.map((r) => { const t = valorFecha(r[c]); return t == null ? null : new Date(t); }) };
          }
          if (tipo === "numero") {
            const data = this.filas.map((r) => valorNumero(r[c]));
            const enteros = data.every((v) => v == null || (Number.isInteger(v) && Math.abs(v) < 2147483647));
            return { name: c, type: enteros ? "INT32" : "DOUBLE", data };
          }
          return { name: c, type: "STRING", data: this.filas.map((r) => (r[c] == null || r[c] === "" ? null : String(r[c]))) };
        });
        // Con esquema explícito las columnas no llevan tipo: se deduce el esquema (con las fechas como DATE) y se escriben los datos
        const schema = schemaFromColumnData({ columnData, schemaOverrides: esquema });
        const buffer = parquetWriteBuffer({ columnData: columnData.map(({ type, ...columna }) => columna), schema });
        const enlace = document.createElement("a");
        enlace.href = URL.createObjectURL(new Blob([buffer], { type: "application/vnd.apache.parquet" }));
        enlace.download = `${this.archivo} ${C.ultima_ejecucion}.parquet`;
        document.body.appendChild(enlace); enlace.click(); enlace.remove();
        setTimeout(() => URL.revokeObjectURL(enlace.href), 10000);
      } catch (e) {
        console.error(e);
        alert("No se pudo generar el archivo de descarga: " + e.message);
      } finally {
        boton.disabled = false; boton.textContent = "DESCARGAR";
      }
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
  /** Series por categoría con colores estables según `orden` (lista completa de valores posibles); `etiquetas` cambia el nombre visible. */
  function seriesPorCategoria(mapa, orden, colores, etiquetas = {}) {
    return orden.filter((k) => mapa[k] && mapa[k].some((v) => v > 0)).map((k) => ({ nombre: etiquetas[k] || k, valores: mapa[k], color: colores[k].color, dash: colores[k].dash }));
  }

  // ── Rango temporal de las gráficas ────────────────────────────────────────
  /** Primera fecha (AAAA-MM-DD) con algún registro; vacío si no hay registros. Las gráficas de una métrica empiezan ahí. */
  function primeraFecha(registros) {
    let min = null;
    registros.forEach((r) => { const f = r.FECHA_DE_OBTENCION; if (f && (min === null || f < min)) min = f; });
    return min;
  }
  /** Fechas de la lista desde `inicio` en adelante; sin `inicio` (la métrica no tiene datos) no queda ninguna. */
  const desdeFecha = (lista, inicio) => (inicio ? lista.filter((f) => f >= inicio) : []);

  /**
   * Opciones del filtro de programas académicos: un programa por CODIGO_SNIES_DEL_PROGRAMA, con su nombre e
   * institución más recientes (`recencia(r)`: valor mayor = registro más reciente). Retorna { opciones, etiquetas }.
   */
  function opcionesProgramas(registros, recencia = (r) => r.FECHA_DE_OBTENCION || "") {
    const limpio = (s) => String(s ?? "").replace(/[\s-]+$/, "");
    const mejor = new Map();
    registros.forEach((r) => {
      const c = r.CODIGO_SNIES_DEL_PROGRAMA;
      if (c == null || c === "") return;
      const k = String(c), rec = recencia(r), previo = mejor.get(k);
      if (!previo || rec >= previo.rec) mejor.set(k, { rec, r });
    });
    const etiquetas = {};
    mejor.forEach(({ r }, k) => { etiquetas[k] = `${limpio(r.NOMBRE_DEL_PROGRAMA)} · ${limpio(r.NOMBRE_INSTITUCION)} (${k})`; });
    return { opciones: [...mejor.keys()], etiquetas };
  }

  window.App = {
    C, CAT, ESTADO, CATEGORICA, MAX_SERIES, GRIS, SECTOR_COLOR, colorSector, ROJO, AMARILLO, NEGRO, OCRE, colorLinea, marcador, TINTA, TINTA2, fmt, fmtFecha, plural, variacion, esc, normalizar,
    decodificar, derivar, asignarColores, Filtros, SelectorCine, Tabla, lineas, barrasH, barrasV, torta, plotly, vacio,
    contarPorFecha, contar, seriesPorCategoria, ejeMeses, ejeEntero, grupoModalidad, valorFecha, tipoColumna,
    primeraFecha, desdeFecha, opcionesProgramas, etqModalidad, etiquetasModalidad,
  };
})();
