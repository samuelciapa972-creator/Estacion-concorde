import { useState, useEffect } from 'react';
import { Plus, RefreshCw } from 'lucide-react';
import { obtenerDespachos, obtenerCatalogos } from '../services/api';
import { galones, pesos, oracion } from '../lib/formato';
import { useCarga } from '../lib/useCarga';
import FacturarVenta from '../components/FacturarVenta';
import VentaManual from '../components/VentaManual';
import { Boton, Cargando, Encabezado, Mensaje, Rodillo, Segmentado } from '../components/ui';

// Un pendiente es un despacho del surtidor o una venta manual: se identifica por los dos campos.
const claveDe = (d) => `${d.origen_tipo}-${d.origen_id}`;
const hora = (fecha) => new Date(fecha).toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' });

/** "Actualizado hace 4 s": cuánto hace que llegó el último dato de la pista. */
function Actualizado({ en, caido }) {
  const [ahora, setAhora] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setAhora(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const s = en ? Math.max(0, Math.round((ahora - en) / 1000)) : null;
  const hace = s === null ? '' : s < 60 ? `hace ${s} s` : `hace ${Math.floor(s / 60)} min`;
  return (
    <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 400, color: 'var(--text-secondary)' }}>
      {caido && <i style={{ width: '7px', height: '7px', borderRadius: '50%', background: 'var(--color-error, #c0392b)' }} aria-hidden="true" />}
      {caido ? `Sin conexión con la pista${hace && ` · último dato ${hace}`}` : hace && `Actualizado ${hace}`}
    </span>
  );
}

/** Un despacho por facturar, con el display del surtidor (monto y galones) y su placa. */
function Despacho({ venta, nuevo, onFacturar }) {
  return (
    <article className={nuevo ? 'surtidor surtidor--nuevo' : 'surtidor'}>
      <div className="surtidor__cabeza">
        <span className="surtidor__producto">{oracion(venta.producto)}</span>
        <time dateTime={venta.fecha}>{hora(venta.fecha)}</time>
      </div>
      <div className="display">
        <span className="display__etiqueta">
          {venta.surtidor}
          {venta.origen_tipo === 'venta_manual' && ' · manual'}
          {venta.forma_pago === 'CREDITO' && ' · crédito'}
        </span>
        <span className="display__monto">
          <Rodillo texto={pesos(venta.valor)} />
        </span>
        <div className="display__fila">
          <span>{galones(venta.volumen)} gal</span>
          {venta.ppu && <span>{pesos(venta.ppu)}/gal</span>}
        </div>
      </div>
      <div className="surtidor__pie">
        {venta.placa ? <span className="placa">{venta.placa}</span> : <span className="placa placa--sin">Sin placa</span>}
        <Boton onClick={() => onFacturar(venta)} style={{ padding: '10px 18px' }}>
          Facturar
        </Boton>
      </div>
    </article>
  );
}

export default function PosVentas() {
  // Venta elegida para facturar, o la pantalla de venta manual.
  const [facturando, setFacturando] = useState(null);
  const [manual, setManual] = useState(false);
  const [surtidores, setSurtidores] = useState([]);
  const [surtidor, setSurtidor] = useState(null);
  const [lado, setLado] = useState(null);

  // Pendientes cada 5 s mientras se ve la lista (no mientras se factura o se digita una venta manual).
  const pendientes = useCarga(() => obtenerDespachos({ surtidor_id: surtidor, lado }), [surtidor, lado, facturando, manual], {
    cadaMs: facturando || manual ? undefined : 5000,
  });
  const despachos = pendientes.datos || [];
  const cargando = pendientes.cargando && !pendientes.datos;
  const error = pendientes.error ? 'No se pudo conectar con el surtidor / backend.' : '';
  // Despachos que no estaban en la consulta anterior: se iluminan al llegar (no en la primera carga).
  const anteriores = pendientes.previos ? new Set(pendientes.previos.map(claveDe)) : null;

  useEffect(() => {
    obtenerCatalogos().then((c) => setSurtidores(c.surtidores || [])).catch(() => setSurtidores([]));
  }, []);

  if (facturando) {
    return <FacturarVenta venta={facturando} onTerminar={() => setFacturando(null)} />;
  }
  if (manual) {
    return (
      <VentaManual
        onCancelar={() => setManual(false)}
        onGuardada={(venta) => {
          setManual(false);
          setFacturando(venta);
        }}
      />
    );
  }

  // Una columna por lado de la isla (o solo la del lado elegido).
  const lados = lado ? [lado] : [...new Set(['A', 'B', ...despachos.map((d) => d.lado)])];

  return (
    <div className="vista pagina">
      <Encabezado
        titulo={
          <span style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            Ventas por facturar
            {/* La lista se consulta cada 5 s. */}
            <Actualizado en={pendientes.actualizado} caido={!!error} />
          </span>
        }
        subtitulo="Despachos que llegan de la pista"
        grande
      >
        <Boton variante="secundario" onClick={pendientes.recargar} aria-label="Actualizar" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <RefreshCw size={14} strokeWidth={2} aria-hidden="true" /> Actualizar
        </Boton>
        <Boton onClick={() => setManual(true)} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px' }}>
          <Plus size={14} strokeWidth={2.5} aria-hidden="true" /> Venta manual
        </Boton>
      </Encabezado>

      <div style={{ display: 'flex', gap: '12px', marginBottom: '24px', flexWrap: 'wrap', alignItems: 'center' }}>
        <Segmentado opciones={[[null, 'Todos'], ...surtidores.map((s) => [s.id, s.marca])]} valor={surtidor} onCambio={setSurtidor} />
        <Segmentado opciones={[[null, 'Ambos lados'], ['A', 'Lado A'], ['B', 'Lado B']]} valor={lado} onCambio={setLado} />
      </div>

      <Mensaje tipo="error">{error}</Mensaje>

      {cargando ? (
        <Cargando texto="Cargando despachos..." />
      ) : (
        <div className="pista" style={{ '--lados': lados.length }}>
          {lados.map((l) => {
            const delLado = despachos.filter((d) => d.lado === l);
            return (
              <section key={l} aria-label={`Lado ${l}`}>
                <h2 className="pista__lado-titulo">
                  Lado {l}
                  <span>
                    <b>{delLado.length}</b> por facturar
                  </span>
                </h2>
                {delLado.length === 0 ? (
                  <p className="pista__vacio">Sin despachos pendientes en el lado {l}.</p>
                ) : (
                  <div className="pista__tarjetas">
                    {delLado.map((d) => (
                      <Despacho key={claveDe(d)} venta={d} nuevo={anteriores && !anteriores.has(claveDe(d))} onFacturar={setFacturando} />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
