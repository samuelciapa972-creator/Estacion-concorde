import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, LogIn } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";
import { api } from "../api/cliente";
import { guardarSesion } from "../api/sesion";
import { Marca } from "../componentes/Marca";
import { useSesion } from "../componentes/SesionContexto";
import { Boton, Campo, ESTILO_ENTRADA, ErrorDeApi } from "../componentes/ui";

const esquema = z.object({
  usuario: z.string().trim().min(3, "mínimo 3 caracteres").max(40),
  pin: z.string().regex(/^\d{4,8}$/, "el PIN tiene de 4 a 8 dígitos"),
});
type Datos = z.infer<typeof esquema>;

export function Ingreso() {
  const sesion = useSesion();
  const navegar = useNavigate();
  const volver = (useLocation().state as { volver?: string } | null)?.volver;
  const { register, handleSubmit, formState, resetField } = useForm<Datos>({ resolver: zodResolver(esquema) });
  const ingreso = useMutation({
    mutationFn: (d: Datos) => api.ingresar(d.usuario, d.pin),
    onSuccess: (t) => {
      guardarSesion(t);
      void navegar(volver ?? (t.usuario.rol === "ADMIN" ? "/admin/reportes" : "/pista"), { replace: true });
    },
    onError: () => {
      resetField("pin");
    },
  });

  if (sesion && !ingreso.isPending) return <Navigate to="/pista" replace />;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-negro p-4">
      <Marca grande como="h1" />
      <form
        onSubmit={(e) => void handleSubmit((d) => ingreso.mutate(d))(e)}
        className="flex w-full max-w-md flex-col gap-4 rounded-2xl border-t-4 border-dorado bg-white p-6 shadow-lg"
        noValidate
      >
        <h2 className="flex items-center gap-2 text-2xl font-bold">
          <KeyRound className="size-6 text-dorado-oscuro" aria-hidden="true" />
          Ingreso
        </h2>
        <Campo id="usuario" etiqueta="Usuario" error={formState.errors.usuario?.message}>
          <input id="usuario" autoComplete="username" autoCapitalize="none" className={ESTILO_ENTRADA} {...register("usuario")} />
        </Campo>
        <Campo id="pin" etiqueta="PIN" error={formState.errors.pin?.message}>
          <input
            id="pin"
            type="password"
            inputMode="numeric"
            autoComplete="current-password"
            className={ESTILO_ENTRADA}
            {...register("pin")}
          />
        </Campo>
        <ErrorDeApi error={ingreso.error} />
        <Boton type="submit" grande icono={LogIn} disabled={ingreso.isPending}>
          {ingreso.isPending ? "Ingresando…" : "Ingresar"}
        </Boton>
      </form>
    </main>
  );
}
