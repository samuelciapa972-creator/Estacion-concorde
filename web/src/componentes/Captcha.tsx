/**
 * Widget de captcha (Cloudflare Turnstile o hCaptcha, según lo que diga la API). Los dos tienen la misma API de
 * render explícito: render(elemento, {sitekey, callback, "expired-callback"}) y reset(id).
 * El script se carga del proveedor solo en la página pública de registro.
 */
import { useEffect, useLayoutEffect, useRef } from "react";
import type { CaptchaConfig } from "../api/tipos";

interface WidgetApi {
  render: (el: HTMLElement, opciones: Record<string, unknown>) => string;
  reset: (id?: string) => void;
  remove?: (id: string) => void;
}

declare global {
  interface Window {
    turnstile?: WidgetApi;
    hcaptcha?: WidgetApi;
  }
}

const SCRIPT: Record<CaptchaConfig["proveedor"], string> = {
  turnstile: "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
  hcaptcha: "https://js.hcaptcha.com/1/api.js?render=explicit&hl=es",
};

const cargas = new Map<string, Promise<void>>();

function cargarScript(url: string): Promise<void> {
  let p = cargas.get(url);
  if (!p) {
    p = new Promise<void>((ok, falla) => {
      const s = document.createElement("script");
      s.src = url;
      s.async = true;
      s.onload = () => {
        ok();
      };
      s.onerror = () => {
        cargas.delete(url);
        falla(new Error("no se pudo cargar el captcha"));
      };
      document.head.appendChild(s);
    });
    cargas.set(url, p);
  }
  return p;
}

export function Captcha({
  config,
  onToken,
  reinicio,
  onError,
}: {
  config: CaptchaConfig;
  onToken: (token: string | null) => void;
  /** Cambiar este número reinicia el widget (p. ej. tras un registro rechazado: el token ya se gastó). */
  reinicio: number;
  onError: (mensaje: string) => void;
}) {
  const caja = useRef<HTMLDivElement>(null);
  const id = useRef<string | null>(null);
  const llamadas = useRef({ onToken, onError });
  useLayoutEffect(() => {
    llamadas.current = { onToken, onError };
  });

  useEffect(() => {
    let vivo = true;
    cargarScript(SCRIPT[config.proveedor])
      .then(() => {
        const widget = window[config.proveedor];
        if (!vivo || !widget || !caja.current) return;
        id.current = widget.render(caja.current, {
          sitekey: config.clave_sitio,
          callback: (t: string) => {
            llamadas.current.onToken(t);
          },
          "expired-callback": () => {
            llamadas.current.onToken(null);
          },
          "error-callback": () => {
            llamadas.current.onToken(null);
          },
        });
      })
      .catch((e: unknown) => {
        llamadas.current.onError(e instanceof Error ? e.message : "no se pudo cargar el captcha");
      });
    return () => {
      vivo = false;
      const widget = window[config.proveedor];
      if (id.current && widget?.remove) widget.remove(id.current);
      id.current = null;
    };
  }, [config.proveedor, config.clave_sitio]);

  useEffect(() => {
    if (reinicio === 0 || !id.current) return;
    window[config.proveedor]?.reset(id.current);
    llamadas.current.onToken(null);
  }, [reinicio, config.proveedor]);

  return <div ref={caja} className="min-h-16" data-testid="captcha" />;
}
