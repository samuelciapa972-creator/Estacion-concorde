import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { BusquedaVentas, Importacion, Vehiculo, Venta } from "../../api/tipos";
import { EMPRESA, PERSONA, apiFalsa, conSesion, montar } from "../../test/apiFalsa";

const CATALOGOS = {
  surtidores: [{ id: 1, marca: "SIMULADOR", codigo: "SIM", descripcion: null }],
  productos: [{ id: 1, codigo: "DIESEL", nombre: "Diésel" }],
  medios_pago: ["EFECTIVO", "TARJETA"],
  tipos_documento: ["CC", "NIT"],
};

const VENTA: Venta = {
  origen_tipo: "despacho",
  origen_id: 7,
  id_externo: 36001,
  surtidor_id: 1,
  surtidor: "SIMULADOR",
  lado: "A",
  producto: "DIESEL",
  volumen: "1.861",
  valor: "29757",
  forma_pago: "CONTADO",
  fecha: "2026-09-28T10:15:00",
  placa: "ABC123",
  vendedor: "Bombero Uno",
  vendedor_id: 1,
  cliente_tipo: "NIT",
  cliente_documento: EMPRESA.numero_documento,
  cliente_nombre: EMPRESA.nombre_mostrar,
  estado_facturacion: "FACTURADO",
  factura_numero: "PRUE-1",
  factura_estado: "FACTURADO",
};

const BUSQUEDA: BusquedaVentas = {
  filas: [VENTA, { ...VENTA, origen_tipo: "venta_manual", origen_id: 5, id_externo: null, factura_numero: null, factura_estado: null, estado_facturacion: "PENDIENTE", cliente_tipo: null, cliente_documento: null, cliente_nombre: null }],
  total: { ventas: 2, galones: "3.722", valor: "59514" },
  por_manguera: [{ surtidor_id: 1, lado: "A", producto: "DIESEL", ventas: 2, galones: "3.722", valor: "59514" }],
};

function parametros(ruta: string | undefined): URLSearchParams {
  return new URLSearchParams((ruta ?? "").split("?")[1] ?? "");
}

