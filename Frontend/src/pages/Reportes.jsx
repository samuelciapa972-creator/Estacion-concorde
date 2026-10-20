import { useState, useEffect } from 'react';

import { api, obtenerCatalogos } from '../services/api';
import { guardarArchivo } from '../lib/descarga';
import { fecha, fechaHora, galones, hoyIso, pesos, rangoHastaHoy, sumar, oracion } from '../lib/formato';
import { useCarga } from '../lib/useCarga';
import { Boton, Campo, Celda, ConSecciones, Encabezado, Etiqueta, ErrorApi, Indicador, Mensaje, Rejilla, Segmentado, Tabla, Vacio, Cargando } from '../components/ui';
import { COLOR, estilos } from '../components/estilos';

const SECCIONES = [
  ['periodo', 'Ventas del periodo'],
  ['resumen', 'Resumen y descargas'],
  ['contador', 'Exportar al contador'],
  ['anomalias', 'Anomalías'],
];

export default function Reportes() {
  const [seccion, setSeccion] = useState('periodo');
  return (
    <ConSecciones secciones={SECCIONES} seccion={seccion} onSeccion={setSeccion}>
      {seccion === 'periodo' && <VentasDelPeriodo />}
      {seccion === 'resumen' && <Resumen />}
      {seccion === 'contador' && <Contador />}
      {seccion === 'anomalias' && <Anomalias />}
    </ConSecciones>
  );
}

const PERIODOS = [['hoy', 'Diario'], ['semana', 'Semanal'], ['mes', 'Mensual']];

/** Pantalla original del diseñador: ventas del periodo con sus indicadores. */
function VentasDelPeriodo() {
  const [periodo, setPeriodo] = useState('hoy');
  const ventas = useCarga(() => api.ventas({ ...rangoHastaHoy(periodo), limite: 500 }), [periodo]);
  const filas = ventas.datos?.filas || [];
  const total = ventas.datos?.total; // del periodo completo (la tabla muestra hasta 500 filas)

  return (
    <div>
      <Encabezado titulo="Ventas del periodo" subtitulo="Consolidado de ventas de pista">
        <Segmentado opciones={PERIODOS} valor={periodo} onCambio={setPeriodo} />
      </Encabezado>
      <ErrorApi error={ventas.error} />

      <div className="rejilla-indicadores" style={{ marginBottom: '24px' }}>
        <Indicador titulo="Total recaudado" valor={pesos(total?.valor ?? '0')} />
        <Indicador titulo="Volumen total" valor={`${galones(total?.galones ?? '0')} gal`} />
        <Indicador titulo="Operaciones" valor={String(total?.ventas ?? 0)} />
      </div>

      {ventas.cargando && !ventas.datos ? (
        <Cargando texto="Cargando reporte..." />
      ) : filas.length === 0 ? (
        <Vacio>Sin registros para este periodo.</Vacio>
      ) : (
        <Tabla columnas={['Fecha / hora', 'Equipo', 'Producto', 'Placa', 'Galones', { texto: 'Monto', alinear: 'right' }]}>
          {filas.map((v) => (
            <tr key={`${v.origen_tipo}-${v.origen_id}`} style={estilos.fila}>
              <Celda suave>{fechaHora(v.fecha)}</Celda>
              <Celda style={{ fontWeight: '600' }}>{v.surtidor} - Lado {v.lado}</Celda>
              <Celda>{oracion(v.producto)}</Celda>
              <Celda style={{ fontWeight: '600' }}>{v.placa || '---'}</Celda>
              <Celda>{galones(v.volumen)} gal</Celda>
              <Celda alinear="right" fuerte>{pesos(v.valor)}</Celda>
            </tr>
          ))}
        </Tabla>
      )}
    </div>
  );
}

function primeroDelMes() {
  return hoyIso().slice(0, 8) + '01';
}

const NOMBRE_TIPO_REPORTE = { diario: 'Diario', mensual: 'Mensual', turno: 'Por turno' };

