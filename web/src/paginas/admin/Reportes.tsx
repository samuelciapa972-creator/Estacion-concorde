import { FileDown, FileText, Landmark } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type FormatoContador, type RangoReporte } from "../../api/cliente";
import type { FilaTurno, FilaVentas } from "../../api/tipos";
import { Aviso, Boton, Campo, Cargando, ESTILO_ENTRADA, ErrorDeApi } from "../../componentes/ui";
import { fecha, fechaHora, galones, hoyIso, pesos, sumar } from "../../lib/formato";

type Tipo = "diario" | "mensual" | "turno";

function primeroDelMes(): string {
  return hoyIso().slice(0, 8) + "01";
}

/** Descarga un archivo que llegó como Blob (la API exige el token: no sirve un enlace directo). */
function guardar(blob: Blob, nombre: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  a.click();
  URL.revokeObjectURL(url);
}

export function Reportes() {
  const [tipo, setTipo] = useState<Tipo>("diario");
  const [rango, setRango] = useState<RangoReporte>({ desde: primeroDelMes(), hasta: hoyIso(), surtidor_id: null });
  const catalogos = useQuery({ queryKey: ["catalogos"], queryFn: api.catalogos, staleTime: 5 * 60_000 });
  const reporte = useQuery({
    queryKey: ["reporte", tipo, rango],
    queryFn: (): Promise<FilaVentas[] | FilaTurno[]> =>
      tipo === "diario" ? api.reporteDiario(rango) : tipo === "mensual" ? api.reporteMensual(rango) : api.reporteTurno(rango),
  });
  const excel = useMutation({
    mutationFn: async () => {
      if (!rango.surtidor_id) throw new Error("elija un surtidor para exportar");
      const blob = await api.exportarExcel({ ...rango, surtidor_id: rango.surtidor_id });
      guardar(blob, `ventas_surtidor${rango.surtidor_id}_${rango.desde}_${rango.hasta}.xlsx`);
    },
  });
  const pdf = useMutation({
    mutationFn: async () => guardar(await api.reportePdf(tipo, rango), `ventas_${tipo}_${rango.desde}_${rango.hasta}.pdf`),
  });
  const [formato, setFormato] = useState<FormatoContador>("xlsx");
  const contador = useMutation({
    mutationFn: async () => guardar(await api.contador(rango, formato), `contador_${rango.desde}_${rango.hasta}.${formato}`),
  });
  const duplicados = useQuery({
    queryKey: ["posibles-duplicados", rango.desde, rango.hasta],
    queryFn: () => api.posiblesDuplicados(rango),
  });
  const nombreSurtidor = (id: number) => catalogos.data?.surtidores.find((s) => s.id === id)?.marca ?? `#${id}`;
  const filas = reporte.data ?? [];

  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Reportes de ventas</h1>
      <div className="flex flex-wrap gap-2" role="tablist">
        {(["diario", "mensual", "turno"] as const).map((t) => (
          <Boton key={t} role="tab" aria-selected={tipo === t} variante={tipo === t ? "primario" : "secundario"} onClick={() => setTipo(t)}>
            {{ diario: "Diario", mensual: "Mensual", turno: "Por turno" }[t]}
          </Boton>
        ))}
      </div>
      <div className="grid items-end gap-3 rounded-2xl bg-white p-4 md:grid-cols-4">
        <Campo id="desde" etiqueta="Desde">
          <input id="desde" type="date" className={ESTILO_ENTRADA} value={rango.desde} onChange={(e) => setRango({ ...rango, desde: e.target.value })} />
        </Campo>
        <Campo id="hasta" etiqueta="Hasta">
          <input id="hasta" type="date" className={ESTILO_ENTRADA} value={rango.hasta} onChange={(e) => setRango({ ...rango, hasta: e.target.value })} />
        </Campo>
        <Campo id="surtidor" etiqueta="Surtidor">
          <select
            id="surtidor"
            className={ESTILO_ENTRADA}
            value={rango.surtidor_id ?? ""}
            onChange={(e) => setRango({ ...rango, surtidor_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Todos</option>
            {catalogos.data?.surtidores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.marca}
              </option>
            ))}
          </select>
        </Campo>
        <div className="flex flex-wrap gap-2">
          <Boton variante="secundario" icono={FileText} disabled={pdf.isPending} onClick={() => pdf.mutate()}>
            {pdf.isPending ? "Generando…" : "Descargar PDF"}
          </Boton>
          <Boton
            variante="secundario"
            icono={FileDown}
            disabled={!rango.surtidor_id || excel.isPending}
            title={rango.surtidor_id ? undefined : "Elija un surtidor: el Excel analiza un surtidor a la vez"}
            onClick={() => excel.mutate()}
          >
            {excel.isPending ? "Generando…" : "Excel del surtidor"}
          </Boton>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3 rounded-2xl bg-white p-4">
        <Campo id="formato_contador" etiqueta="Formato para el contador">
          <select
            id="formato_contador"
            className={ESTILO_ENTRADA}
            value={formato}
            onChange={(e) => setFormato(e.target.value as FormatoContador)}
          >
            <option value="xlsx">Excel</option>
            <option value="csv">CSV (;)</option>
            <option value="pdf">PDF</option>
          </select>
        </Campo>
        <Boton icono={Landmark} disabled={contador.isPending} onClick={() => contador.mutate()}>
          {contador.isPending ? "Generando…" : "Exportar para el contador"}
        </Boton>
        <p className="basis-full text-sm text-stone-600">
          Facturas del rango (con datos de clientes) y el resumen diario. Formato provisional hasta que el contador
          defina el suyo; cada descarga queda registrada.
        </p>
      </div>
      <p className="text-sm text-stone-600">
        {tipo === "turno"
          ? "Por turno solo cuentan los despachos del surtidor: las ventas manuales no tienen turno."
          : "Suma los despachos del surtidor y las ventas manuales sin enlace (columna Origen); una manual enlazada a su despacho no se cuenta dos veces. Incluye el simulador si no se filtra."}
      </p>
      {duplicados.data && duplicados.data.length > 0 && (
        <Aviso
          tipo="alerta"
          titulo={`${duplicados.data.length} venta${duplicados.data.length === 1 ? "" : "s"} manual${duplicados.data.length === 1 ? "" : "es"} podría${duplicados.data.length === 1 ? "" : "n"} estar contada${duplicados.data.length === 1 ? "" : "s"} dos veces`}
        >
          <p>Coinciden con un despacho del surtidor (mismo lado, producto, galones y valor, a menos de 2 horas):</p>
          <ul className="mt-1 list-disc pl-6">
            {duplicados.data.map((x) => (
              <li key={`${x.venta_manual_id}-${x.despacho_id}`}>
                Venta manual {x.venta_manual_id} ({fechaHora(x.registrada_en)}) y despacho {x.despacho_id} (
                {fechaHora(x.inicio_despacho)}): lado {x.lado}, {x.producto}, {galones(x.galones)} gal, {pesos(x.valor)}
              </li>
            ))}
          </ul>
        </Aviso>
      )}
      <ErrorDeApi error={reporte.error ?? excel.error ?? pdf.error ?? contador.error ?? duplicados.error} />
      {reporte.isPending ? (
        <Cargando />
      ) : filas.length === 0 ? (
        <p className="rounded-xl bg-white p-6 text-lg text-stone-600">Sin ventas en ese rango.</p>
      ) : tipo === "turno" ? (
        <TablaTurnos filas={filas as FilaTurno[]} nombreSurtidor={nombreSurtidor} />
      ) : (
        <TablaVentas filas={filas as FilaVentas[]} mensual={tipo === "mensual"} nombreSurtidor={nombreSurtidor} />
      )}
    </section>
  );
}

