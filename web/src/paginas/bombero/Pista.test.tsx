import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PENDIENTE, apiFalsa, conSesion, montar } from "../../test/apiFalsa";

const CATALOGOS = {
  surtidores: [{ id: 1, marca: "SIMULADOR", codigo: "SIM", descripcion: null }],
  productos: [{ id: 1, codigo: "DIESEL", nombre: "Diésel" }],
  medios_pago: ["EFECTIVO", "TARJETA"],
  tipos_documento: ["CC", "NIT"],
};

describe("pista", () => {
  it("sin sesión pide el ingreso", async () => {
    apiFalsa({});
    const { router } = montar("/pista");
    await waitFor(() => expect(router.state.location.pathname).toBe("/ingreso"));
  });

  it("lista los pendientes con formato es-CO y abre la factura", async () => {
    conSesion();
    apiFalsa({
      "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }),
      "GET /despachos/pendientes": () => ({ estado: 200, cuerpo: [PENDIENTE] }),
    });
    const u = userEvent.setup();
    const { router } = montar("/pista");
    const tarjeta = await screen.findByRole("button", { name: /Facturar DIESEL lado A/ });
    expect(tarjeta).toHaveTextContent("$ 29.757");
    expect(tarjeta).toHaveTextContent("1,861 gal");
    expect(tarjeta).toHaveTextContent("Placa ABC123");
    await u.click(tarjeta);
    expect(router.state.location.pathname).toBe("/pista/facturar");
  });

  it("filtra por surtidor y lado en la consulta", async () => {
    conSesion();
    const api = apiFalsa({
      "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }),
      "GET /despachos/pendientes": () => ({ estado: 200, cuerpo: [] }),
    });
    const u = userEvent.setup();
    montar("/pista");
    await u.click(await screen.findByRole("button", { name: "SIMULADOR" }));
    await u.click(screen.getByRole("button", { name: "B" }));
    await waitFor(() => expect(api.de("GET", "/despachos/pendientes").slice(-1)[0]?.ruta).toBe("/despachos/pendientes?surtidor_id=1&lado=B"));
  });

  it("venta manual: envía galones y pesos como texto y pasa a facturar", async () => {
    conSesion();
    const api = apiFalsa({
      "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }),
      "POST /ventas-manuales": () => ({ estado: 201, cuerpo: { id: 5 } }),
    });
    const u = userEvent.setup();
    const { router } = montar("/pista/venta-manual");
    await u.selectOptions(await screen.findByLabelText("Surtidor"), "1");
    await u.click(screen.getByRole("radio", { name: "A" }));
    await u.selectOptions(screen.getByLabelText("Producto"), "DIESEL");
    await u.type(screen.getByLabelText("Galones"), "1,861");
    await u.type(screen.getByLabelText(/Valor cobrado/), "29.757");
    expect(screen.getByText("1,861 gal por $ 29.757")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Guardar y facturar" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/pista/facturar"));
    expect(api.de("POST", "/ventas-manuales")[0]?.cuerpo).toEqual({
      surtidor_id: 1,
      lado: "A",
      producto: "DIESEL",
      volumen: "1.861",
      valor: "29757",
      forma_pago: "CONTADO",
    });
  });

  it("venta manual con galones mal digitados no se envía", async () => {
    conSesion();
    const api = apiFalsa({ "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }) });
    const u = userEvent.setup();
    montar("/pista/venta-manual");
    await u.type(await screen.findByLabelText("Galones"), "1,8615");
    await u.click(screen.getByRole("button", { name: "Guardar y facturar" }));
    expect(await screen.findByText(/hasta 3 decimales/)).toBeInTheDocument();
    expect(api.de("POST", "/ventas-manuales")).toHaveLength(0);
  });
});

describe("ingreso", () => {
  it("PIN incorrecto: mensaje de la API y el PIN se borra", async () => {
    apiFalsa({ "POST /auth/login": () => ({ estado: 401, cuerpo: { detail: "usuario o PIN incorrectos" } }) });
    const u = userEvent.setup();
    montar("/ingreso");
    await u.type(screen.getByLabelText("Usuario"), "bombero1");
    await u.type(screen.getByLabelText("PIN"), "9999");
    await u.click(screen.getByRole("button", { name: "Ingresar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Usuario o PIN incorrectos");
    expect(screen.getByLabelText("PIN")).toHaveValue("");
  });

  it("un bombero no entra a los reportes", async () => {
    conSesion("BOMBERO");
    apiFalsa({});
    montar("/admin/reportes");
    expect(await screen.findByText(/solo para administradores/)).toBeInTheDocument();
  });
});