/** Resumen agregado (por día, mes o turno) con descargas en PDF y Excel del surtidor. */
function Resumen() {
  const [tipo, setTipo] = useState('diario');
  const [rango, setRango] = useState({ desde: primeroDelMes(), hasta: hoyIso(), surtidor_id: '' });
  const [surtidores, setSurtidores] = useState([]);
  const [descarga, setDescarga] = useState({ cual: null, error: null });
  const filtro = { desde: rango.desde, hasta: rango.hasta, surtidor_id: rango.surtidor_id || null };
  const reporte = useCarga(() => api.reporte(tipo, filtro), [tipo, rango.desde, rango.hasta, rango.surtidor_id]);
  const duplicados = useCarga(() => api.posiblesDuplicados(filtro), [rango.desde, rango.hasta]);

  useEffect(() => {
    obtenerCatalogos().then((c) => setSurtidores(c.surtidores || [])).catch(() => setSurtidores([]));
  }, []);

  const nombreSurtidor = (id) => surtidores.find((s) => s.id === id)?.marca || `#${id}`;
  const filas = reporte.datos || [];
  const descargar = async (cual, pedir, nombre) => {
    setDescarga({ cual, error: null });
    try {
      guardarArchivo(await pedir(), nombre);
      setDescarga({ cual: null, error: null });
    } catch (err) {
      setDescarga({ cual: null, error: err });
    }
  };
  const cambiar = (campo) => (e) => setRango({ ...rango, [campo]: e.target.value });
  const dup = duplicados.datos || [];

  return (
    <div>
      <Encabezado titulo="Resumen de ventas" subtitulo="Por día, por mes o por turno, con sus descargas">
        <Segmentado opciones={Object.entries(NOMBRE_TIPO_REPORTE)} valor={tipo} onCambio={setTipo} />
      </Encabezado>

      <div style={{ ...estilos.caja, padding: '16px', marginBottom: '16px' }}>
        <Rejilla columnas="repeat(auto-fill, minmax(170px, 1fr))">
          <Campo etiqueta="Desde" id="r_desde">
            <input id="r_desde" type="date" value={rango.desde} onChange={cambiar('desde')} style={estilos.entrada} />
          </Campo>
          <Campo etiqueta="Hasta" id="r_hasta">
            <input id="r_hasta" type="date" value={rango.hasta} onChange={cambiar('hasta')} style={estilos.entrada} />
          </Campo>
          <Campo etiqueta="Surtidor" id="r_surtidor">
            <select id="r_surtidor" value={rango.surtidor_id} onChange={cambiar('surtidor_id')} style={estilos.entrada}>
              <option value="">Todos</option>
              {surtidores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.marca}
                </option>
              ))}
            </select>
          </Campo>
          <Boton variante="secundario" disabled={descarga.cual === 'pdf'} onClick={() => descargar('pdf', () => api.reportePdf(tipo, filtro), `ventas_${tipo}_${rango.desde}_${rango.hasta}.pdf`)}>
            {descarga.cual === 'pdf' ? 'Generando...' : 'Descargar PDF'}
          </Boton>
          <Boton
            variante="secundario"
            disabled={!rango.surtidor_id || descarga.cual === 'excel'}
            title={rango.surtidor_id ? undefined : 'Elija un surtidor: el Excel analiza un surtidor a la vez'}
            onClick={() => descargar('excel', () => api.exportarExcel({ ...filtro }), `ventas_surtidor${rango.surtidor_id}_${rango.desde}_${rango.hasta}.xlsx`)}
          >
            {descarga.cual === 'excel' ? 'Generando...' : 'Excel del surtidor'}
          </Boton>
        </Rejilla>
      </div>

      <ErrorApi error={reporte.error || descarga.error || duplicados.error} />

      {dup.length > 0 && (
        <Mensaje tipo="alerta" titulo={`${dup.length} venta(s) manual(es) podría(n) estar contada(s) dos veces`}>
          Coinciden con un despacho del surtidor (mismo lado, producto, galones y valor, a menos de 2 horas):
          <ul style={{ margin: '6px 0 0 18px' }}>
            {dup.map((x) => (
              <li key={`${x.venta_manual_id}-${x.despacho_id}`}>
                Venta manual {x.venta_manual_id} ({fechaHora(x.registrada_en)}) y despacho {x.despacho_id} ({fechaHora(x.inicio_despacho)}): lado {x.lado},{' '}
                {x.producto}, {galones(x.galones)} gal, {pesos(x.valor)}
              </li>
            ))}
          </ul>
        </Mensaje>
      )}

      {reporte.datos && (
        <div className="rejilla-indicadores" style={{ marginBottom: '16px' }}>
          <Indicador titulo="Total recaudado" valor={pesos(sumar(filas.map((f) => f.valor), 2))} />
          <Indicador titulo="Volumen total" valor={`${galones(sumar(filas.map((f) => f.galones), 3))} gal`} />
          <Indicador titulo="Operaciones" valor={String(filas.reduce((n, f) => n + f.despachos, 0))} />
        </div>
      )}

      {reporte.cargando && !reporte.datos ? (
        <Cargando texto="Cargando reporte..." />
      ) : filas.length === 0 ? (
        <Vacio>Sin registros para este periodo.</Vacio>
      ) : tipo === 'turno' ? (
        <Tabla columnas={['Cierre', 'Surtidor', 'Lado', 'Estado', 'Desde', 'Hasta', { texto: 'Despachos', alinear: 'right' }, { texto: 'Galones', alinear: 'right' }, { texto: 'Valor', alinear: 'right' }]}>
          {filas.map((f) => (
            <tr key={`${f.turno_id}-${f.lado}`} style={estilos.fila}>
              <Celda fuerte>{f.id_cierre}</Celda>
              <Celda>{nombreSurtidor(f.surtidor_id)}</Celda>
              <Celda>{f.lado}</Celda>
              <Celda>
                <Etiqueta color={f.estado === 'ABIERTO' ? COLOR.exito : 'var(--text-secondary)'}>{f.estado}</Etiqueta>
              </Celda>
              <Celda suave>{fechaHora(f.inicio)}</Celda>
              <Celda suave>{fechaHora(f.fin)}</Celda>
              <Celda alinear="right">{f.despachos}</Celda>
              <Celda alinear="right">{galones(f.galones)}</Celda>
              <Celda alinear="right" fuerte>{pesos(f.valor)}</Celda>
            </tr>
          ))}
        </Tabla>
      ) : (
        <Tabla
          columnas={[tipo === 'mensual' ? 'Mes' : 'Día', 'Surtidor', 'Producto', 'Pago', 'Origen', { texto: 'Ventas', alinear: 'right' }, { texto: 'Galones', alinear: 'right' }, { texto: 'Valor', alinear: 'right' }]}
          pie={
            <tr style={{ color: 'var(--text-primary)', fontWeight: '700' }}>
              <Celda colSpan={5}>Total</Celda>
              <Celda alinear="right">{filas.reduce((n, f) => n + f.despachos, 0)}</Celda>
              <Celda alinear="right">{galones(sumar(filas.map((f) => f.galones), 3))}</Celda>
              <Celda alinear="right">{pesos(sumar(filas.map((f) => f.valor), 2))}</Celda>
            </tr>
          }
        >
          {filas.map((f) => (
            <tr key={`${f.periodo}-${f.surtidor_id}-${f.producto}-${f.forma_pago}-${f.origen}`} style={estilos.fila}>
              <Celda suave>{tipo === 'mensual' ? f.periodo.slice(0, 7) : fecha(f.periodo)}</Celda>
              <Celda fuerte>{nombreSurtidor(f.surtidor_id)}</Celda>
              <Celda>{oracion(f.producto)}</Celda>
              <Celda>{oracion(f.forma_pago)}</Celda>
              <Celda>{oracion(f.origen)}</Celda>
              <Celda alinear="right">{f.despachos}</Celda>
              <Celda alinear="right">{galones(f.galones)}</Celda>
              <Celda alinear="right" fuerte>{pesos(f.valor)}</Celda>
            </tr>
          ))}
        </Tabla>
      )}
      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '12px' }}>
        {tipo === 'turno'
          ? 'Por turno solo cuentan los despachos del surtidor: las ventas manuales no tienen turno.'
          : 'Suma los despachos del surtidor y las ventas manuales sin enlace (columna Origen); una manual enlazada a su despacho no se cuenta dos veces.'}
      </p>
    </div>
  );
}

