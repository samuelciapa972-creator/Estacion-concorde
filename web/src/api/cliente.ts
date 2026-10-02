/** Cliente HTTP de la API. En desarrollo Vite la sirve bajo /api (ver vite.config.ts). */
import { cerrarSesion, obtenerSesion } from "./sesion";
import type {
  Anomalia,
  BusquedaVentas,
  PosibleDuplicado,
  ClientesPagina,
  Importacion,
  UsuarioListado,
  Vehiculo,
  VehiculoEntrada,
  VehiculosPagina,
  Catalogos,
  Cliente,
  ClienteEntrada,
  ConfiguracionPublica,
  Factura,
  FacturaEntrada,
  FilaTurno,
  FilaVentas,
  RegistroPublicoEntrada,
  Pendiente,
  Token,
  Usuario,
  VentaManualEntrada,
} from "./tipos";

export const BASE = "/api";

/** Error con el mensaje de la API (`detail`), listo para mostrar. `estado` 0 = no hubo respuesta (red). */
export class ErrorApi extends Error {
  constructor(
    readonly estado: number,
    mensaje: string,
    readonly problemas: string[] = [],
    readonly datos: unknown = null,
  ) {
    super(mensaje);
    this.name = "ErrorApi";
  }

  get sinRespuesta(): boolean {
    return this.estado === 0;
  }
}

function mensajeDe(detalle: unknown): { mensaje: string; problemas: string[] } {
  if (typeof detalle === "string") return { mensaje: detalle, problemas: [] };
  if (Array.isArray(detalle)) {
    // Validación de FastAPI: [{loc: [...], msg: "..."}]
    const problemas = detalle.map((d: { loc?: unknown[]; msg?: string }) => {
      const campo = (d.loc ?? []).filter((x) => x !== "body").join(".");
      return campo ? `${campo}: ${d.msg ?? ""}` : (d.msg ?? "");
    });
    return { mensaje: "datos inválidos", problemas };
  }
  if (detalle && typeof detalle === "object") {
    const d = detalle as { mensaje?: string; problemas?: string[] };
    return { mensaje: d.mensaje ?? "error", problemas: d.problemas ?? [] };
  }
  return { mensaje: "error inesperado", problemas: [] };
}

type Metodo = "GET" | "POST";

/** Cuerpo crudo (p. ej. un CSV) en vez de JSON. */
export class Crudo {
  constructor(
    readonly contenido: Blob | string,
    readonly tipo: string,
  ) {}
}

async function pedir(metodo: Metodo, ruta: string, cuerpo?: unknown): Promise<Response> {
  const cabeceras: Record<string, string> = { Accept: "application/json" };
  const sesion = obtenerSesion();
  if (sesion) cabeceras.Authorization = `Bearer ${sesion.token}`;
  let body: BodyInit | null = null;
  if (cuerpo instanceof Crudo) {
    cabeceras["Content-Type"] = cuerpo.tipo;
    body = cuerpo.contenido;
  } else if (cuerpo !== undefined) {
    cabeceras["Content-Type"] = "application/json";
    body = JSON.stringify(cuerpo);
  }
  let r: Response;
  try {
    r = await fetch(BASE + ruta, { method: metodo, headers: cabeceras, body });
  } catch {
    throw new ErrorApi(0, "sin conexión con el servidor");
  }
  if (r.ok) return r;
  let detalle: unknown = null;
  try {
    detalle = ((await r.json()) as { detail?: unknown }).detail;
  } catch {
    /* respuesta sin JSON */
  }
  if (r.status === 401 && sesion) cerrarSesion();
  const { mensaje, problemas } = mensajeDe(detalle);
  throw new ErrorApi(r.status, mensaje, problemas, detalle);
}

async function json<T>(metodo: Metodo, ruta: string, cuerpo?: unknown): Promise<T> {
  return (await (await pedir(metodo, ruta, cuerpo)).json()) as T;
}

