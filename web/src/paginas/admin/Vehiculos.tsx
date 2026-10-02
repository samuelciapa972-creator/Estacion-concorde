import { Plus, Search } from "lucide-react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../../api/cliente";
import type { Cliente } from "../../api/tipos";
import { CELDA, EtiquetaActivo, FiltroEstado, ImportarCsv, Paginador, type EstadoLista } from "../../componentes/admin";
import { Aviso, Boton, Campo, Cargando, ESTILO_ENTRADA, ErrorDeApi } from "../../componentes/ui";
import { NOMBRE_TIPO, TIPOS_DOCUMENTO, problemaNumero, type TipoDocumento } from "../../lib/documentos";

const LIMITE = 50;

/** Dueño elegido desde la lista de clientes (viaja en el estado de la navegación, no en la URL). */
interface Dueno {
  id: number;
  nombre: string;
}

/** Vehículos y equipos de los clientes: búsqueda, alta, baja lógica e importación CSV. */
export function Vehiculos() {
  const ubicacion = useLocation();
  const navegar = useNavigate();
  const dueno = (ubicacion.state as { cliente?: Dueno } | null)?.cliente ?? null;
  const [texto, setTexto] = useState("");
  const [buscado, setBuscado] = useState("");
  const [estado, setEstado] = useState<EstadoLista>("activos");
  const [desde, setDesde] = useState(0);
  const consultas = useQueryClient();

  const lista = useQuery({
    queryKey: ["vehiculos", buscado, estado, dueno?.id ?? null, desde],
    queryFn: () =>
      api.vehiculos({ texto: buscado, estado, cliente_id: dueno?.id ?? null, limite: LIMITE, desplazamiento: desde }),
    placeholderData: keepPreviousData,
  });
  const activar = useMutation({
    mutationFn: ({ id, activo }: { id: number; activo: boolean }) => api.activarVehiculo(id, activo),
    onSuccess: () => consultas.invalidateQueries({ queryKey: ["vehiculos"] }),
  });

  function buscar(e: FormEvent) {
    e.preventDefault();
    setDesde(0);
    setBuscado(texto.trim());
  }

  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Vehículos</h1>
      {dueno && (
        <Aviso tipo="info" titulo={`Vehículos de ${dueno.nombre}`}>
          <Boton variante="fantasma" onClick={() => void navegar("/admin/vehiculos", { replace: true, state: null })}>
            Ver todos
          </Boton>
        </Aviso>
      )}
      <form className="flex flex-wrap items-end gap-3 rounded-2xl bg-white p-4" onSubmit={buscar}>
        <div className="min-w-64 flex-1">
          <Campo id="ve_texto" etiqueta="Placa (o parte)">
            <input
              id="ve_texto"
              className={`${ESTILO_ENTRADA} uppercase`}
              maxLength={60}
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
            />
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
      {lista.data?.filas.length === 0 && <p className="rounded-xl bg-white p-6 text-lg text-stone-600">Sin vehículos.</p>}
      {lista.data && lista.data.filas.length > 0 && (
        <div className="overflow-x-auto rounded-2xl bg-white">
          <table className="w-full text-base">
            <thead className="bg-stone-50 text-left">
              <tr>
                <th className={CELDA}>Placa o equipo</th>
                <th className={CELDA}>Descripción</th>
                <th className={CELDA}>Cliente</th>
                <th className={CELDA}>Estado</th>
                <th className={CELDA}>
                  <span className="sr-only">Acciones</span>
                </th>
              </tr>
            </thead>
            <tbody className="animar-lista">
              {lista.data.filas.map((v) => (
                <tr key={v.id}>
                  <td className={CELDA}>
                    {v.identificador}
                    {v.tipo === "EQUIPO_TEXTO" && <span className="ml-2 text-sm text-stone-600">(equipo)</span>}
                  </td>
                  <td className={CELDA}>{v.descripcion ?? "—"}</td>
                  <td className={CELDA}>{v.cliente_nombre}</td>
                  <td className={CELDA}>
                    <EtiquetaActivo activo={v.activo} />
                  </td>
                  <td className={`${CELDA} text-right`}>
                    <Boton
                      variante={v.activo ? "peligro" : "secundario"}
                      disabled={activar.isPending}
                      aria-label={`${v.activo ? "Desactivar" : "Activar"} ${v.identificador}`}
                      onClick={() => activar.mutate({ id: v.id, activo: !v.activo })}
                    >
                      {v.activo ? "Desactivar" : "Activar"}
                    </Boton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {lista.data && <Paginador desde={desde} limite={LIMITE} total={lista.data.total} onCambio={setDesde} />}
      <NuevoVehiculo dueno={dueno} />
      <ImportarCsv que="vehiculos" />
    </section>
  );
}

/** Alta de un vehículo. El dueño se toma de la navegación o se busca por documento (debe existir y estar activo). */
function NuevoVehiculo({ dueno }: { dueno: Dueno | null }) {
  const [tipo, setTipo] = useState<TipoDocumento>("CC");
  const [numero, setNumero] = useState("");
  const [placa, setPlaca] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [problema, setProblema] = useState<string | null>(null);
  const consultas = useQueryClient();

  const busqueda = useMutation({ mutationFn: () => api.buscarCliente(tipo, numero.trim()) });
  const encontrado: Cliente | null | undefined = busqueda.data;
  const elegido: Dueno | null = dueno ?? (encontrado ? { id: encontrado.id, nombre: encontrado.nombre_mostrar } : null);

  const crear = useMutation({
    mutationFn: (cliente_id: number) =>
      api.crearVehiculo({ cliente_id, placa: placa.trim().toUpperCase(), descripcion: descripcion.trim() || null }),
    onSuccess: () => {
      setPlaca("");
      setDescripcion("");
      void consultas.invalidateQueries({ queryKey: ["vehiculos"] });
    },
  });

  function guardar(e: FormEvent) {
    e.preventDefault();
    if (!elegido) {
      setProblema("primero busque al dueño por su documento");
      return;
    }
    if (!placa.trim()) {
      setProblema("digite la placa o el nombre del equipo");
      return;
    }
    setProblema(null);
    crear.mutate(elegido.id);
  }

  return (
    <div className="flex flex-col gap-3 rounded-2xl bg-white p-4">
      <h2 className="text-xl font-bold">Registrar vehículo</h2>
      {!dueno && (
        <form
          className="grid items-end gap-3 md:grid-cols-[1fr_1.5fr_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            const p = problemaNumero(tipo, numero);
            setProblema(p);
            if (!p) busqueda.mutate();
          }}
          noValidate
        >
          <Campo id="ve_tipo" etiqueta="Tipo de documento del dueño">
            <select
              id="ve_tipo"
              className={ESTILO_ENTRADA}
              value={tipo}
              onChange={(e) => {
                setTipo(e.target.value as TipoDocumento);
                busqueda.reset();
              }}
            >
              {TIPOS_DOCUMENTO.map((t) => (
                <option key={t} value={t}>
                  {NOMBRE_TIPO[t]}
                </option>
              ))}
            </select>
          </Campo>
          <Campo id="ve_numero" etiqueta="Número de documento">
            <input
              id="ve_numero"
              className={ESTILO_ENTRADA}
              maxLength={20}
              value={numero}
              onChange={(e) => {
                setNumero(e.target.value);
                busqueda.reset();
              }}
            />
          </Campo>
          <Boton type="submit" variante="secundario" icono={Search} disabled={busqueda.isPending}>
            Buscar dueño
          </Boton>
        </form>
      )}
      {busqueda.isSuccess && encontrado === null && (
        <Aviso tipo="alerta" titulo="No hay un cliente activo con ese documento">
          Regístrelo primero (por QR o desde la pista, con autorización de datos).
        </Aviso>
      )}
      {elegido && <p className="text-lg">Dueño: <strong>{elegido.nombre}</strong></p>}
      <form className="grid items-end gap-3 md:grid-cols-[1fr_2fr_auto]" onSubmit={guardar} noValidate>
        <Campo id="ve_placa" etiqueta="Placa o equipo">
          <input
            id="ve_placa"
            className={`${ESTILO_ENTRADA} uppercase`}
            maxLength={60}
            value={placa}
            onChange={(e) => setPlaca(e.target.value)}
          />
        </Campo>
        <Campo id="ve_desc" etiqueta="Descripción (opcional)">
          <input id="ve_desc" className={ESTILO_ENTRADA} maxLength={200} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
        </Campo>
        <Boton type="submit" icono={Plus} disabled={crear.isPending}>
          {crear.isPending ? "Guardando…" : "Registrar"}
        </Boton>
      </form>
      {problema && (
        <p role="alert" className="font-semibold text-red-700">
          {problema}
        </p>
      )}
      <ErrorDeApi error={busqueda.error ?? crear.error} />
      {crear.isSuccess && (
        <Aviso tipo="exito" titulo={`Vehículo ${crear.data.identificador} registrado`}>
          {crear.data.tipo === "EQUIPO_TEXTO" && "No es una placa válida: quedó como equipo (texto)."}
        </Aviso>
      )}
    </div>
  );
}
