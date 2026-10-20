import { describe, expect, it } from "vitest";
import { avisoEntrega, seguirConsultando } from "./entrega.js";

const f = (estado, entrega) => ({ estado, entrega });

describe("seguirConsultando", () => {
  it("consulta mientras la factura está en proceso", () => {
    expect(seguirConsultando(f("EN_PROCESO", null), 0)).toBe(true);
  });
  it("facturada: consulta hasta que el correo sale o falla, con un límite", () => {
    expect(seguirConsultando(f("FACTURADO", null), 0)).toBe(true);
    expect(seguirConsultando(f("FACTURADO", { estado: "PENDIENTE" }), 5)).toBe(true);
    expect(seguirConsultando(f("FACTURADO", { estado: "PENDIENTE" }), 30)).toBe(false);
    expect(seguirConsultando(f("FACTURADO", { estado: "ENVIADA" }), 0)).toBe(false);
    expect(seguirConsultando(f("FACTURADO", { estado: "FALLIDA" }), 0)).toBe(false);
  });
  it("rechazada o incierta: no espera correo", () => {
    expect(seguirConsultando(f("RECHAZADO", null), 0)).toBe(false);
  });
});

describe("avisoEntrega", () => {
  it("dice a qué correo va o fue", () => {
    expect(avisoEntrega(null, "ju***@gmail.com")).toMatch(/Enviando.*ju\*\*\*@gmail\.com/);
    expect(avisoEntrega({ estado: "PENDIENTE" }, "ju***@gmail.com")).toMatch(/Enviando/);
    expect(avisoEntrega({ estado: "ENVIADA" }, "ju***@gmail.com")).toMatch(/Enviada al correo ju\*\*\*@gmail\.com/);
  });
  it("si falla pide entregar el PDF", () => {
    expect(avisoEntrega({ estado: "FALLIDA" }, "x")).toMatch(/No se pudo enviar.*PDF/);
  });
  it("sin correo conocido no muestra undefined", () => {
    expect(avisoEntrega({ estado: "ENVIADA" }, undefined)).not.toMatch(/undefined/);
  });
});
