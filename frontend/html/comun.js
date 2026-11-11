// Funciones compartidas por las tres pantallas
async function api(ruta, opciones = {}) {
  const r = await fetch(ruta, opciones);
  if (r.status === 204) return null;
  const tipo = r.headers.get("content-type") || "";
  const cuerpo = tipo.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    const detalle = cuerpo && cuerpo.detail ? cuerpo.detail : cuerpo;
    throw new Error(typeof detalle === "string" ? detalle : JSON.stringify(detalle));
  }
  return cuerpo;
}

function mostrar(id, texto, tipo = "ok") {
  const el = document.getElementById(id);
  el.className = "mensaje " + tipo;
  el.textContent = texto;
}

function esc(v) {
  return v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fechaHora(iso) {
  return iso ? new Date(iso).toLocaleString("es-CO") : "";
}

function tamano(bytes) {
  if (bytes < 1000) return bytes + " B";
  if (bytes < 1e6) return (bytes / 1000).toFixed(1) + " KB";
  return (bytes / 1e6).toFixed(2) + " MB";
}

function chipCopia(nombre, estado) {
  const clase = estado === "ok" ? "ok" : estado === "pendiente" ? "pend" : estado ? "mal" : "gris";
  const texto = estado ? `${nombre}: ${estado}` : `${nombre}: sin copia`;
  return `<span class="chip ${clase}">${esc(texto)}</span>`;
}

function cuentaRegresiva(isoHasta) {
  const ms = new Date(isoHasta) - new Date();
  if (ms <= 0) return `<span class="chip gris">retención cumplida</span>`;
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000);
  return `<span class="chip pend">protegido ${m}:${String(s).padStart(2, "0")}</span>`;
}
