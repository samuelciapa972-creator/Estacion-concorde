import { useState } from 'react';
import { api } from '../services/api';
import { NOMBRE_TIPO, TIPOS_DOCUMENTO, problemaNumero } from '../lib/documentos';
import { useCarga } from '../lib/useCarga';
import { Boton, Campo, Celda, ConSecciones, Encabezado, Etiqueta, ErrorApi, Mensaje, Paginador, Rejilla, Segmentado, Tabla, Vacio, Cargando } from '../components/ui';
import { COLOR, estilos } from '../components/estilos';

const LIMITE = 50;
const SECCIONES = [
  ['clientes', 'Clientes'],
  ['vehiculos', 'Vehículos'],
  ['nuevo_vehiculo', 'Registrar vehículo'],
  ['importar', 'Importar CSV'],
  ['qr', 'Registro por QR'],
];
const FILTRO_ESTADO = [['activos', 'Activos'], ['inactivos', 'Inactivos'], ['todos', 'Todos']];

const Activo = ({ activo }) => <Etiqueta color={activo ? COLOR.exito : 'var(--text-secondary)'}>{activo ? 'Activo' : 'Inactivo'}</Etiqueta>;

export default function Clientes() {
  const [seccion, setSeccion] = useState('clientes');
  // Dueño elegido desde la lista de clientes para ver o registrar sus vehículos.
  const [dueno, setDueno] = useState(null);
  const ir = (s) => {
    if (s === 'clientes') setDueno(null);
    setSeccion(s);
  };
  return (
    <ConSecciones secciones={SECCIONES} seccion={seccion} onSeccion={ir}>
      {seccion === 'clientes' && (
        <ListaClientes
          onVehiculos={(c) => {
            setDueno({ id: c.id, nombre: c.nombre_mostrar });
            setSeccion('vehiculos');
          }}
        />
      )}
      {seccion === 'vehiculos' && <ListaVehiculos dueno={dueno} onTodos={() => setDueno(null)} onNuevo={() => setSeccion('nuevo_vehiculo')} />}
      {seccion === 'nuevo_vehiculo' && <NuevoVehiculo dueno={dueno} />}
      {seccion === 'importar' && <Importar />}
      {seccion === 'qr' && <RegistroQr />}
    </ConSecciones>
  );
}

function Buscador({ etiqueta, id, onBuscar, mayusculas, children }) {
  const [texto, setTexto] = useState('');
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onBuscar(texto.trim());
      }}
      style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '16px' }}
    >
      <Campo etiqueta={etiqueta} id={id} style={{ flex: '1 1 240px' }}>
        <input id={id} maxLength={100} value={texto} onChange={(e) => setTexto(e.target.value)} style={{ ...estilos.entrada, textTransform: mayusculas ? 'uppercase' : 'none' }} />
      </Campo>
      <Boton type="submit">Buscar</Boton>
      {children}
    </form>
  );
}

/** Estado común de las listas de clientes y vehículos: texto, activos/inactivos, página y resultado de una acción. */
function useListado(cargar, deps = []) {
  const [filtro, setFiltro] = useState({ texto: '', estado: 'activos', desde: 0 });
  const [accion, setAccion] = useState({ error: null, mensaje: null });
  const lista = useCarga(() => cargar(filtro), [filtro.texto, filtro.estado, filtro.desde, ...deps]);
  const filtrar = (cambio) => setFiltro((f) => ({ ...f, ...cambio, desde: 0 }));
  const paginar = (desde) => setFiltro((f) => ({ ...f, desde }));
  const ejecutar = async (fn, mensaje) => {
    try {
      await fn();
      setAccion({ error: null, mensaje });
      lista.recargar();
    } catch (err) {
      setAccion({ error: err, mensaje: null });
    }
  };
  return { filtro, filtrar, paginar, lista, accion, ejecutar };
}

