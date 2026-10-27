/** Lógica pura de la anulación de facturas (qué se muestra y quién puede anular). */

/** Aviso bajo el estado: "Nota crédito NCE1 · anula EDSC5", "Anulada por NCE1" o "" si es una factura normal. */
export function etiquetaDocumento(f) {
  if (f.tipo === 'NOTA_CREDITO') return `Nota crédito ${f.numero} · anula ${f.referencia}`;
  if (f.anulada_por) return `Anulada por ${f.anulada_por}`;
  return '';
}

/** El botón "Anular" es solo del administrador y de facturas que la API marca como anulables. */
export function puedeAnular(f, usuario) {
  return usuario?.rol === 'ADMIN' && f.anulable === true;
}

/** Advertencia del diálogo: la factura sigue vigente hasta que la DIAN acepte la nota. */
export const ADVERTENCIA_ANULAR =
  'La nota crédito se envía a la DIAN. Registre la venta nueva con los datos correctos solo cuando la DIAN acepte la nota: si la rechaza, la factura sigue vigente.';

/** Aviso tras anular: la nota salió, pero la factura aún no está anulada (eso lo decide la DIAN). */
export function avisoNotaEnviada(nota) {
  return `Nota crédito ${nota.numero} enviada; la factura queda anulada cuando la DIAN la acepte. Registre la venta nueva solo después.`;
}
