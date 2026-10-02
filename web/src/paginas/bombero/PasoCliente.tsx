import { Search, UserCheck } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, ErrorApi } from "../../api/cliente";
import type { Cliente, ClienteEntrada } from "../../api/tipos";
import { FormularioCliente } from "../../componentes/FormularioCliente";
import { Aviso, Boton, Campo, ESTILO_ENTRADA, ErrorDeApi } from "../../componentes/ui";
import { NOMBRE_TIPO, TIPOS_DOCUMENTO, calcularDv, esTipoDocumento, problemaNumero, type TipoDocumento } from "../../lib/documentos";

/** Identificar al comprador: buscar por documento y, si no existe, registro rápido con autorización. */
export function PasoCliente({ onElegido }: { onElegido: (c: Cliente) => void }) {
  const [tipo, setTipo] = useState<TipoDocumento>("CC");
  const [numero, setNumero] = useState("");
  const [buscado, setBuscado] = useState<{ tipo: TipoDocumento; numero: string } | null>(null);
  const problema = numero ? problemaNumero(tipo, numero) : null;

  const busqueda = useMutation({ mutationFn: (b: { tipo: TipoDocumento; numero: string }) => api.buscarCliente(b.tipo, b.numero) });
  const registro = useMutation({
    mutationFn: api.crearCliente,
    onSuccess: onElegido,
    onError: async (e) => {
      // Otro equipo lo registró mientras tanto: usar ese.
      if (e instanceof ErrorApi && e.estado === 409 && buscado) {
        const c = await api.buscarCliente(buscado.tipo, buscado.numero);
        if (c) onElegido(c);
      }
    },
  });

  function buscar() {
    if (problemaNumero(tipo, numero)) return;
    const b = { tipo, numero: numero.trim() };
    setBuscado(b);
    registro.reset();
    busqueda.mutate(b);
  }

  const encontrado = busqueda.data;
  const dv = tipo === "NIT" ? calcularDv(numero.trim()) : null;

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl font-bold">¿A nombre de quién?</h2>
      <form
        className="grid items-end gap-3 md:grid-cols-[1fr_1.5fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          buscar();
        }}
        noValidate
      >
        <Campo id="buscar_tipo" etiqueta="Tipo">
          <select
            id="buscar_tipo"
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
        <Campo
          id="buscar_numero"
          etiqueta={tipo === "NIT" ? "NIT (sin DV)" : "Número"}
          error={problema ?? undefined}
          {...(dv !== null && !problema ? { ayuda: `DV ${dv}` } : {})}
        >
          <input
            id="buscar_numero"
            inputMode={tipo === "CC" || tipo === "NIT" ? "numeric" : "text"}
            autoComplete="off"
            className={ESTILO_ENTRADA}
            value={numero}
            onChange={(e) => {
              setNumero(e.target.value);
              busqueda.reset();
            }}
          />
        </Campo>
        <Boton type="submit" icono={Search} disabled={!numero || !!problema || busqueda.isPending}>
          {busqueda.isPending ? "Buscando…" : "Buscar"}
        </Boton>
      </form>

      <ErrorDeApi error={busqueda.error} />

      {encontrado && (
        <div className="flex flex-wrap items-center gap-4 rounded-2xl border-2 border-dorado bg-white p-4">
          <div className="flex-1">
            <p className="text-sm text-stone-600">
              {esTipoDocumento(encontrado.tipo_documento) ? NOMBRE_TIPO[encontrado.tipo_documento] : encontrado.tipo_documento}{" "}
              {encontrado.numero_documento}
              {encontrado.digito_verificacion !== null && `-${encontrado.digito_verificacion}`}
            </p>
            <p className="text-2xl font-bold">{encontrado.nombre_mostrar}</p>
            <p className="text-base text-stone-700">Factura a: {encontrado.email_enmascarado}</p>
          </div>
          <Boton grande icono={UserCheck} onClick={() => onElegido(encontrado)}>
            Usar este cliente
          </Boton>
        </div>
      )}

      {busqueda.isSuccess && encontrado === null && buscado && (
        <div className="flex flex-col gap-3 rounded-2xl bg-white p-4">
          <Aviso tipo="info" titulo="No está registrado">
            Regístrelo aquí con su autorización de datos, o pídale que use el QR de registro.
          </Aviso>
          <ErrorDeApi error={registro.error} />
          <FormularioCliente
            key={`${buscado.tipo}-${buscado.numero}`}
            fijo={buscado}
            enviando={registro.isPending}
            textoEnviar="Registrar y continuar"
            onEnviar={(e: ClienteEntrada) => registro.mutate(e)}
          />
        </div>
      )}
    </section>
  );
}
