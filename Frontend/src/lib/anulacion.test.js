import { describe, expect, it } from "vitest";
import { ADVERTENCIA_ANULAR, avisoNotaEnviada, etiquetaDocumento, puedeAnular } from "./anulacion.js";

describe("etiqueta del documento", () => {
  it("la nota crédito dice qué factura anula", () => {
    expect(etiquetaDocumento({ tipo: "NOTA_CREDITO", numero: "NCE1", referencia: "EDSC5", anulada_por: null })).toBe("Nota crédito NCE1 · anula EDSC5");
  });
  it("la factura anulada dice qué nota la anula", () => {
    expect(etiquetaDocumento({ tipo: "FACTURA", numero: "EDSC5", referencia: null, anulada_por: "NCE1" })).toBe("Anulada por NCE1");
  });
  it("una factura normal no lleva etiqueta", () => {
    expect(etiquetaDocumento({ tipo: "FACTURA", numero: "EDSC6", referencia: null, anulada_por: null })).toBe("");
  });
});

describe("quién puede anular", () => {
  const f = { anulable: true };
  it("solo el administrador", () => {
    expect(puedeAnular(f, { rol: "BOMBERO" })).toBe(false);
    expect(puedeAnular(f, null)).toBe(false);
    expect(puedeAnular(f, { rol: "ADMIN" })).toBe(true);
  });
  it("y solo si la factura es anulable", () => {
    expect(puedeAnular({ anulable: false }, { rol: "ADMIN" })).toBe(false);
    expect(puedeAnular({}, { rol: "ADMIN" })).toBe(false);
  });
});

describe("textos de la anulación", () => {
  // La factura NO queda anulada al enviar la nota: solo cuando la DIAN la acepta (si la rechaza, sigue vigente).
  it("el aviso tras anular no da la factura por anulada", () => {
    expect(avisoNotaEnviada({ numero: "NCE1" })).toBe(
      "Nota crédito NCE1 enviada; la factura queda anulada cuando la DIAN la acepte. Registre la venta nueva solo después."
    );
  });
  it("la advertencia del diálogo dice que la venta nueva espera a la DIAN", () => {
    expect(ADVERTENCIA_ANULAR).toMatch(/solo cuando la DIAN acepte la nota/);
    expect(ADVERTENCIA_ANULAR).toMatch(/si la rechaza, la factura sigue vigente/);
    expect(ADVERTENCIA_ANULAR).not.toMatch(/quedará anulada/);
  });
});