/** Mensajes, buscador y filtro de estado de un listado. */
function FiltrosListado({ listado, etiqueta, id, mayusculas }) {
  return (
    <>
      {listado.accion.mensaje && <Mensaje tipo="exito">{listado.accion.mensaje}</Mensaje>}
      <ErrorApi error={listado.lista.error || listado.accion.error} />
      <Buscador etiqueta={etiqueta} id={id} mayusculas={mayusculas} onBuscar={(t) => listado.filtrar({ texto: t })}>
        <Segmentado opciones={FILTRO_ESTADO} valor={listado.filtro.estado} onCambio={(e) => listado.filtrar({ estado: e })} />
      </Buscador>
    </>
  );
}

/** Cargando / vacío / la tabla (`children`), y el paginador. */
function ResultadoListado({ listado, vacio, children }) {
  const { lista, filtro, paginar } = listado;
  return (
    <>
      {lista.cargando && !lista.datos ? <Cargando /> : !lista.datos?.filas.length ? <Vacio>{vacio}</Vacio> : children(lista.datos.filas)}
      {lista.datos && <Paginador desde={filtro.desde} limite={LIMITE} total={lista.datos.total} onCambio={paginar} />}
    </>
  );
}

const BotonActivo = ({ activo, onClick }) => (
  <Boton variante={activo ? 'peligro' : 'chico'} onClick={onClick}>
    {activo ? 'Desactivar' : 'Activar'}
  </Boton>
);

function ListaClientes({ onVehiculos }) {
  const listado = useListado(({ texto, estado, desde }) => api.clientes({ texto, estado, limite: LIMITE, desplazamiento: desde }));

  const cambiarActivo = (c) => {
    if (c.activo && !window.confirm(`Desactivar al cliente ${c.nombre_mostrar}? Ya no se le podrá facturar.`)) return;
    listado.ejecutar(() => api.activarCliente(c.id, !c.activo), `Cliente ${c.nombre_mostrar} ${c.activo ? 'desactivado' : 'activado'}.`);
  };

  return (
    <div>
      <Encabezado titulo="Clientes" subtitulo="Registrados por QR o desde la pista, con autorización de datos" />
      <FiltrosListado listado={listado} etiqueta="Documento (inicio) o nombre" id="cl_texto" />
      <ResultadoListado listado={listado} vacio="Sin clientes.">
        {(filas) => (
          <Tabla columnas={['Documento', 'Nombre o razón social', 'Correo', { texto: 'Estado', alinear: 'center' }, { texto: 'Acción', alinear: 'center' }]}>
            {filas.map((c) => (
              <tr key={c.id} style={estilos.fila}>
                <Celda style={{ whiteSpace: 'nowrap' }}>
                  {c.tipo_documento} {c.numero_documento}
                  {c.digito_verificacion !== null && `-${c.digito_verificacion}`}
                </Celda>
                <Celda fuerte>{c.nombre_mostrar}</Celda>
                <Celda suave>{c.email_enmascarado}</Celda>
                <Celda alinear="center">
                  <Activo activo={c.activo} />
                </Celda>
                <Celda alinear="center" style={{ whiteSpace: 'nowrap' }}>
                  <Boton variante="chico" onClick={() => onVehiculos(c)} style={{ marginRight: '6px' }}>
                    Vehículos
                  </Boton>
                  <BotonActivo activo={c.activo} onClick={() => cambiarActivo(c)} />
                </Celda>
              </tr>
            ))}
          </Tabla>
        )}
      </ResultadoListado>
      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '16px' }}>
        Desactivar es una baja lógica: el cliente deja de aparecer en la pista y no se le factura, pero su historial se conserva.
      </p>
    </div>
  );
}

