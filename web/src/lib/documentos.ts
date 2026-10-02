/** Tipos de documento y dígito de verificación. Espejo de `facturacion/documentos.py` (pruebas de paridad en
 * documentos.test.ts); la validación que vale es la de la API, esto solo ayuda a digitar. */

export const TIPOS_DOCUMENTO = ["CC", "NIT", "CE", "PA", "PPT", "NIT_EXTERIOR"] as const;
export type TipoDocumento = (typeof TIPOS_DOCUMENTO)[number];

export const NOMBRE_TIPO: Record<TipoDocumento, string> = {
  CC: "Cédula de ciudadanía",
  NIT: "NIT",
  CE: "Cédula de extranjería",
  PA: "Pasaporte",
  PPT: "Permiso por protección temporal",
  NIT_EXTERIOR: "NIT de otro país",
};

export function esTipoDocumento(t: string): t is TipoDocumento {
  return (TIPOS_DOCUMENTO as readonly string[]).includes(t);
}

const PESOS_DV = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71];

/** Dígito de verificación de un NIT (sin el DV). null si el texto no es un NIT de dígitos. */
export function calcularDv(nit: string): number | null {
  if (!/^\d+$/.test(nit) || nit.length > PESOS_DV.length) return null;
  let total = 0;
  for (let i = 0; i < nit.length; i++) {
    total += Number(nit.charAt(nit.length - 1 - i)) * (PESOS_DV[i] ?? 0);
  }
  const r = total % 11;
  return r < 2 ? r : 11 - r;
}

/** Mismas reglas que `validar_cliente` para el número; devuelve el problema o null. */
export function problemaNumero(tipo: TipoDocumento, numero: string): string | null {
  const doc = numero.trim();
  if (tipo === "CC") return /^\d{5,10}$/.test(doc) ? null : "la cédula debe tener entre 5 y 10 dígitos";
  if (tipo === "NIT") {
    return /^\d{8,10}$/.test(doc) ? null : "el NIT debe tener entre 8 y 10 dígitos, sin dígito de verificación";
  }
  return /^[A-Za-z0-9]{3,20}$/.test(doc) ? null : "debe ser alfanumérico, de 3 a 20 caracteres";
}

export const CORREO = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;
