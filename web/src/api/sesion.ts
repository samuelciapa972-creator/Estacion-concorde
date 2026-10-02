/**
 * Sesión del usuario: el token vive en memoria y en sessionStorage (sobrevive a recargar la página, no a cerrar la
 * pestaña). sessionStorage puede fallar (modo privado, almacenamiento bloqueado): todo va en try/catch.
 */
import type { Token, Usuario } from "./tipos";

const LLAVE = "estacion.sesion";

export interface Sesion {
  token: string;
  usuario: Usuario;
  vence: number; // ms desde la época
}

let actual: Sesion | null = null;
const oyentes = new Set<() => void>();

function avisar() {
  oyentes.forEach((f) => {
    f();
  });
}

function leerGuardada(): Sesion | null {
  try {
    const crudo = sessionStorage.getItem(LLAVE);
    if (!crudo) return null;
    const s = JSON.parse(crudo) as Sesion;
    return s.vence > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function obtenerSesion(): Sesion | null {
  actual ??= leerGuardada();
  if (actual && actual.vence <= Date.now()) {
    cerrarSesion();
    return null;
  }
  return actual;
}

export function guardarSesion(t: Token, ahora: number = Date.now()): Sesion {
  actual = { token: t.access_token, usuario: t.usuario, vence: ahora + t.expira_en * 1000 };
  try {
    sessionStorage.setItem(LLAVE, JSON.stringify(actual));
  } catch {
    /* sin almacenamiento: la sesión dura lo que la página */
  }
  avisar();
  return actual;
}

export function cerrarSesion(): void {
  actual = null;
  try {
    sessionStorage.removeItem(LLAVE);
  } catch {
    /* nada que borrar */
  }
  avisar();
}

export function suscribirSesion(f: () => void): () => void {
  oyentes.add(f);
  return () => oyentes.delete(f);
}
