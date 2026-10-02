import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { FilaVentas, PosibleDuplicado } from "../../api/tipos";
import { apiFalsa, conSesion, montar } from "../../test/apiFalsa";

const CATALOGOS = {
  surtidores: [{ id: 1, marca: "SIMULADOR", codigo: "SIM", descripcion: null }],
  productos: [{ id: 1, codigo: "DIESEL", nombre: "Diésel" }],
  medios_pago: ["EFECTIVO", "TARJETA"],
  tipos_documento: ["CC", "NIT"],
};

const fila = (origen: FilaVentas["origen"], valor: string): FilaVentas => ({
  surtidor_id: 1,
  periodo: "2026-09-30",
  producto: "DIESEL",
  forma_pago: "CONTADO",
  origen,
  despachos: 1,
  galones: "1.861",
  valor,
});

const DUPLICADO: PosibleDuplicado = {
  venta_manual_id: 5,
  despacho_id: 7,
  surtidor_id: 1,
  lado: "A",
  producto: "DIESEL",
  galones: "1.861",
  valor: "29757",
  registrada_en: "2026-09-30T10:30:00",
  inicio_despacho: "2026-09-30T10:00:00",
};

function preparar(duplicados: PosibleDuplicado[] = []) {
  conSesion("ADMIN");
  // jsdom no implementa estas dos: se agregan para las descargas.
  Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined });
  return apiFalsa({
    "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }),
    "GET /reportes/diario": () => ({ estado: 200, cuerpo: [fila("SURTIDOR", "29757"), fila("MANUAL", "50000")] }),
    "GET /reportes/posibles-duplicados": () => ({ estado: 200, cuerpo: duplicados }),
    "GET /reportes/pdf": () => ({ estado: 200, cuerpo: "%PDF-" }),
    "GET /reportes/contador": () => ({ estado: 200, cuerpo: "x" }),
  });
}

function parametros(ruta: string | undefined): URLSearchParams {
  return new URLSearchParams((ruta ?? "").split("?")[1] ?? "");
}

describe("reportes", () => {
  it("muestra el origen de cada fila y el total incluye las manuales", async () => {
    preparar();
    montar("/admin/reportes");
    const tabla = await screen.findByRole("table");
    expect(within(tabla).getByText("MANUAL")).toBeInTheDocument();
    expect(within(tabla).getByText("SURTIDOR")).toBeInTheDocument();
    expect(within(tabla).getByText("$ 79.757")).toBeInTheDocument(); // 29.757 + 50.000
  });

  it("avisa de posibles ventas contadas dos veces", async () => {
    preparar([DUPLICADO]);
    montar("/admin/reportes");
    const aviso = await screen.findByText(/1 venta manual podría estar contada dos veces/);
    expect(aviso.closest("[role=status]")).toHaveTextContent("1,861 gal");
  });

  it("sin posibles duplicados no muestra aviso", async () => {
    const api = preparar([]);
    montar("/admin/reportes");
    await screen.findByRole("table");
    await waitFor(() => expect(api.de("GET", "/reportes/posibles-duplicados")).toHaveLength(1));
    expect(screen.queryByText(/contada dos veces/)).not.toBeInTheDocument();
  });

  it("descarga el PDF del reporte que se está viendo", async () => {
    const api = preparar();
    const u = userEvent.setup();
    montar("/admin/reportes");
    await screen.findByRole("table");
    await u.click(screen.getByRole("button", { name: "Descargar PDF" }));
    await waitFor(() => expect(api.de("GET", "/reportes/pdf")).toHaveLength(1));
    const p = parametros(api.de("GET", "/reportes/pdf")[0]?.ruta);
    expect(p.get("tipo")).toBe("diario");
    expect(p.get("desde")).toMatch(/^\d{4}-\d{2}-01$/);
  });

  it("exporta para el contador en el formato elegido", async () => {
    const api = preparar();
    const u = userEvent.setup();
    montar("/admin/reportes");
    await screen.findByRole("table");
    await u.selectOptions(screen.getByLabelText("Formato para el contador"), "csv");
    await u.click(screen.getByRole("button", { name: "Exportar para el contador" }));
    await waitFor(() => expect(api.de("GET", "/reportes/contador")).toHaveLength(1));
    expect(parametros(api.de("GET", "/reportes/contador")[0]?.ruta).get("formato")).toBe("csv");
  });
});
