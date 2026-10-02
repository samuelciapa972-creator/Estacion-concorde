import { ArrowRight, Banknote, CircleCheck, CircleX, Clock, CreditCard, FileDown, LoaderCircle, type LucideIcon, Receipt } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, ErrorApi } from "../../api/cliente";
import type { Cliente, Factura, MedioPago, Pendiente } from "../../api/tipos";
import { Aviso, Boton, ErrorDeApi } from "../../componentes/ui";
import { configuracion } from "../../config";
import { nuevaClave } from "../../lib/clave";
import { galones, pesos } from "../../lib/formato";
import { PasoCliente } from "./PasoCliente";

type Paso = "cliente" | "pago" | "resumen";

/** Facturar una venta: cliente → medio de pago → resumen y emitir → estado de la factura. */
export function Facturar() {
  const navegar = useNavigate();
  const pendiente = (useLocation().state as { pendiente?: Pendiente } | null)?.pendiente;
  const [paso, setPaso] = useState<Paso>("cliente");
  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [medio, setMedio] = useState<MedioPago | null>(null);

  // Al recargar la página se pierde la venta elegida: volver a la lista (que muestra si sigue pendiente).
  if (!pendiente) return <Navigate to="/pista" replace />;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-white p-4">
        <div className="flex-1">
          <p className="text-base text-stone-600">
            Lado {pendiente.lado} · {pendiente.producto}
            {pendiente.origen_tipo === "venta_manual" && " · venta manual"}
          </p>
          <p className="text-3xl font-bold">
            {pesos(pendiente.valor)} <span className="text-xl font-normal">· {galones(pendiente.volumen)} gal</span>
          </p>
        </div>
        <Boton variante="secundario" onClick={() => void navegar("/pista")}>
          Cancelar
        </Boton>
      </div>

      {paso === "cliente" && (
        <>
          <PasoCliente
            onElegido={(c) => {
              setCliente(c);
              setPaso("pago");
            }}
          />
          <Boton
            variante="secundario"
            disabled={!configuracion.consumidorFinal}
            title="Pendiente de definir con el contador"
          >
            {configuracion.consumidorFinal?.etiqueta ??
              "Consumidor final / sin factura — pendiente de definir con el contador"}
          </Boton>
        </>
      )}

      {paso === "pago" && cliente && (
        <section className="flex flex-col gap-4">
          <h2 className="text-xl font-bold">¿Cómo pagó {cliente.nombre_mostrar}?</h2>
          <div className="grid grid-cols-2 gap-4">
            {(["EFECTIVO", "TARJETA"] as const).map((m) => (
              <Boton
                key={m}
                grande
                className="min-h-24 text-2xl"
                icono={m === "EFECTIVO" ? Banknote : CreditCard}
                onClick={() => {
                  setMedio(m);
                  setPaso("resumen");
                }}
              >
                {m === "EFECTIVO" ? "Efectivo" : "Tarjeta"}
              </Boton>
            ))}
          </div>
          <Boton variante="fantasma" onClick={() => setPaso("cliente")}>
            Cambiar cliente
          </Boton>
        </section>
      )}

      {paso === "resumen" && cliente && medio && (
        <Emision
          pendiente={pendiente}
          cliente={cliente}
          medio={medio}
          onCambiar={() => setPaso("pago")}
          onTerminar={() => void navegar("/pista")}
        />
      )}
    </section>
  );
}

function Emision({
  pendiente,
  cliente,
  medio,
  onCambiar,
  onTerminar,
}: {
  pendiente: Pendiente;
  cliente: Cliente;
  medio: MedioPago;
  onCambiar: () => void;
  onTerminar: () => void;
}) {
  const consultas = useQueryClient();
  // UNA clave por intento: se reusa en cada reintento para que la API no cree dos facturas.
  const [clave] = useState(nuevaClave);
  const emision = useMutation({
    mutationFn: () =>
      api.emitir({
        clave,
        origen_tipo: pendiente.origen_tipo,
        origen_id: pendiente.origen_id,
        cliente_id: cliente.id,
        medio_pago: medio,
      }),
    onSuccess: () => void consultas.invalidateQueries({ queryKey: ["pendientes"] }),
  });

  if (emision.data) return <Resultado inicial={emision.data} onTerminar={onTerminar} />;

  const error = emision.error;
  // Sin respuesta, o error del servidor que no es una negativa clara: no se sabe si la factura quedó creada.
  const dudoso = error instanceof ErrorApi && (error.sinRespuesta || (error.estado >= 500 && error.estado !== 503));
  const esNit = cliente.tipo_documento === "NIT";

  return (
    <section className="flex flex-col gap-4 rounded-2xl bg-white p-6">
      <h2 className="text-xl font-bold">Revise antes de emitir</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-lg">
        <dt className="text-stone-600">{esNit ? "Razón social" : "Cliente"}</dt>
        <dd className="font-bold">{cliente.nombre_mostrar}</dd>
        <dt className="text-stone-600">Documento</dt>
        <dd>
          {cliente.tipo_documento} {cliente.numero_documento}
          {cliente.digito_verificacion !== null && `-${cliente.digito_verificacion}`}
        </dd>
        <dt className="text-stone-600">Correo</dt>
        <dd>{cliente.email_enmascarado}</dd>
        <dt className="text-stone-600">Producto</dt>
        <dd>
          {pendiente.producto} · Lado {pendiente.lado}
        </dd>
        <dt className="text-stone-600">Galones</dt>
        <dd>{galones(pendiente.volumen)}</dd>
        {pendiente.ppu && (
          <>
            <dt className="text-stone-600">Precio por galón</dt>
            <dd>{pesos(pendiente.ppu)}</dd>
          </>
        )}
        <dt className="text-stone-600">Medio de pago</dt>
        <dd>{medio === "EFECTIVO" ? "Efectivo" : "Tarjeta"}</dd>
        <dt className="text-stone-600">Total</dt>
        <dd className="text-3xl font-bold">{pesos(pendiente.valor)}</dd>
      </dl>

      {dudoso ? (
        <Aviso tipo="alerta" titulo="No se sabe si la factura quedó creada">
          <p>{error.message}. Reintentar es seguro: no se crea una segunda factura.</p>
        </Aviso>
      ) : (
        <ErrorDeApi error={error} />
      )}
      {error instanceof ErrorApi && error.estado === 409 && (
        <Boton variante="secundario" onClick={onTerminar}>
          Volver a la lista
        </Boton>
      )}

      <div className="flex gap-3">
        <Boton variante="secundario" onClick={onCambiar} disabled={emision.isPending || dudoso}>
          Atrás
        </Boton>
        <Boton
          grande
          className="flex-1"
          icono={Receipt}
          disabled={emision.isPending}
          onClick={() => {
            if (!emision.isPending) emision.mutate();
          }}
        >
          {emision.isPending ? "Emitiendo…" : dudoso ? "Reintentar" : "Emitir factura"}
        </Boton>
      </div>
    </section>
  );
}

