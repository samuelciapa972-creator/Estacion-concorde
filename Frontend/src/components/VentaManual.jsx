import { useEffect, useState } from 'react';
import { api, obtenerCatalogos } from '../services/api';
import { galones, leerGalones, leerPesos, pesos } from '../lib/formato';
import { Boton, Campo, ErrorApi as MostrarError, Rejilla } from './ui';
import { estilos } from './estilos';

/**
 * Modo manual: el bombero digita lo que marcó el surtidor (respaldo mientras no hay captura automática).
 * Al guardar pasa directo a facturarla, de contado o a crédito. La placa es opcional: a un particular no se le pide.
 */
export default function VentaManual({ onCancelar, onGuardada }) {
  const [catalogos, setCatalogos] = useState({ surtidores: [], productos: [] });
  const [d, setD] = useState({ surtidor_id: '', lado: '', producto: '', forma_pago: 'CONTADO', volumen: '', valor: '', placa: '' });
  const [errores, setErrores] = useState({});
  const [error, setError] = useState(null);
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    obtenerCatalogos().then(setCatalogos).catch(setError);
  }, []);

  const cambiar = (campo) => (e) => setD({ ...d, [campo]: e.target.value });
  const vol = leerGalones(d.volumen);
  const val = leerPesos(d.valor);

  const guardar = async (e) => {
    e.preventDefault();
    const encontrados = {};
    if (!d.surtidor_id) encontrados.surtidor_id = 'elija el surtidor';
    if (!d.lado) encontrados.lado = 'elija el lado';
    if (!d.producto) encontrados.producto = 'elija el producto';
    if (!vol) encontrados.volumen = 'galones con hasta 3 decimales, p. ej. 1,861';
    if (!val) encontrados.valor = 'valor en pesos sin centavos, p. ej. 29757';
    setErrores(encontrados);
    if (Object.keys(encontrados).length) return;
    setGuardando(true);
    setError(null);
    try {
      const placa = d.placa.toUpperCase().replace(/[\s.-]/g, '') || null;
      const datos = { surtidor_id: Number(d.surtidor_id), lado: d.lado, producto: d.producto, volumen: vol, valor: val, forma_pago: d.forma_pago, placa };
      const { id } = await api.crearVentaManual(datos);
      const surtidor = catalogos.surtidores.find((s) => s.id === datos.surtidor_id)?.marca || `#${datos.surtidor_id}`;
      onGuardada({ ...datos, origen_tipo: 'venta_manual', origen_id: id, surtidor, ppu: null });
    } catch (err) {
      setError(err);
    } finally {
      setGuardando(false);
    }
  };

  return (
    <div className="vista pagina">
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: '700', color: 'var(--text-primary)' }}>Nueva venta manual</h1>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Digite lo que marcó el surtidor</p>
      </div>
      <form onSubmit={guardar} noValidate style={{ ...estilos.caja, maxWidth: '720px', borderRadius: '6px', padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <MostrarError error={error} />
        <Rejilla columnas="1fr 1fr">
          <Campo etiqueta="Surtidor" id="vm_surtidor" error={errores.surtidor_id}>
            <select id="vm_surtidor" value={d.surtidor_id} onChange={cambiar('surtidor_id')} style={estilos.entrada}>
              <option value="">Elija...</option>
              {catalogos.surtidores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.marca}
                  {s.descripcion ? ` — ${s.descripcion}` : ''}
                </option>
              ))}
            </select>
          </Campo>
          <Campo etiqueta="Lado" id="vm_lado" error={errores.lado}>
            <div id="vm_lado" style={{ display: 'flex', gap: '8px' }}>
              {['A', 'B'].map((l) => (
                <Boton key={l} variante={d.lado === l ? 'primario' : 'secundario'} aria-pressed={d.lado === l} style={{ flex: 1 }} onClick={() => setD({ ...d, lado: l })}>
                  {l}
                </Boton>
              ))}
            </div>
          </Campo>
          <Campo etiqueta="Producto" id="vm_producto" error={errores.producto}>
            <select id="vm_producto" value={d.producto} onChange={cambiar('producto')} style={estilos.entrada}>
              <option value="">Elija...</option>
              {catalogos.productos.map((p) => (
                <option key={p.id} value={p.codigo}>
                  {p.nombre}
                </option>
              ))}
            </select>
          </Campo>
          <Campo etiqueta="Forma de pago" id="vm_pago">
            <select id="vm_pago" value={d.forma_pago} onChange={cambiar('forma_pago')} style={estilos.entrada}>
              <option value="CONTADO">Contado</option>
              <option value="CREDITO">Crédito</option>
            </select>
          </Campo>
          <Campo etiqueta="Placa (opcional)" id="vm_placa" ayuda="Déjela vacía si el cliente no la da">
            <input id="vm_placa" autoComplete="off" autoCapitalize="characters" maxLength={20} value={d.placa} onChange={cambiar('placa')} style={estilos.entrada} />
          </Campo>
          <Campo etiqueta="Galones" id="vm_volumen" error={errores.volumen} ayuda="Como lo marca el surtidor, con 3 decimales">
            <input id="vm_volumen" inputMode="decimal" autoComplete="off" value={d.volumen} onChange={cambiar('volumen')} style={estilos.entrada} />
          </Campo>
          <Campo etiqueta="Valor cobrado (pesos)" id="vm_valor" error={errores.valor} ayuda="El valor que marca el surtidor">
            <input id="vm_valor" inputMode="numeric" autoComplete="off" value={d.valor} onChange={cambiar('valor')} style={estilos.entrada} />
          </Campo>
        </Rejilla>
        {vol && val && (
          <div className="aviso" style={{ background: 'var(--bg-input)', padding: '12px', borderRadius: '4px', fontSize: '14px', color: 'var(--text-primary)' }}>
            {galones(vol)} gal por {pesos(val)}
          </div>
        )}
        <div style={{ display: 'flex', gap: '10px' }}>
          <Boton variante="secundario" onClick={onCancelar}>
            Cancelar
          </Boton>
          <Boton type="submit" tamano="grande" disabled={guardando} style={{ flex: 1 }}>
            {guardando ? 'Guardando…' : 'Guardar y facturar'}
          </Boton>
        </div>
      </form>
    </div>
  );
}
