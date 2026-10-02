import { zodResolver } from "@hookform/resolvers/zod";
import type { ReactNode } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import type { ClienteEntrada } from "../api/tipos";
import { configuracion } from "../config";
import {
  CORREO,
  NOMBRE_TIPO,
  TIPOS_DOCUMENTO,
  calcularDv,
  problemaNumero,
  type TipoDocumento,
} from "../lib/documentos";
import { Boton, Campo, ESTILO_ENTRADA } from "./ui";

const esquema = z
  .object({
    tipo_documento: z.enum(TIPOS_DOCUMENTO),
    numero_documento: z.string().trim(),
    nombres: z.string().trim().min(1, "falta el nombre").max(200),
    apellidos: z.string().trim().max(200),
    email: z.string().trim().regex(CORREO, "el correo no es válido").max(200),
    autoriza_tratamiento: z.boolean().refine((v) => v, "sin la autorización no se pueden guardar sus datos"),
  })
  .superRefine((d, ctx) => {
    const p = problemaNumero(d.tipo_documento, d.numero_documento);
    if (p) ctx.addIssue({ code: "custom", path: ["numero_documento"], message: p });
    if (d.tipo_documento !== "NIT" && !d.apellidos) {
      ctx.addIssue({ code: "custom", path: ["apellidos"], message: "faltan los apellidos" });
    }
  });
export type DatosCliente = z.infer<typeof esquema>;

/** Convierte el formulario en la entrada de la API: mayúsculas como en la factura y DV calculado si es NIT. */
export function aEntrada(d: DatosCliente): ClienteEntrada {
  const dv = d.tipo_documento === "NIT" ? calcularDv(d.numero_documento) : null;
  return {
    tipo_documento: d.tipo_documento,
    numero_documento: d.numero_documento.trim(),
    digito_verificacion: dv === null ? null : String(dv),
    nombres: d.nombres.trim().toUpperCase(),
    apellidos: d.tipo_documento === "NIT" ? "" : d.apellidos.trim().toUpperCase(),
    email: d.email.trim().toLowerCase(),
    autoriza_tratamiento: d.autoriza_tratamiento,
  };
}

/**
 * Datos del cliente con la casilla de autorización (Ley 1581). Si viene `fijo`, el tipo y número ya los
 * buscó el bombero y no se cambian aquí.
 */
export function FormularioCliente({
  fijo,
  enviando,
  textoEnviar,
  onEnviar,
  antesDeEnviar,
  bloqueo,
}: {
  fijo?: { tipo: TipoDocumento; numero: string };
  enviando: boolean;
  textoEnviar: string;
  onEnviar: (e: ClienteEntrada) => void;
  /** Algo que va justo antes del botón (el captcha del registro público). */
  antesDeEnviar?: ReactNode;
  /** Si viene, el botón queda deshabilitado y se explica por qué. */
  bloqueo?: string;
}) {
  const { register, handleSubmit, control, formState } = useForm<DatosCliente>({
    resolver: zodResolver(esquema),
    defaultValues: {
      tipo_documento: fijo?.tipo ?? "CC",
      numero_documento: fijo?.numero ?? "",
      nombres: "",
      apellidos: "",
      email: "",
      autoriza_tratamiento: false,
    },
  });
  const [tipo, numero] = useWatch({ control, name: ["tipo_documento", "numero_documento"] });
  const esNit = tipo === "NIT";
  const dv = esNit ? calcularDv(numero.trim()) : null;
  const e = formState.errors;

  return (
    <form onSubmit={(ev) => void handleSubmit((d) => onEnviar(aEntrada(d)))(ev)} className="flex flex-col gap-4" noValidate>
      <div className="grid gap-4 md:grid-cols-2">
        <Campo id="tipo_documento" etiqueta="Tipo de documento" error={e.tipo_documento?.message}>
          {fijo ? (
            // Un <select disabled> no entrega su valor al formulario: el tipo fijo se muestra como texto.
            <input id="tipo_documento" readOnly value={NOMBRE_TIPO[fijo.tipo]} className={ESTILO_ENTRADA} />
          ) : (
            <select id="tipo_documento" className={ESTILO_ENTRADA} {...register("tipo_documento")}>
              {TIPOS_DOCUMENTO.map((t) => (
                <option key={t} value={t}>
                  {NOMBRE_TIPO[t]}
                </option>
              ))}
            </select>
          )}
        </Campo>
        <Campo
          id="numero_documento"
          etiqueta={esNit ? "NIT (sin dígito de verificación)" : "Número de documento"}
          error={e.numero_documento?.message}
          {...(esNit && dv !== null && numero.trim().length >= 8 ? { ayuda: `Dígito de verificación: ${dv}` } : {})}
        >
          <input
            id="numero_documento"
            readOnly={!!fijo}
            inputMode={tipo === "CC" || esNit ? "numeric" : "text"}
            autoComplete="off"
            className={ESTILO_ENTRADA}
            {...register("numero_documento")}
          />
        </Campo>
        <Campo id="nombres" etiqueta={esNit ? "Razón social" : "Nombres"} error={e.nombres?.message}>
          <input id="nombres" autoComplete={esNit ? "organization" : "given-name"} className={`${ESTILO_ENTRADA} uppercase`} {...register("nombres")} />
        </Campo>
        {!esNit && (
          <Campo id="apellidos" etiqueta="Apellidos" error={e.apellidos?.message}>
            <input id="apellidos" autoComplete="family-name" className={`${ESTILO_ENTRADA} uppercase`} {...register("apellidos")} />
          </Campo>
        )}
        <Campo id="email" etiqueta="Correo para recibir la factura" error={e.email?.message}>
          <input id="email" type="email" inputMode="email" autoComplete="email" className={ESTILO_ENTRADA} {...register("email")} />
        </Campo>
      </div>
      <div className="flex flex-col gap-1">
        <label className="flex min-h-12 items-start gap-3 rounded-xl border-2 border-stone-300 p-3">
          <input type="checkbox" className="mt-1 h-7 w-7 shrink-0" {...register("autoriza_tratamiento")} />
          <span className="text-base">
            {configuracion.politicaDatos.resumen}
            {configuracion.politicaDatos.provisional && (
              <em className="block text-sm text-amber-800">Texto provisional: falta la política de la empresa.</em>
            )}
          </span>
        </label>
        {e.autoriza_tratamiento && (
          <p role="alert" className="text-sm font-semibold text-red-700">
            {e.autoriza_tratamiento.message}
          </p>
        )}
      </div>
      {antesDeEnviar}
      {bloqueo && <p className="text-base text-stone-700">{bloqueo}</p>}
      <Boton type="submit" grande disabled={enviando || !!bloqueo}>
        {enviando ? "Guardando…" : textoEnviar}
      </Boton>
    </form>
  );
}
