import { Receipt } from "lucide-react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";
import { api } from "../../api/cliente";
import type { Pendiente } from "../../api/tipos";
import { Boton, Campo, Cargando, ESTILO_ENTRADA, ErrorDeApi } from "../../componentes/ui";
import { ahoraIso, galones, leerGalones, leerPesos, pesos } from "../../lib/formato";

const esquema = z.object({
  surtidor_id: z.string().regex(/^\d+$/, "elija el surtidor"),
  lado: z.enum(["A", "B"], { error: "elija el lado" }),
  producto: z.string().min(1, "elija el producto"),
  volumen: z.string().refine((v) => leerGalones(v) !== null, "galones con hasta 3 decimales, p. ej. 1,861"),
  valor: z.string().refine((v) => leerPesos(v) !== null, "valor en pesos sin centavos, p. ej. 29757"),
  forma_pago: z.enum(["CONTADO", "CREDITO"]),
});
type Datos = z.infer<typeof esquema>;

/** Modo manual: el bombero digita lo que marcó el surtidor (respaldo mientras no hay captura automática). */
export function VentaManual() {
  const navegar = useNavigate();
  const cliente = useQueryClient();
  const catalogos = useQuery({ queryKey: ["catalogos"], queryFn: api.catalogos, staleTime: 5 * 60_000 });
  const { register, handleSubmit, formState, control, setValue } = useForm<Datos>({
    resolver: zodResolver(esquema),
    defaultValues: { forma_pago: "CONTADO", surtidor_id: "", producto: "", volumen: "", valor: "" },
  });
  const crear = useMutation({
    mutationFn: (d: Datos) =>
      api.crearVentaManual({
        surtidor_id: Number(d.surtidor_id),
        lado: d.lado,
        producto: d.producto,
        volumen: leerGalones(d.volumen) ?? "",
        valor: leerPesos(d.valor) ?? "",
        forma_pago: d.forma_pago,
      }),
    onSuccess: ({ id }, d) => {
      void cliente.invalidateQueries({ queryKey: ["pendientes"] });
      if (d.forma_pago !== "CONTADO") {
        void navegar("/pista", { replace: true });
        return;
      }
      const pendiente: Pendiente = {
        origen_tipo: "venta_manual",
        origen_id: id,
        surtidor_id: Number(d.surtidor_id),
        lado: d.lado,
        producto: d.producto,
        volumen: leerGalones(d.volumen) ?? "",
        valor: leerPesos(d.valor) ?? "",
        ppu: "",
        forma_pago: d.forma_pago,
        fecha: ahoraIso(), // la hora exacta la guarda la API; aquí solo se muestra
        placa: null,
      };
      void navegar("/pista/facturar", { replace: true, state: { pendiente } });
    },
  });

  const [volumen, valor, lado] = useWatch({ control, name: ["volumen", "valor", "lado"] });
  if (catalogos.isPending) return <Cargando />;
  const vol = leerGalones(volumen);
  const val = leerPesos(valor);

  return (
    <form
      onSubmit={(e) => void handleSubmit((d) => crear.mutate(d))(e)}
      className="mx-auto flex max-w-2xl flex-col gap-4 rounded-2xl bg-white p-6"
      noValidate
    >
      <h1 className="text-2xl font-bold">Nueva venta manual</h1>
      <ErrorDeApi error={catalogos.error} />
      <div className="grid gap-4 md:grid-cols-2">
        <Campo id="surtidor" etiqueta="Surtidor" error={formState.errors.surtidor_id?.message}>
          <select id="surtidor" className={ESTILO_ENTRADA} {...register("surtidor_id")}>
            <option value="">Elija…</option>
            {catalogos.data?.surtidores.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.marca} {s.descripcion ? `— ${s.descripcion}` : ""}
              </option>
            ))}
          </select>
        </Campo>
        <Campo id="lado" etiqueta="Lado" error={formState.errors.lado?.message}>
          <div id="lado" className="flex gap-2" role="radiogroup" aria-label="Lado">
            {(["A", "B"] as const).map((l) => (
              <Boton
                key={l}
                role="radio"
                aria-checked={lado === l}
                variante={lado === l ? "primario" : "secundario"}
                className="flex-1"
                onClick={() => setValue("lado", l, { shouldValidate: true })}
              >
                {l}
              </Boton>
            ))}
          </div>
        </Campo>
        <Campo id="producto" etiqueta="Producto" error={formState.errors.producto?.message}>
          <select id="producto" className={ESTILO_ENTRADA} {...register("producto")}>
            <option value="">Elija…</option>
            {catalogos.data?.productos.map((p) => (
              <option key={p.id} value={p.codigo}>
                {p.nombre}
              </option>
            ))}
          </select>
        </Campo>
        <Campo id="forma_pago" etiqueta="Forma de pago" error={formState.errors.forma_pago?.message}>
          <select id="forma_pago" className={ESTILO_ENTRADA} {...register("forma_pago")}>
            <option value="CONTADO">Contado</option>
            <option value="CREDITO">Crédito (solo se registra; no se factura aquí)</option>
          </select>
        </Campo>
        <Campo id="volumen" etiqueta="Galones" error={formState.errors.volumen?.message} ayuda="Como lo marca el surtidor, con 3 decimales">
          <input id="volumen" inputMode="decimal" autoComplete="off" className={ESTILO_ENTRADA} {...register("volumen")} />
        </Campo>
        <Campo id="valor" etiqueta="Valor cobrado (pesos)" error={formState.errors.valor?.message} ayuda="El valor que marca el surtidor">
          <input id="valor" inputMode="numeric" autoComplete="off" className={ESTILO_ENTRADA} {...register("valor")} />
        </Campo>
      </div>
      {vol && val && (
        <p className="rounded-xl bg-stone-100 p-3 text-lg">
          {galones(vol)} gal por {pesos(val)}
        </p>
      )}
      <ErrorDeApi error={crear.error} />
      <div className="flex gap-3">
        <Boton variante="secundario" onClick={() => void navegar("/pista")}>
          Cancelar
        </Boton>
        <Boton type="submit" grande className="flex-1" icono={Receipt} disabled={crear.isPending}>
          {crear.isPending ? "Guardando…" : "Guardar y facturar"}
        </Boton>
      </div>
    </form>
  );
}
