/** Piezas de interfaz para la tablet: objetivos táctiles de al menos 48 px y nada que dependa del hover. */
import type { LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variante = "primario" | "secundario" | "peligro" | "fantasma";

const VARIANTES: Record<Variante, string> = {
  primario: "bg-dorado text-negro active:bg-dorado-hover disabled:bg-stone-300 disabled:text-stone-500",
  secundario: "bg-white text-stone-900 border-2 border-stone-300 active:bg-stone-100 disabled:text-stone-400",
  peligro: "bg-red-700 text-white active:bg-red-900 disabled:bg-stone-400",
  fantasma: "bg-transparent text-dorado-oscuro underline active:text-negro disabled:text-stone-400",
};

export function Boton({
  variante = "primario",
  grande = false,
  icono: Icono,
  className = "",
  type = "button",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variante?: Variante; grande?: boolean; icono?: LucideIcon }) {
  return (
    <button
      type={type}
      className={`boton-tactil inline-flex min-h-12 items-center justify-center gap-2 rounded-xl px-5 font-semibold disabled:cursor-not-allowed ${
        grande ? "min-h-16 text-xl" : "text-lg"
      } ${VARIANTES[variante]} ${className}`}
      {...props}
    >
      {Icono && <Icono className={grande ? "size-6" : "size-5"} aria-hidden="true" />}
      {children}
    </button>
  );
}

export function Aviso({
  tipo = "error",
  titulo,
  children,
}: {
  tipo?: "error" | "info" | "exito" | "alerta";
  titulo?: string;
  children?: ReactNode;
}) {
  const estilos = {
    error: "border-red-700 bg-red-50 text-red-900",
    info: "border-sky-700 bg-sky-50 text-sky-900",
    exito: "border-green-700 bg-green-50 text-green-900",
    alerta: "border-amber-600 bg-amber-50 text-amber-900",
  }[tipo];
  return (
    <div role={tipo === "error" ? "alert" : "status"} className={`animar-aviso rounded-xl border-l-8 p-4 ${estilos}`}>
      {titulo && <p className="text-lg font-bold">{titulo}</p>}
      {children}
    </div>
  );
}

export function ErrorDeApi({ error }: { error: unknown }) {
  if (!error) return null;
  const mensaje = error instanceof Error ? error.message : "error inesperado";
  const problemas = (error as { problemas?: string[] }).problemas ?? [];
  return (
    <Aviso titulo={mensaje.charAt(0).toUpperCase() + mensaje.slice(1)}>
      {problemas.length > 0 && (
        <ul className="mt-1 list-disc pl-6">
          {problemas.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </Aviso>
  );
}

export function Campo({
  etiqueta,
  error,
  ayuda,
  children,
  id,
}: {
  etiqueta: string;
  error?: string | undefined;
  ayuda?: string;
  children: ReactNode;
  id: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-base font-semibold text-stone-800">
        {etiqueta}
      </label>
      {children}
      {ayuda && !error && <p className="text-sm text-stone-600">{ayuda}</p>}
      {error && (
        <p role="alert" className="text-sm font-semibold text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

export const ESTILO_ENTRADA =
  "min-h-12 rounded-xl border-2 border-stone-300 bg-white px-3 text-lg focus:border-dorado-oscuro focus:outline-none focus:ring-2 focus:ring-dorado/40";

export function Cargando({ texto = "Cargando…" }: { texto?: string }) {
  return (
    <p role="status" className="p-4 text-lg text-stone-600">
      {texto}
    </p>
  );
}
