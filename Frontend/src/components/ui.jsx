/**
 * Piezas de interfaz con los MISMOS estilos en línea de las pantallas del diseñador (AdminUsuarios, Reportes,
 * PosVentas, Facturacion). Las pantallas nuevas las usan para verse igual que las originales.
 */
import { oracion } from '../lib/formato';
import { useIndicador } from '../lib/useIndicador';
import { COLOR, estilos } from './estilos';

const deshabilitado = (estilo, d) => (d ? { ...estilo, opacity: 0.5, cursor: 'not-allowed' } : estilo);

// ------------------------------------------------------------------ componentes
export function Encabezado({ titulo, subtitulo, grande, children }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px', gap: '12px', flexWrap: 'wrap' }}>
      <div>
        <h2 style={grande ? { ...estilos.titulo, fontSize: '20px' } : estilos.titulo}>{titulo}</h2>
        {subtitulo && <p style={estilos.subtitulo}>{subtitulo}</p>}
      </div>
      {children && <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>{children}</div>}
    </div>
  );
}

/** `tamano`: "normal" (32 px, el alto de los campos) o "grande" (44 px, acciones principales de la pista). Las
 *  variantes chico y peligro son las acciones de fila (26 px). El alto y el relleno viven en el CSS (.boton). */
export function Boton({ variante = 'primario', tamano = 'normal', disabled, style, className = '', children, ...props }) {
  const base = {
    primario: estilos.botonPrimario,
    secundario: estilos.botonSecundario,
    peligro: estilos.botonPeligro,
    chico: estilos.botonChico,
  }[variante];
  return (
    <button type="button" disabled={disabled} className={`boton boton--${variante} boton--${tamano} ${className}`.trim()} style={{ ...deshabilitado(base, disabled), ...style }} {...props}>
      {children}
    </button>
  );
}

/** Nota al margen: filo de color a la izquierda y texto en tinta (el color marca el tipo, no pinta el texto). */
export function Mensaje({ tipo = 'error', titulo, children, style }) {
  if (!titulo && !children) return null;
  return (
    <div
      role={tipo === 'error' ? 'alert' : 'status'}
      className="aviso"
      style={{
        padding: '10px 14px',
        borderRadius: '4px',
        fontSize: '12px',
        marginBottom: '20px',
        border: '1px solid var(--border-color)',
        borderLeft: `3px solid ${COLOR[tipo] || 'var(--text-secondary)'}`,
        background: 'var(--bg-card)',
        color: 'var(--text-primary)',
        ...style,
      }}
    >
      {titulo && <div style={{ fontWeight: '700', color: COLOR[tipo] || 'var(--text-primary)', marginBottom: children ? '4px' : 0 }}>{titulo}</div>}
      {children}
    </div>
  );
}

export function ErrorApi({ error }) {
  if (!error) return null;
  return <Mensaje tipo="error">{error.message || String(error)}</Mensaje>;
}

export function Campo({ etiqueta, id, ayuda, error, children, style }) {
  return (
    <div style={style}>
      <label htmlFor={id} style={estilos.etiqueta}>
        {etiqueta}
      </label>
      {children}
      {ayuda && !error && <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>{ayuda}</span>}
      {error && <span role="alert" style={{ display: 'block', fontSize: '11px', color: COLOR.error, marginTop: '4px' }}>{error}</span>}
    </div>
  );
}

export function Vacio({ children }) {
  return (
    <div className="vista" style={estilos.vacio}>
      {children}
    </div>
  );
}

const ANCHOS_ESQUELETO = ['92%', '74%', '86%', '61%'];

/** Esqueleto de filas mientras llega la respuesta; el texto queda para lectores de pantalla. */
export function Cargando({ texto = 'Cargando...', filas = 4 }) {
  return (
    <div role="status" style={{ ...estilos.caja, padding: '16px' }}>
      <span className="solo-lectores">{texto}</span>
      {ANCHOS_ESQUELETO.slice(0, filas).map((ancho) => (
        <div key={ancho} className="esqueleto" style={{ width: ancho }} />
      ))}
    </div>
  );
}

/** Cifra con dígitos de ancho fijo (para que no salte al cambiar). Recibe el texto ya formateado. */
export function Rodillo({ texto }) {
  return <span style={{ fontVariantNumeric: 'tabular-nums' }}>{texto}</span>;
}

