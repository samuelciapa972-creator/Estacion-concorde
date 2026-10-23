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
