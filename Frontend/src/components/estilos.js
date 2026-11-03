/** Estilos en línea copiados de las pantallas del diseñador (AdminUsuarios, Reportes, PosVentas, Facturacion). */

export const estilos = {
  titulo: { fontSize: '16px', fontWeight: '700', color: 'var(--text-primary)', margin: 0 },
  subtitulo: { fontSize: '12px', color: 'var(--text-secondary)', margin: '4px 0 0 0' },
  // Panel: flota sobre el lienzo con una sombra (en modo noche, un filo de luz) en vez de un borde.
  caja: { background: 'var(--bg-card)', borderRadius: '6px', boxShadow: 'var(--sombra-panel)' },
  tabla: { width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' },
  // Encabezado de planilla: texto normal y una regla fuerte debajo, sin fondo de color.
  filaEncabezado: {
    borderBottom: '1.5px solid var(--regla-fuerte)',
    color: 'var(--text-secondary)',
    fontSize: '11px',
    fontWeight: '600',
  },
  th: { padding: '10px 16px' },
  fila: { borderBottom: '1px solid var(--border-color)', color: 'var(--text-primary)' },
  td: { padding: '10px 16px' },
  vacio: {
    padding: '24px',
    borderRadius: '6px',
    border: '1px dashed var(--border-color)',
    fontSize: '12px',
    color: 'var(--text-secondary)',
  },
  etiqueta: {
    display: 'block',
    fontSize: '11px',
    color: 'var(--text-secondary)',
    marginBottom: '4px',
  },
  entrada: {
    width: '100%',
    padding: '8px 10px',
    border: '1px solid var(--border-color)',
    background: 'var(--bg-input)',
    color: 'var(--text-primary)',
    fontSize: '12px',
    borderRadius: '4px',
  },
  botonPrimario: {
    background: 'var(--text-primary)',
    color: 'var(--bg-main)',
    border: '1px solid var(--text-primary)',
    borderRadius: '4px',
    fontSize: '12px',
    fontWeight: '700',
    cursor: 'pointer',
  },
  botonSecundario: {
    background: 'var(--bg-card)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
    borderRadius: '4px',
    fontSize: '12px',
    cursor: 'pointer',
    fontWeight: '600',
  },
  botonPeligro: {
    background: 'transparent',
    color: 'var(--color-error, #c0392b)',
    border: '1px solid var(--color-error, #c0392b)',
    borderRadius: '4px',
    fontSize: '10px',
    fontWeight: '700',
    cursor: 'pointer',
  },
  botonChico: {
    background: 'transparent',
    color: 'var(--text-primary)',
    border: '1px solid var(--border-color)',
    borderRadius: '4px',
    fontSize: '10px',
    fontWeight: '700',
    cursor: 'pointer',
  },
};

// Colores de estado como tintas de sello (apagados, legibles sobre papel y en modo noche); nunca van solos.
export const COLOR = { exito: '#3d8a4f', error: '#c0392b', alerta: '#b7791f', info: '#3b6fa0' };

