import { useState } from 'react';
import { CORREO, NOMBRE_TIPO, TIPOS_DOCUMENTO, calcularDv, problemaNumero } from '../lib/documentos';
import { Boton, Campo, Rejilla } from './ui';
import { COLOR, estilos } from './estilos';

/**
 * TEXTO PROVISIONAL de la autorización de datos (Ley 1581 de 2012). La empresa debe aportar su política real
 * (responsable, finalidades, derechos, canal de consultas) antes de publicar el QR fuera del banco de pruebas.
 */
export const POLITICA_DATOS =
  'Autorizo a la estación de servicio a tratar mis datos personales (documento, nombre y correo) para ' +
  'expedir y enviarme facturas electrónicas, conforme a la Ley 1581 de 2012. Puedo conocer, actualizar, ' +
  'rectificar y suprimir mis datos.';

function validar(d) {
  const e = {};
  const p = problemaNumero(d.tipo_documento, d.numero_documento);
  if (p) e.numero_documento = p;
  if (!d.nombres.trim()) e.nombres = d.tipo_documento === 'NIT' ? 'falta la razón social' : 'falta el nombre';
  if (d.tipo_documento !== 'NIT' && !d.apellidos.trim()) e.apellidos = 'faltan los apellidos';
  if (!CORREO.test(d.email.trim())) e.email = 'el correo no es válido';
  if (!d.autoriza_tratamiento) e.autoriza_tratamiento = 'sin la autorización no se pueden guardar los datos';
  return e;
}

/** Entrada de la API: mayúsculas como en la factura y DV calculado si es NIT. */
function aEntrada(d) {
  const esNit = d.tipo_documento === 'NIT';
  const dv = esNit ? calcularDv(d.numero_documento.trim()) : null;
  return {
    tipo_documento: d.tipo_documento,
    numero_documento: d.numero_documento.trim(),
    digito_verificacion: dv === null ? null : String(dv),
    nombres: d.nombres.trim().toUpperCase(),
    apellidos: esNit ? '' : d.apellidos.trim().toUpperCase(),
    email: d.email.trim().toLowerCase(),
    autoriza_tratamiento: d.autoriza_tratamiento,
  };
}

/**
 * Datos del cliente con la casilla de autorización. Si viene `fijo` ({tipo, numero}), el documento ya lo buscó
 * el bombero y no se cambia aquí.
 */
export default function FormularioCliente({ fijo, enviando, textoEnviar, onEnviar, antesDeEnviar, bloqueo }) {
  const [d, setD] = useState({
    tipo_documento: fijo?.tipo || 'CC',
    numero_documento: fijo?.numero || '',
    nombres: '',
    apellidos: '',
    email: '',
    autoriza_tratamiento: false,
  });
  const [errores, setErrores] = useState({});
  const esNit = d.tipo_documento === 'NIT';
  const dv = esNit ? calcularDv(d.numero_documento.trim()) : null;
  const cambiar = (campo) => (e) => setD({ ...d, [campo]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  const enviar = (e) => {
    e.preventDefault();
    const encontrados = validar(d);
    setErrores(encontrados);
    if (Object.keys(encontrados).length === 0) onEnviar(aEntrada(d));
  };

  return (
    <form onSubmit={enviar} noValidate style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <Rejilla columnas="repeat(auto-fill, minmax(220px, 1fr))">
        <Campo etiqueta="Tipo de documento" id="fc_tipo">
          {fijo ? (
            <input id="fc_tipo" readOnly value={NOMBRE_TIPO[fijo.tipo]} style={estilos.entrada} />
          ) : (
            <select id="fc_tipo" value={d.tipo_documento} onChange={cambiar('tipo_documento')} style={estilos.entrada}>
              {TIPOS_DOCUMENTO.map((t) => (
                <option key={t} value={t}>
                  {NOMBRE_TIPO[t]}
                </option>
              ))}
            </select>
          )}
        </Campo>
        <Campo
          etiqueta={esNit ? 'NIT (sin dígito de verificación)' : 'Número de documento'}
          id="fc_numero"
          error={errores.numero_documento}
          ayuda={esNit && dv !== null && d.numero_documento.trim().length >= 8 ? `Dígito de verificación: ${dv}` : undefined}
        >
          <input id="fc_numero" readOnly={!!fijo} inputMode={d.tipo_documento === 'CC' || esNit ? 'numeric' : 'text'} autoComplete="off" value={d.numero_documento} onChange={cambiar('numero_documento')} style={estilos.entrada} />
        </Campo>
        <Campo etiqueta={esNit ? 'Razón social' : 'Nombres'} id="fc_nombres" error={errores.nombres}>
          <input id="fc_nombres" value={d.nombres} onChange={cambiar('nombres')} style={{ ...estilos.entrada, textTransform: 'uppercase' }} />
        </Campo>
        {!esNit && (
          <Campo etiqueta="Apellidos" id="fc_apellidos" error={errores.apellidos}>
            <input id="fc_apellidos" value={d.apellidos} onChange={cambiar('apellidos')} style={{ ...estilos.entrada, textTransform: 'uppercase' }} />
          </Campo>
        )}
        <Campo etiqueta="Correo para recibir la factura" id="fc_email" error={errores.email}>
          <input id="fc_email" type="email" inputMode="email" autoComplete="email" value={d.email} onChange={cambiar('email')} style={estilos.entrada} />
        </Campo>
      </Rejilla>

      <div>
        <label style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', padding: '12px', border: '1px solid var(--border-color)', background: 'var(--bg-input)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-primary)', cursor: 'pointer' }}>
          <input type="checkbox" checked={d.autoriza_tratamiento} onChange={cambiar('autoriza_tratamiento')} style={{ marginTop: '2px', width: '16px', height: '16px', flexShrink: 0 }} />
          <span>
            {POLITICA_DATOS}
            <em style={{ display: 'block', marginTop: '4px', color: COLOR.alerta, fontSize: '11px' }}>Texto provisional: falta la política de la empresa.</em>
          </span>
        </label>
        {errores.autoriza_tratamiento && <span role="alert" style={{ display: 'block', fontSize: '11px', color: COLOR.error, marginTop: '4px' }}>{errores.autoriza_tratamiento}</span>}
      </div>

      {antesDeEnviar}
      {bloqueo && <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{bloqueo}</span>}
      <Boton type="submit" disabled={enviando || !!bloqueo} style={{ padding: '12px' }}>
        {enviando ? 'Guardando…' : textoEnviar}
      </Boton>
    </form>
  );
}
