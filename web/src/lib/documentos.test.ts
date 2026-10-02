import { describe, expect, it } from "vitest";
import { calcularDv, problemaNumero } from "./documentos";

describe("dígito de verificación (paridad con calcular_dv de Python)", () => {
  // Valores obtenidos de estacion.facturacion.documentos.calcular_dv.
  it.each([
    ["900123456", 8],
    ["800197268", 4],
    ["900373115", 3],
    ["860002964", 4],
    ["899999068", 1],
    ["1020304050", 8],
    ["12345678", 8],
  ])("%s -> %d", (nit, dv) => {
    expect(calcularDv(nit)).toBe(dv);
  });

  it("no calcula sobre algo que no es un NIT", () => {
    expect(calcularDv("90012345-6")).toBeNull();
    expect(calcularDv("1234567890123456")).toBeNull();
  });
});

describe("número de documento", () => {
  it("mismas reglas que validar_cliente", () => {
    expect(problemaNumero("CC", "1234")).toMatch(/5 y 10/);
    expect(problemaNumero("CC", "1234567")).toBeNull();
    expect(problemaNumero("NIT", "9001234567")).toBeNull();
    expect(problemaNumero("NIT", "900123456-8")).toMatch(/sin dígito/);
    expect(problemaNumero("PA", "AB12345")).toBeNull();
  });
});
