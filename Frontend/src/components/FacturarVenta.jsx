import { useEffect, useState } from 'react';
import { api, ErrorApi } from '../services/api';
import { nuevaClave } from '../lib/clave';
import { guardarArchivo } from '../lib/descarga';
import { avisoEntrega, seguirConsultando } from '../lib/entrega';
import { NOMBRE_TIPO, TIPOS_DOCUMENTO, calcularDv, problemaNumero } from '../lib/documentos';
import { galones, oracion, pesos } from '../lib/formato';
import FormularioCliente from './FormularioCliente';
import { Boton, Campo, ErrorApi as MostrarError, Mensaje, Rejilla, Rodillo } from './ui';
import { COLOR, estilos } from './estilos';

const tarjeta = { ...estilos.caja, padding: '20px', borderRadius: '6px' };
const subtituloPaso = { fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', margin: '0 0 14px 0' };

/** Facturar una venta pendiente: cliente → medio de pago → revisar y emitir → estado de la factura. */
export default function FacturarVenta({ venta, onTerminar }) {
  const [paso, setPaso] = useState('cliente');
  const [cliente, setCliente] = useState(null);
  const [medio, setMedio] = useState(null);

  return (
    <div className="vista pagina" style={{ display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '900px' }}>
      {/* La venta en el mismo display del surtidor que tenía en la pista. */}
      <div className="display" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap', padding: '18px 20px', borderRadius: '6px' }}>
        <div>
          <span className="display__etiqueta">
            {venta.surtidor} · lado {venta.lado} · {oracion(venta.producto)}
            {venta.origen_tipo === 'venta_manual' && ' · venta manual'}
          </span>
          <span className="display__monto">
            <Rodillo texto={pesos(venta.valor)} />
          </span>
          <div className="display__fila" style={{ justifyContent: 'flex-start', gap: '16px' }}>
            <span>{galones(venta.volumen)} gal</span>
            {venta.ppu && <span>{pesos(venta.ppu)}/gal</span>}
            {venta.placa && <span>Placa {venta.placa}</span>}
          </div>
        </div>
        {paso !== 'resultado' && (
          <Boton variante="secundario" onClick={() => onTerminar(false)}>
            Cancelar
          </Boton>
        )}
      </div>

      {paso === 'cliente' && (
        <PasoCliente
          onElegido={(c) => {
            setCliente(c);
            setPaso('pago');
          }}
        />
      )}

      {paso === 'pago' && cliente && (
        <div className="vista" style={tarjeta}>
          <h3 style={subtituloPaso}>¿Cómo pagó {cliente.nombre_mostrar}?</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            {[
              ['EFECTIVO', 'Efectivo'],
              ['TARJETA', 'Tarjeta'],
            ].map(([m, texto]) => (
              <Boton
                key={m}
                style={{ padding: '24px', fontSize: '16px' }}
                onClick={() => {
                  setMedio(m);
                  setPaso('resumen');
                }}
              >
                {texto}
              </Boton>
            ))}
          </div>
          <button type="button" onClick={() => setPaso('cliente')} className="enlace" style={{ marginTop: '14px', background: 'none', border: 'none', color: 'var(--text-secondary)', textDecoration: 'underline', fontSize: '12px', cursor: 'pointer' }}>
            Cambiar cliente
          </button>
        </div>
      )}

      {(paso === 'resumen' || paso === 'resultado') && cliente && medio && (
        <Emision venta={venta} cliente={cliente} medio={medio} onAtras={() => setPaso('pago')} onEmitida={() => setPaso('resultado')} onTerminar={() => onTerminar(true)} />
      )}
    </div>
  );
}

/** Identificar al comprador: buscar por documento y, si no existe, registrarlo con su autorización. */
function PasoCliente({ onElegido }) {
  const [tipo, setTipo] = useState('CC');
  const [numero, setNumero] = useState('');
  const [buscado, setBuscado] = useState(null);
  const [resultado, setResultado] = useState(undefined); // undefined = sin buscar; null = no existe
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState(null);
  const [registrando, setRegistrando] = useState(false);
  const problema = numero ? problemaNumero(tipo, numero) : null;
  const dv = tipo === 'NIT' ? calcularDv(numero.trim()) : null;

  const buscar = async (e) => {
    e.preventDefault();
    if (!numero || problema) return;
    const b = { tipo, numero: numero.trim() };
    setBuscado(b);
    setError(null);
    setCargando(true);
    try {
      setResultado(await api.buscarCliente(b.tipo, b.numero));
    } catch (err) {
      setError(err);
    } finally {
      setCargando(false);
    }
  };

  const registrar = async (entrada) => {
    setRegistrando(true);
    setError(null);
    try {
      onElegido(await api.crearCliente(entrada));
    } catch (err) {
      // Otro equipo lo registró mientras tanto: usar ese.
      if (err instanceof ErrorApi && err.estado === 409) {
        const c = await api.buscarCliente(buscado.tipo, buscado.numero).catch(() => null);
        if (c) return onElegido(c);
      }
      setError(err);
    } finally {
      setRegistrando(false);
    }
  };

  return (
    <div className="vista" style={tarjeta}>
      <h3 style={subtituloPaso}>¿A nombre de quién?</h3>
      <form onSubmit={buscar} noValidate>
        <Rejilla columnas="1fr 1.5fr auto">
          <Campo etiqueta="Tipo" id="pc_tipo">
            <select
              id="pc_tipo"
              value={tipo}
              onChange={(e) => {
                setTipo(e.target.value);
                setResultado(undefined);
              }}
              style={estilos.entrada}
            >
              {TIPOS_DOCUMENTO.map((t) => (
                <option key={t} value={t}>
                  {NOMBRE_TIPO[t]}
                </option>
              ))}
            </select>
          </Campo>
          <Campo etiqueta={tipo === 'NIT' ? 'NIT (sin DV)' : 'Número'} id="pc_numero" error={problema || undefined} ayuda={dv !== null && !problema ? `DV ${dv}` : undefined}>
            <input
              id="pc_numero"
              autoFocus
              inputMode={tipo === 'CC' || tipo === 'NIT' ? 'numeric' : 'text'}
              autoComplete="off"
              value={numero}
              onChange={(e) => {
                setNumero(e.target.value);
                setResultado(undefined);
              }}
              style={estilos.entrada}
            />
          </Campo>
          <Boton type="submit" disabled={!numero || !!problema || cargando}>
            {cargando ? 'Buscando…' : 'Buscar'}
          </Boton>
        </Rejilla>
      </form>

      <div style={{ marginTop: '16px' }}>
        <MostrarError error={error} />
      </div>

      {resultado && (
        <div className="aviso" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap', border: '1px solid var(--text-primary)', padding: '16px', borderRadius: '6px' }}>
          <div>
            <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0 }}>
              {NOMBRE_TIPO[resultado.tipo_documento] || resultado.tipo_documento} {resultado.numero_documento}
              {resultado.digito_verificacion !== null && `-${resultado.digito_verificacion}`}
            </p>
            <p style={{ fontSize: '18px', fontWeight: '700', color: 'var(--text-primary)', margin: '4px 0' }}>{resultado.nombre_mostrar}</p>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>Factura a: {resultado.email_enmascarado}</p>
          </div>
          <Boton onClick={() => onElegido(resultado)} style={{ padding: '12px 18px' }}>
            Usar este cliente
          </Boton>
        </div>
      )}

      {resultado === null && buscado && (
        <div>
          <Mensaje tipo="info" titulo="No está registrado">
            Regístrelo aquí con su autorización de datos, o pídale que use el QR de registro.
          </Mensaje>
          <FormularioCliente key={`${buscado.tipo}-${buscado.numero}`} fijo={buscado} enviando={registrando} textoEnviar="Registrar y continuar" onEnviar={registrar} />
        </div>
      )}
    </div>
  );
}

