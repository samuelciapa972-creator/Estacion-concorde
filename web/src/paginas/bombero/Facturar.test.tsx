import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { FacturaEntrada } from "../../api/tipos";
import { EMPRESA, PENDIENTE, PERSONA, apiFalsa, conSesion, factura, montar } from "../../test/apiFalsa";

async function hastaResumen(numero = PERSONA.numero_documento, tipo = "CC") {
  const u = userEvent.setup();
  montar("/pista/facturar", { pendiente: PENDIENTE });
  if (tipo !== "CC") await u.selectOptions(screen.getByLabelText("Tipo"), tipo);
  await u.type(screen.getByLabelText(/^(Número|NIT)/), numero);
  await u.click(screen.getByRole("button", { name: "Buscar" }));
  await u.click(await screen.findByRole("button", { name: "Usar este cliente" }));
  await u.click(screen.getByRole("button", { name: "Efectivo" }));
  return u;
}

describe("emitir la factura", () => {
  it("doble clic en Emitir: una sola petición", async () => {
    conSesion();
    let soltar = () => {};
    const api = apiFalsa({
      "GET /clientes/buscar": () => ({ estado: 200, cuerpo: PERSONA }),
      "POST /facturas": () =>
        new Promise((ok) => {
          soltar = () => ok({ estado: 201, cuerpo: factura("EN_PROCESO") });
        }),
      "GET /facturas/99": () => ({ estado: 200, cuerpo: factura("EN_PROCESO") }),
    });
    const u = await hastaResumen();
    const emitir = screen.getByRole("button", { name: "Emitir factura" });
    await u.dblClick(emitir);
    await u.click(emitir);
    expect(api.de("POST", "/facturas")).toHaveLength(1);
    soltar();
    expect(await screen.findByText(/Factura creada: PRUE-1/)).toBeInTheDocument();
    expect(screen.getByText(/Puede atender al siguiente cliente/)).toBeInTheDocument();
  });

  it("corte de red: el reintento usa la MISMA clave", async () => {
    conSesion();
    let intentos = 0;
    const api = apiFalsa({
      "GET /clientes/buscar": () => ({ estado: 200, cuerpo: PERSONA }),
      "POST /facturas": () => (++intentos === 1 ? "red" : { estado: 200, cuerpo: factura("EN_PROCESO", { nueva: false }) }),
      "GET /facturas/99": () => ({ estado: 200, cuerpo: factura("EN_PROCESO") }),
    });
    const u = await hastaResumen();
    await u.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(await screen.findByText("No se sabe si la factura quedó creada")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Atrás" })).toBeDisabled(); // no se cambia el pago a mitad de una duda
    await u.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText(/ya tenía esta factura/)).toBeInTheDocument();
    const [a, b] = api.de("POST", "/facturas").map((p) => p.cuerpo as FacturaEntrada);
    expect(a?.clave).toMatch(/^[0-9a-f]{32}$/);
    expect(b?.clave).toBe(a?.clave);
    expect(b).toEqual({ clave: a?.clave, origen_tipo: "despacho", origen_id: 7, cliente_id: PERSONA.id, medio_pago: "EFECTIVO" });
  });

  it("muestra el estado final cuando la cola termina", async () => {
    conSesion();
    let consultas = 0;
    apiFalsa({
      "GET /clientes/buscar": () => ({ estado: 200, cuerpo: PERSONA }),
      "POST /facturas": () => ({ estado: 201, cuerpo: factura("EN_PROCESO") }),
      "GET /facturas/99": () => ({ estado: 200, cuerpo: factura(++consultas < 2 ? "EN_PROCESO" : "FACTURADO") }),
    });
    const u = await hastaResumen();
    await u.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(await screen.findByText(/Factura validada: PRUE-1/, {}, { timeout: 6000 })).toBeInTheDocument();
  }, 10_000);

  it("factura validada: descarga el PDF con la sesión; antes de validar no ofrece PDF", async () => {
    conSesion();
    // jsdom no implementa estas dos: se agregan solo para esta prueba.
    const crear = vi.fn(() => "blob:pdf");
    let consultas = 0;
    Object.assign(URL, { createObjectURL: crear, revokeObjectURL: vi.fn() });
    const api = apiFalsa({
      "GET /clientes/buscar": () => ({ estado: 200, cuerpo: PERSONA }),
      "POST /facturas": () => ({ estado: 201, cuerpo: factura("EN_PROCESO") }),
      "GET /facturas/99": () => ({ estado: 200, cuerpo: factura(++consultas < 2 ? "EN_PROCESO" : "FACTURADO") }),
      "GET /facturas/99/pdf": () => ({ estado: 200, cuerpo: "%PDF-" }),
    });
    const u = await hastaResumen();
    await u.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(await screen.findByText(/Factura creada: PRUE-1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Descargar PDF" })).not.toBeInTheDocument();
    const boton = await screen.findByRole("button", { name: "Descargar PDF" }, { timeout: 6000 });
    expect(screen.getByText(/envío por correo todavía no está disponible/)).toBeInTheDocument();
    await u.click(boton);
    await waitFor(() => expect(api.de("GET", "/facturas/99/pdf")).toHaveLength(1));
    expect(crear).toHaveBeenCalledOnce();
  }, 10_000);

  it("la venta ya no está pendiente (409): mensaje de la API, sin reintento", async () => {
    conSesion();
    apiFalsa({
      "GET /clientes/buscar": () => ({ estado: 200, cuerpo: PERSONA }),
      "POST /facturas": () => ({ estado: 409, cuerpo: { detail: "la venta ya no está pendiente" } }),
    });
    const u = await hastaResumen();
    await u.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("La venta ya no está pendiente");
    expect(screen.getByRole("button", { name: "Volver a la lista" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reintentar" })).not.toBeInTheDocument();
  });

  it("comprador con NIT: el resumen muestra la razón social", async () => {
    conSesion();
    apiFalsa({ "GET /clientes/buscar": () => ({ estado: 200, cuerpo: EMPRESA }) });
    await hastaResumen(EMPRESA.numero_documento, "NIT");
    expect(screen.getByText("Razón social")).toBeInTheDocument();
    expect(screen.getByText("TRANSPORTES DE PRUEBA S.A.S.")).toBeInTheDocument();
    expect(screen.getByText("NIT 900123456-8")).toBeInTheDocument();
  });

  it("caso no soportado (422): se explica y no se reintenta solo", async () => {
    conSesion();
    const api = apiFalsa({
      "GET /clientes/buscar": () => ({ estado: 200, cuerpo: PERSONA }),
      "POST /facturas": () => ({ estado: 422, cuerpo: { detail: "caso todavía no soportado: medio de pago TARJETA" } }),
    });
    const u = await hastaResumen();
    await u.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/todavía no soportado/);
    expect(api.de("POST", "/facturas")).toHaveLength(1);
  });
});

describe("cliente nuevo desde la pista", () => {
  it("no existe: registro rápido con autorización y sigue al pago", async () => {
    conSesion();
    const api = apiFalsa({
      "GET /clientes/buscar": () => ({ estado: 404, cuerpo: { detail: "no hay un cliente registrado con ese documento" } }),
      "POST /clientes": () => ({ estado: 201, cuerpo: PERSONA }),
    });
    const u = userEvent.setup();
    montar("/pista/facturar", { pendiente: PENDIENTE });
    await u.type(screen.getByLabelText("Número"), "1234567");
    await u.click(screen.getByRole("button", { name: "Buscar" }));
    expect(await screen.findByText("No está registrado")).toBeInTheDocument();

    await u.type(screen.getByLabelText("Nombres"), "Ana");
    await u.type(screen.getByLabelText("Apellidos"), "Prueba Ejemplo");
    await u.type(screen.getByLabelText(/Correo/), "Ana@Example.com");
    await u.click(screen.getByRole("button", { name: "Registrar y continuar" }));
    expect(await screen.findByText(/sin la autorización/)).toBeInTheDocument();
    expect(api.de("POST", "/clientes")).toHaveLength(0);

    await u.click(screen.getByRole("checkbox"));
    await u.click(screen.getByRole("button", { name: "Registrar y continuar" }));
    expect(await screen.findByText(/¿Cómo pagó ANA PRUEBA EJEMPLO\?/)).toBeInTheDocument();
    expect(api.de("POST", "/clientes")[0]?.cuerpo).toEqual({
      tipo_documento: "CC",
      numero_documento: "1234567",
      digito_verificacion: null,
      nombres: "ANA",
      apellidos: "PRUEBA EJEMPLO",
      email: "ana@example.com",
      autoriza_tratamiento: true,
    });
  });

  it("sin la venta elegida (recarga) vuelve a la lista", async () => {
    conSesion();
    apiFalsa({
      "GET /catalogos": () => ({ estado: 200, cuerpo: { surtidores: [], productos: [], medios_pago: [], tipos_documento: [] } }),
      "GET /despachos/pendientes": () => ({ estado: 200, cuerpo: [] }),
    });
    const { router } = montar("/pista/facturar");
    await waitFor(() => expect(router.state.location.pathname).toBe("/pista"));
  });
});
