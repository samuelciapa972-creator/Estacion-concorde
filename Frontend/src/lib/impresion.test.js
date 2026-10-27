import { describe, expect, it, vi } from "vitest";
import { aBase64, enlaceRawbt, imprimir, salidaImpresora } from "./impresion.js";

const BYTES = new Uint8Array([0x1b, 0x40, 0x48, 0x4f, 0x4c, 0x41]);

function impresoraUsb() {
  const enviados = [];
  const configuracion = {
    interfaces: [
      { interfaceNumber: 0, alternate: { interfaceClass: 7, endpoints: [{ direction: "in", type: "bulk", endpointNumber: 1 }, { direction: "out", type: "bulk", endpointNumber: 2 }] } },
    ],
  };
  const d = {
    configuration: null,
    open: vi.fn(async () => {}),
    selectConfiguration: vi.fn(async () => {
      d.configuration = configuracion;
    }),
    claimInterface: vi.fn(async () => {}),
    transferOut: vi.fn(async (endpoint, datos) => enviados.push([endpoint, Array.from(datos)])),
    close: vi.fn(async () => {}),
  };
  return { d, enviados };
}

function almacen(inicial = {}) {
  const datos = { ...inicial };
  return { datos, getItem: (k) => datos[k] ?? null, setItem: (k, v) => (datos[k] = v), removeItem: (k) => delete datos[k] };
}

const noEligio = () => Object.assign(new Error("No device selected."), { name: "NotFoundError" });

describe("enlace para RawBT", () => {
  it("lleva los bytes en base64", () => {
    expect(aBase64(BYTES)).toBe("G0BIT0xB");
    expect(enlaceRawbt(BYTES)).toBe("rawbt:base64,G0BIT0xB");
  });
  it("aguanta un recibo grande (con imagen)", () => {
    const grande = new Uint8Array(100_000).fill(0xff);
    expect(atob(aBase64(grande)).length).toBe(100_000);
  });
});

describe("imprimir", () => {
  it("sin WebUSB (Bluetooth, otro navegador): va a RawBT", async () => {
    const abrir = vi.fn();
    expect(await imprimir(BYTES, { usb: undefined, almacen: almacen(), abrir })).toBe("rawbt");
    expect(abrir).toHaveBeenCalledWith("rawbt:base64,G0BIT0xB");
  });

  it("con una impresora USB ya autorizada: imprime directo sin preguntar", async () => {
    const { d, enviados } = impresoraUsb();
    const usb = { getDevices: async () => [d], requestDevice: vi.fn() };
    const abrir = vi.fn();
    expect(await imprimir(BYTES, { usb, almacen: almacen(), abrir })).toBe("usb");
    expect(usb.requestDevice).not.toHaveBeenCalled();
    expect(d.claimInterface).toHaveBeenCalledWith(0);
    expect(enviados).toEqual([[2, Array.from(BYTES)]]);
    expect(d.close).toHaveBeenCalled();
    expect(abrir).not.toHaveBeenCalled();
  });

  it("la primera vez pregunta cuál impresora USB usar", async () => {
    const { d } = impresoraUsb();
    const usb = { getDevices: async () => [], requestDevice: vi.fn(async () => d) };
    expect(await imprimir(BYTES, { usb, almacen: almacen(), abrir: vi.fn() })).toBe("usb");
    expect(usb.requestDevice).toHaveBeenCalledWith({ filters: [{ classCode: 7 }] });
  });

  it("si no elige ninguna USB, usa RawBT y lo recuerda para no volver a preguntar", async () => {
    const usb = { getDevices: async () => [], requestDevice: vi.fn(async () => Promise.reject(noEligio())) };
    const a = almacen();
    const abrir = vi.fn();
    expect(await imprimir(BYTES, { usb, almacen: a, abrir })).toBe("rawbt");
    expect(a.datos.impresora).toBe("rawbt");
    expect(await imprimir(BYTES, { usb, almacen: a, abrir })).toBe("rawbt");
    expect(usb.requestDevice).toHaveBeenCalledTimes(1);
    expect(abrir).toHaveBeenCalledTimes(2);
  });

  it("parte el recibo en trozos pequeños", async () => {
    const { d, enviados } = impresoraUsb();
    const usb = { getDevices: async () => [d] };
    await imprimir(new Uint8Array(10_000), { usb, almacen: almacen(), abrir: vi.fn() });
    expect(enviados.map(([, datos]) => datos.length)).toEqual([4096, 4096, 1808]);
  });

  it("un error del USB se muestra y no se manda también a RawBT (no saldría doble)", async () => {
    const { d } = impresoraUsb();
    d.claimInterface = vi.fn(async () => Promise.reject(new Error("Unable to claim interface.")));
    const abrir = vi.fn();
    await expect(imprimir(BYTES, { usb: { getDevices: async () => [d] }, almacen: almacen(), abrir })).rejects.toThrow("claim");
    expect(d.close).toHaveBeenCalled();
    expect(abrir).not.toHaveBeenCalled();
  });
});

describe("salidaImpresora", () => {
  it("ignora dispositivos que no son impresoras", () => {
    const teclado = { configuration: { interfaces: [{ interfaceNumber: 0, alternate: { interfaceClass: 3, endpoints: [] } }] } };
    expect(salidaImpresora(teclado)).toBeNull();
  });
});
