import { Search } from "lucide-react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/cliente";
import { CELDA, EtiquetaActivo, FiltroEstado, ImportarCsv, Paginador, type EstadoLista } from "../../componentes/admin";
import { Boton, Campo, Cargando, ESTILO_ENTRADA, ErrorDeApi } from "../../componentes/ui";

const LIMITE = 50;

/** Clientes: búsqueda, baja lógica (se conserva el historial) e importación CSV. */
export function Clientes() {
  const [texto, setTexto] = useState("");
  const [buscado, setBuscado] = useState("");
  const [estado, setEstado] = useState<EstadoLista>("activos");
  const [desde, setDesde] = useState(0);
  const consultas = useQueryClient();

  const lista = useQuery({
    queryKey: ["clientes", buscado, estado, desde],
    queryFn: () => api.clientes({ texto: buscado, estado, limite: LIMITE, desplazamiento: desde }),
    placeholderData: keepPreviousData,
  });
  const activar = useMutation({
    mutationFn: ({ id, activo }: { id: number; activo: boolean }) => api.activarCliente(id, activo),
    onSuccess: () => consultas.invalidateQueries({ queryKey: ["clientes"] }),
  });

  function buscar(e: FormEvent) {
    e.preventDefault();
    setDesde(0);
    setBuscado(texto.trim());
  }

  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Clientes</h1>
      <form className="flex flex-wrap items-end gap-3 rounded-2xl bg-white p-4" onSubmit={buscar}>
        <div className="min-w-64 flex-1">
          <Campo id="c_texto" etiqueta="Documento (inicio) o nombre">
            <input id="c_texto" className={ESTILO_ENTRADA} maxLength={100} value={texto} onChange={(e) => setTexto(e.target.value)} />
          </Campo>
        </div>
        <Boton type="submit" icono={Search}>Buscar</Boton>
        <FiltroEstado
          valor={estado}
          onCambio={(e) => {
            setEstado(e);
            setDesde(0);
          }}
        />
      </form>
      <ErrorDeApi error={lista.error ?? activar.error} />
      {lista.isPending && <Cargando />}
      {lista.data?.filas.length === 0 && <p className="rounded-xl bg-white p-6 text-lg text-stone-600">Sin clientes.</p>}
      {lista.data && lista.data.filas.length > 0 && (
        <div className="overflow-x-auto rounded-2xl bg-white">
          <table className="w-full text-base">
            <thead className="bg-stone-50 text-left">
              <tr>
                <th className={CELDA}>Documento</th>
                <th className={CELDA}>Nombre o razón social</th>
                <th className={CELDA}>Correo</th>
                <th className={CELDA}>Estado</th>
                <th className={CELDA}>
                  <span className="sr-only">Acciones</span>
                </th>
              </tr>
            </thead>
            <tbody className="animar-lista">
              {lista.data.filas.map((c) => (
                <tr key={c.id}>
                  <td className={`${CELDA} whitespace-nowrap`}>
                    {c.tipo_documento} {c.numero_documento}
                    {c.digito_verificacion !== null && `-${c.digito_verificacion}`}
                  </td>
                  <td className={CELDA}>{c.nombre_mostrar}</td>
                  <td className={CELDA}>{c.email_enmascarado}</td>
                  <td className={CELDA}>
                    <EtiquetaActivo activo={c.activo} />
                  </td>
                  <td className={`${CELDA} flex flex-wrap justify-end gap-2`}>
                    <Link
                      to="/admin/vehiculos"
                      state={{ cliente: { id: c.id, nombre: c.nombre_mostrar } }}
                      className="flex min-h-12 items-center px-3 font-semibold text-dorado-oscuro underline"
                    >
                      Vehículos
                    </Link>
                    <Boton
                      variante={c.activo ? "peligro" : "secundario"}
                      disabled={activar.isPending}
                      aria-label={`${c.activo ? "Desactivar" : "Activar"} ${c.nombre_mostrar}`}
                      onClick={() => activar.mutate({ id: c.id, activo: !c.activo })}
                    >
                      {c.activo ? "Desactivar" : "Activar"}
                    </Boton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {lista.data && <Paginador desde={desde} limite={LIMITE} total={lista.data.total} onCambio={setDesde} />}
      <p className="text-sm text-stone-600">
        Desactivar es una baja lógica: el cliente deja de aparecer en la pista y no se le factura, pero su historial
        se conserva. Los clientes nuevos se registran por QR (/registro) o desde la pista, siempre con autorización de
        datos.
      </p>
      <ImportarCsv que="clientes" />
    </section>
  );
}