/** Exportación de ventas y facturas para la contabilidad (formato provisional). */
function Contador() {
  const [rango, setRango] = useState({ desde: primeroDelMes(), hasta: hoyIso() });
  const [formato, setFormato] = useState('xlsx');
  const [estado, setEstado] = useState({ cargando: false, error: null, listo: false });

  const exportar = async () => {
    setEstado({ cargando: true, error: null, listo: false });
    try {
      guardarArchivo(await api.contador(rango, formato), `contador_${rango.desde}_${rango.hasta}.${formato}`);
      setEstado({ cargando: false, error: null, listo: true });
    } catch (err) {
      setEstado({ cargando: false, error: err, listo: false });
    }
  };

  return (
    <div style={{ maxWidth: '560px' }}>
      <Encabezado titulo="Exportar al contador" subtitulo="Facturas del rango (con datos de clientes) y el resumen diario" />
      <ErrorApi error={estado.error} />
      {estado.listo && <Mensaje tipo="exito">Archivo descargado. La descarga quedó registrada.</Mensaje>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <Rejilla columnas="1fr 1fr">
          <Campo etiqueta="Desde" id="c_desde">
            <input id="c_desde" type="date" value={rango.desde} onChange={(e) => setRango({ ...rango, desde: e.target.value })} style={estilos.entrada} />
          </Campo>
          <Campo etiqueta="Hasta" id="c_hasta">
            <input id="c_hasta" type="date" value={rango.hasta} onChange={(e) => setRango({ ...rango, hasta: e.target.value })} style={estilos.entrada} />
          </Campo>
        </Rejilla>
        <Campo etiqueta="Formato" id="c_formato">
          <select id="c_formato" value={formato} onChange={(e) => setFormato(e.target.value)} style={estilos.entrada}>
            <option value="xlsx">Excel</option>
            <option value="csv">CSV (;)</option>
            <option value="pdf">PDF</option>
          </select>
        </Campo>
        <Boton onClick={exportar} disabled={estado.cargando} style={{ marginTop: '8px' }}>
          {estado.cargando ? 'Generando...' : 'Exportar'}
        </Boton>
        <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
          Formato provisional hasta que el contador defina el suyo. El archivo lleva datos personales de los clientes: manéjelo con cuidado.
        </p>
      </div>
    </div>
  );
}

const SEVERIDAD = { ALTA: COLOR.error, AVISO: COLOR.alerta, INFO: COLOR.info };

/** Anomalías de los datos del surtidor para revisión humana. */
function Anomalias() {
  const [estado, setEstado] = useState('abiertas');
  const [severidad, setSeveridad] = useState(null);
  const { datos, error, cargando } = useCarga(() => api.anomalias(estado, severidad), [estado, severidad]);
  const lista = datos || [];

  return (
    <div>
      <Encabezado titulo="Anomalías" subtitulo="Datos del surtidor para revisar">
        <Segmentado opciones={[['abiertas', 'Abiertas'], ['resueltas', 'Resueltas'], ['todas', 'Todas']]} valor={estado} onCambio={setEstado} />
      </Encabezado>
      <div style={{ marginBottom: '16px', display: 'flex' }}>
        <Segmentado opciones={[[null, 'Toda severidad'], ['ALTA', 'Alta'], ['AVISO', 'Aviso'], ['INFO', 'Info']]} valor={severidad} onCambio={setSeveridad} />
      </div>
      <ErrorApi error={error} />
      {cargando && !datos ? (
        <Cargando />
      ) : lista.length === 0 ? (
        <Vacio>No hay anomalías.</Vacio>
      ) : (
        <Tabla columnas={['Severidad', 'Tipo', 'Despacho', 'Fecha', 'Detalle']}>
          {lista.map((a) => (
            <tr key={a.id} style={estilos.fila}>
              <Celda>
                <Etiqueta color={SEVERIDAD[a.severidad]}>{a.severidad}</Etiqueta>
              </Celda>
              <Celda fuerte>{a.tipo}</Celda>
              <Celda suave>{a.despacho_id_externo ?? '—'}</Celda>
              <Celda suave style={{ whiteSpace: 'nowrap' }}>{fechaHora(a.creada_en)}</Celda>
              <Celda>
                <code style={{ fontSize: '11px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {Object.entries(a.detalle || {})
                    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
                    .join(' · ')}
                </code>
              </Celda>
            </tr>
          ))}
        </Tabla>
      )}
    </div>
  );
}
