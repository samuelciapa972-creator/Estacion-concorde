import { ChevronLeft, ShieldCheck, Upload } from "lucide-react";
/** Piezas compartidas de las pantallas del administrador: tablas, paginación, filtro de estado e importación CSV. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/cliente";
import type { Importacion } from "../api/tipos";
import { Aviso, Boton, Campo, ErrorDeApi } from "./ui";

export const CELDA = "border-b border-stone-200 px-3 py-2";
export const NUM = `${CELDA} text-right tabular-nums`;

export type EstadoLista = "activos" | "inactivos" | "todos";

export function FiltroEstado({ valor, onCambio }: { valor: EstadoLista; onCambio: (e: EstadoLista) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {(["activos", "inactivos", "todos"] as const).map((e) => (
        <Boton key={e} variante={valor === e ? "primario" : "secundario"} aria-pressed={valor === e} onClick={() => onCambio(e)}>
          {e.charAt(0).toUpperCase() + e.slice(1)}
        </Boton>
      ))}
    </div>
  );
}

/** "1–50 de 120" con Anterior/Siguiente. `desde` es el desplazamiento (0 = primera página). */
export function Paginador({
  desde,
  limite,
  total,
  onCambio,
}: {
  desde: number;
  limite: number;
  total: number;
  onCambio: (desplazamiento: number) => void;
}) {
  if (total <= limite && desde === 0) return null;
  const hasta = Math.min(desde + limite, total);
  return (
    <nav className="flex items-center gap-3" aria-label="Páginas">
      <Boton variante="secundario" icono={ChevronLeft} disabled={desde === 0} onClick={() => onCambio(Math.max(0, desde - limite))}>
        Anterior
      </Boton>
      <span className="text-stone-700">
        {total === 0 ? 0 : desde + 1}–{hasta} de {total}
      </span>
      <Boton variante="secundario" disabled={hasta >= total} onClick={() => onCambio(desde + limite)}>
        Siguiente
      </Boton>
    </nav>
  );
}

export function EtiquetaActivo({ activo }: { activo: boolean }) {
  return (
    <span className={`rounded px-2 font-semibold ${activo ? "bg-green-100 text-green-900" : "bg-stone-200 text-stone-700"}`}>
      {activo ? "Activo" : "Inactivo"}
    </span>
  );
}

const FORMATO: Record<"clientes" | "vehiculos", string> = {
  clientes: "tipo_documento;numero_documento;nombres;apellidos;email;autoriza_tratamiento;fecha_autorizacion",
  vehiculos: "placa;tipo_documento;numero_documento;descripcion",
};

/**
 * Importación CSV en dos pasos: primero se valida en seco (no escribe nada) y solo después se habilita
 * "Importar", con el mismo archivo. Cambiar de archivo obliga a validar de nuevo.
 */
export function ImportarCsv({ que }: { que: "clientes" | "vehiculos" }) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [validado, setValidado] = useState<Importacion | null>(null);
  const consultas = useQueryClient();
  const validar = useMutation({
    mutationFn: (f: File) => api.importar(que, f, true),
    onSuccess: setValidado,
  });
  const importar = useMutation({
    mutationFn: (f: File) => api.importar(que, f, false),
    onSuccess: () => {
      setValidado(null);
      void consultas.invalidateQueries({ queryKey: [que] });
    },
  });
  const resultado = importar.data ?? validado;
  const id = `csv_${que}`;

  return (
    <details className="rounded-2xl bg-white p-4">
      <summary className="min-h-12 cursor-pointer content-center text-lg font-semibold">Importar desde CSV</summary>
      <div className="mt-3 flex flex-col gap-3">
        <p className="text-sm text-stone-700">
          Formato provisional (separador <code>;</code> o <code>,</code>; UTF-8 o Windows-1252):{" "}
          <code className="break-all">{FORMATO[que]}</code>.
          {que === "clientes"
            ? " Un cliente sin autorización de tratamiento de datos (SI y fecha) no se importa."
            : " El dueño (cliente) debe existir antes."}{" "}
          Lo que ya existe no se modifica.
        </p>
        <Campo id={id} etiqueta="Archivo">
          <input
            id={id}
            type="file"
            accept=".csv,text/csv"
            className="text-lg"
            onChange={(e) => {
              setArchivo(e.target.files?.[0] ?? null);
              setValidado(null);
              validar.reset();
              importar.reset();
            }}
          />
        </Campo>
        <div className="flex flex-wrap gap-2">
          <Boton variante="secundario" icono={ShieldCheck} disabled={!archivo || validar.isPending} onClick={() => archivo && validar.mutate(archivo)}>
            {validar.isPending ? "Validando…" : "Validar (no guarda)"}
          </Boton>
          <Boton
            icono={Upload}
            disabled={!archivo || !validado || validado.nuevas === 0 || importar.isPending}
            onClick={() => archivo && importar.mutate(archivo)}
          >
            {importar.isPending ? "Importando…" : validado ? `Importar ${validado.nuevas} nuevos` : "Importar"}
          </Boton>
        </div>
        <ErrorDeApi error={validar.error ?? importar.error} />
        {resultado && <ResultadoImportacion r={resultado} />}
      </div>
    </details>
  );
}

function ResultadoImportacion({ r }: { r: Importacion }) {
  return (
    <Aviso
      tipo={r.rechazadas.length > 0 ? "alerta" : r.en_seco ? "info" : "exito"}
      titulo={r.en_seco ? "Validación (no se guardó nada)" : "Importación terminada"}
    >
      <p>
        {r.filas} filas: {r.nuevas} {r.en_seco ? "se crearían" : "creadas"}, {r.existentes} ya existían,{" "}
        {r.rechazadas.length} rechazadas.
      </p>
      {r.rechazadas.length > 0 && (
        <ul className="mt-2 max-h-64 list-disc overflow-y-auto pl-6 text-sm">
          {r.rechazadas.map((f) => (
            <li key={f.fila}>
              Fila {f.fila}: {f.problemas.join("; ")}
            </li>
          ))}
        </ul>
      )}
    </Aviso>
  );
}
