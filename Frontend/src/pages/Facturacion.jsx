import { useState } from 'react';
import { api } from '../services/api';
import { guardarArchivo } from '../lib/descarga';
import { fechaHora, pesos, rangoHastaHoy } from '../lib/formato';
import { useCarga } from '../lib/useCarga';
import { Boton, Celda, Encabezado, Etiqueta, ErrorApi, Paginador, Segmentado, Tabla, Vacio, Cargando } from '../components/ui';
import { COLOR, estilos } from '../components/estilos';

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
  const { datos, error, cargando } = useCarga(
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

      {cargando && !datos ? (
        <Cargando />
      ) : facturas.length === 0 ? (
        <Vacio>Sin registros de facturación</Vacio>
      ) : (
        <Tabla columnas={['N° factura', 'Fecha', 'Cliente', 'Vendedor', 'Estado', { texto: 'Total', alinear: 'right' }, { texto: 'PDF', alinear: 'center' }]}>
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
                {f.error && <span style={{ display: 'block', fontSize: '10px', color: 'var(--text-secondary)', maxWidth: '260px' }}>{f.error}</span>}
              </Celda>
              <Celda alinear="right" fuerte>{pesos(f.total)}</Celda>
              <Celda alinear="center">
                {f.estado === 'FACTURADO' && (
                  <Boton variante="chico" onClick={() => pdf(f)} disabled={descarga.id === f.id} aria-label={`Descargar PDF de ${f.numero}`}>
                    {descarga.id === f.id ? '...' : 'PDF'}
                  </Boton>
                )}
              </Celda>
            </tr>
          ))}
        </Tabla>
      )}
      {datos && <Paginador desde={desde} limite={LIMITE} total={datos.total} onCambio={setDesde} />}
    </div>
  );
}