function ListaVehiculos({ dueno, onTodos, onNuevo }) {
  const listado = useListado(
    ({ texto, estado, desde }) => api.vehiculos({ texto, estado, cliente_id: dueno?.id ?? null, limite: LIMITE, desplazamiento: desde }),
    [dueno?.id],
  );

  const cambiarActivo = (v) =>
    listado.ejecutar(() => api.activarVehiculo(v.id, !v.activo), `Vehículo ${v.identificador} ${v.activo ? 'desactivado' : 'activado'}.`);

  return (
    <div>
      <Encabezado titulo={dueno ? `Vehículos de ${dueno.nombre}` : 'Vehículos'} subtitulo="Placas y equipos de los clientes">
        {dueno && (
          <Boton variante="secundario" onClick={onTodos}>
            Ver todos
          </Boton>
        )}
        <Boton onClick={onNuevo}>+ REGISTRAR VEHÍCULO</Boton>
      </Encabezado>
      <FiltrosListado listado={listado} etiqueta="Placa (o parte)" id="ve_texto" mayusculas />
      <ResultadoListado listado={listado} vacio="Sin vehículos.">
        {(filas) => (
          <Tabla columnas={['Placa o equipo', 'Descripción', 'Cliente', { texto: 'Estado', alinear: 'center' }, { texto: 'Acción', alinear: 'center' }]}>
            {filas.map((v) => (
              <tr key={v.id} style={estilos.fila}>
                <Celda fuerte>
                  {v.identificador}
                  {v.tipo === 'EQUIPO_TEXTO' && <span style={{ marginLeft: '6px', fontSize: '10px', color: 'var(--text-secondary)', fontWeight: 400 }}>(equipo)</span>}
                </Celda>
                <Celda suave>{v.descripcion ?? '—'}</Celda>
                <Celda>{v.cliente_nombre}</Celda>
                <Celda alinear="center">
                  <Activo activo={v.activo} />
                </Celda>
                <Celda alinear="center">
                  <BotonActivo activo={v.activo} onClick={() => cambiarActivo(v)} />
                </Celda>
              </tr>
            ))}
          </Tabla>
        )}
      </ResultadoListado>
    </div>
  );
}