describe("administración", () => {
  it("un bombero no entra a las pantallas de administración", async () => {
    conSesion("BOMBERO");
    apiFalsa({});
    montar("/admin/ventas");
    expect(await screen.findByText("Esta sección es solo para administradores.")).toBeInTheDocument();
  });

  it("ventas: muestra filas, totales y totales por manguera con formato es-CO; la manual se distingue", async () => {
    conSesion("ADMIN");
    const api = apiFalsa({
      "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }),
      "GET /usuarios": () => ({ estado: 200, cuerpo: [] }),
      "GET /ventas": () => ({ estado: 200, cuerpo: BUSQUEDA }),
    });
    montar("/admin/ventas");
    const totales = await screen.findByLabelText("Totales");
    expect(totales).toHaveTextContent("3,722");
    expect(totales).toHaveTextContent("$ 59.514");
    expect(screen.getByText("Totales por manguera")).toBeInTheDocument();
    expect(screen.getByText("36001")).toBeInTheDocument();
    expect(screen.getByText("Manual 5")).toBeInTheDocument();
    expect(screen.getByText(`${EMPRESA.nombre_mostrar} (NIT ${EMPRESA.numero_documento})`)).toBeInTheDocument();
    const p = parametros(api.de("GET", "/ventas")[0]?.ruta);
    expect(p.get("desde")).toMatch(/^\d{4}-\d{2}-\d{2}T00:00$/);
    expect(p.get("limite")).toBe("100");
  });

  it("ventas: envía los filtros normalizados y vuelve a la primera página", async () => {
    conSesion("ADMIN");
    const api = apiFalsa({
      "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }),
      "GET /usuarios": () => ({ estado: 200, cuerpo: [{ id: 3, usuario: "b3", nombre: "Bombero Tres", rol: "BOMBERO", activo: true }] }),
      "GET /ventas": () => ({ estado: 200, cuerpo: BUSQUEDA }),
    });
    const u = userEvent.setup();
    montar("/admin/ventas");
    await screen.findByLabelText("Totales");
    await u.type(screen.getByLabelText("Placa (o parte)"), "abc");
    await u.type(screen.getByLabelText("# venta (surtidor)"), "36001");
    await u.selectOptions(screen.getByLabelText("Vendedor"), "3");
    await u.selectOptions(screen.getByLabelText("Facturación"), "PENDIENTE");
    await u.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => expect(api.de("GET", "/ventas")).toHaveLength(2));
    const p = parametros(api.de("GET", "/ventas")[1]?.ruta);
    expect(p.get("placa")).toBe("ABC");
    expect(p.get("id_externo")).toBe("36001");
    expect(p.get("vendedor_id")).toBe("3");
    expect(p.get("estado_facturacion")).toBe("PENDIENTE");
    expect(p.get("desplazamiento")).toBe("0");
    expect(p.has("cliente")).toBe(false);
  });

  it("ventas: un rango al revés no consulta la API", async () => {
    conSesion("ADMIN");
    const api = apiFalsa({
      "GET /catalogos": () => ({ estado: 200, cuerpo: CATALOGOS }),
      "GET /usuarios": () => ({ estado: 200, cuerpo: [] }),
      "GET /ventas": () => ({ estado: 200, cuerpo: BUSQUEDA }),
    });
    const u = userEvent.setup();
    montar("/admin/ventas");
    await screen.findByLabelText("Totales");
    const hasta = screen.getByLabelText("Hasta (sin incluir)");
    await u.clear(hasta);
    await u.type(hasta, "2020-01-01T00:00");
    await u.click(screen.getByRole("button", { name: "Buscar" }));
    expect(await screen.findByText("«Hasta» debe ser posterior a «Desde»")).toBeInTheDocument();
    expect(api.de("GET", "/ventas")).toHaveLength(1);
  });

  it("clientes: lista con DV del NIT y la baja lógica llama a la API y recarga", async () => {
    conSesion("ADMIN");
    let activo = true;
    const api = apiFalsa({
      "GET /clientes": () => ({ estado: 200, cuerpo: { filas: [PERSONA, { ...EMPRESA, activo }], total: 2 } }),
      "POST /clientes/12/desactivar": () => {
        activo = false;
        return { estado: 200, cuerpo: { ...EMPRESA, activo } };
      },
    });
    const u = userEvent.setup();
    montar("/admin/clientes");
    expect(await screen.findByText(`NIT ${EMPRESA.numero_documento}-8`)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: `Desactivar ${EMPRESA.nombre_mostrar}` }));
    expect(await screen.findByRole("button", { name: `Activar ${EMPRESA.nombre_mostrar}` })).toBeInTheDocument();
    expect(api.de("POST", "/clientes/12/desactivar")).toHaveLength(1);
    expect(api.de("GET", "/clientes").length).toBeGreaterThanOrEqual(2);
  });

  it("importación: primero valida en seco y solo entonces permite importar", async () => {
    conSesion("ADMIN");
    const seco: Importacion = { en_seco: true, filas: 3, nuevas: 2, existentes: 0, rechazadas: [{ fila: 4, problemas: ["falta la autorización de datos"] }] };
    const api = apiFalsa({
      "GET /clientes": () => ({ estado: 200, cuerpo: { filas: [], total: 0 } }),
      "POST /clientes/importar": (p) =>
        p.ruta.includes("en_seco=true")
          ? { estado: 200, cuerpo: seco }
          : { estado: 200, cuerpo: { ...seco, en_seco: false } },
    });
    const u = userEvent.setup();
    montar("/admin/clientes");
    await u.click(await screen.findByText("Importar desde CSV"));
    const importar = screen.getByRole("button", { name: "Importar" });
    expect(importar).toBeDisabled();
    const csv = new File(["tipo_documento;numero_documento\n"], "clientes.csv", { type: "text/csv" });
    await u.upload(screen.getByLabelText("Archivo"), csv);
    await u.click(screen.getByRole("button", { name: "Validar (no guarda)" }));
    const aviso = await screen.findByText("Validación (no se guardó nada)");
    expect(aviso.parentElement).toHaveTextContent("Fila 4: falta la autorización de datos");
    await u.click(screen.getByRole("button", { name: "Importar 2 nuevos" }));
    expect(await screen.findByText("Importación terminada")).toBeInTheDocument();
    const envios = api.de("POST", "/clientes/importar").map((p) => parametros(p.ruta).get("en_seco"));
    expect(envios).toEqual(["true", "false"]);
  });

  it("vehículos: busca al dueño por documento y registra con su id", async () => {
    conSesion("ADMIN");
    const creado: Vehiculo = { id: 1, identificador: "XYZ987", tipo: "PLACA", descripcion: null, cliente_id: PERSONA.id, cliente_nombre: PERSONA.nombre_mostrar, activo: true };
    const api = apiFalsa({
      "GET /vehiculos": () => ({ estado: 200, cuerpo: { filas: [], total: 0 } }),
      "GET /clientes/buscar": () => ({ estado: 200, cuerpo: PERSONA }),
      "POST /vehiculos": () => ({ estado: 201, cuerpo: creado }),
    });
    const u = userEvent.setup();
    montar("/admin/vehiculos");
    await u.click(await screen.findByRole("button", { name: "Registrar" }));
    expect(await screen.findByText("primero busque al dueño por su documento")).toBeInTheDocument();
    await u.type(screen.getByLabelText("Número de documento"), PERSONA.numero_documento);
    await u.click(screen.getByRole("button", { name: "Buscar dueño" }));
    expect(await screen.findByText(PERSONA.nombre_mostrar)).toBeInTheDocument();
    await u.type(screen.getByLabelText("Placa o equipo"), "xyz987");
    await u.click(screen.getByRole("button", { name: "Registrar" }));
    expect(await screen.findByText("Vehículo XYZ987 registrado")).toBeInTheDocument();
    expect(api.de("POST", "/vehiculos")[0]?.cuerpo).toEqual({ cliente_id: PERSONA.id, placa: "XYZ987", descripcion: null });
  });

  it("vehículos: desde la lista de clientes filtra por ese dueño", async () => {
    conSesion("ADMIN");
    const api = apiFalsa({
      "GET /clientes": () => ({ estado: 200, cuerpo: { filas: [PERSONA], total: 1 } }),
      "GET /vehiculos": () => ({ estado: 200, cuerpo: { filas: [], total: 0 } }),
    });
    const u = userEvent.setup();
    montar("/admin/clientes");
    const fila = (await screen.findByText(PERSONA.nombre_mostrar)).closest("tr");
    await u.click(within(fila as HTMLElement).getByRole("link", { name: "Vehículos" }));
    expect(await screen.findByText(`Vehículos de ${PERSONA.nombre_mostrar}`)).toBeInTheDocument();
    expect(parametros(api.de("GET", "/vehiculos")[0]?.ruta).get("cliente_id")).toBe(String(PERSONA.id));
    expect(screen.queryByRole("button", { name: "Buscar dueño" })).not.toBeInTheDocument();
  });
});
