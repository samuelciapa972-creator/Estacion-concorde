import { useEffect, useState } from 'react';
import { api } from '../services/api';
import logoConcorde from '../assets/images/LogoH1.png';
import Captcha from '../components/Captcha';
import FormularioCliente from '../components/FormularioCliente';
import { ErrorApi, Mensaje, Cargando } from '../components/ui';

/** Autorregistro del cliente por QR (público, desde su celular). Misma tarjeta que el ingreso. */
export default function Registro({ tema, setTema }) {
  const [config, setConfig] = useState({ datos: null, error: null });
  const [token, setToken] = useState(null);
  const [reinicio, setReinicio] = useState(0);
  const [errorCaptcha, setErrorCaptcha] = useState(null);
  const [envio, setEnvio] = useState({ enviando: false, error: null, listo: null });

  useEffect(() => {
    api.configuracionPublica().then((datos) => setConfig({ datos, error: null })).catch((error) => setConfig({ datos: null, error }));
  }, []);

  const captcha = config.datos?.captcha || null;
  const registrar = async (entrada) => {
    setEnvio({ enviando: true, error: null, listo: null });
    try {
      const r = await api.registroPublico({ ...entrada, captcha: token });
      setEnvio({ enviando: false, error: null, listo: r.mensaje });
    } catch (error) {
      // Un token de captcha sirve una sola vez: tras un rechazo hay que resolverlo de nuevo.
      setReinicio((n) => n + 1);
      setEnvio({ enviando: false, error, listo: null });
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', backgroundColor: 'var(--bg-main)', padding: '20px' }}>
      <button
        onClick={() => setTema(tema === 'dark' ? 'light' : 'dark')}
        className="boton--secundario"
        style={{ position: 'absolute', top: '24px', right: '24px', background: 'var(--bg-card)', border: '1px solid var(--border-color)', color: 'var(--text-primary)', padding: '8px 14px', borderRadius: '4px', fontSize: '12px', fontWeight: '600', cursor: 'pointer', boxShadow: 'var(--shadow)' }}
      >
        {tema === 'dark' ? 'Modo Luz' : 'Modo Noche'}
      </button>

      <div className="vista" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '36px 32px', width: '100%', maxWidth: '560px', boxShadow: 'var(--sombra-panel)', marginTop: '60px' }}>
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '20px' }}>
          <img
            src={logoConcorde}
            alt="EDS Concorde Logo"
            style={{ maxWidth: '220px', height: 'auto', maxHeight: '60px', objectFit: 'contain', filter: tema === 'dark' ? 'brightness(0) invert(1)' : 'none', transition: 'filter 0.2s ease' }}
          />
        </div>
        <p style={{ color: 'var(--text-secondary)', marginBottom: '8px', fontSize: '13px', fontWeight: '600', textAlign: 'center' }}>
          Registro para factura electrónica
        </p>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '24px', textAlign: 'center' }}>
          Regístrese una sola vez. Después, en la estación, solo diga su número de documento y la factura le llega al correo.
        </p>

        {envio.listo ? (
          <Mensaje tipo="exito" titulo="Listo">
            {envio.listo}
          </Mensaje>
        ) : config.error ? (
          <ErrorApi error={config.error} />
        ) : !config.datos ? (
          <Cargando />
        ) : (
          <>
            <ErrorApi error={envio.error} />
            {errorCaptcha && <Mensaje tipo="error">{errorCaptcha}. Revise su conexión y recargue la página.</Mensaje>}
            <FormularioCliente
              enviando={envio.enviando}
              textoEnviar="Registrarme"
              bloqueo={captcha && !token ? 'Complete la verificación de arriba' : undefined}
              antesDeEnviar={captcha && <Captcha config={captcha} onToken={setToken} reinicio={reinicio} onError={setErrorCaptcha} />}
              onEnviar={registrar}
            />
          </>
        )}
      </div>

      <p style={{ marginTop: '20px', fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>Concorde, mil razones para viajar</p>
    </div>
  );
}
