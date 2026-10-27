/**
 * Imprimir el recibo (comandos ESC/POS que arma la API) en la térmica de la isla.
 *
 * Dos caminos, en este orden:
 *  1. WebUSB: Chrome habla directo con la impresora conectada por cable a la tablet. La primera vez pregunta
 *     qué impresora usar; después imprime sin preguntar. Exige HTTPS.
 *  2. RawBT: app de Android que hace de driver (USB o Bluetooth). La página le pasa el recibo con un enlace
 *     `rawbt:base64,...`. Si alguna vez no se eligió impresora USB, se recuerda y se va directo a RawBT.
 */

const CLASE_IMPRESORA = 7; // clase USB de las impresoras
const TROZO = 4096; // bytes por envío: las impresoras baratas tienen poca memoria
const PREFERENCIA = 'impresora'; // 'rawbt' cuando no hay impresora USB

export function aBase64(bytes) {
  let binario = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binario += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binario);
}

export const enlaceRawbt = (bytes) => `rawbt:base64,${aBase64(bytes)}`;

/** La interfaz de impresora y su salida de datos (endpoint bulk "out"), o null si no es una impresora. */
export function salidaImpresora(dispositivo) {
  for (const interfaz of dispositivo.configuration?.interfaces || []) {
    const alt = interfaz.alternate;
    if (alt?.interfaceClass !== CLASE_IMPRESORA) continue;
    const salida = alt.endpoints.find((e) => e.direction === 'out' && e.type === 'bulk');
    if (salida) return { interfaz: interfaz.interfaceNumber, endpoint: salida.endpointNumber };
  }
  return null;
}

async function enviarUsb(dispositivo, bytes) {
  await dispositivo.open();
  try {
    if (dispositivo.configuration === null) await dispositivo.selectConfiguration(1);
    const salida = salidaImpresora(dispositivo);
    if (!salida) throw new Error('El dispositivo USB elegido no es una impresora.');
    await dispositivo.claimInterface(salida.interfaz);
    for (let i = 0; i < bytes.length; i += TROZO) await dispositivo.transferOut(salida.endpoint, bytes.subarray(i, i + TROZO));
  } finally {
    await dispositivo.close().catch(() => {});
  }
}

function leerPreferencia(almacen) {
  try {
    return almacen?.getItem(PREFERENCIA) ?? null;
  } catch {
    return null;
  }
}

function guardarPreferencia(almacen, valor) {
  try {
    if (valor) almacen?.setItem(PREFERENCIA, valor);
    else almacen?.removeItem(PREFERENCIA);
  } catch {
    // sin almacenamiento (ventana privada): se vuelve a preguntar la próxima vez
  }
}

/**
 * Imprime `bytes` (Uint8Array). Devuelve el camino usado: 'usb' o 'rawbt'.
 * `entorno` se reemplaza en las pruebas.
 */
export async function imprimir(bytes, entorno = {}) {
  const {
    usb = globalThis.navigator?.usb,
    almacen = globalThis.localStorage,
    abrir = (url) => {
      globalThis.location.href = url;
    },
  } = entorno;
  if (usb) {
    // Las ya autorizadas que son impresoras (sin configuración todavía no se sabe: se intenta).
    const conocidas = (await usb.getDevices()).filter((d) => d.configuration === null || salidaImpresora(d));
    let dispositivo = conocidas[0];
    if (!dispositivo && leerPreferencia(almacen) !== 'rawbt') {
      try {
        dispositivo = await usb.requestDevice({ filters: [{ classCode: CLASE_IMPRESORA }] });
      } catch (e) {
        if (e?.name !== 'NotFoundError') throw e; // NotFoundError: no eligió ninguna (no hay USB)
        guardarPreferencia(almacen, 'rawbt');
      }
    }
    if (dispositivo) {
      await enviarUsb(dispositivo, bytes);
      guardarPreferencia(almacen, null);
      return 'usb';
    }
  }
  abrir(enlaceRawbt(bytes));
  return 'rawbt';
}

/** Volver a preguntar por una impresora USB (p. ej. si se pasó de RawBT a cable). */
export const olvidarImpresora = (almacen = globalThis.localStorage) => guardarPreferencia(almacen, null);
