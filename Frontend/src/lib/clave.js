/**
 * Clave de emisión: se genera UNA vez por intento de facturar una venta y se reusa en cada reintento, así un doble
 * clic o un corte de red no producen dos facturas (la API es idempotente por clave). Usa crypto.getRandomValues,
 * que existe también por http:// en la red local (crypto.randomUUID solo en HTTPS).
 */
export function nuevaClave() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}
