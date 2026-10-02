import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/cliente";
import { Captcha } from "../../componentes/Captcha";
import { Marca, useConfiguracionPublica } from "../../componentes/Marca";
import { FormularioCliente } from "../../componentes/FormularioCliente";
import { Aviso, Cargando, ErrorDeApi } from "../../componentes/ui";

/** Autorregistro del cliente por QR (público, desde su celular). */
export function Registro() {
  const config = useConfiguracionPublica();
  const [token, setToken] = useState<string | null>(null);
  const [reinicio, setReinicio] = useState(0);
  const [errorCaptcha, setErrorCaptcha] = useState<string | null>(null);
  const registro = useMutation({
    mutationFn: api.registroPublico,
    // Un token de captcha sirve una sola vez: tras un rechazo hay que resolverlo de nuevo.
    onError: () => {
      setReinicio((n) => n + 1);
    },
  });
  const captcha = config.data?.captcha ?? null;
  const falta = captcha !== null && !token;

  return (
    <main className="min-h-screen bg-fondo">
      <header className="border-b-2 border-dorado bg-negro px-4 py-4">
        <div className="mx-auto max-w-xl">
          <Marca />
        </div>
      </header>
      <div className="mx-auto my-6 flex max-w-xl flex-col gap-4 rounded-2xl bg-white p-5 shadow">
        <h1 className="text-2xl font-bold">Registro para factura electrónica</h1>
        {registro.data ? (
          <Aviso tipo="exito" titulo="Listo">
            <p className="text-lg">{registro.data.mensaje}</p>
          </Aviso>
        ) : config.isPending ? (
          <Cargando />
        ) : config.error ? (
          <ErrorDeApi error={config.error} />
        ) : (
          <>
            <p className="text-base text-stone-700">
              Regístrese una sola vez. Después, en la estación, solo diga su número de documento y la factura le llega
              al correo.
            </p>
            <ErrorDeApi error={registro.error} />
            {errorCaptcha && <Aviso>{errorCaptcha}. Revise su conexión y recargue la página.</Aviso>}
            <FormularioCliente
              enviando={registro.isPending}
              textoEnviar="Registrarme"
              {...(falta ? { bloqueo: "Complete la verificación de arriba" } : {})}
              antesDeEnviar={
                captcha && <Captcha config={captcha} onToken={setToken} reinicio={reinicio} onError={setErrorCaptcha} />
              }
              onEnviar={(e) => {
                registro.mutate({ ...e, captcha: token });
              }}
            />
          </>
        )}
      </div>
    </main>
  );
}