function consulta(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export interface FiltroPendientes {
  surtidor_id?: number | null;
  lado?: string | null;
  forma_pago?: "CONTADO" | "CREDITO" | "TODAS";
}

export type FormatoContador = "xlsx" | "csv" | "pdf";

export interface RangoReporte {
  desde: string;
  hasta: string;
  surtidor_id?: number | null;
}

export const api = {
  ingresar: (usuario: string, pin: string) => json<Token>("POST", "/auth/login", { usuario, pin }),
  yo: () => json<Usuario>("GET", "/auth/yo"),
  catalogos: () => json<Catalogos>("GET", "/catalogos"),
  pendientes: (f: FiltroPendientes = {}) => json<Pendiente[]>("GET", "/despachos/pendientes" + consulta({ ...f })),
  crearVentaManual: (v: VentaManualEntrada) => json<{ id: number }>("POST", "/ventas-manuales", v),
  buscarCliente: async (tipo: string, numero: string): Promise<Cliente | null> => {
    try {
      return await json<Cliente>("GET", "/clientes/buscar" + consulta({ tipo, numero }));
    } catch (e) {
      if (e instanceof ErrorApi && e.estado === 404) return null;
      throw e;
    }
  },
  crearCliente: (c: ClienteEntrada) => json<Cliente>("POST", "/clientes", c),
  configuracionPublica: () => json<ConfiguracionPublica>("GET", "/publico/configuracion"),
  registroPublico: (c: RegistroPublicoEntrada) => json<{ mensaje: string }>("POST", "/publico/registro", c),
  emitir: (f: FacturaEntrada) => json<Factura>("POST", "/facturas", f),
  factura: (id: number) => json<Factura>("GET", `/facturas/${id}`),
  facturaPdf: async (id: number): Promise<Blob> => (await pedir("GET", `/facturas/${id}/pdf`)).blob(),
  reporteDiario: (r: RangoReporte) => json<FilaVentas[]>("GET", "/reportes/diario" + consulta({ ...r })),
  reporteMensual: (r: RangoReporte) => json<FilaVentas[]>("GET", "/reportes/mensual" + consulta({ ...r })),
  reporteTurno: (r: RangoReporte) => json<FilaTurno[]>("GET", "/reportes/turno" + consulta({ ...r })),
  exportarExcel: async (r: RangoReporte & { surtidor_id: number }): Promise<Blob> =>
    (await pedir("GET", "/reportes/exportar" + consulta({ ...r }))).blob(),
  posiblesDuplicados: (r: RangoReporte) =>
    json<PosibleDuplicado[]>("GET", "/reportes/posibles-duplicados" + consulta({ desde: r.desde, hasta: r.hasta })),
  reportePdf: async (tipo: "diario" | "mensual" | "turno", r: RangoReporte): Promise<Blob> =>
    (await pedir("GET", "/reportes/pdf" + consulta({ tipo, ...r }))).blob(),
  contador: async (r: RangoReporte, formato: FormatoContador): Promise<Blob> =>
    (await pedir("GET", "/reportes/contador" + consulta({ desde: r.desde, hasta: r.hasta, formato }))).blob(),
  anomalias: (estado: "abiertas" | "resueltas" | "todas", severidad: string | null) =>
    json<Anomalia[]>("GET", "/anomalias" + consulta({ estado, severidad })),

  // ------------------------------------------------------------------ administración
  ventas: (f: FiltroVentas) => json<BusquedaVentas>("GET", "/ventas" + consulta({ ...f })),
  usuarios: () => json<UsuarioListado[]>("GET", "/usuarios"),
  clientes: (f: FiltroLista) => json<ClientesPagina>("GET", "/clientes" + consulta({ ...f })),
  activarCliente: (id: number, activo: boolean) =>
    json<Cliente>("POST", `/clientes/${id}/${activo ? "activar" : "desactivar"}`),
  vehiculos: (f: FiltroLista & { cliente_id?: number | null }) =>
    json<VehiculosPagina>("GET", "/vehiculos" + consulta({ ...f })),
  crearVehiculo: (v: VehiculoEntrada) => json<Vehiculo>("POST", "/vehiculos", v),
  activarVehiculo: (id: number, activo: boolean) =>
    json<Vehiculo>("POST", `/vehiculos/${id}/${activo ? "activar" : "desactivar"}`),
  importar: (que: "clientes" | "vehiculos", archivo: Blob, enSeco: boolean) =>
    json<Importacion>("POST", `/${que}/importar` + consulta({ en_seco: String(enSeco) }), new Crudo(archivo, "text/csv")),
};

export interface FiltroVentas {
  desde: string;
  hasta: string;
  surtidor_id?: number | null;
  lado?: string | null;
  producto?: string | null;
  forma_pago?: string | null;
  estado_facturacion?: string | null;
  placa?: string | null;
  cliente?: string | null;
  id_externo?: number | null;
  factura?: string | null;
  vendedor_id?: number | null;
  limite?: number;
  desplazamiento?: number;
}

export interface FiltroLista {
  texto?: string | null;
  estado?: "activos" | "inactivos" | "todos";
  limite?: number;
  desplazamiento?: number;
}
