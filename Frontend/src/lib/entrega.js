// Entrega por correo de una factura ya emitida: qué decirle al bombero y hasta cuándo consultar su estado.

// Consultas de 2 s tras la validación: ~1 minuto esperando el correo; después la entrega sigue en segundo plano.
export const MAX_CONSULTAS_ENTREGA = 30;

export function seguirConsultando(factura, consultas) {
  if (factura.estado === "EN_PROCESO") return true;
  if (factura.estado !== "FACTURADO") return false;
  const estado = factura.entrega?.estado ?? "PENDIENTE";
  return estado === "PENDIENTE" && consultas < MAX_CONSULTAS_ENTREGA;
}

export function avisoEntrega(entrega, email) {
  const destino = email ? ` ${email}` : " del cliente";
  switch (entrega?.estado) {
    case "ENVIADA":
      return `Enviada al correo${destino}.`;
    case "FALLIDA":
      return "No se pudo enviar por correo: descargue el PDF para entregárselo al cliente.";
    default:
      return `Enviando la factura al correo${destino}...`;
  }
}