/** Selector segmentado de Reportes (DIARIO / SEMANAL / MENSUAL). `opciones`: [[valor, texto], ...]. */
export function Segmentado({ opciones, valor, onCambio }) {
  const { contenedor, indicador } = useIndicador(String(valor));
  return (
    <div
      ref={contenedor}
      className="segmentado"
      style={{ position: 'relative', display: 'flex', gap: '2px', background: 'var(--bg-input)', padding: '3px', borderRadius: '6px', flexWrap: 'wrap' }}
    >
      <span ref={indicador} className="indicador" style={{ background: 'var(--bg-card)', boxShadow: 'var(--sombra-panel)', borderRadius: '4px' }} />
      {opciones.map(([v, texto]) => (
        <button
          key={String(v)}
          type="button"
          onClick={() => onCambio(v)}
          aria-pressed={valor === v}
          data-activo={valor === v}
          style={{
            position: 'relative',
            background: 'transparent',
            border: 'none',
            padding: '6px 12px',
            borderRadius: '4px',
            fontSize: '12px',
            fontWeight: valor === v ? '600' : '400',
            color: valor === v ? 'var(--text-primary)' : 'var(--text-secondary)',
            cursor: 'pointer',
          }}
        >
          {texto}
        </button>
      ))}
    </div>
  );
}

/** Tarjeta de indicador de Reportes (TOTAL RECAUDADO, VOLUMEN TOTAL, OPERACIONES). */
export function Indicador({ titulo, valor }) {
  return (
    <div style={{ ...estilos.caja, padding: '18px 20px' }}>
      <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)' }}>{titulo}</span>
      <span style={{ display: 'block', fontSize: '20px', fontWeight: '700', color: 'var(--text-primary)', marginTop: '6px' }}>
        <Rodillo texto={valor} />
      </span>
    </div>
  );
}

/** Tabla con el encabezado de la maqueta. `columnas`: [texto | {texto, alinear}]. */
export function Tabla({ columnas, children, pie }) {
  return (
    <div style={{ ...estilos.caja, overflowX: 'auto' }}>
      <table className="tabla" style={estilos.tabla}>
        <thead>
          <tr style={estilos.filaEncabezado}>
            {columnas.map((c, i) => {
              const col = typeof c === 'string' ? { texto: c } : c;
              return (
                <th key={i} style={{ ...estilos.th, textAlign: col.alinear || 'left', whiteSpace: 'nowrap' }}>
                  {col.texto}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>{children}</tbody>
        {pie && <tfoot>{pie}</tfoot>}
      </table>
    </div>
  );
}

export function Celda({ alinear, fuerte, suave, children, style, ...props }) {
  return (
    <td
      style={{
        ...estilos.td,
        textAlign: alinear || 'left',
        fontWeight: fuerte ? '700' : undefined,
        color: suave ? 'var(--text-secondary)' : undefined,
        whiteSpace: alinear === 'right' ? 'nowrap' : undefined,
        ...style,
      }}
      {...props}
    >
      {children}
    </td>
  );
}

/** Estado: punto de color + texto en tinta (el color nunca va solo). */
export function Etiqueta({ color, children }) {
  return (
    <span className="estado">
      <i style={{ background: color || 'var(--text-secondary)' }} aria-hidden="true" />
      {oracion(children)}
    </span>
  );
}

/** Subsecciones de una vista (Reportes, Clientes, Personal) como pestañas. `secciones`: [[id, texto], ...]. */
export function ConSecciones({ secciones, seccion, onSeccion, children }) {
  const { contenedor, indicador } = useIndicador(seccion, { subrayado: true });
  return (
    <div className="pagina">
      <nav ref={contenedor} className="secciones" aria-label="Subsecciones">
        <span ref={indicador} className="indicador secciones__indicador" />
        {secciones.map(([id, texto]) => (
          <button
            key={id}
            type="button"
            className="secciones__item"
            data-activo={seccion === id}
            aria-current={seccion === id ? 'page' : undefined}
            onClick={() => onSeccion(id)}
          >
            {texto}
          </button>
        ))}
      </nav>
      <div key={seccion} className="vista">
        {children}
      </div>
    </div>
  );
}

/** "1–50 de 120" con Anterior / Siguiente. */
export function Paginador({ desde, limite, total, onCambio }) {
  if (total <= limite && desde === 0) return null;
  const hasta = Math.min(desde + limite, total);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '16px', fontSize: '12px', color: 'var(--text-secondary)' }}>
      <Boton variante="secundario" disabled={desde === 0} onClick={() => onCambio(Math.max(0, desde - limite))}>
        Anterior
      </Boton>
      <span>
        {total === 0 ? 0 : desde + 1}–{hasta} de {total}
      </span>
      <Boton variante="secundario" disabled={hasta >= total} onClick={() => onCambio(desde + limite)}>
        Siguiente
      </Boton>
    </div>
  );
}

/** Rejilla de campos de formulario. */
export function Rejilla({ columnas = 'repeat(auto-fill, minmax(200px, 1fr))', children, style }) {
  return (
    <div className="rejilla" style={{ display: 'grid', gridTemplateColumns: columnas, gap: '14px', alignItems: 'end', ...style }}>
      {children}
    </div>
  );
}
