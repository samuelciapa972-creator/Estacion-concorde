import { Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, type FiltroVentas } from "../../api/cliente";
import type { BusquedaVentas, Venta } from "../../api/tipos";
import { CELDA, NUM, Paginador } from "../../componentes/admin";
import { Boton, Campo, Cargando, ESTILO_ENTRADA, ErrorDeApi } from "../../componentes/ui";
import { fechaHora, galones, hoyIso, pesos } from "../../lib/formato";

const LIMITE = 100;
const LADOS = ["A", "B"] as const;
const ESTADOS: Venta["estado_facturacion"][] = ["PENDIENTE", "EN_PROCESO", "FACTURADO", "INCIERTO", "NO_FACTURABLE"];
const FORMAS_PAGO: Venta["forma_pago"][] = ["CONTADO", "CREDITO", "OTRO"];

/** Mañana a medianoche (hora de la estación) como "AAAA-MM-DDT00:00", para que "hoy" quede completo. */
function mananaIso(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return `${hoyIso(d)}T00:00`;
}

/** Formulario: todo como texto, tal cual lo digitó la persona. */
interface Formulario {
  desde: string;
  hasta: string;
  surtidor_id: string;
  lado: string;
  producto: string;
  forma_pago: string;
  estado_facturacion: string;
  placa: string;
  cliente: string;
  id_externo: string;
  factura: string;
  vendedor_id: string;
}

const VACIO: Omit<Formulario, "desde" | "hasta"> = {
  surtidor_id: "",
  lado: "",
  producto: "",
  forma_pago: "",
  estado_facturacion: "",
  placa: "",
  cliente: "",
  id_externo: "",
  factura: "",
  vendedor_id: "",
};

function aFiltro(f: Formulario): FiltroVentas | string {
  if (!f.desde || !f.hasta) return "indique desde y hasta";
  if (f.hasta <= f.desde) return "«Hasta» debe ser posterior a «Desde»";
  if (f.id_externo && !/^\d+$/.test(f.id_externo.trim())) return "el número de venta debe ser un número";
  const texto = (s: string) => s.trim() || null;
  return {
    desde: f.desde,
    hasta: f.hasta,
    surtidor_id: f.surtidor_id ? Number(f.surtidor_id) : null,
    lado: texto(f.lado),
    producto: texto(f.producto),
    forma_pago: texto(f.forma_pago),
    estado_facturacion: texto(f.estado_facturacion),
    placa: texto(f.placa.toUpperCase()),
    cliente: texto(f.cliente),
    id_externo: f.id_externo.trim() ? Number(f.id_externo.trim()) : null,
    factura: texto(f.factura.toUpperCase()),
    vendedor_id: f.vendedor_id ? Number(f.vendedor_id) : null,
  };
}

