import { useEffect, useLayoutEffect, useRef } from 'react';

/**
 * Widget de captcha (Cloudflare Turnstile o hCaptcha, según lo que diga la API). Los dos tienen la misma API de
 * render explícito. El script se carga del proveedor solo en la página pública de registro.
 */
const SCRIPT = {
  turnstile: 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit',
  hcaptcha: 'https://js.hcaptcha.com/1/api.js?render=explicit&hl=es',
};

const cargas = new Map();

function cargarScript(url) {
  let p = cargas.get(url);
  if (!p) {
    p = new Promise((ok, falla) => {
      const s = document.createElement('script');
      s.src = url;
      s.async = true;
      s.onload = () => ok();
      s.onerror = () => {
        cargas.delete(url);
        falla(new Error('no se pudo cargar el captcha'));
      };
      document.head.appendChild(s);
    });
    cargas.set(url, p);
  }
  return p;
}

/** `reinicio`: cambiar este número reinicia el widget (tras un registro rechazado el token ya se gastó). */
export default function Captcha({ config, onToken, reinicio, onError }) {
  const caja = useRef(null);
  const id = useRef(null);
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
          callback: (t) => llamadas.current.onToken(t),
          'expired-callback': () => llamadas.current.onToken(null),
          'error-callback': () => llamadas.current.onToken(null),
        });
      })
      .catch((e) => llamadas.current.onError(e.message));
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

  return <div ref={caja} style={{ minHeight: '65px' }} />;
}