/** Alta de un vehículo. El dueño viene de la lista de clientes o se busca por documento (debe existir y estar activo). */
function NuevoVehiculo({ dueno }) {
  const [tipo, setTipo] = useState('CC');
  const [numero, setNumero] = useState('');
  const [encontrado, setEncontrado] = useState(undefined);
  const [placa, setPlaca] = useState('');
  const [descripcion, setDescripcion] = useState('');
  const [estado, setEstado] = useState({ error: null, problema: null, creado: null, guardando: false });
  const elegido = dueno || (encontrado ? { id: encontrado.id, nombre: encontrado.nombre_mostrar } : null);

  const buscarDueno = async (e) => {
    e.preventDefault();
    const p = problemaNumero(tipo, numero);
    setEstado({ ...estado, problema: p, error: null });
    if (p) return;
    try {
      setEncontrado(await api.buscarCliente(tipo, numero.trim()));
    } catch (err) {
      setEstado({ ...estado, error: err });
    }
  };

  const guardar = async (e) => {
    e.preventDefault();
    if (!elegido) return setEstado({ ...estado, problema: 'primero busque al dueño por su documento' });
    if (!placa.trim()) return setEstado({ ...estado, problema: 'digite la placa o el nombre del equipo' });
    setEstado({ error: null, problema: null, creado: null, guardando: true });
    try {
      const v = await api.crearVehiculo({ cliente_id: elegido.id, placa: placa.trim().toUpperCase(), descripcion: descripcion.trim() || null });
      setPlaca('');
      setDescripcion('');
      setEstado({ error: null, problema: null, creado: v, guardando: false });
    } catch (err) {
      setEstado({ error: err, problema: null, creado: null, guardando: false });
    }
  };

  return (
    <div style={{ maxWidth: '640px' }}>
      <Encabezado titulo="Registrar vehículo" subtitulo="La placa queda asociada al cliente para reconocer sus despachos" />
      {estado.creado && (
        <Mensaje tipo="exito">
          Vehículo {estado.creado.identificador} registrado.
          {estado.creado.tipo === 'EQUIPO_TEXTO' && ' No es una placa válida: quedó como equipo (texto).'}
        </Mensaje>
      )}
      {estado.problema && <Mensaje tipo="error">{estado.problema}</Mensaje>}
      <ErrorApi error={estado.error} />

      {!dueno && (
        <form onSubmit={buscarDueno} noValidate style={{ marginBottom: '20px' }}>
          <Rejilla columnas="1fr 1.3fr auto">
            <Campo etiqueta="Tipo de documento del dueño" id="nv_tipo">
              <select
                id="nv_tipo"
                value={tipo}
                onChange={(e) => {
                  setTipo(e.target.value);
                  setEncontrado(undefined);
                }}
                style={estilos.entrada}
              >
                {TIPOS_DOCUMENTO.map((t) => (
                  <option key={t} value={t}>
                    {NOMBRE_TIPO[t]}
                  </option>
                ))}
              </select>
            </Campo>
            <Campo etiqueta="Número de documento" id="nv_numero">
              <input
                id="nv_numero"
                maxLength={20}
                value={numero}
                onChange={(e) => {
                  setNumero(e.target.value);
                  setEncontrado(undefined);
                }}
                style={estilos.entrada}
              />
            </Campo>
            <Boton type="submit" variante="secundario">
              Buscar dueño
            </Boton>
          </Rejilla>
        </form>
      )}
      {encontrado === null && <Mensaje tipo="alerta" titulo="No hay un cliente activo con ese documento">Regístrelo primero (por QR o desde la pista, con autorización de datos).</Mensaje>}
      {elegido && (
        <p style={{ fontSize: '13px', color: 'var(--text-primary)', marginBottom: '16px' }}>
          Dueño: <strong>{elegido.nombre}</strong>
        </p>
      )}

      <form onSubmit={guardar} noValidate style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <Campo etiqueta="Placa o equipo" id="nv_placa">
          <input id="nv_placa" maxLength={60} value={placa} onChange={(e) => setPlaca(e.target.value)} style={{ ...estilos.entrada, textTransform: 'uppercase' }} />
        </Campo>
        <Campo etiqueta="Descripción (opcional)" id="nv_desc">
          <input id="nv_desc" maxLength={200} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} style={estilos.entrada} />
        </Campo>
        <Boton type="submit" disabled={estado.guardando} style={{ marginTop: '8px' }}>
          {estado.guardando ? 'Guardando...' : 'Guardar'}
        </Boton>
      </form>
    </div>
  );
}

const FORMATO_CSV = {
  clientes: 'tipo_documento;numero_documento;nombres;apellidos;email;autoriza_tratamiento;fecha_autorizacion',
  vehiculos: 'placa;tipo_documento;numero_documento;descripcion',
};

/**
 * Importación CSV en dos pasos: primero se valida en seco (no escribe nada) y solo después se habilita
 * "Importar", con el mismo archivo. Cambiar de archivo obliga a validar de nuevo.
 */