/** Búsqueda de ventas con los filtros que tiene Nexus (fecha/hora, placa, cliente, #venta, equipo, vendedor). */
export function Ventas() {
  const [form, setForm] = useState<Formulario>(() => ({ desde: `${hoyIso()}T00:00`, hasta: mananaIso(), ...VACIO }));
  const [aplicado, setAplicado] = useState<FiltroVentas | null>(() => aFiltro(form) as FiltroVentas);
  const [problema, setProblema] = useState<string | null>(null);
  const [desde, setDesde] = useState(0);

  const catalogos = useQuery({ queryKey: ["catalogos"], queryFn: api.catalogos, staleTime: 5 * 60_000 });
  const usuarios = useQuery({ queryKey: ["usuarios"], queryFn: api.usuarios, staleTime: 5 * 60_000 });
  const busqueda = useQuery({
    queryKey: ["ventas", aplicado, desde],
    queryFn: () => api.ventas({ ...(aplicado as FiltroVentas), limite: LIMITE, desplazamiento: desde }),
    enabled: aplicado !== null,
  });

  const cambiar = (campo: keyof Formulario) => (e: { target: { value: string } }) =>
    setForm({ ...form, [campo]: e.target.value });

  function buscar(e: FormEvent) {
    e.preventDefault();
    const f = aFiltro(form);
    if (typeof f === "string") {
      setProblema(f);
      return;
    }
    setProblema(null);
    setDesde(0);
    setAplicado(f);
  }

  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Buscar ventas</h1>
      <form className="grid items-end gap-3 rounded-2xl bg-white p-4 md:grid-cols-4" onSubmit={buscar} noValidate>
        <Campo id="v_desde" etiqueta="Desde (fecha y hora)">
          <input id="v_desde" type="datetime-local" className={ESTILO_ENTRADA} value={form.desde} onChange={cambiar("desde")} />
        </Campo>
        <Campo id="v_hasta" etiqueta="Hasta (sin incluir)">
          <input id="v_hasta" type="datetime-local" className={ESTILO_ENTRADA} value={form.hasta} onChange={cambiar("hasta")} />
        </Campo>
        <Campo id="v_surtidor" etiqueta="Equipo (surtidor)">
          <select id="v_surtidor" className={ESTILO_ENTRADA} value={form.surtidor_id} onChange={cambiar("surtidor_id")}>
            <option value="">Todos</option>
            {catalogos.data?.surtidores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.marca}
              </option>
            ))}
          </select>
        </Campo>
        <Campo id="v_lado" etiqueta="Lado">
          <select id="v_lado" className={ESTILO_ENTRADA} value={form.lado} onChange={cambiar("lado")}>
            <option value="">Todos</option>
            {LADOS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </Campo>
        <Campo id="v_producto" etiqueta="Producto">
          <select id="v_producto" className={ESTILO_ENTRADA} value={form.producto} onChange={cambiar("producto")}>
            <option value="">Todos</option>
            {catalogos.data?.productos.map((p) => (
              <option key={p.id} value={p.codigo}>
                {p.nombre}
              </option>
            ))}
          </select>
        </Campo>
        <Campo id="v_pago" etiqueta="Forma de pago">
          <select id="v_pago" className={ESTILO_ENTRADA} value={form.forma_pago} onChange={cambiar("forma_pago")}>
            <option value="">Todas</option>
            {FORMAS_PAGO.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </Campo>
        <Campo id="v_estado" etiqueta="Facturación">
          <select id="v_estado" className={ESTILO_ENTRADA} value={form.estado_facturacion} onChange={cambiar("estado_facturacion")}>
            <option value="">Todas</option>
            {ESTADOS.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Campo>
        <Campo id="v_vendedor" etiqueta="Vendedor">
          <select id="v_vendedor" className={ESTILO_ENTRADA} value={form.vendedor_id} onChange={cambiar("vendedor_id")}>
            <option value="">Todos</option>
            {usuarios.data?.map((u) => (
              <option key={u.id} value={u.id}>
                {u.nombre}
                {u.activo ? "" : " (inactivo)"}
              </option>
            ))}
          </select>
        </Campo>
        <Campo id="v_placa" etiqueta="Placa (o parte)">
          <input id="v_placa" className={`${ESTILO_ENTRADA} uppercase`} maxLength={40} value={form.placa} onChange={cambiar("placa")} />
        </Campo>
        <Campo id="v_cliente" etiqueta="Documento del cliente">
          <input id="v_cliente" inputMode="numeric" className={ESTILO_ENTRADA} maxLength={20} value={form.cliente} onChange={cambiar("cliente")} />
        </Campo>
        <Campo id="v_venta" etiqueta="# venta (surtidor)">
          <input id="v_venta" inputMode="numeric" className={ESTILO_ENTRADA} value={form.id_externo} onChange={cambiar("id_externo")} />
        </Campo>
        <Campo id="v_factura" etiqueta="# factura">
          <input id="v_factura" className={`${ESTILO_ENTRADA} uppercase`} maxLength={40} value={form.factura} onChange={cambiar("factura")} />
        </Campo>
        <div className="flex gap-2 md:col-span-4">
          <Boton type="submit" icono={Search}>Buscar</Boton>
          <Boton variante="secundario" icono={X} onClick={() => setForm({ ...form, ...VACIO })}>
            Limpiar filtros
          </Boton>
        </div>
        {problema && (
          <p role="alert" className="font-semibold text-red-700 md:col-span-4">
            {problema}
          </p>
        )}
      </form>
      <ErrorDeApi error={busqueda.error} />
      {busqueda.isPending && aplicado && <Cargando />}
      {busqueda.data && (
        <Resultados
          datos={busqueda.data}
          nombreSurtidor={(id) => catalogos.data?.surtidores.find((s) => s.id === id)?.marca ?? `#${id}`}
          desde={desde}
          onPagina={setDesde}
        />
      )}
    </section>
  );
}

function Resultados({
  datos,
  nombreSurtidor,
  desde,
  onPagina,
}: {
  datos: BusquedaVentas;
  nombreSurtidor: (id: number) => string;
  desde: number;
  onPagina: (d: number) => void;
}) {
  const { filas, total, por_manguera } = datos;
  return (
    <>
      <div className="grid gap-3 md:grid-cols-3" aria-label="Totales">
        <Total titulo="Ventas" valor={String(total.ventas)} />
        <Total titulo="Galones" valor={galones(total.galones)} />
        <Total titulo="Valor" valor={pesos(total.valor)} />
      </div>
      {por_manguera.length > 0 && (
        <div className="overflow-x-auto rounded-2xl bg-white">
          <table className="w-full text-base">
            <caption className="p-3 text-left text-lg font-semibold">Totales por manguera</caption>
            <thead className="bg-stone-50 text-left">
              <tr>
                <th className={CELDA}>Equipo</th>
                <th className={CELDA}>Lado</th>
                <th className={CELDA}>Producto</th>
                <th className={NUM}>Ventas</th>
                <th className={NUM}>Galones</th>
                <th className={NUM}>Valor</th>
              </tr>
            </thead>
            <tbody className="animar-lista">
              {por_manguera.map((m) => (
                <tr key={`${m.surtidor_id}-${m.lado}-${m.producto}`}>
                  <td className={CELDA}>{nombreSurtidor(m.surtidor_id)}</td>
                  <td className={CELDA}>{m.lado}</td>
                  <td className={CELDA}>{m.producto}</td>
                  <td className={NUM}>{m.ventas}</td>
                  <td className={NUM}>{galones(m.galones)}</td>
                  <td className={NUM}>{pesos(m.valor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {filas.length === 0 ? (
        <p className="rounded-xl bg-white p-6 text-lg text-stone-600">Sin ventas con esos filtros.</p>
      ) : (
        <div className="overflow-x-auto rounded-2xl bg-white">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-left">
              <tr>
                <th className={CELDA}>Fecha</th>
                <th className={CELDA}>Equipo</th>
                <th className={CELDA}>Venta</th>
                <th className={CELDA}>Comprobante</th>
                <th className={CELDA}>Vendedor</th>
                <th className={CELDA}>Placa</th>
                <th className={CELDA}>Producto</th>
                <th className={NUM}>Galones</th>
                <th className={NUM}>Valor</th>
                <th className={CELDA}>Pago</th>
                <th className={CELDA}>Cliente</th>
                <th className={CELDA}>Facturación</th>
              </tr>
            </thead>
            <tbody className="animar-lista">
              {filas.map((v) => (
                <tr key={`${v.origen_tipo}-${v.origen_id}`}>
                  <td className={`${CELDA} whitespace-nowrap`}>{fechaHora(v.fecha)}</td>
                  <td className={CELDA}>
                    {v.surtidor} {v.lado}
                  </td>
                  <td className={CELDA}>{v.origen_tipo === "venta_manual" ? `Manual ${v.origen_id}` : (v.id_externo ?? "—")}</td>
                  <td className={CELDA}>{v.factura_numero ?? "—"}</td>
                  <td className={CELDA}>{v.vendedor ?? "—"}</td>
                  <td className={CELDA}>{v.placa ?? "—"}</td>
                  <td className={CELDA}>{v.producto}</td>
                  <td className={NUM}>{galones(v.volumen)}</td>
                  <td className={NUM}>{pesos(v.valor)}</td>
                  <td className={CELDA}>{v.forma_pago}</td>
                  <td className={CELDA}>
                    {v.cliente_nombre ? `${v.cliente_nombre} (${v.cliente_tipo ?? ""} ${v.cliente_documento ?? ""})` : "—"}
                  </td>
                  <td className={CELDA}>{v.factura_estado === "RECHAZADO" ? "RECHAZADO" : v.estado_facturacion}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Paginador desde={desde} limite={LIMITE} total={total.ventas} onCambio={onPagina} />
    </>
  );
}

function Total({ titulo, valor }: { titulo: string; valor: string }) {
  return (
    <div className="rounded-2xl bg-white p-4">
      <p className="text-sm text-stone-600">{titulo}</p>
      <p className="text-2xl font-bold tabular-nums">{valor}</p>
    </div>
  );
}