const ESTADO = {
  EN_PROCESO: { color: COLOR.info, titulo: 'Factura creada', texto: 'Se está enviando en segundo plano. Puede atender al siguiente cliente.' },
  FACTURADO: { color: COLOR.exito, titulo: 'Factura validada' }, // el texto depende de la entrega por correo
  RECHAZADO: { color: COLOR.error, titulo: 'Factura rechazada', texto: 'La venta vuelve a quedar pendiente. Revise el motivo con el administrador antes de reintentar.' },
  INCIERTO: { color: COLOR.alerta, titulo: 'Estado por confirmar', texto: 'No se sabe todavía si la factura llegó. El sistema lo concilia solo: NO la vuelva a emitir.' },
};

function Emision({ venta, cliente, medio, onAtras, onEmitida, onTerminar }) {
  // UNA clave por intento: se reusa en cada reintento para que la API no cree dos facturas.
  const [clave] = useState(nuevaClave);
  const [factura, setFactura] = useState(null);
  const [nueva, setNueva] = useState(true);
  const [error, setError] = useState(null);
  const [emitiendo, setEmitiendo] = useState(false);
  const [pdf, setPdf] = useState({ cargando: false, error: null });

  const [consultas, setConsultas] = useState(0);

  // Cada 2 s mientras la factura está EN_PROCESO y, ya validada, hasta que el correo sale o falla (con límite).
  useEffect(() => {
    if (!factura || !seguirConsultando(factura, consultas)) return undefined;
    const t = setTimeout(async () => {
      if (factura.estado === 'FACTURADO') setConsultas((n) => n + 1);
      try {
        setFactura(await api.factura(factura.id));
      } catch {
        setFactura({ ...factura });
      }
    }, 2000);
    return () => clearTimeout(t);
  }, [factura, consultas]);

  const emitir = async () => {
    if (emitiendo) return;
    setEmitiendo(true);
    setError(null);
    try {
      const f = await api.emitir({ clave, origen_tipo: venta.origen_tipo, origen_id: venta.origen_id, cliente_id: cliente.id, medio_pago: medio });
      setNueva(f.nueva !== false);
      setFactura(f);
      onEmitida();
    } catch (err) {
      setError(err);
    } finally {
      setEmitiendo(false);
    }
  };

  const descargarPdf = async () => {
    setPdf({ cargando: true, error: null });
    try {
      guardarArchivo(await api.facturaPdf(factura.id), `factura_${factura.numero}.pdf`);
      setPdf({ cargando: false, error: null });
    } catch (err) {
      setPdf({ cargando: false, error: err });
    }
  };

  const fila = (titulo, valor, grande) => (
    <>
      <dt style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>{titulo}</dt>
      <dd style={{ margin: 0, color: 'var(--text-primary)', fontSize: grande ? '22px' : '13px', fontWeight: grande ? '700' : '400' }}>{valor}</dd>
    </>
  );

  if (factura) {
    const e = ESTADO[factura.estado];
    return (
      <div className="vista" style={tarjeta}>
        {/* Al cambiar de estado (en proceso → validada) el recuadro vuelve a entrar para que se note. */}
        <div key={factura.estado} className="aviso" style={{ border: `1px solid ${e.color}`, padding: '12px 14px', borderRadius: '4px', marginBottom: '16px' }}>
          <div style={{ color: e.color, fontWeight: '700', fontSize: '14px' }}>
            {e.titulo}: {factura.numero}
          </div>
          <div style={{ color: 'var(--text-primary)', fontSize: '12px', marginTop: '4px' }}>
            {factura.estado === 'FACTURADO' ? avisoEntrega(factura.entrega, cliente.email_enmascarado) : e.texto}
          </div>
          {factura.error && <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '4px' }}>Motivo: {factura.error}</div>}
          {!nueva && <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '4px' }}>Esta venta ya tenía esta factura (no se creó otra).</div>}
        </div>
        <dl style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 24px', margin: '0 0 16px 0' }}>
          {fila('Cliente', factura.cliente.nombre_mostrar)}
          {fila('Total', pesos(factura.total))}
          {factura.cufe && fila('CUFE', <span style={{ fontFamily: 'monospace', fontSize: '11px', wordBreak: 'break-all' }}>{factura.cufe}</span>)}
        </dl>
        <MostrarError error={pdf.error} />
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          {factura.estado === 'FACTURADO' && (
            <Boton variante="secundario" disabled={pdf.cargando} onClick={descargarPdf}>
              {pdf.cargando ? 'Generando PDF...' : 'Descargar PDF'}
            </Boton>
          )}
          <Boton onClick={onTerminar} style={{ padding: '10px 18px' }}>
            Siguiente venta
          </Boton>
        </div>
      </div>
    );
  }

  // Sin respuesta, o error del servidor que no es una negativa clara: no se sabe si la factura quedó creada.
  const dudoso = error instanceof ErrorApi && (error.estado === 0 || (error.estado >= 500 && error.estado !== 503));
  return (
    <div className="vista" style={tarjeta}>
      <h3 style={subtituloPaso}>Revise antes de emitir</h3>
      <dl style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '8px 24px', margin: '0 0 16px 0' }}>
        {fila(cliente.tipo_documento === 'NIT' ? 'Razón social' : 'Cliente', <strong>{cliente.nombre_mostrar}</strong>)}
        {fila('Documento', `${cliente.tipo_documento} ${cliente.numero_documento}${cliente.digito_verificacion !== null ? `-${cliente.digito_verificacion}` : ''}`)}
        {fila('Correo', cliente.email_enmascarado)}
        {fila('Producto', `${venta.producto} · Lado ${venta.lado}`)}
        {fila('Galones', galones(venta.volumen))}
        {venta.ppu && fila('Precio por galón', pesos(venta.ppu))}
        {fila('Medio de pago', medio === 'EFECTIVO' ? 'Efectivo' : 'Tarjeta')}
        {fila('Total', pesos(venta.valor), true)}
      </dl>
      {dudoso ? (
        <Mensaje tipo="alerta" titulo="No se sabe si la factura quedó creada">
          {error.message} Reintentar es seguro: no se crea una segunda factura.
        </Mensaje>
      ) : (
        <MostrarError error={error} />
      )}
      <div style={{ display: 'flex', gap: '10px' }}>
        <Boton variante="secundario" onClick={error instanceof ErrorApi && error.estado === 409 ? onTerminar : onAtras} disabled={emitiendo || dudoso}>
          {error instanceof ErrorApi && error.estado === 409 ? 'Volver a la lista' : 'Atrás'}
        </Boton>
        <Boton onClick={emitir} disabled={emitiendo} style={{ flex: 1, padding: '12px' }}>
          {emitiendo ? 'Emitiendo…' : dudoso ? 'Reintentar' : 'Emitir factura'}
        </Boton>
      </div>
    </div>
  );
}
