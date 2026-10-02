import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/cliente";
import { Boton, Cargando, ErrorDeApi } from "../../componentes/ui";
import { fechaHora } from "../../lib/formato";

type Estado = "abiertas" | "resueltas" | "todas";
const COLOR: Record<string, string> = {
  ALTA: "bg-red-100 text-red-900",
  AVISO: "bg-amber-100 text-amber-900",
  INFO: "bg-sky-100 text-sky-900",
};

export function Anomalias() {
  const [estado, setEstado] = useState<Estado>("abiertas");
  const [severidad, setSeveridad] = useState<string | null>(null);
  const consulta = useQuery({ queryKey: ["anomalias", estado, severidad], queryFn: () => api.anomalias(estado, severidad) });
  const sel = (activo: boolean): "primario" | "secundario" => (activo ? "primario" : "secundario");

  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Anomalías para revisar</h1>
      <div className="flex flex-wrap gap-2">
        {(["abiertas", "resueltas", "todas"] as const).map((e) => (
          <Boton key={e} variante={sel(estado === e)} aria-pressed={estado === e} onClick={() => setEstado(e)}>
            {e.charAt(0).toUpperCase() + e.slice(1)}
          </Boton>
        ))}
        <span className="mx-2 self-center text-stone-500">Severidad:</span>
        {[null, "ALTA", "AVISO", "INFO"].map((s) => (
          <Boton key={s ?? "todas"} variante={sel(severidad === s)} aria-pressed={severidad === s} onClick={() => setSeveridad(s)}>
            {s ?? "Todas"}
          </Boton>
        ))}
      </div>
      <ErrorDeApi error={consulta.error} />
      {consulta.isPending && <Cargando />}
      {consulta.data?.length === 0 && <p className="rounded-xl bg-white p-6 text-lg text-stone-600">No hay anomalías.</p>}
      <ul className="animar-lista flex flex-col gap-2">
        {consulta.data?.map((a) => (
          <li key={a.id} className="rounded-xl bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded px-2 font-semibold ${COLOR[a.severidad] ?? ""}`}>{a.severidad}</span>
              <span className="font-semibold">{a.tipo}</span>
              {a.despacho_id_externo !== null && <span className="text-stone-600">despacho {a.despacho_id_externo}</span>}
              <span className="ml-auto text-sm text-stone-600">{fechaHora(a.creada_en)}</span>
            </div>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-sm text-stone-700">
              {JSON.stringify(a.detalle, null, 1)}
            </pre>
          </li>
        ))}
      </ul>
    </section>
  );
}