function Importar() {
  const [que, setQue] = useState('clientes');
  const [archivo, setArchivo] = useState(null);
  const [resultado, setResultado] = useState(null);
  const [estado, setEstado] = useState({ cargando: false, error: null });

  const enviar = async (enSeco) => {
    setEstado({ cargando: true, error: null });
    try {
      setResultado(await api.importar(que, archivo, enSeco));
      setEstado({ cargando: false, error: null });
    } catch (err) {
      setEstado({ cargando: false, error: err });
    }
  };
  const validado = resultado?.en_seco ? resultado : null;

  return (
    <div style={{ maxWidth: '720px' }}>
      <Encabezado titulo="Importar desde CSV" subtitulo="Migración de clientes y vehículos (lo que ya existe no se modifica)">
        <Segmentado
          opciones={[['clientes', 'Clientes'], ['vehiculos', 'Vehículos']]}
          valor={que}
          onCambio={(q) => {
            setQue(q);
            setResultado(null);
          }}
        />
      </Encabezado>
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
        Formato provisional (separador <code>;</code> o <code>,</code>; UTF-8 o Windows-1252):{' '}
        <code style={{ wordBreak: 'break-all', color: 'var(--text-primary)' }}>{FORMATO_CSV[que]}</code>.{' '}
        {que === 'clientes' ? 'Un cliente sin autorización de tratamiento de datos (SI y fecha) no se importa.' : 'El dueño (cliente) debe existir antes.'}
      </p>
      <Campo etiqueta="Archivo" id="csv_archivo" style={{ marginBottom: '16px' }}>
        <input
          id="csv_archivo"
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            setArchivo(e.target.files?.[0] || null);
            setResultado(null);
          }}
          style={{ ...estilos.entrada, padding: '6px' }}
        />
      </Campo>
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
        <Boton variante="secundario" disabled={!archivo || estado.cargando} onClick={() => enviar(true)}>
          {estado.cargando ? 'Procesando...' : 'Validar (no guarda)'}
        </Boton>
        <Boton disabled={!archivo || !validado || validado.nuevas === 0 || estado.cargando} onClick={() => enviar(false)}>
          {validado ? `Importar ${validado.nuevas} nuevos` : 'Importar'}
        </Boton>
      </div>
      <ErrorApi error={estado.error} />
      {resultado && (
        <Mensaje tipo={resultado.rechazadas.length ? 'alerta' : resultado.en_seco ? 'info' : 'exito'} titulo={resultado.en_seco ? 'Validación (no se guardó nada)' : 'Importación terminada'}>
          {resultado.filas} filas: {resultado.nuevas} {resultado.en_seco ? 'se crearían' : 'creadas'}, {resultado.existentes} ya existían, {resultado.rechazadas.length} rechazadas.
          {resultado.rechazadas.length > 0 && (
            <ul style={{ margin: '6px 0 0 18px', maxHeight: '240px', overflowY: 'auto' }}>
              {resultado.rechazadas.map((f) => (
                <li key={f.fila}>
                  Fila {f.fila}: {f.problemas.join('; ')}
                </li>
              ))}
            </ul>
          )}
        </Mensaje>
      )}
    </div>
  );
}

/** Dirección del formulario público de registro (para imprimir como QR en la estación). */
function RegistroQr() {
  const url = `${window.location.origin}/registro`;
  const [copiado, setCopiado] = useState(false);
  return (
    <div style={{ maxWidth: '640px' }}>
      <Encabezado titulo="Registro por QR" subtitulo="El cliente se registra una sola vez desde su celular" />
      <div style={{ ...estilos.caja, padding: '20px' }}>
        <span style={estilos.etiqueta}>Dirección del formulario</span>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          <code style={{ ...estilos.entrada, width: 'auto', flex: 1, fontSize: '13px' }}>{url}</code>
          <Boton
            variante="secundario"
            onClick={() => {
              navigator.clipboard?.writeText(url).then(() => setCopiado(true));
            }}
          >
            {copiado ? 'Copiado' : 'Copiar'}
          </Boton>
          <a href="/registro" target="_blank" rel="noreferrer" style={{ ...estilos.botonPrimario, textDecoration: 'none' }}>
            ABRIR
          </a>
        </div>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '14px' }}>
          Genere el código QR con esta dirección y ubíquelo en la pista. El cliente digita su documento, nombre y correo y autoriza el tratamiento de sus datos; después, al tanquear, solo dice su número de documento.
        </p>
        <p style={{ fontSize: '12px', color: COLOR.alerta, marginTop: '8px' }}>
          Antes de publicarlo fuera del banco de pruebas, la empresa debe aportar su política de tratamiento de datos (el texto actual es provisional).
        </p>
      </div>
    </div>
  );
}
