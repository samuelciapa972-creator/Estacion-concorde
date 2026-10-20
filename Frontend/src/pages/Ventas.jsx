import { useEffect, useState } from 'react';
import { ChevronDown, Search, SlidersHorizontal, X } from 'lucide-react';
import { api, obtenerCatalogos } from '../services/api';
import { fechaHora, galones, hoyIso, pesos, oracion } from '../lib/formato';
import { useCarga } from '../lib/useCarga';
import { Boton, Campo, Celda, Encabezado, ErrorApi, Mensaje, Paginador, Rejilla, Rodillo, Segmentado, Tabla, Vacio, Cargando } from '../components/ui';
import { COLOR, estilos } from '../components/estilos';

const LIMITE = 100;
const ESTADOS = ['PENDIENTE', 'EN_PROCESO', 'FACTURADO', 'INCIERTO', 'NO_FACTURABLE'];
const COLOR_ESTADO = { FACTURADO: COLOR.exito, PENDIENTE: 'var(--text-secondary)', EN_PROCESO: COLOR.info, INCIERTO: COLOR.alerta, RECHAZADO: COLOR.error };

const VACIO = { surtidor_id: '', lado: '', producto: '', forma_pago: '', estado_facturacion: '', placa: '', cliente: '', id_externo: '', factura: '', vendedor_id: '' };
// Los que van plegados bajo "Más filtros" (placa, documento y fechas quedan siempre a la vista).
const SECUNDARIOS = ['surtidor_id', 'lado', 'producto', 'forma_pago', 'estado_facturacion', 'vendedor_id', 'id_externo', 'factura'];

/** "AAAA-MM-DDT00:00" del día que está `dias` después de hoy (negativo: antes). */
function inicioDia(dias = 0) {
  const d = new Date();
  d.setDate(d.getDate() + dias);
  return `${hoyIso(d)}T00:00`;
}

/** Atajos de periodo: [desde, hasta) en hora local, como los pide la API. */
const PERIODOS = {
  hoy: () => ({ desde: inicioDia(0), hasta: inicioDia(1) }),
  ayer: () => ({ desde: inicioDia(-1), hasta: inicioDia(0) }),
  semana: () => ({ desde: inicioDia(-6), hasta: inicioDia(1) }),
  mes: () => ({ desde: `${hoyIso().slice(0, 8)}01T00:00`, hasta: inicioDia(1) }),
};

/** Convierte el formulario en el filtro de la API; devuelve un texto si hay un problema. */
function aFiltro(f) {
  if (!f.desde || !f.hasta) return 'indique desde y hasta';
  if (f.hasta <= f.desde) return '«Hasta» debe ser posterior a «Desde»';
  if (f.id_externo && !/^\d+$/.test(f.id_externo.trim())) return 'el número de venta debe ser un número';
  return {
    ...f,
    placa: f.placa.trim().toUpperCase(),
    factura: f.factura.trim().toUpperCase(),
    cliente: f.cliente.trim(),
    id_externo: f.id_externo.trim(),
  };
}

/** Estado de facturación: punto de color + texto (el color nunca va solo). */
function Estado({ estado }) {
  return (
    <span className="estado">
      <i style={{ background: COLOR_ESTADO[estado] || 'var(--text-secondary)' }} aria-hidden="true" />
      {oracion(estado)}
    </span>
  );
}

// Tonos de la tinta del display para la mezcla por producto: de más a menos valor (un solo matiz, sin colores).
const TONOS = ['rgba(250, 250, 250, 0.95)', 'rgba(250, 250, 250, 0.5)', 'rgba(250, 250, 250, 0.25)'];

/** Valor por producto (suma de sus mangueras), de mayor a menor; del cuarto en adelante se agrupa en "Otros". */
function porProducto(mangueras) {
  const suma = {};
  for (const m of mangueras) suma[m.producto] = (suma[m.producto] || 0) + Number(m.valor);
  const lista = Object.entries(suma).sort((a, b) => b[1] - a[1]);
  if (lista.length <= TONOS.length) return lista;
  const otros = lista.slice(TONOS.length - 1).reduce((n, [, v]) => n + v, 0);
  return [...lista.slice(0, TONOS.length - 1), ['Otros', otros]];
}

