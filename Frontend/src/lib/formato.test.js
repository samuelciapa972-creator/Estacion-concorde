import { describe, expect, it } from "vitest";
import { fechaHora, galones, leerGalones, leerPesos, pesos, rangoHastaHoy, sumar } from "./formato.js";

describe("formato es-CO sin punto flotante", () => {
  it("pesos", () => {
    expect(pesos("29757")).toBe("$ 29.757");
    expect(pesos("1763290421")).toBe("$ 1.763.290.421");
    expect(pesos("29757.00")).toBe("$ 29.757");
    expect(pesos("29757.39")).toBe("$ 29.757,39");
  });

  it("galones siempre con 3 decimales", () => {
    expect(galones("1.861")).toBe("1,861");
    expect(galones("12.5")).toBe("12,500");
    expect(galones("158142.765")).toBe("158.142,765");
  });

  it("un valor que no es decimal no se formatea en silencio", () => {
    expect(() => pesos("1e3")).toThrow();
    expect(() => galones("1.2345")).toThrow();
  });

  it("totales exactos (0,1 + 0,2 no da 0,30000000000000004)", () => {
    expect(sumar(["0.1", "0.2"], 3)).toBe("0.300");
    expect(sumar(["29757", "15990.50"], 2)).toBe("45747.50");
    expect(sumar([], 3)).toBe("0.000");
  });

  it("fecha local sin zona", () => {
    expect(fechaHora("2026-09-28T02:18:00")).toBe("28/09/2026 02:18");
    expect(fechaHora("2026-09-28T02:18:00.123456")).toBe("28/09/2026 02:18");
  });

  it("una marca con zona (UTC) se muestra en hora de Colombia", () => {
    expect(fechaHora("2026-10-01T21:34:41.893601Z")).toBe("01/10/2026 16:34");
    expect(fechaHora("2026-10-01T02:00:00+00:00")).toBe("30/09/2026 21:00");
  });
});

describe("lo que digita el bombero", () => {
  it.each([
    ["1,861", "1.861"],
    ["1.861", "1.861"],
    ["12", "12"],
    ["007,5", "7.5"],
  ])("galones %s -> %s", (entrada, api) => {
    expect(leerGalones(entrada)).toBe(api);
  });

  it.each(["", "0", "0,000", "1,2345", "1.2.3", "abc", "-1", "1,"])("galones inválidos: %s", (e) => {
    expect(leerGalones(e)).toBeNull();
  });

  it.each([
    ["29757", "29757"],
    ["29.757", "29757"],
    ["$ 1.234.567", "1234567"],
  ])("pesos %s -> %s", (entrada, api) => {
    expect(leerPesos(entrada)).toBe(api);
  });

  it.each(["", "0", "29.75", "29757,39", "2.97.57", "-5"])("pesos inválidos: %s", (e) => {
    expect(leerPesos(e)).toBeNull();
  });
});

describe("rangoHastaHoy", () => {
  const ahora = new Date(2026, 9, 13, 15, 30); // 13-oct-2026 15:30 local
  it.each([
    ["hoy", "2026-10-13T00:00:00"],
    ["semana", "2026-10-07T00:00:00"],
    ["mes", "2026-10-01T00:00:00"],
    ["anio", "2025-11-01T00:00:00"],
  ])("%s", (periodo, desde) => {
    expect(rangoHastaHoy(periodo, ahora)).toEqual({ desde, hasta: "2026-10-14T00:00:00" });
  });

  it("fin de mes pasa al día siguiente", () => {
    expect(rangoHastaHoy("hoy", new Date(2026, 9, 31)).hasta).toBe("2026-11-01T00:00:00");
  });

  it("periodo desconocido", () => {
    expect(() => rangoHastaHoy("diario", ahora)).toThrow();
  });
});
