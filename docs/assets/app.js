/* Monitor SNIES Pregrado · Universitario — componentes compartidos del Dashboard */
(function () {
  "use strict";
  const S = window.SNIES || {};
  const C = S.comun;
  const CAT = C.catalogos;

  // ── Colores (paleta institucional Universidad del Norte) ──────────────────
  const ROJO = "#BE0000", AMARILLO = "#FFB700", NEGRO = "#000000";
  // Estados: verde/naranja/rojo validados para daltonismo (ΔE ≥ 8 entre los tres)
  const ESTADO = { nuevos: "#1E8E5A", modificados: "#F28C00", inactivos: "#BE0000" };
  // Paleta categórica complementaria (validada: separación para daltonismo ≥ 8 entre colores vecinos).
  // Orden fijo: el color sigue a la categoría, nunca a su posición en un ranking.
  const CATEGORICA = ["#BE0000", "#2563B0", "#D4A100", "#0E9384", "#7B4FB0", "#E8743B", "#C2408A", "#6B8E23"];
  const GRIS = "#9E9E9E";
  const SECTOR_COLOR = { PRIVADO: ROJO, OFICIAL: NEGRO };
  const TINTA = "#000000", TINTA2 = "#4D4D4D", REJILLA = "#E5E5E5";

  // ── Utilidades ────────────────────────────────────────────────────────────
  const fmt = (n) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString("es-CO"));
  const fmtFecha = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "");
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
    o._GU = grupoUniversidad(o.CODIGO_INSTITUCION);
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

  // ── Histórico de programas activos (docs/data/historico/AAAA-MM-DD.parquet) ─
  const cacheHistorico = new Map();
  let hyparquet = null;
  /** Registros de programas activos de una ejecución, leídos del histórico (una sola descarga por fecha). */
  function leerHistorico(fecha) {
    if (!cacheHistorico.has(fecha)) {
      cacheHistorico.set(fecha, (async () => {
        hyparquet = hyparquet || (await import("hyparquet"));
        const resp = await fetch(`data/historico/${fecha}.parquet`);
        if (!resp.ok) throw new Error(`No se pudo leer el histórico del ${fecha} (${resp.status})`);
        const filas = await hyparquet.parquetReadObjects({ file: await resp.arrayBuffer() });
        filas.forEach((o) => {
          for (const k in o) {
            const v = o[k];
            if (typeof v === "bigint") o[k] = Number(v);
            else if (v instanceof Date) o[k] = isNaN(v) ? null : v.toISOString().slice(0, 10);
          }
          derivar(o);
        });
        return filas;
      })());
      cacheHistorico.get(fecha).catch(() => cacheHistorico.delete(fecha));
    }
    return cacheHistorico.get(fecha);
  }

  /** Color estable por entidad: orden fijo; 9.ª categoría en adelante con línea punteada; OTRO/SIN INFORMACIÓN en gris. */
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

  // ── Filtros desplegables (selección múltiple) ─────────────────────────────
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

  class Multiselect {
    constructor(def, opciones, etiquetas, alCambiar) {
      this.def = def; this.sel = new Set(); this.alCambiar = alCambiar; this.etiquetas = etiquetas || {};
      const el = (this.el = document.createElement("div"));
      el.className = "ms";
      el.innerHTML = `<button type="button" class="ms-btn" aria-haspopup="listbox" aria-expanded="false"><span></span></button>
        <div class="ms-panel" role="listbox" aria-multiselectable="true">
          ${def.buscar ? '<input type="search" placeholder="Buscar..." aria-label="Buscar opción">' : ""}
          <label class="ms-op todos"><input type="checkbox" data-todos checked> ${esc(def.etiqueta)}</label>
          <div class="ms-ops">${opciones.map((o) => `<label class="ms-op"><input type="checkbox" value="${esc(o)}"> ${esc(this.etiquetas[o] || o)}</label>`).join("")}</div>
        </div>`;
      this.btn = el.querySelector(".ms-btn");
      this.btn.addEventListener("click", (e) => { e.stopPropagation(); cerrarDesplegables(this); this.alternar(); });
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
      desplegables.push(this);
      this.refrescar();
    }
    alternar() { const a = this.el.classList.toggle("abierto"); this.btn.setAttribute("aria-expanded", a); if (a) ajustarPanel(this.el); }
    cerrar() { this.el.classList.remove("abierto"); this.btn.setAttribute("aria-expanded", "false"); }
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

  // ── Selector jerárquico de universidades ──────────────────────────────────
  /**
   * TODAS LAS UNIVERSIDADES · UNIVERSIDAD DEL NORTE · UNIVERSIDADES DE MAYOR INTERÉS (desplegable con cada
   * universidad) · OTRAS UNIVERSIDADES. Marcar el grupo de mayor interés selecciona todas sus universidades;
   * marcar solo algunas deja el grupo en estado parcial.
   */
  class SelectorUniversidades {
    constructor(alCambiar) {
      this.alCambiar = alCambiar; this.sel = new Set();
      const [gU, gM, gO] = CAT.grupos_universidad;
      this.grupos = { U: gU, M: gM, O: gO };
      const nombres = CAT.mayor_interes_nombres || {};
      this.codigos = CAT.mayor_interes.map(String).sort((a, b) => String(nombres[a] || a).localeCompare(String(nombres[b] || b), "es"));
      this.nombres = Object.fromEntries(this.codigos.map((c) => [c, nombres[c] || `INSTITUCIÓN ${c}`]));
      const el = (this.el = document.createElement("div"));
      el.className = "ms";
      el.innerHTML = `<button type="button" class="ms-btn" aria-haspopup="listbox" aria-expanded="false"><span></span></button>
        <div class="ms-panel" role="listbox" aria-multiselectable="true">
          <label class="ms-op todos"><input type="checkbox" data-todos checked> TODAS LAS UNIVERSIDADES</label>
          <div class="ms-ops">
            <label class="ms-op"><input type="checkbox" data-g="U"> ${esc(gU)}</label>
            <div class="ms-op ms-padre">
              <label><input type="checkbox" data-g="M"> ${esc(gM)}</label>
              <button type="button" class="ms-expandir" aria-expanded="false" aria-label="Mostrar las universidades de mayor interés">▸</button>
            </div>
            <div class="ms-hijos" hidden>
              ${this.codigos.map((c) => `<label class="ms-op hijo"><input type="checkbox" data-c="${c}"> ${esc(this.nombres[c])}</label>`).join("")}
            </div>
            <label class="ms-op"><input type="checkbox" data-g="O"> ${esc(gO)}</label>
          </div>
        </div>`;
      this.btn = el.querySelector(".ms-btn");
      this.padre = el.querySelector('[data-g="M"]');
      this.hijos = el.querySelector(".ms-hijos");
      const expandir = el.querySelector(".ms-expandir");
      this.btn.addEventListener("click", (e) => { e.stopPropagation(); cerrarDesplegables(this); this.alternar(); });
      el.querySelector(".ms-panel").addEventListener("click", (e) => e.stopPropagation());
      expandir.addEventListener("click", () => {
        const abrir = this.hijos.hidden;
        this.hijos.hidden = !abrir; expandir.textContent = abrir ? "▾" : "▸"; expandir.setAttribute("aria-expanded", abrir);
      });
      el.querySelector('[data-g="U"]').addEventListener("change", (e) => this.marcar("U", e.target.checked));
      el.querySelector('[data-g="O"]').addEventListener("change", (e) => this.marcar("O", e.target.checked));
      this.padre.addEventListener("change", (e) => { this.codigos.forEach((c) => this.marcar(`C:${c}`, e.target.checked, false)); this.listo(); });
      el.querySelectorAll("[data-c]").forEach((cb) => cb.addEventListener("change", () => this.marcar(`C:${cb.dataset.c}`, cb.checked)));
      el.querySelector("[data-todos]").addEventListener("change", () => { this.limpiar(); this.alCambiar(); });
      desplegables.push(this);
      this.refrescar();
    }
    marcar(token, activo, avisar = true) {
      activo ? this.sel.add(token) : this.sel.delete(token);
      if (avisar) this.listo();
    }
    listo() { this.refrescar(); this.alCambiar(); }
    alternar() { const a = this.el.classList.toggle("abierto"); this.btn.setAttribute("aria-expanded", a); if (a) ajustarPanel(this.el); }
    cerrar() { this.el.classList.remove("abierto"); this.btn.setAttribute("aria-expanded", "false"); }
    limpiar() { this.sel.clear(); this.refrescar(); }
    /** ¿El registro pertenece a la selección? (sin selección: todas las universidades) */
    pasa(r) {
      if (!this.sel.size) return true;
      if (r._GU === this.grupos.U) return this.sel.has("U");
      if (r._GU === this.grupos.O) return this.sel.has("O");
      return this.sel.has(`C:${Number(r.CODIGO_INSTITUCION)}`);
    }
    refrescar() {
      const marcadas = this.codigos.filter((c) => this.sel.has(`C:${c}`));
      this.el.querySelectorAll("[data-c]").forEach((cb) => (cb.checked = this.sel.has(`C:${cb.dataset.c}`)));
      this.el.querySelector('[data-g="U"]').checked = this.sel.has("U");
      this.el.querySelector('[data-g="O"]').checked = this.sel.has("O");
      this.padre.checked = marcadas.length === this.codigos.length;
      this.padre.indeterminate = marcadas.length > 0 && marcadas.length < this.codigos.length;
      this.el.querySelector("[data-todos]").checked = this.sel.size === 0;
      const partes = [];
      if (this.sel.has("U")) partes.push(this.grupos.U);
      if (marcadas.length === this.codigos.length) partes.push(this.grupos.M);
      else if (marcadas.length === 1) partes.push(this.nombres[marcadas[0]]);
      else if (marcadas.length > 1) partes.push(`${marcadas.length} DE MAYOR INTERÉS`);
      if (this.sel.has("O")) partes.push(this.grupos.O);
      this.btn.querySelector("span").textContent = partes.length ? partes.join(" + ") : "TODAS LAS UNIVERSIDADES";
      this.btn.title = partes.length ? [this.sel.has("U") && this.grupos.U, ...marcadas.map((c) => this.nombres[c]), this.sel.has("O") && this.grupos.O].filter(Boolean).join(" · ") : "TODAS LAS UNIVERSIDADES";
      this.btn.classList.toggle("con-valor", this.sel.size > 0);
    }
  }

  // ── Selector de fechas de ejecución: fecha, rango, mes o año ──────────────
  class SelectorFechas {
    constructor(fechas, alCambiar) {
      this.fechas = fechas; this.alCambiar = alCambiar;
      this.modo = "fecha"; this.desde = null; this.hasta = null; this.etiqueta = "TODAS LAS FECHAS";
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
          <div class="fs-pie"><button type="button" class="btn btn-reset">TODAS LAS FECHAS</button><span>Solo se habilitan los días con ejecución</span></div>
        </div>`;
      this.btn = el.querySelector(".fs-btn"); this.quitar = el.querySelector(".fs-quitar");
      this.cuerpo = el.querySelector(".fs-cuerpo"); this.ayuda = el.querySelector(".fs-ayuda");
      this.btn.addEventListener("click", (e) => { e.stopPropagation(); cerrarDesplegables(this); this.alternar(); });
      el.querySelector(".fs-panel").addEventListener("click", (e) => e.stopPropagation());
      el.querySelectorAll("[data-modo]").forEach((b) => b.addEventListener("click", () => this.cambiarModo(b.dataset.modo)));
      el.querySelector(".btn-reset").addEventListener("click", () => { this.limpiar(); this.cerrar(); this.alCambiar(); });
      this.quitar.addEventListener("click", (e) => { e.stopPropagation(); this.limpiar(); this.alCambiar(); });
      desplegables.push(this);
      this.cambiarModo("fecha");
    }
    alternar() { const a = this.el.classList.toggle("abierto"); this.btn.setAttribute("aria-expanded", a); if (a) ajustarPanel(this.el); }
    cerrar() { this.el.classList.remove("abierto"); this.btn.setAttribute("aria-expanded", "false"); }
    cambiarModo(modo) {
      this.modo = modo;
      this.el.querySelectorAll("[data-modo]").forEach((b) => { const a = b.dataset.modo === modo; b.classList.toggle("activo", a); b.setAttribute("aria-selected", a); });
      if (this.fp) { this.fp.destroy(); this.fp = null; }
      this.cuerpo.innerHTML = "";
      const textos = {
        fecha: "Seleccione una ejecución específica.",
        rango: "Seleccione la fecha inicial y luego la fecha final.",
        mes: "Seleccione un mes con ejecuciones registradas.",
        anio: "Seleccione un año con ejecuciones registradas.",
      };
      this.ayuda.textContent = textos[modo];
      if (modo === "fecha" || modo === "rango") {
        const cont = document.createElement("div");
        this.cuerpo.appendChild(cont);
        this.fp = flatpickr(cont, {
          inline: true, mode: modo === "rango" ? "range" : "single", locale: "es", dateFormat: "Y-m-d",
          enable: this.fechas, minDate: this.fechas[0], maxDate: this.fechas[this.fechas.length - 1],
          defaultDate: this.fechas[this.fechas.length - 1] && this.desde ? (modo === "rango" ? [this.desde, this.hasta] : this.desde) : null,
          onChange: (sel) => {
            if (modo === "fecha" && sel.length === 1) {
              const f = isoDe(sel[0]);
              this.aplicar(f, f, fmtFecha(f));
            } else if (modo === "rango" && sel.length === 2) {
              const a = isoDe(sel[0]), b = isoDe(sel[1]);
              this.aplicar(a, b, `${fmtFecha(a)} A ${fmtFecha(b)}`);
            }
          },
        });
        return;
      }
      const porAnio = {};
      this.fechas.forEach((f) => { (porAnio[f.slice(0, 4)] = porAnio[f.slice(0, 4)] || new Set()).add(Number(f.slice(5, 7))); });
      const anios = Object.keys(porAnio).sort();
      if (modo === "anio") {
        this.cuerpo.innerHTML = `<div class="fs-rejilla">${anios.map((a) => `<button type="button" class="btn" data-anio="${a}">${a}</button>`).join("")}</div>`;
        this.cuerpo.querySelectorAll("[data-anio]").forEach((b) => b.addEventListener("click", () => {
          const a = b.dataset.anio; this.aplicar(`${a}-01-01`, `${a}-12-31`, `AÑO ${a}`);
        }));
      } else {
        this.cuerpo.innerHTML = anios.map((a) => `<div class="fs-anio">${a}</div><div class="fs-rejilla meses">${MESES.map((m, i) => {
          const hay = porAnio[a].has(i + 1);
          return `<button type="button" class="btn" data-mes="${a}-${String(i + 1).padStart(2, "0")}" ${hay ? "" : "disabled"}>${m}</button>`;
        }).join("")}</div>`).join("");
        this.cuerpo.querySelectorAll("[data-mes]").forEach((b) => b.addEventListener("click", () => {
          const [a, m] = b.dataset.mes.split("-").map(Number);
          const fin = new Date(a, m, 0).getDate();
          this.aplicar(`${b.dataset.mes}-01`, `${b.dataset.mes}-${String(fin).padStart(2, "0")}`, `${MESES[m - 1]} ${a}`);
        }));
      }
      this.marcar();
    }
    marcar() {
      this.cuerpo.querySelectorAll("[data-anio],[data-mes]").forEach((b) => {
        const a = b.dataset.anio ? `${b.dataset.anio}-01-01` : `${b.dataset.mes}-01`;
        b.classList.toggle("activo", !!this.desde && this.desde === a);
      });
    }
    aplicar(desde, hasta, etiqueta) {
      this.desde = desde; this.hasta = hasta; this.etiqueta = etiqueta;
      this.refrescar(); this.marcar(); this.cerrar(); this.alCambiar();
    }
    limpiar() {
      this.desde = this.hasta = null; this.etiqueta = "TODAS LAS FECHAS";
      if (this.fp) this.fp.clear();
      this.refrescar(); this.marcar();
    }
    refrescar() {
      this.btn.querySelector("span").textContent = this.etiqueta;
      this.btn.classList.toggle("con-valor", !!this.desde);
      this.quitar.hidden = !this.desde;
    }
  }

  /**
   * Barra de filtros. config = { buscar, claves, registros (para opciones), opciones, etiquetas,
   * camposBusqueda, fechas, alCambiar }
   */
  class Filtros {
    constructor(cont, config) {
      this.cfg = config; this.texto = ""; this.ms = {};
      const barra = document.createElement("div");
      barra.className = "filtros";
      barra.setAttribute("role", "search");
      const input = document.createElement("input");
      input.type = "search"; input.className = "buscar"; input.placeholder = config.buscar; input.setAttribute("aria-label", config.buscar);
      let t;
      input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { this.texto = normalizar(input.value).trim(); config.alCambiar(); }, 200); });
      barra.appendChild(input);
      this.input = input;
      config.claves.forEach((clave) => {
        const def = DEF_FILTROS[clave];
        if (def.tipo === "fecha") {
          this.fechas = new SelectorFechas(config.fechas, config.alCambiar);
          barra.appendChild(this.fechas.el);
          return;
        }
        if (clave === "universidades") {
          this.ms[clave] = new SelectorUniversidades(config.alCambiar);
          barra.appendChild(this.ms[clave].el);
          return;
        }
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
      this.conteo.setAttribute("aria-live", "polite");
      barra.appendChild(this.conteo);
      cont.appendChild(barra);
    }
    get desde() { return this.fechas ? this.fechas.desde : null; }
    get hasta() { return this.fechas ? this.fechas.hasta : null; }
    opcionesDe(campo) {
      const set = new Set();
      this.cfg.registros.forEach((r) => { const v = r[campo]; if (v != null && v !== "") set.add(v); });
      return [...set].sort((a, b) => String(a).localeCompare(String(b), "es"));
    }
    limpiar() {
      this.texto = ""; this.input.value = "";
      Object.values(this.ms).forEach((m) => m.limpiar());
      if (this.fechas) this.fechas.limpiar();
      this.cfg.alCambiar();
    }
    /** ¿El registro cumple los filtros? opciones.sinFecha ignora el rango de fechas. */
    pasa(r, opciones = {}) {
      for (const [clave, ms] of Object.entries(this.ms)) {
        if (ms.pasa) { if (!ms.pasa(r)) return false; continue; }
        if (ms.sel.size && !ms.sel.has(String(r[DEF_FILTROS[clave].campo]))) return false;
      }
      if (!opciones.sinFecha && this.desde && r.FECHA_DE_OBTENCION) {
        if (r.FECHA_DE_OBTENCION < this.desde || r.FECHA_DE_OBTENCION > this.hasta) return false;
      }
      if (this.texto) {
        let ok = false;
        for (const c of this.cfg.camposBusqueda) { if (normalizar(r[c]).includes(this.texto)) { ok = true; break; } }
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
  /** Eje X por meses: etiquetas MES AÑO; cada punto es una ejecución. */
  function ejeMeses(fechas) {
    const ini = new Date(fechas[0] + "T00:00:00"), fin = new Date(fechas[fechas.length - 1] + "T00:00:00");
    const meses = (fin.getFullYear() - ini.getFullYear()) * 12 + fin.getMonth() - ini.getMonth() + 1;
    const paso = meses <= 12 ? 1 : meses <= 24 ? 2 : meses <= 36 ? 3 : 6;
    const vals = [], txt = [];
    for (let y = ini.getFullYear(), m = ini.getMonth(); ; m += paso) {
      while (m > 11) { m -= 12; y++; }
      if (y > fin.getFullYear() || (y === fin.getFullYear() && m > fin.getMonth())) break;
      vals.push(`${y}-${String(m + 1).padStart(2, "0")}-01`);
      txt.push(`${MESES[m]}<br>${y}`);
    }
    const rango = fechas.length === 1 ? undefined : [fechas[0], fechas[fechas.length - 1]];
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
    const ser = opciones.modo === "acumulado" ? series.map((s) => Object.assign({}, s, { valores: acumular(s.valores) })) : series;
    const total = (s) => s.valores[s.valores.length - 1];
    const varias = ser.length > 1;
    const etiquetaHover = opciones.modo === "acumulado" ? "acumulado" : "programas";
    const trazas = ser.map((s) => ({
      type: "scatter", mode: "lines", x: fechas, y: s.valores,
      name: varias ? `${s.nombre} · ${fmt(total(s))}` : s.nombre,
      line: { color: s.color, width: 2, dash: s.dash || "solid", shape: "linear" },
      hovertemplate: `${esc(s.nombre)}: <b>%{y:,}</b> ${etiquetaHover}<extra></extra>`,
    }));
    const layout = baseLayout({
      xaxis: ejeMeses(fechas),
      yaxis: Object.assign({ rangemode: opciones.modo === "stock" && !varias ? "normal" : "tozero", gridcolor: REJILLA, zeroline: false, tickformat: ",d", automargin: true },
        ejeEntero(ser.flatMap((s) => s.valores), !(opciones.modo === "stock" && !varias))),
      hovermode: "x unified",
      margin: { l: 56, r: varias ? 12 : 16, t: varias ? 12 : 40, b: 48 },
    });
    if (varias) {
      Object.assign(layout, {
        showlegend: true,
        legend: { title: { text: "<b>TOTAL</b>", font: { color: TINTA, size: 11 } }, x: 1.01, xanchor: "left", y: 1, yanchor: "top", font: { size: 10, color: TINTA }, bgcolor: "rgba(255,255,255,0)", itemclick: "toggle", itemdoubleclick: "toggleothers" },
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
      customdata: etiquetas.slice().reverse(), marker: { color: Array.isArray(color) ? color.slice().reverse() : color },
      text: valores.slice().reverse().map(fmt), textposition: "outside", cliponaxis: false,
      textfont: { color: TINTA, size: 10 },
      hovertemplate: `%{customdata}: <b>%{x:,}</b>${opciones.unidad ? " " + opciones.unidad : ""}<extra></extra>`,
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

  // ── Tabla: orden por tipo de dato, paginación y exportación a Parquet ─────
  const COLS_FECHA = new Set(["FECHA_DE_OBTENCION", "FECHA_DE_RESOLUCION", "FECHA_EJECUTORIA", "FECHA_DE_REGISTRO_EN_SNIES", "VIGENCIA_TRANSITORIA", "REACTIVADO_POSTERIORMENTE"]);
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
      cont.innerHTML = `<div class="tabla-cab"><span class="kpi-s" data-n aria-live="polite"></span><button type="button" class="btn btn-primario" data-pq>DESCARGAR PARQUET</button></div>
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
        const { parquetWriteBuffer } = await import("hyparquet-writer");
        const columnData = this.columnas.map((c) => {
          const tipo = this.tipos[c];
          if (tipo === "fecha") {
            return { name: c, type: "TIMESTAMP", data: this.filas.map((r) => { const t = valorFecha(r[c]); return t == null ? null : new Date(t); }) };
          }
          if (tipo === "numero") {
            const data = this.filas.map((r) => valorNumero(r[c]));
            const enteros = data.every((v) => v == null || (Number.isInteger(v) && Math.abs(v) < 2147483647));
            return { name: c, type: enteros ? "INT32" : "DOUBLE", data };
          }
          return { name: c, type: "STRING", data: this.filas.map((r) => (r[c] == null || r[c] === "" ? null : String(r[c]))) };
        });
        const buffer = parquetWriteBuffer({ columnData });
        const enlace = document.createElement("a");
        enlace.href = URL.createObjectURL(new Blob([buffer], { type: "application/vnd.apache.parquet" }));
        enlace.download = `${this.archivo} ${C.ultima_ejecucion}.parquet`;
        document.body.appendChild(enlace); enlace.click(); enlace.remove();
        setTimeout(() => URL.revokeObjectURL(enlace.href), 10000);
      } catch (e) {
        console.error(e);
        alert("No se pudo generar el archivo Parquet: " + e.message);
      } finally {
        boton.disabled = false; boton.textContent = "DESCARGAR PARQUET";
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
  /** Series por categoría con colores estables según `orden` (lista completa de valores posibles). */
  function seriesPorCategoria(mapa, orden, colores) {
    return orden.filter((k) => mapa[k] && mapa[k].some((v) => v > 0)).map((k) => ({ nombre: k, valores: mapa[k], color: colores[k].color, dash: colores[k].dash }));
  }

  window.App = {
    C, CAT, ESTADO, CATEGORICA, GRIS, SECTOR_COLOR, ROJO, AMARILLO, NEGRO, fmt, fmtFecha, esc, normalizar,
    decodificar, derivar, leerHistorico, asignarColores, Filtros, SelectorCine, Tabla, lineas, barrasH, barrasV, torta, plotly, vacio,
    contarPorFecha, contar, seriesPorCategoria, ejeMeses, ejeEntero, grupoModalidad, valorFecha, tipoColumna,
  };
})();
