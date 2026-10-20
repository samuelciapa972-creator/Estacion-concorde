import { useEffect, useState } from 'react';
import { ChartColumn, Fuel, LogOut, Moon, ReceiptText, Search, Sun, UserCog, Users } from 'lucide-react';
import logoConcorde from '../assets/images/LogoH1.png';
import { useIndicador } from '../lib/useIndicador';

// `corto`: la etiqueta bajo el ícono en tablet y celular, donde no cabe la larga.
// `grupo`: lo que se hace en la isla y lo que se hace en la oficina.
const SECCIONES = [
  { id: 'ventas', texto: 'Punto de venta', corto: 'Pista', Icono: Fuel, grupo: 'Pista' },
  { id: 'facturacion', texto: 'Facturas emitidas', corto: 'Facturas', Icono: ReceiptText, admin: true, grupo: 'Oficina' },
  { id: 'buscar', texto: 'Buscar ventas', corto: 'Ventas', Icono: Search, admin: true, grupo: 'Oficina' },
  { id: 'reportes', texto: 'Reportes', corto: 'Reportes', Icono: ChartColumn, admin: true, grupo: 'Oficina' },
  { id: 'clientes', texto: 'Clientes', corto: 'Clientes', Icono: Users, admin: true, grupo: 'Oficina' },
  { id: 'usuarios', texto: 'Personal', corto: 'Personal', Icono: UserCog, admin: true, grupo: 'Oficina' },
];

function Reloj() {
  const [ahora, setAhora] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setAhora(new Date()), 15000);
    return () => clearInterval(t);
  }, []);
  return (
    <time className="barra-superior__reloj" dateTime={ahora.toISOString()}>
      {ahora.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' })}
    </time>
  );
}

/** "Martes 14 de octubre": la fecha como se diría en la estación. */
function Fecha() {
  const texto = new Date().toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long' }).replace(',', '');
  return <span>{texto.charAt(0).toUpperCase() + texto.slice(1)}</span>;
}

/** Consola de la estación: menú lateral (abajo en celular), barra superior con hora y usuario, y el contenido. */
export default function Marco({ vista, esAdmin, usuario, onVista, tema, setTema, onSalir, children }) {
  const items = SECCIONES.filter((s) => esAdmin || !s.admin);
  // El islero solo tiene la pista: sin menú.
  const conRiel = items.length > 1;
  const { contenedor, indicador } = useIndicador(vista);
  const logo = { filter: tema === 'dark' ? 'brightness(0) invert(1)' : 'none', transition: 'filter 0.2s ease' };

  return (
    <div className={conRiel ? 'consola' : 'consola consola--sin-riel'}>
      {conRiel && (
        <aside className="riel">
          <button type="button" className="riel__marca" onClick={() => onVista('ventas')} aria-label="Ir al punto de venta">
            <img src={logoConcorde} alt="EDS Concorde" className="riel__logo" style={logo} />
          </button>
          <nav ref={contenedor} className="riel__nav" aria-label="Secciones">
            <span ref={indicador} className="indicador riel__indicador" />
            {items.map(({ id, texto, corto, Icono, grupo }, i) => [
              grupo !== items[i - 1]?.grupo && (
                <span key={`g-${grupo}`} className="riel__grupo">
                  {grupo}
                </span>
              ),
              <button
                key={id}
                type="button"
                className="riel__item"
                data-activo={vista === id}
                aria-current={vista === id ? 'page' : undefined}
                onClick={() => onVista(id)}
              >
                <Icono size={16} strokeWidth={1.6} aria-hidden="true" />
                <span className="riel__texto">{texto}</span>
                <span className="riel__corto">{corto}</span>
              </button>,
            ])}
          </nav>
          <p className="riel__pie">
            <em>Concorde, mil razones para viajar</em>
            v1.0.0
          </p>
        </aside>
      )}

      <div className="consola__cuerpo">
        <header className="barra-superior">
          <div className="barra-superior__estacion">
            <img src={logoConcorde} alt="" className="barra-superior__logo" style={logo} />
            <div className="barra-superior__nombre">
              <strong>EDS Concorde</strong>
              <Fecha />
            </div>
          </div>
          <div className="barra-superior__acciones">
            <Reloj />
            {usuario && (
              <span className="barra-superior__usuario">
                {usuario.nombre}
                <small>{usuario.rol === 'ADMIN' ? 'administrador' : 'islero'}</small>
              </span>
            )}
            <button
              type="button"
              className="boton-icono"
              onClick={() => setTema(tema === 'dark' ? 'light' : 'dark')}
              aria-label={tema === 'dark' ? 'Cambiar a modo luz' : 'Cambiar a modo noche'}
              title={tema === 'dark' ? 'Modo luz' : 'Modo noche'}
            >
              {tema === 'dark' ? <Sun size={16} strokeWidth={1.6} /> : <Moon size={16} strokeWidth={1.6} />}
            </button>
            <button type="button" className="boton-icono" onClick={onSalir} aria-label="Salir" title="Salir">
              <LogOut size={16} strokeWidth={1.6} />
            </button>
          </div>
        </header>
        <main className="consola__contenido">{children}</main>
      </div>
    </div>
  );
}