/** Barra partida con la mezcla por producto, dentro del display; cada parte lleva su rótulo (no solo el tono). */
function Mezcla({ mangueras, total }) {
  const partes = porProducto(mangueras);
  if (!partes.length || Number(total) <= 0) return null;
  const pct = (v) => Math.round((v / Number(total)) * 100);
  return (
    <div className="mezcla">
      <span className="display__etiqueta">Por producto</span>
      <div className="mezcla__barra" role="img" aria-label={partes.map(([p, v]) => `${p} ${pct(v)}%`).join(', ')}>
        {partes.map(([p, v], i) => (
          <span key={p} style={{ flexGrow: v, background: TONOS[i] }} />
        ))}
      </div>
      <ul className="mezcla__rotulos">
        {partes.map(([p, v], i) => (
          <li key={p}>
            <i style={{ background: TONOS[i] }} aria-hidden="true" />
            {oracion(p)} <b>{pct(v)}%</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Participación de cada manguera en el valor del periodo: una barra por manguera, del mismo tono. */
function PorManguera({ filas, total, nombreSurtidor }) {
  const mayor = Math.max(...filas.map((m) => Number(m.valor)), 1);
  const ordenadas = [...filas].sort((a, b) => Number(b.valor) - Number(a.valor));
  return (
    <section style={{ ...estilos.caja, padding: '18px 20px' }} aria-label="Valor por manguera">
      <h3 className="ventas__subtitulo">Por manguera</h3>
      <ul className="mangueras">
        {ordenadas.map((m) => {
          const parte = Number(total) > 0 ? Math.round((Number(m.valor) / Number(total)) * 100) : 0;
          return (
            <li key={`${m.surtidor_id}-${m.lado}-${m.producto}`} className="manguera">
              <div className="manguera__fila">
                <span className="manguera__nombre">
                  {nombreSurtidor(m.surtidor_id)} · lado {m.lado} · {oracion(m.producto)}
                </span>
                <strong className="manguera__valor">{pesos(m.valor)}</strong>
              </div>
              <div className="manguera__pista" aria-hidden="true">
                <span className="manguera__barra" style={{ width: `${(Number(m.valor) / mayor) * 100}%` }} />
              </div>
              <span className="manguera__detalle">
                {m.ventas} {m.ventas === 1 ? 'venta' : 'ventas'} · {galones(m.galones)} gal · {parte}% del valor
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Búsqueda de ventas con los filtros de Nexus (fecha/hora, placa, cliente, #venta, equipo, vendedor). */
export default function Ventas() {
  const [periodo, setPeriodo] = useState('hoy');
  const [form, setForm] = useState({ ...PERIODOS.hoy(), ...VACIO });
  const [aplicado, setAplicado] = useState(() => aFiltro(form));
  const [problema, setProblema] = useState(null);
  const [masFiltros, setMasFiltros] = useState(false);
  const [desde, setDesde] = useState(0);
  const [catalogos, setCatalogos] = useState({ surtidores: [], productos: [] });
  const [usuarios, setUsuarios] = useState([]);

  useEffect(() => {
    obtenerCatalogos().then(setCatalogos).catch(() => {});
    api.usuarios().then(setUsuarios).catch(() => {});
  }, []);

  const busqueda = useCarga(() => api.ventas({ ...aplicado, limite: LIMITE, desplazamiento: desde }), [aplicado, desde]);
  const cambiar = (campo) => (e) => {
    if (campo === 'desde' || campo === 'hasta') setPeriodo(null);
    setForm({ ...form, [campo]: e.target.value });
  };

  const aplicar = (f) => {
    const filtro = aFiltro(f);
    if (typeof filtro === 'string') return setProblema(filtro);
    setProblema(null);
    setDesde(0);
    setAplicado(filtro);
  };
  const buscar = (e) => {
    e.preventDefault();
    aplicar(form);
  };
  // Los atajos de periodo y las fichas de filtro buscan de una vez.
  const elegirPeriodo = (p) => {
    setPeriodo(p);
    const f = { ...form, ...PERIODOS[p]() };
    setForm(f);
    aplicar(f);
  };
  const quitarFiltro = (campo) => {
    const f = { ...form, [campo]: '' };
    setForm(f);
    aplicar(f);
  };

  const nombreSurtidor = (id) => catalogos.surtidores.find((s) => s.id === Number(id))?.marca || `#${id}`;
  const nombreVendedor = (id) => usuarios.find((u) => u.id === Number(id))?.nombre || `#${id}`;
  const textoFicha = {
    surtidor_id: (v) => `Equipo ${nombreSurtidor(v)}`,
    lado: (v) => `Lado ${v}`,
    producto: (v) => oracion(v),
    forma_pago: (v) => oracion(v),
    estado_facturacion: (v) => oracion(v),
    vendedor_id: (v) => `Vendedor ${nombreVendedor(v)}`,
    placa: (v) => `Placa ${v}`,
    cliente: (v) => `Cliente ${v}`,
    id_externo: (v) => `Venta #${v}`,
    factura: (v) => `Factura ${v}`,
  };
  const fichas = typeof aplicado === 'object' ? Object.keys(textoFicha).filter((k) => aplicado[k]) : [];
  const secundariosActivos = SECUNDARIOS.filter((k) => form[k]).length;

  const opciones = (lista) => [<option key="" value="">Todos</option>, ...lista];
  const datos = busqueda.datos;

  return (
    <div className="pagina">
      <Encabezado titulo="Buscar ventas" subtitulo="Despachos y ventas manuales con su facturación">
        <Segmentado
          opciones={[['hoy', 'Hoy'], ['ayer', 'Ayer'], ['semana', '7 días'], ['mes', 'Este mes']]}
          valor={periodo}
          onCambio={elegirPeriodo}
        />
      </Encabezado>

      <form onSubmit={buscar} noValidate style={{ ...estilos.caja, padding: '16px', marginBottom: '20px' }}>
        <div className="ventas__busqueda">
          <Campo etiqueta="Placa (o parte)" id="v_placa">
            <input id="v_placa" maxLength={40} value={form.placa} onChange={cambiar('placa')} placeholder="ABC123" style={{ ...estilos.entrada, textTransform: 'uppercase' }} />
          </Campo>
          <Campo etiqueta="Documento del cliente" id="v_cliente">
            <input id="v_cliente" inputMode="numeric" maxLength={20} value={form.cliente} onChange={cambiar('cliente')} style={estilos.entrada} />
          </Campo>
          <Campo etiqueta="Desde" id="v_desde">
            <input id="v_desde" type="datetime-local" value={form.desde} onChange={cambiar('desde')} style={estilos.entrada} />
          </Campo>
          <Campo etiqueta="Hasta (sin incluir)" id="v_hasta">
            <input id="v_hasta" type="datetime-local" value={form.hasta} onChange={cambiar('hasta')} style={estilos.entrada} />
          </Campo>
          <div className="ventas__acciones">
            <Boton type="submit" style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px' }}>
              <Search size={14} strokeWidth={2.25} aria-hidden="true" /> Buscar
            </Boton>
            <Boton
              variante="secundario"
              onClick={() => setMasFiltros(!masFiltros)}
              aria-expanded={masFiltros}
              aria-controls="v_mas"
              style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
            >
              <SlidersHorizontal size={14} strokeWidth={2} aria-hidden="true" />
              Filtros{secundariosActivos > 0 && ` (${secundariosActivos})`}
              <ChevronDown size={14} strokeWidth={2} aria-hidden="true" className={masFiltros ? 'giro giro--abierto' : 'giro'} />
            </Boton>
          </div>
        </div>

        {masFiltros && (
          <div id="v_mas" className="aviso" style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border-color)' }}>
            <Rejilla columnas="repeat(auto-fill, minmax(170px, 1fr))">
              <Campo etiqueta="Equipo" id="v_surtidor">
                <select id="v_surtidor" value={form.surtidor_id} onChange={cambiar('surtidor_id')} style={estilos.entrada}>
                  {opciones(catalogos.surtidores.map((s) => <option key={s.id} value={s.id}>{s.marca}</option>))}
                </select>
              </Campo>
              <Campo etiqueta="Lado" id="v_lado">
                <select id="v_lado" value={form.lado} onChange={cambiar('lado')} style={estilos.entrada}>
                  {opciones(['A', 'B'].map((l) => <option key={l}>{l}</option>))}
                </select>
              </Campo>
              <Campo etiqueta="Producto" id="v_producto">
                <select id="v_producto" value={form.producto} onChange={cambiar('producto')} style={estilos.entrada}>
                  {opciones(catalogos.productos.map((p) => <option key={p.id} value={p.codigo}>{p.nombre}</option>))}
                </select>
              </Campo>
              <Campo etiqueta="Forma de pago" id="v_pago">
                <select id="v_pago" value={form.forma_pago} onChange={cambiar('forma_pago')} style={estilos.entrada}>
                  {opciones(['CONTADO', 'CREDITO', 'OTRO'].map((p) => <option key={p}>{p}</option>))}
                </select>
              </Campo>
              <Campo etiqueta="Facturación" id="v_estado">
                <select id="v_estado" value={form.estado_facturacion} onChange={cambiar('estado_facturacion')} style={estilos.entrada}>
                  {opciones(ESTADOS.map((s) => <option key={s}>{s}</option>))}
                </select>
              </Campo>
              <Campo etiqueta="Vendedor" id="v_vendedor">
                <select id="v_vendedor" value={form.vendedor_id} onChange={cambiar('vendedor_id')} style={estilos.entrada}>
                  {opciones(usuarios.map((u) => <option key={u.id} value={u.id}>{u.nombre}{u.activo ? '' : ' (inactivo)'}</option>))}
                </select>
              </Campo>
              <Campo etiqueta="# venta (surtidor)" id="v_venta">
                <input id="v_venta" inputMode="numeric" value={form.id_externo} onChange={cambiar('id_externo')} style={estilos.entrada} />
              </Campo>
              <Campo etiqueta="# factura" id="v_factura">
                <input id="v_factura" maxLength={40} value={form.factura} onChange={cambiar('factura')} style={{ ...estilos.entrada, textTransform: 'uppercase' }} />
              </Campo>
            </Rejilla>
            <Boton variante="secundario" style={{ marginTop: '14px' }} onClick={() => setForm({ ...form, ...VACIO })}>
              Limpiar filtros
            </Boton>
          </div>
        )}
        {problema && <Mensaje tipo="error" style={{ margin: '12px 0 0 0' }}>{problema}</Mensaje>}
      </form>

      {fichas.length > 0 && (
        <div className="fichas" aria-label="Filtros activos">
          {fichas.map((k) => (
            <button key={k} type="button" className="ficha aviso" onClick={() => quitarFiltro(k)} aria-label={`Quitar filtro ${textoFicha[k](aplicado[k])}`}>
              {textoFicha[k](aplicado[k])}
              <X size={12} strokeWidth={2.5} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}

      <ErrorApi error={busqueda.error} />
      {busqueda.cargando && !datos && <Cargando />}
      {datos && (
        <>
          <div className="ventas__resumen">
            {/* El total del periodo en el display del surtidor. */}
            <div className="display ventas__total">
              <span className="display__etiqueta">Valor del periodo</span>
              <span className="display__monto">
                <Rodillo texto={pesos(datos.total.valor)} />
              </span>
              <Mezcla mangueras={datos.por_manguera} total={datos.total.valor} />
              <div className="ventas__cifras">
                <div>
                  <span className="display__etiqueta">Ventas</span>
                  <strong>
                    <Rodillo texto={String(datos.total.ventas)} />
                  </strong>
                </div>
                <div>
                  <span className="display__etiqueta">Galones</span>
                  <strong>
                    <Rodillo texto={galones(datos.total.galones)} />
                  </strong>
                </div>
                <div>
                  <span className="display__etiqueta">Promedio</span>
                  <strong>{datos.total.ventas > 0 ? pesos(Math.round(Number(datos.total.valor) / datos.total.ventas)) : '—'}</strong>
                </div>
              </div>
            </div>
            {datos.por_manguera.length > 0 ? (
              <PorManguera filas={datos.por_manguera} total={datos.total.valor} nombreSurtidor={nombreSurtidor} />
            ) : (
              <Vacio>Sin mangueras con ventas en este periodo.</Vacio>
            )}
          </div>

          <h3 className="ventas__subtitulo" style={{ margin: '28px 0 12px' }}>
            Detalle <span style={{ fontWeight: 400, color: 'var(--text-secondary)' }}>· {datos.total.ventas} {datos.total.ventas === 1 ? 'venta' : 'ventas'}</span>
          </h3>
          {datos.filas.length === 0 ? (
            <Vacio>Sin ventas con esos filtros.</Vacio>
          ) : (
            <Tabla
              columnas={['Fecha', 'Equipo', 'Producto', 'Placa', { texto: 'Galones', alinear: 'right' }, { texto: 'Valor', alinear: 'right' }, 'Pago', 'Cliente', 'Comprobante', 'Facturación']}
            >
              {datos.filas.map((v) => {
                const estado = v.factura_estado === 'RECHAZADO' ? 'RECHAZADO' : v.estado_facturacion;
                return (
                  <tr key={`${v.origen_tipo}-${v.origen_id}`} style={estilos.fila}>
                    <Celda suave style={{ whiteSpace: 'nowrap' }}>{fechaHora(v.fecha)}</Celda>
                    <Celda style={{ whiteSpace: 'nowrap' }}>
                      <strong>{v.surtidor} · lado {v.lado}</strong>
                      <span style={{ display: 'block', fontSize: '10px', color: 'var(--text-secondary)' }}>
                        {v.origen_tipo === 'venta_manual' ? `Manual ${v.origen_id}` : `Venta #${v.id_externo ?? '—'}`}
                        {v.vendedor && ` · ${v.vendedor}`}
                      </span>
                    </Celda>
                    <Celda>
                      {oracion(v.producto)}
                    </Celda>
                    <Celda>{v.placa ? <span className="placa placa--chica">{v.placa}</span> : <span style={{ color: 'var(--text-secondary)' }}>—</span>}</Celda>
                    <Celda alinear="right" style={{ fontVariantNumeric: 'tabular-nums' }}>{galones(v.volumen)}</Celda>
                    <Celda alinear="right" fuerte style={{ fontVariantNumeric: 'tabular-nums' }}>{pesos(v.valor)}</Celda>
                    <Celda suave>{oracion(v.forma_pago)}</Celda>
                    <Celda>
                      {v.cliente_nombre ? (
                        <>
                          {v.cliente_nombre}
                          <span style={{ display: 'block', fontSize: '10px', color: 'var(--text-secondary)' }}>
                            {v.cliente_tipo} {v.cliente_documento}
                          </span>
                        </>
                      ) : (
                        <span style={{ color: 'var(--text-secondary)' }}>—</span>
                      )}
                    </Celda>
                    <Celda fuerte style={{ whiteSpace: 'nowrap' }}>{v.factura_numero ?? <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>—</span>}</Celda>
                    <Celda>
                      <Estado estado={estado} />
                    </Celda>
                  </tr>
                );
              })}
            </Tabla>
          )}
          <Paginador desde={desde} limite={LIMITE} total={datos.total.ventas} onCambio={setDesde} />
        </>
      )}
    </div>
  );
}
