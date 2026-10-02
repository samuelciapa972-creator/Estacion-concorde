import { useSyncExternalStore } from "react";
import { obtenerSesion, suscribirSesion, type Sesion } from "../api/sesion";

/** Sesión actual; se actualiza al ingresar, salir o cuando la API responde 401. */
export function useSesion(): Sesion | null {
  return useSyncExternalStore(suscribirSesion, obtenerSesion);
}
