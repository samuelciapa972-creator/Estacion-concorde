/** API falsa para las pruebas: reemplaza `fetch` y registra cada petición. */
import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { vi } from "vitest";
import { nuevoClienteConsultas, rutas } from "../App";
import { guardarSesion } from "../api/sesion";
import type { Cliente, Factura, Pendiente, Rol } from "../api/tipos";

export interface Peticion {
  metodo: string;
  ruta: string;
  cuerpo: unknown;
}

type Respuesta = { estado: number; cuerpo?: unknown } | "red";
type Manejador = (p: Peticion) => Respuesta | Promise<Respuesta>;

export function apiFalsa(manejadores: Record<string, Manejador>) {
  const peticiones: Peticion[] = [];
  const fetchFalso = vi.fn(async (url: string, init?: RequestInit) => {
    const ruta = url.replace(/^\/api/, "");
    const metodo = init?.method ?? "GET";
    const p: Peticion = { metodo, ruta, cuerpo: typeof init?.body === "string" ? JSON.parse(init.body) : null };
    peticiones.push(p);
    const llave = `${metodo} ${ruta.split("?")[0] ?? ""}`;
    const m = manejadores[llave];
    if (!m) return new Response(JSON.stringify({ detail: `sin manejador: ${llave}` }), { status: 500 });
    const r = await m(p);
    if (r === "red") throw new TypeError("Failed to fetch");
    return new Response(r.cuerpo === undefined ? null : JSON.stringify(r.cuerpo), {
      status: r.estado,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchFalso);
  return { peticiones, de: (metodo: string, prefijo: string) => peticiones.filter((p) => p.metodo === metodo && p.ruta.startsWith(prefijo)) };
}

export function conSesion(rol: Rol = "BOMBERO") {
  guardarSesion({
    access_token: "token-de-prueba",
    token_type: "bearer",
    expira_en: 3600,
    usuario: { id: 1, usuario: "bombero1", nombre: "Bombero Uno", rol },
  });
}

export function montar(ruta: string, estado?: unknown) {
  const router = createMemoryRouter(rutas, { initialEntries: [{ pathname: ruta, state: estado }] });
  return { router, ...render(<QueryClientProvider client={nuevoClienteConsultas()}><RouterProvider router={router} /></QueryClientProvider>) };
}

// ------------------------------------------------------------------ datos sintéticos (nunca reales)
export const PENDIENTE: Pendiente = {
  origen_tipo: "despacho",
  origen_id: 7,
  surtidor_id: 1,
  lado: "A",
  producto: "DIESEL",
  volumen: "1.861",
  valor: "29757",
  ppu: "15990.00",
  forma_pago: "CONTADO",
  fecha: "2026-09-28T10:15:00",
  placa: "ABC123",
};

export const PERSONA: Cliente = {
  id: 11,
  tipo_documento: "CC",
  numero_documento: "1234567",
  digito_verificacion: null,
  nombre_mostrar: "ANA PRUEBA EJEMPLO",
  nombres: "ANA",
  apellidos: "PRUEBA EJEMPLO",
  email_enmascarado: "an*@example.com",
  activo: true,
};

export const EMPRESA: Cliente = {
  id: 12,
  tipo_documento: "NIT",
  numero_documento: "900123456",
  digito_verificacion: "8",
  nombre_mostrar: "TRANSPORTES DE PRUEBA S.A.S.",
  nombres: "TRANSPORTES DE PRUEBA S.A.S.",
  apellidos: "",
  email_enmascarado: "fa*****@example.com",
  activo: true,
};

export function factura(estado: Factura["estado"], extra: Partial<Factura> = {}): Factura {
  return {
    id: 99,
    numero: "PRUE-1",
    estado,
    total: "29757.00",
    cufe: estado === "FACTURADO" ? "abc123" : null,
    fecha_emision: "2026-09-28T10:16:00",
    medio_pago: "EFECTIVO",
    origen_tipo: "despacho",
    origen_id: 7,
    cliente: { id: PERSONA.id, tipo_documento: "CC", nombre_mostrar: PERSONA.nombre_mostrar },
    error: null,
    nueva: true,
    ...extra,
  };
}
