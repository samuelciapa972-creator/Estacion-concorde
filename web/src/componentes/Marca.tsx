/** Marca de la estación: el nombre sale de la base (estación activa) vía /publico/configuracion. */
import { useQuery } from "@tanstack/react-query";
import { Fuel } from "lucide-react";
import { api } from "../api/cliente";

export const NOMBRE_POR_DEFECTO = "Estación";

/** Misma consulta que usa el registro público: se pide una vez por sesión del navegador. */
export function useConfiguracionPublica() {
  return useQuery({ queryKey: ["configuracion-publica"], queryFn: api.configuracionPublica, staleTime: Infinity });
}

/** Nombre comercial de la estación activa; "Estación" si no hay o si la consulta falla (nunca bloquea la pantalla). */
export function useNombreEstacion(): string {
  return useConfiguracionPublica().data?.estacion?.nombre ?? NOMBRE_POR_DEFECTO;
}

/** Ícono y nombre en dorado. `grande` para el ingreso y el registro; normal para el encabezado. */
export function Marca({ grande = false, como: Etiqueta = "span" }: { grande?: boolean; como?: "span" | "h1" }) {
  const nombre = useNombreEstacion();
  return (
    <span className="flex items-center gap-2 text-dorado">
      <span
        className={`flex items-center justify-center rounded-lg border border-dorado/60 ${grande ? "size-12" : "size-9"}`}
        aria-hidden="true"
      >
        <Fuel className={grande ? "size-7" : "size-5"} strokeWidth={2} />
      </span>
      <Etiqueta className={`font-bold tracking-wide ${grande ? "text-3xl" : "text-xl"}`}>{nombre}</Etiqueta>
    </span>
  );
}
