import { useState, useEffect } from 'react';
import Marco from './components/Marco';
import PosVentas from './pages/PosVentas';
import Facturacion from './pages/Facturacion';
import Reportes from './pages/Reportes';
import AdminUsuarios from './pages/AdminUsuarios';
import Login from './pages/Login';
import Ventas from './pages/Ventas';
import Clientes from './pages/Clientes';
import Registro from './pages/Registro';
import { cargarUsuario, cerrarSesion, usuarioActual } from './services/api';

export default function App() {
  const [autenticado, setAutenticado] = useState(!!localStorage.getItem('token'));
  const [vistaActual, setVistaActual] = useState('ventas');
  const [tema, setTema] = useState(localStorage.getItem('tema') || 'light');

  useEffect(() => {
    // Los colores cambian con un fundido corto solo durante el cambio de tema (no en cada render).
    const raiz = document.documentElement;
    raiz.classList.add('tema-cambiando');
    raiz.setAttribute('data-theme', tema);
    localStorage.setItem('tema', tema);
    const fin = setTimeout(() => raiz.classList.remove('tema-cambiando'), 260);
    return () => clearTimeout(fin);
  }, [tema]);

  // La API respondió 401 (sesión vencida o usuario desactivado): volver al ingreso.
  useEffect(() => {
    const alCerrar = () => setAutenticado(false);
    window.addEventListener('sesion-cerrada', alCerrar);
    return () => window.removeEventListener('sesion-cerrada', alCerrar);
  }, []);

  // Nombre y rol guardados al ingresar; una sesión anterior que no los tenga los pide a la API.
  const [, setUsuarioCargado] = useState(null);
  useEffect(() => {
    if (autenticado && !usuarioActual()) cargarUsuario().then(setUsuarioCargado).catch(() => {});
  }, [autenticado]);
  const usuario = autenticado ? usuarioActual() : null;

  // Formulario público de registro por QR: no pide sesión.
  if (window.location.pathname.startsWith('/registro')) {
    return <Registro tema={tema} setTema={setTema} />;
  }

  const esAdmin = usuario?.rol === 'ADMIN';
  // Un bombero solo usa el punto de venta (la API además le niega lo demás).
  const vista = esAdmin ? vistaActual : 'ventas';

  if (!autenticado) {
    return <Login onLoginExitoso={() => setAutenticado(true)} tema={tema} setTema={setTema} />;
  }

  return (
    <Marco
      vista={vista}
      esAdmin={esAdmin}
      usuario={usuario}
      onVista={setVistaActual}
      tema={tema}
      setTema={setTema}
      onSalir={() => {
        cerrarSesion();
        setVistaActual('ventas');
        setAutenticado(false);
      }}
    >
      <div key={vista} className="vista">
        {vista === 'ventas' && <PosVentas />}
        {vista === 'facturacion' && <Facturacion />}
        {vista === 'buscar' && <Ventas />}
        {vista === 'reportes' && <Reportes />}
        {vista === 'clientes' && <Clientes />}
        {vista === 'usuarios' && <AdminUsuarios />}
      </div>
    </Marco>
  );
}