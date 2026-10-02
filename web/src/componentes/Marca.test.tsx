import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { apiFalsa, conSesion, montar } from "../test/apiFalsa";

const CONFIG = (estacion: { nombre: string } | null) => ({
  "GET /publico/configuracion": () => ({ estado: 200, cuerpo: { captcha: null, estacion } }),
});

describe("marca de la estación", () => {
  it("el encabezado muestra el nombre de la estación que entrega la API", async () => {
    conSesion();
    apiFalsa({ ...CONFIG({ nombre: "EDS DE PRUEBA" }), "GET /despachos/pendientes": () => ({ estado: 200, cuerpo: [] }) });
    montar("/pista");
    const encabezado = await screen.findByRole("banner");
    expect(await within(encabezado).findByText("EDS DE PRUEBA")).toBeInTheDocument();
  });

  it("el ingreso muestra el nombre de la estación", async () => {
    apiFalsa(CONFIG({ nombre: "EDS DE PRUEBA" }));
    montar("/ingreso");
    expect(await screen.findByText("EDS DE PRUEBA")).toBeInTheDocument();
  });

  it("el registro público muestra el nombre de la estación", async () => {
    apiFalsa(CONFIG({ nombre: "EDS DE PRUEBA" }));
    montar("/registro");
    expect(await screen.findByText("EDS DE PRUEBA")).toBeInTheDocument();
  });

  it("sin estación configurada dice «Estación»", async () => {
    apiFalsa(CONFIG(null));
    montar("/ingreso");
    expect(await screen.findByRole("heading", { name: "Estación" })).toBeInTheDocument();
  });

  it("si la consulta falla, la pantalla funciona igual y dice «Estación»", async () => {
    apiFalsa({ "GET /publico/configuracion": () => "red" });
    montar("/ingreso");
    expect(await screen.findByRole("heading", { name: "Estación" })).toBeInTheDocument();
    expect(screen.getByLabelText("PIN")).toBeInTheDocument();
  });

  it("el menú usa íconos SVG con texto, sin emojis", async () => {
    conSesion("ADMIN");
    apiFalsa({ ...CONFIG({ nombre: "EDS DE PRUEBA" }), "GET /despachos/pendientes": () => ({ estado: 200, cuerpo: [] }) });
    montar("/pista");
    const menu = await screen.findByRole("navigation", { name: "Secciones" });
    const enlaces = within(menu).getAllByRole("link");
    expect(enlaces.length).toBeGreaterThan(1);
    for (const enlace of enlaces) {
      expect(enlace.querySelector("svg")).not.toBeNull();
      expect(enlace.textContent.trim()).not.toBe("");
      expect(enlace.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
    }
  });
});

describe("menú en celular", () => {
  it("el botón despliega las secciones, navega y se cierra", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    conSesion("ADMIN");
    apiFalsa({
      ...CONFIG({ nombre: "EDS DE PRUEBA" }),
      "GET /despachos/pendientes": () => ({ estado: 200, cuerpo: [] }),
      "GET /catalogos": () => ({ estado: 200, cuerpo: { surtidores: [], productos: [], medios_pago: [], tipos_documento: [] } }),
      "GET /clientes": () => ({ estado: 200, cuerpo: { filas: [], total: 0 } }),
    });
    const u = userEvent.setup();
    const { router } = montar("/pista");
    const boton = await screen.findByRole("button", { name: "Abrir menú" });
    expect(boton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("navigation", { name: "Menú" })).not.toBeInTheDocument();

    await u.click(boton);
    expect(screen.getByRole("button", { name: "Cerrar menú" })).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("navigation", { name: "Menú" });
    await u.click(within(menu).getByRole("link", { name: "Clientes" }));

    expect(router.state.location.pathname).toBe("/admin/clientes");
    expect(screen.queryByRole("navigation", { name: "Menú" })).not.toBeInTheDocument();
  });
});
