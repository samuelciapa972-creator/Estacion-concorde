import { useState } from 'react';

import { api } from '../services/api';
import { useCarga } from '../lib/useCarga';
import { Boton, Campo, Celda, ConSecciones, Encabezado, Etiqueta, ErrorApi, Mensaje, Tabla, Vacio, Cargando } from '../components/ui';
import { COLOR, estilos } from '../components/estilos';

const SECCIONES = [
  ['ver', 'Ver usuarios'],
  ['crear', 'Crear usuario'],
  ['eliminar', 'Eliminar usuario'],
];
const VACIO = { usuario: '', nombre: '', rol: 'BOMBERO', pin: '' };
const nombreRol = (rol) => (rol === 'ADMIN' ? 'Administrador' : 'Islero');

export default function AdminUsuarios() {
  const [seccion, setSeccion] = useState('ver');
  const [mensaje, setMensaje] = useState({ tipo: '', texto: '' });
  const [nuevoUsuario, setNuevoUsuario] = useState(VACIO);
  const lista = useCarga(() => api.usuarios(), []);
  // Un usuario "eliminado" queda desactivado en la API (conserva su historial): no se lista.
  const usuarios = (lista.datos || []).filter((u) => u.activo !== false);

  const cambiarSeccion = (s) => {
    setSeccion(s);
    setMensaje({ tipo: '', texto: '' });
  };

  const handleCrear = async (e) => {
    e.preventDefault();
    setMensaje({ tipo: '', texto: '' });
    try {
      await api.crearUsuario(nuevoUsuario);
      setMensaje({ tipo: 'exito', texto: `Usuario ${nuevoUsuario.usuario} creado.` });
      setNuevoUsuario(VACIO);
      lista.recargar();
    } catch (err) {
      setMensaje({ tipo: 'error', texto: err.message || 'Error al crear usuario.' });
    }
  };

  const handleEliminar = async (u) => {
    if (!window.confirm(`Eliminar usuario ${u.usuario}?`)) return;
    setMensaje({ tipo: '', texto: '' });
    try {
      await api.desactivarUsuario(u.id);
      setMensaje({ tipo: 'exito', texto: `Usuario ${u.usuario} eliminado.` });
      lista.recargar();
    } catch (err) {
      setMensaje({ tipo: 'error', texto: err.message || 'Error al eliminar usuario.' });
    }
  };

  const campo = (nombre) => ({
    id: `usuario-${nombre}`,
    value: nuevoUsuario[nombre],
    onChange: (e) => setNuevoUsuario({ ...nuevoUsuario, [nombre]: e.target.value }),
    style: estilos.entrada,
  });

  const tablaUsuarios = (ultima) => (
    <Tabla columnas={['Usuario', 'Nombre', 'Rol', { texto: ultima.titulo, alinear: 'center' }]}>
      {usuarios.map((u) => (
        <tr key={u.id} style={estilos.fila}>
          <Celda style={{ fontWeight: '600' }}>{u.usuario}</Celda>
          <Celda>{u.nombre || 'Operador'}</Celda>
          <Celda suave>{nombreRol(u.rol)}</Celda>
          <Celda alinear="center">{ultima.celda(u)}</Celda>
        </tr>
      ))}
    </Tabla>
  );

  return (
    <ConSecciones secciones={SECCIONES} seccion={seccion} onSeccion={cambiarSeccion}>
      <Mensaje tipo={mensaje.tipo || 'error'}>{mensaje.texto}</Mensaje>
      <ErrorApi error={lista.error} />

      {seccion === 'ver' && (
        <div>
          <Encabezado titulo="Usuarios autorizados" />
          {lista.cargando && !lista.datos ? (
            <Cargando />
          ) : usuarios.length === 0 ? (
            <Vacio>Sin registros</Vacio>
          ) : (
            tablaUsuarios({ titulo: 'Estado', celda: () => <Etiqueta color={COLOR.exito}>Activo</Etiqueta> })
          )}
        </div>
      )}

      {seccion === 'crear' && (
        <div style={{ maxWidth: '360px' }}>
          <Encabezado titulo="Crear usuario" />
          <form onSubmit={handleCrear} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            <Campo etiqueta="Usuario" id="usuario-usuario">
              <input type="text" required {...campo('usuario')} />
            </Campo>
            <Campo etiqueta="Nombre" id="usuario-nombre">
              <input type="text" required {...campo('nombre')} />
            </Campo>
            <Campo etiqueta="Rol" id="usuario-rol">
              <select {...campo('rol')}>
                <option value="BOMBERO">Islero</option>
                <option value="ADMIN">Administrador</option>
              </select>
            </Campo>
            <Campo
              etiqueta="PIN"
              id="usuario-pin"
              ayuda={nuevoUsuario.rol === 'ADMIN' ? 'Solo números, de 8 a 10 dígitos.' : 'Solo números, de 4 a 10 dígitos.'}
            >
              <input type="password" inputMode="numeric" required {...campo('pin')} />
            </Campo>
            <Boton type="submit" style={{ marginTop: '8px' }}>
              Guardar
            </Boton>
          </form>
        </div>
      )}

      {seccion === 'eliminar' && (
        <div>
          <Encabezado titulo="Eliminar usuario" />
          {usuarios.length === 0 ? (
            <Vacio>Sin registros para eliminar.</Vacio>
          ) : (
            tablaUsuarios({
              titulo: 'Acción',
              celda: (u) => (
                <Boton variante="peligro" onClick={() => handleEliminar(u)}>
                  Eliminar
                </Boton>
              ),
            })
          )}
        </div>
      )}
    </ConSecciones>
  );
}
