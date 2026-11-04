// La API se sirve en el mismo origen bajo /api (Caddy en Docker; el proxy de Vite en desarrollo).
export const API_URL = "/api";

// Helper para incluir el token JWT en peticiones protegidas
function getAuthHeaders() {
  const token = localStorage.getItem("token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

// Usuario de la sesión (nombre y rol), guardado al ingresar.
export function usuarioActual() {
  try {
    return JSON.parse(localStorage.getItem("usuario") || "null");
  } catch {
    return null;
  }
}

export function cerrarSesion() {
  localStorage.removeItem("token");
  localStorage.removeItem("usuario");
  window.dispatchEvent(new Event("sesion-cerrada"));
}

// Mensaje legible del `detail` de FastAPI (texto, lista de validación u objeto {mensaje, problemas}).
function mensajeDe(detalle, porDefecto) {
  if (typeof detalle === "string") return detalle;
  if (Array.isArray(detalle)) {
    return detalle
      .map((d) => {
        const campo = (d.loc || []).filter((x) => x !== "body" && x !== "query").join(".");
        return campo ? `${campo}: ${d.msg}` : d.msg;
      })
      .join("; ");
  }
  if (detalle && typeof detalle === "object") {
    return (detalle.mensaje || porDefecto) + (detalle.problemas?.length ? ": " + detalle.problemas.join("; ") : "");
  }
  return porDefecto;
}

/** Error de la API con su código HTTP (0 = sin respuesta: no se sabe si la operación se hizo). */
export class ErrorApi extends Error {
  constructor(estado, mensaje) {
    super(mensaje);
    this.estado = estado;
  }
}

function consulta(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function pedir(metodo, ruta, cuerpo, tipoCuerpo) {
  const headers = getAuthHeaders();
  let body;
  if (tipoCuerpo) {
    headers["Content-Type"] = tipoCuerpo;
    body = cuerpo;
  } else if (cuerpo !== undefined) {
    body = JSON.stringify(cuerpo);
  }
  let res;
  try {
    res = await fetch(`${API_URL}${ruta}`, { method: metodo, headers, body });
  } catch {
    throw new ErrorApi(0, "Sin conexión con el servidor.");
  }
  if (res.ok) return res;
  const data = await res.json().catch(() => ({}));
  // Sesión vencida o usuario desactivado: volver al ingreso.
  if (res.status === 401 && localStorage.getItem("token") && ruta !== "/auth/login") cerrarSesion();
  throw new ErrorApi(res.status, mensajeDe(data.detail, `Error ${res.status}`));
}

const json = async (metodo, ruta, cuerpo) => (await pedir(metodo, ruta, cuerpo)).json();
const archivo = async (ruta) => (await pedir("GET", ruta)).blob();

// Usuario de la sesión según la API (para sesiones abiertas antes de guardar el rol).
export async function cargarUsuario() {
  const usuario = await json("GET", "/auth/yo");
  localStorage.setItem("usuario", JSON.stringify(usuario));
  return usuario;
}

// 1. Login (Ruta exacta de Swagger: /auth/login)
export async function loginUsuario(usuario, pin) {
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ usuario, pin }),
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Usuario o PIN incorrectos");
  }

  const data = await res.json();
  const token = data.token_acceso || data.access_token || data.token;
  if (token) localStorage.setItem("token", token);
  if (data.usuario) localStorage.setItem("usuario", JSON.stringify(data.usuario));
  return data;
}

// 2. Obtener Despachos Pendientes (Ruta exacta de Swagger: /despachos/pendientes)
export async function obtenerDespachos(filtro = {}) {
  // La API entrega surtidor_id: se agrega el nombre del surtidor (catálogo) para la columna EQUIPO / LADO.
  const despachos = await json("GET", "/despachos/pendientes" + consulta(filtro));
  const catalogos = await obtenerCatalogos().catch(() => ({ surtidores: [] }));
  const nombres = Object.fromEntries((catalogos.surtidores || []).map((s) => [s.id, s.marca]));
  return despachos.map((d) => ({ ...d, surtidor: nombres[d.surtidor_id] || `#${d.surtidor_id}` }));
}

// 3. Obtener Catálogos (Ruta exacta de Swagger: /catalogos)
export const obtenerCatalogos = () => json("GET", "/catalogos");

// 4. El resto de la API del banco de pruebas
export const api = {
  // pista
  crearVentaManual: (v) => json("POST", "/ventas-manuales", v),
  buscarCliente: async (tipo, numero) => {
    try {
      return await json("GET", "/clientes/buscar" + consulta({ tipo, numero }));
    } catch (e) {
      if (e instanceof ErrorApi && e.estado === 404) return null;
      throw e;
    }
  },
  crearCliente: (c) => json("POST", "/clientes", c),
  emitir: (f) => json("POST", "/facturas", f),
  factura: (id) => json("GET", `/facturas/${id}`),
  facturaPdf: (id) => archivo(`/facturas/${id}/pdf`),
  facturaRecibo: async (id) => new Uint8Array(await (await pedir("GET", `/facturas/${id}/recibo`)).arrayBuffer()),
  anular: (id, { clave, motivo }) => json("POST", `/facturas/${id}/anular`, { clave, motivo }),
  reenviar: (id) => json("POST", `/facturas/${id}/reenviar`),

  // público (registro por QR)
  configuracionPublica: () => json("GET", "/publico/configuracion"),
  registroPublico: (c) => json("POST", "/publico/registro", c),

  // administración
  facturas: (f) => json("GET", "/facturas" + consulta(f)),
  ventas: (f) => json("GET", "/ventas" + consulta(f)),
  usuarios: () => json("GET", "/usuarios"),
  crearUsuario: (u) => json("POST", "/usuarios", u),
  desactivarUsuario: (id) => json("POST", `/usuarios/${id}/desactivar`),
  clientes: (f) => json("GET", "/clientes" + consulta(f)),
  activarCliente: (id, activo) => json("POST", `/clientes/${id}/${activo ? "activar" : "desactivar"}`),
  corregirCorreoCliente: (id, email) => json("POST", `/clientes/${id}/correo`, { email }),
  vehiculos: (f) => json("GET", "/vehiculos" + consulta(f)),
  crearVehiculo: (v) => json("POST", "/vehiculos", v),
  activarVehiculo: (id, activo) => json("POST", `/vehiculos/${id}/${activo ? "activar" : "desactivar"}`),
  importar: async (que, file, enSeco) =>
    (await pedir("POST", `/${que}/importar` + consulta({ en_seco: String(enSeco) }), file, "text/csv")).json(),

  // reportes
  reporte: (tipo, r) => json("GET", `/reportes/${tipo}` + consulta(r)),
  reportePdf: (tipo, r) => archivo("/reportes/pdf" + consulta({ tipo, ...r })),
  exportarExcel: (r) => archivo("/reportes/exportar" + consulta(r)),
  contador: (r, formato) => archivo("/reportes/contador" + consulta({ desde: r.desde, hasta: r.hasta, formato })),
  posiblesDuplicados: (r) => json("GET", "/reportes/posibles-duplicados" + consulta({ desde: r.desde, hasta: r.hasta })),
  anomalias: (estado, severidad) => json("GET", "/anomalias" + consulta({ estado, severidad })),
};
