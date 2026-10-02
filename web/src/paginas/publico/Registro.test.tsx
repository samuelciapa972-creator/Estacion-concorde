import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { apiFalsa, montar } from "../../test/apiFalsa";

const SIN_CAPTCHA = { "GET /publico/configuracion": () => ({ estado: 200, cuerpo: { captcha: null } }) };

describe("registro público por QR", () => {
  it("NIT: calcula el DV, pide razón social y no apellidos", async () => {
    const api = apiFalsa({ ...SIN_CAPTCHA, "POST /publico/registro": () => ({ estado: 202, cuerpo: { mensaje: "Gracias." } }) });
    const u = userEvent.setup();
    montar("/registro");
    await u.selectOptions(await screen.findByLabelText("Tipo de documento"), "NIT");
    await u.type(screen.getByLabelText(/NIT \(sin dígito/), "900123456");
    expect(screen.getByText("Dígito de verificación: 8")).toBeInTheDocument();
    expect(screen.queryByLabelText("Apellidos")).not.toBeInTheDocument();
    await u.type(screen.getByLabelText("Razón social"), "Transportes de Prueba S.A.S.");
    await u.type(screen.getByLabelText(/Correo/), "facturas@example.com");
    await u.click(screen.getByRole("checkbox"));
    await u.click(screen.getByRole("button", { name: "Registrarme" }));
    expect(await screen.findByText("Gracias.")).toBeInTheDocument();
    expect(api.de("POST", "/publico/registro")[0]?.cuerpo).toEqual({
      tipo_documento: "NIT",
      numero_documento: "900123456",
      digito_verificacion: "8",
      nombres: "TRANSPORTES DE PRUEBA S.A.S.",
      apellidos: "",
      email: "facturas@example.com",
      autoriza_tratamiento: true,
      captcha: null,
    });
  });

  it("sin autorización de datos no se envía nada", async () => {
    const api = apiFalsa(SIN_CAPTCHA);
    const u = userEvent.setup();
    montar("/registro");
    await u.type(await screen.findByLabelText("Número de documento"), "1234567");
    await u.type(screen.getByLabelText("Nombres"), "Ana");
    await u.type(screen.getByLabelText("Apellidos"), "Prueba");
    await u.type(screen.getByLabelText(/Correo/), "ana@example.com");
    await u.click(screen.getByRole("button", { name: "Registrarme" }));
    expect(await screen.findByText(/sin la autorización/)).toBeInTheDocument();
    expect(api.de("POST", "/publico/registro")).toHaveLength(0);
  });

  it("límite de tasa (429): muestra el mensaje", async () => {
    apiFalsa({ ...SIN_CAPTCHA, "POST /publico/registro": () => ({ estado: 429, cuerpo: { detail: "demasiados registros desde este equipo; espere" } }) });
    const u = userEvent.setup();
    montar("/registro");
    await u.type(await screen.findByLabelText("Número de documento"), "1234567");
    await u.type(screen.getByLabelText("Nombres"), "Ana");
    await u.type(screen.getByLabelText("Apellidos"), "Prueba");
    await u.type(screen.getByLabelText(/Correo/), "ana@example.com");
    await u.click(screen.getByRole("checkbox"));
    await u.click(screen.getByRole("button", { name: "Registrarme" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Demasiados registros");
  });
});

describe("registro público con captcha", () => {
  async function llenar(u: ReturnType<typeof userEvent.setup>) {
    await u.type(await screen.findByLabelText("Número de documento"), "1234567");
    await u.type(screen.getByLabelText("Nombres"), "Ana");
    await u.type(screen.getByLabelText("Apellidos"), "Prueba");
    await u.type(screen.getByLabelText(/Correo/), "ana@example.com");
    await u.click(screen.getByRole("checkbox"));
  }

  it("no deja enviar sin resolverlo, envía el token y lo reinicia tras un rechazo", async () => {
    let resolver: (t: string) => void = () => {};
    const reset = vi.fn();
    vi.stubGlobal("hcaptcha", {
      render: (_el: HTMLElement, o: { sitekey: string; callback: (t: string) => void }) => {
        expect(o.sitekey).toBe("clave-publica");
        resolver = o.callback;
        return "w1";
      },
      reset,
    });
    let intentos = 0;
    const api = apiFalsa({
      "GET /publico/configuracion": () => ({
        estado: 200,
        cuerpo: { captcha: { proveedor: "hcaptcha", clave_sitio: "clave-publica" } },
      }),
      "POST /publico/registro": () =>
        ++intentos === 1
          ? { estado: 422, cuerpo: { detail: "no se pudo verificar que es una persona" } }
          : { estado: 202, cuerpo: { mensaje: "Gracias." } },
    });
    const u = userEvent.setup();
    montar("/registro");
    await llenar(u);
    const boton = screen.getByRole("button", { name: "Registrarme" });
    expect(boton).toBeDisabled();
    expect(screen.getByText("Complete la verificación de arriba")).toBeInTheDocument();

    const script = document.querySelector<HTMLScriptElement>('script[src^="https://js.hcaptcha.com/"]');
    expect(script).not.toBeNull();
    act(() => {
      script?.dispatchEvent(new Event("load"));
    });
    await waitFor(() => {
      expect(resolver).not.toBe(undefined);
    });
    act(() => {
      resolver("token-1");
    });
    await u.click(screen.getByRole("button", { name: "Registrarme" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo verificar que es una persona");
    expect(reset).toHaveBeenCalledWith("w1");
    expect(screen.getByRole("button", { name: "Registrarme" })).toBeDisabled(); // el token ya se gastó

    act(() => {
      resolver("token-2");
    });
    await u.click(screen.getByRole("button", { name: "Registrarme" }));
    expect(await screen.findByText("Gracias.")).toBeInTheDocument();
    expect(api.de("POST", "/publico/registro").map((p) => (p.cuerpo as { captcha: string }).captcha)).toEqual([
      "token-1",
      "token-2",
    ]);
  });
});
