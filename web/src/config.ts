/**
 * Decisiones que NO toma el código (ver CLAUDE.md, "Pendiente de la empresa"). Mientras sigan en null, la pantalla
 * muestra la opción deshabilitada con el motivo, en vez de inventar un tratamiento.
 */
export const configuracion = {
  /**
   * "Consumidor final / sin factura": su tratamiento fiscal lo define el contador (documento equivalente POS,
   * factura a consumidor final con el NIT genérico, o nada). Hasta entonces: null = deshabilitado.
   */
  consumidorFinal: null as null | { etiqueta: string },

  /**
   * TEXTO PROVISIONAL de la autorización de datos (Ley 1581 de 2012). La empresa debe aportar su política real
   * (responsable, finalidades, derechos, canal de consultas) antes de publicar el QR fuera del banco de pruebas.
   */
  politicaDatos: {
    provisional: true,
    resumen:
      "Autorizo a la estación de servicio a tratar mis datos personales (documento, nombre y correo) para " +
      "expedir y enviarme facturas electrónicas, conforme a la Ley 1581 de 2012. Puedo conocer, actualizar, " +
      "rectificar y suprimir mis datos.",
  },

  /** Cada cuánto se refresca la lista de ventas pendientes y el estado de una factura en proceso. */
  refrescoPendientesMs: 10_000,
  refrescoFacturaMs: 2_000,
};
