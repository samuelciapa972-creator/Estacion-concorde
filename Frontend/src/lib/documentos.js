/** Tipos de documento y dígito de verificación. Espejo de `facturacion/documentos.py`; la validación que vale es
 * la de la API, esto solo ayuda a digitar. */

// Solo los que la factura electrónica sabe armar hoy (`facturacion/xml_factura.py`); los demás se agregan cuando
// la DIAN los valide en habilitación. NOMBRE_TIPO conserva todos para mostrar clientes ya registrados.
export const TIPOS_DOCUMENTO = ['CC', 'NIT'];

export const NOMBRE_TIPO = {
  CC: 'Cédula de ciudadanía',
  NIT: 'NIT',
  CE: 'Cédula de extranjería',
  PA: 'Pasaporte',
  PPT: 'Permiso por protección temporal',
  NIT_EXTERIOR: 'NIT de otro país',
};

const PESOS_DV = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71];

/** Dígito de verificación de un NIT (sin el DV). null si el texto no es un NIT de dígitos. */
export function calcularDv(nit) {
  if (!/^\d+$/.test(nit) || nit.length > PESOS_DV.length) return null;
  let total = 0;
  for (let i = 0; i < nit.length; i++) {
    total += Number(nit.charAt(nit.length - 1 - i)) * (PESOS_DV[i] ?? 0);
  }
  const r = total % 11;
  return r < 2 ? r : 11 - r;
}

/** Mismas reglas que `validar_cliente` para el número; devuelve el problema o null. */
export function problemaNumero(tipo, numero) {
  const doc = String(numero).trim();
  if (tipo === 'CC') return /^\d{5,10}$/.test(doc) ? null : 'la cédula debe tener entre 5 y 10 dígitos';
  if (tipo === 'NIT') {
    return /^\d{8,10}$/.test(doc) ? null : 'el NIT debe tener entre 8 y 10 dígitos, sin dígito de verificación';
  }
  return /^[A-Za-z0-9]{3,20}$/.test(doc) ? null : 'debe ser alfanumérico, de 3 a 20 caracteres';
}

export const CORREO = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;
