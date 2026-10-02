/**
 * Clave de emisión: se genera UNA vez por intento de facturar una venta y se reusa en cada reintento, así un doble
 * clic o un corte de red no producen dos facturas (la API es idempotente por clave).
 *
 * No usa crypto.randomUUID: solo existe en contextos seguros (HTTPS) y la tablet entra por http:// en la red
 * local. crypto.getRandomValues sí está disponible ahí.
 */
export function nuevaClave(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