const CELDA = "border-b border-stone-200 px-3 py-2";
const NUM = `${CELDA} text-right tabular-nums`;

function TablaVentas({
  filas,
  mensual,
  nombreSurtidor,
}: {
  filas: FilaVentas[];
  mensual: boolean;
  nombreSurtidor: (id: number) => string;
}) {
  return (
    <div className="overflow-x-auto rounded-2xl bg-white">
      <table className="w-full text-base">
        <thead className="bg-stone-50 text-left">
          <tr>
            <th className={CELDA}>{mensual ? "Mes" : "Día"}</th>
            <th className={CELDA}>Surtidor</th>
            <th className={CELDA}>Producto</th>
            <th className={CELDA}>Pago</th>
            <th className={CELDA}>Origen</th>
            <th className={NUM}>Ventas</th>
            <th className={NUM}>Galones</th>
            <th className={NUM}>Valor</th>
          </tr>
        </thead>
        <tbody className="animar-lista">
          {filas.map((f) => (
            <tr key={`${f.periodo}-${f.surtidor_id}-${f.producto}-${f.forma_pago}-${f.origen}`}>
              <td className={CELDA}>{mensual ? f.periodo.slice(0, 7) : fecha(f.periodo)}</td>
              <td className={CELDA}>{nombreSurtidor(f.surtidor_id)}</td>
              <td className={CELDA}>{f.producto}</td>
              <td className={CELDA}>{f.forma_pago}</td>
              <td className={CELDA}>{f.origen}</td>
              <td className={NUM}>{f.despachos}</td>
              <td className={NUM}>{galones(f.galones)}</td>
              <td className={NUM}>{pesos(f.valor)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot className="font-bold">
          <tr>
            <td className={CELDA} colSpan={5}>
              Total
            </td>
            <td className={NUM}>{filas.reduce((n, f) => n + f.despachos, 0)}</td>
            <td className={NUM}>{galones(sumar(filas.map((f) => f.galones), 3))}</td>
            <td className={NUM}>{pesos(sumar(filas.map((f) => f.valor), 2))}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function TablaTurnos({ filas, nombreSurtidor }: { filas: FilaTurno[]; nombreSurtidor: (id: number) => string }) {
  return (
    <div className="overflow-x-auto rounded-2xl bg-white">
      <table className="w-full text-base">
        <thead className="bg-stone-50 text-left">
          <tr>
            <th className={CELDA}>Cierre</th>
            <th className={CELDA}>Surtidor</th>
            <th className={CELDA}>Lado</th>
            <th className={CELDA}>Estado</th>
            <th className={CELDA}>Desde</th>
            <th className={CELDA}>Hasta</th>
            <th className={NUM}>Despachos</th>
            <th className={NUM}>Galones</th>
            <th className={NUM}>Valor</th>
          </tr>
        </thead>
        <tbody className="animar-lista">
          {filas.map((f) => (
            <tr key={`${f.turno_id}-${f.lado}`}>
              <td className={CELDA}>{f.id_cierre}</td>
              <td className={CELDA}>{nombreSurtidor(f.surtidor_id)}</td>
              <td className={CELDA}>{f.lado}</td>
              <td className={CELDA}>{f.estado}</td>
              <td className={CELDA}>{fechaHora(f.inicio)}</td>
              <td className={CELDA}>{fechaHora(f.fin)}</td>
              <td className={NUM}>{f.despachos}</td>
              <td className={NUM}>{galones(f.galones)}</td>
              <td className={NUM}>{pesos(f.valor)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
