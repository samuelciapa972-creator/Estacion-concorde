import { useRef, useState } from 'react';
import { api, usuarioActual } from '../services/api';
import { guardarArchivo } from '../lib/descarga';
import { imprimir } from '../lib/impresion';
import { fechaHora, pesos, rangoHastaHoy } from '../lib/formato';
import { avisoNotaEnviada, etiquetaDocumento, puedeAnular } from '../lib/anulacion';
import { useCarga } from '../lib/useCarga';
import { Boton, Celda, Encabezado, Etiqueta, ErrorApi, Mensaje, Paginador, Segmentado, Tabla, Vacio, Cargando } from '../components/ui';
import { COLOR, estilos } from '../components/estilos';
import DialogoAnular from '../components/DialogoAnular';

const LIMITE = 100;

const ESTADOS = {
  FACTURADO: { texto: 'Validada', color: COLOR.exito },
  EN_PROCESO: { texto: 'En proceso', color: COLOR.info },
  INCIERTO: { texto: 'Por confirmar', color: COLOR.alerta },
  RECHAZADO: { texto: 'Rechazada', color: COLOR.error },
};

export default function Facturacion() {
  const [periodo, setPeriodo] = useState('mes');
  const [estado, setEstado] = useState(null);
  const [desde, setDesde] = useState(0);
  const [descarga, setDescarga] = useState({ id: null, error: null });
  const [aAnular, setAAnular] = useState(null);
  const [aviso, setAviso] = useState(null);
  const avisoRef = useRef(null);
  const usuario = usuarioActual();
  const { datos, error, cargando, recargar } = useCarga(
    () => api.facturas({ ...rangoHastaHoy(periodo), estado, limite: LIMITE, desplazamiento: desde }),
    [periodo, estado, desde],
  );
  const facturas = datos?.filas || [];

  const pdf = async (f) => {
    setDescarga({ id: f.id, error: null });
    try {
      guardarArchivo(await api.facturaPdf(f.id), `factura_${f.numero}.pdf`);
      setDescarga({ id: null, error: null });
    } catch (err) {
      setDescarga({ id: null, error: err });
    }
  };

  const recibo = async (f) => {
    setDescarga({ id: `r${f.id}`, error: null });
    try {
      const camino = await imprimir(await api.facturaRecibo(f.id));
      setDescarga({ id: null, error: null });
      if (camino === 'rawbt') setAviso(`Recibo de ${f.numero} enviado a RawBT.`);
    } catch (err) {
      setDescarga({ id: null, error: err });
    }
  };

  // Reenvía el MISMO ZIP al correo ACTUAL del cliente (p. ej. tras corregirlo en Clientes por un rebote).
  const reenviar = async (f) => {
    if (!window.confirm(`Reenviar ${f.numero} al correo actual del cliente?`)) return;
    setDescarga({ id: `c${f.id}`, error: null });
    try {
      await api.reenviar(f.id);
      setDescarga({ id: null, error: null });
      setAviso(`${f.numero}: reenvío en cola. Sale en unos segundos al correo actual del cliente.`);
    } catch (err) {
      setDescarga({ id: null, error: err });
    }
  };

  // Tras anular, la fila pierde su botón: el foco pasa al aviso para no perderse.
  const anulada = async (nota) => {
    setAAnular(null);
    setAviso(avisoNotaEnviada(nota));
    await recargar();
    avisoRef.current?.focus();
  };

  return (
    <div className="pagina">
      <Encabezado titulo="Facturas emitidas" subtitulo="Comprobantes y documentos emitidos">
        <Segmentado
          opciones={[['hoy', 'Hoy'], ['semana', '7 días'], ['mes', 'Este mes'], ['anio', '12 meses']]}
          valor={periodo}
          onCambio={(p) => {
            setPeriodo(p);
            setDesde(0);
          }}
        />
      </Encabezado>

      <div style={{ marginBottom: '16px', display: 'flex' }}>
        <Segmentado
          opciones={[[null, 'Todas'], ...Object.entries(ESTADOS).map(([k, v]) => [k, v.texto])]}
          valor={estado}
          onCambio={(e) => {
            setEstado(e);
            setDesde(0);
          }}
        />
      </div>

      <ErrorApi error={error || descarga.error} />
      {aviso && (
        <div ref={avisoRef} tabIndex={-1} style={{ outline: 'none' }}>
          <Mensaje tipo="info">{aviso}</Mensaje>
        </div>
      )}

      {cargando && !datos ? (
        <Cargando />
      ) : facturas.length === 0 ? (
        <Vacio>Sin registros de facturación</Vacio>
      ) : (
        <Tabla columnas={['N° factura', 'Fecha', 'Cliente', 'Vendedor', 'Estado', { texto: 'Total', alinear: 'right' }, { texto: 'Acciones', alinear: 'center' }]}>
          {facturas.map((f) => (
            <tr key={f.id} style={estilos.fila}>
              <Celda style={{ fontWeight: '600' }}>{f.numero}</Celda>
              <Celda suave style={{ whiteSpace: 'nowrap' }}>{fechaHora(f.fecha_emision)}</Celda>
              <Celda>
                {f.cliente_nombre}
                <span style={{ display: 'block', fontSize: '10px', color: 'var(--text-secondary)' }}>
                  {f.cliente_tipo} {f.cliente_documento}
                </span>
              </Celda>
              <Celda suave>{f.vendedor}</Celda>
              <Celda>
                <Etiqueta color={ESTADOS[f.estado]?.color}>{ESTADOS[f.estado]?.texto || f.estado}</Etiqueta>
                {etiquetaDocumento(f) && <span style={{ display: 'block', fontSize: '10px', color: 'var(--text-secondary)' }}>{etiquetaDocumento(f)}</span>}
                {f.error && <span style={{ display: 'block', fontSize: '10px', color: 'var(--text-secondary)', maxWidth: '260px' }}>{f.error}</span>}
              </Celda>
              <Celda alinear="right" fuerte>{f.tipo === 'NOTA_CREDITO' ? '−' : ''}{pesos(f.total)}</Celda>
              <Celda alinear="center">
                <div className="acciones-fila">
                {f.estado === 'FACTURADO' && (
                  <Boton variante="chico" onClick={() => pdf(f)} disabled={descarga.id === f.id} aria-label={`Descargar PDF de ${f.numero}`}>
                    {descarga.id === f.id ? '...' : 'PDF'}
                  </Boton>
                )}
                {f.estado === 'FACTURADO' && (
                  <Boton variante="chico" onClick={() => recibo(f)} disabled={descarga.id === `r${f.id}`} aria-label={`Imprimir el recibo de ${f.numero}`}>
                    {descarga.id === `r${f.id}` ? '...' : 'Recibo'}
                  </Boton>
                )}
                {f.estado === 'FACTURADO' && usuario?.rol === 'ADMIN' && (
                  <Boton variante="chico" onClick={() => reenviar(f)} disabled={descarga.id === `c${f.id}`} aria-label={`Reenviar por correo ${f.numero}`}>
                    {descarga.id === `c${f.id}` ? '...' : 'Reenviar'}
                  </Boton>
                )}
                {puedeAnular(f, usuario) && (
                  <Boton variante="chico" onClick={() => setAAnular(f)} aria-label={`Anular la factura ${f.numero}`}>
                    Anular
                  </Boton>
                )}
                </div>
              </Celda>
            </tr>
          ))}
        </Tabla>
      )}
      {aAnular && <DialogoAnular factura={aAnular} onCerrar={() => setAAnular(null)} onAnulada={anulada} />}
      {datos && <Paginador desde={desde} limite={LIMITE} total={datos.total} onCambio={setDesde} />}
    </div>
  );
}