const TEXTO_ESTADO: Record<Factura["estado"], { tipo: "info" | "exito" | "error" | "alerta"; titulo: string; texto: string }> = {
  EN_PROCESO: {
    tipo: "info",
    titulo: "Factura creada",
    texto: "Se está enviando en segundo plano. Puede atender al siguiente cliente.",
  },
  FACTURADO: {
    tipo: "exito",
    titulo: "Factura validada",
    texto: "El envío por correo todavía no está disponible: descargue el PDF para entregárselo al cliente.",
  },
  RECHAZADO: {
    tipo: "error",
    titulo: "Factura rechazada",
    texto: "La venta vuelve a quedar pendiente. Revise el motivo con el administrador antes de reintentar.",
  },
  INCIERTO: {
    tipo: "alerta",
    titulo: "Estado por confirmar",
    texto: "No se sabe todavía si la factura llegó. El sistema lo concilia solo: NO la vuelva a emitir.",
  },
};

/** Ícono decorativo del estado (el texto del aviso es el que informa). La clave reinicia la animación al cambiar. */
const ICONO_ESTADO: Record<Factura["estado"], { icono: LucideIcon; clase: string }> = {
  EN_PROCESO: { icono: LoaderCircle, clase: "animate-spin text-sky-700" },
  FACTURADO: { icono: CircleCheck, clase: "animar-rebote text-green-700" },
  RECHAZADO: { icono: CircleX, clase: "animar-rebote text-red-700" },
  INCIERTO: { icono: Clock, clase: "text-amber-700" },
};

function Resultado({ inicial, onTerminar }: { inicial: Factura; onTerminar: () => void }) {
  const consulta = useQuery({
    queryKey: ["factura", inicial.id],
    queryFn: () => api.factura(inicial.id),
    initialData: inicial,
    refetchInterval: (q) => (q.state.data?.estado === "EN_PROCESO" ? configuracion.refrescoFacturaMs : false),
  });
  const f = consulta.data;
  const t = TEXTO_ESTADO[f.estado];
  const IconoEstado = ICONO_ESTADO[f.estado].icono;
  return (
    <section className="flex flex-col gap-4 rounded-2xl bg-white p-6">
      <div className="flex items-start gap-4">
        <IconoEstado key={f.estado} className={`mt-2 size-12 shrink-0 ${ICONO_ESTADO[f.estado].clase}`} aria-hidden="true" />
        <div className="flex-1">
          <Aviso tipo={t.tipo} titulo={`${t.titulo}: ${f.numero}`}>
            <p className="text-lg">{t.texto}</p>
            {f.error && <p className="mt-2">Motivo: {f.error}</p>}
            {inicial.nueva === false && <p className="mt-2">Esta venta ya tenía esta factura (no se creó otra).</p>}
          </Aviso>
        </div>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-lg">
        <dt className="text-stone-600">Cliente</dt>
        <dd className="font-bold">{f.cliente.nombre_mostrar}</dd>
        <dt className="text-stone-600">Total</dt>
        <dd>{pesos(f.total)}</dd>
        {f.cufe && (
          <>
            <dt className="text-stone-600">CUFE</dt>
            <dd className="break-all font-mono text-sm">{f.cufe}</dd>
          </>
        )}
      </dl>
      {f.estado === "FACTURADO" && <DescargarPdf id={f.id} numero={f.numero} />}
      <Boton grande icono={ArrowRight} onClick={onTerminar}>
        Siguiente venta
      </Boton>
    </section>
  );
}

/** El PDF se pide con el token de la sesión (no sirve un enlace directo) y se descarga como archivo. */
function DescargarPdf({ id, numero }: { id: number; numero: string }) {
  const descarga = useMutation({
    mutationFn: async () => {
      const url = URL.createObjectURL(await api.facturaPdf(id));
      const a = document.createElement("a");
      a.href = url;
      a.download = `factura_${numero}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    },
  });
  return (
    <>
      <Boton grande variante="secundario" icono={FileDown} disabled={descarga.isPending} onClick={() => descarga.mutate()}>
        {descarga.isPending ? "Generando PDF…" : "Descargar PDF"}
      </Boton>
      <ErrorDeApi error={descarga.error} />
    </>
  );
}
