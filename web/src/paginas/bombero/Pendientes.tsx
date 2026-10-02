import { Plus } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/cliente";
import type { Pendiente } from "../../api/tipos";
import { Boton, Cargando, ErrorDeApi } from "../../componentes/ui";
import { configuracion } from "../../config";
import { fechaHora, galones, pesos } from "../../lib/formato";

export function Pendientes() {
  const navegar = useNavigate();
  const [surtidor, setSurtidor] = useState<number | null>(null);
  const [lado, setLado] = useState<string | null>(null);
  const catalogos = useQuery({ queryKey: ["catalogos"], queryFn: api.catalogos, staleTime: 5 * 60_000 });
  const pendientes = useQuery({
    queryKey: ["pendientes", surtidor, lado],
    queryFn: () => api.pendientes({ surtidor_id: surtidor, lado }),
    refetchInterval: configuracion.refrescoPendientesMs,
  });
  const nombreSurtidor = (id: number) => catalogos.data?.surtidores.find((s) => s.id === id)?.marca ?? `#${id}`;
  const filtro = (activo: boolean): "primario" | "secundario" => (activo ? "primario" : "secundario");

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-2xl font-bold">Ventas por facturar</h1>
        <Link
          to="/pista/venta-manual"
          className="flex min-h-12 items-center gap-2 rounded-xl bg-dorado px-5 text-lg font-semibold text-negro active:bg-dorado-hover"
        >
          <Plus className="size-5" aria-hidden="true" />
          Nueva venta manual
        </Link>
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Surtidor">
        <Boton variante={filtro(surtidor === null)} onClick={() => setSurtidor(null)} aria-pressed={surtidor === null}>
          Todos
        </Boton>
        {catalogos.data?.surtidores.map((s) => (
          <Boton key={s.id} variante={filtro(surtidor === s.id)} onClick={() => setSurtidor(s.id)} aria-pressed={surtidor === s.id}>
            {s.marca}
          </Boton>
        ))}
        <span className="mx-2 self-center text-stone-500">Lado:</span>
        {[null, "A", "B"].map((l) => (
          <Boton key={l ?? "todos"} variante={filtro(lado === l)} onClick={() => setLado(l)} aria-pressed={lado === l}>
            {l ?? "Ambos"}
          </Boton>
        ))}
      </div>

      <ErrorDeApi error={pendientes.error} />
      {pendientes.isPending && <Cargando />}
      {pendientes.data?.length === 0 && (
        <p className="rounded-xl bg-white p-6 text-lg text-stone-600">No hay ventas de contado pendientes.</p>
      )}
      <ul className="animar-lista grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {pendientes.data?.map((p) => (
          <li key={`${p.origen_tipo}-${p.origen_id}`}>
            <TarjetaPendiente
              p={p}
              surtidor={nombreSurtidor(p.surtidor_id)}
              onElegir={() => void navegar("/pista/facturar", { state: { pendiente: p } })}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function TarjetaPendiente({ p, surtidor, onElegir }: { p: Pendiente; surtidor: string; onElegir: () => void }) {
  return (
    <button
      type="button"
      onClick={onElegir}
      className="flex w-full flex-col gap-1 rounded-2xl border-2 border-stone-200 bg-white p-4 text-left active:border-dorado"
      aria-label={`Facturar ${p.producto} lado ${p.lado}, ${pesos(p.valor)}`}
    >
      <span className="flex justify-between text-base text-stone-600">
        <span>
          {surtidor} · Lado {p.lado}
        </span>
        <span>{fechaHora(p.fecha)}</span>
      </span>
      <span className="text-3xl font-bold">{pesos(p.valor)}</span>
      <span className="text-lg">
        {galones(p.volumen)} gal · {p.producto}
      </span>
      <span className="flex gap-2 text-sm">
        {p.origen_tipo === "venta_manual" && <span className="rounded bg-amber-200 px-2">Manual</span>}
        {p.placa && <span className="rounded bg-stone-200 px-2">Placa {p.placa}</span>}
      </span>
    </button>
  );
}
