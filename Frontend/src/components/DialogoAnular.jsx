import { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';
import { nuevaClave } from '../lib/clave';
import { Boton, Campo, ErrorApi } from './ui';
import { estilos } from './estilos';

const MAX_MOTIVO = 500;

/**
 * Diálogo para anular una factura con una nota crédito. Usa <dialog> modal: el foco queda dentro, Escape cancela y,
 * al cerrar, el foco vuelve al botón que lo abrió. La clave se genera UNA vez al abrir (la API es idempotente por
 * clave: un doble clic o un reintento no crean dos notas). Se monta solo mientras está abierto.
 */
export default function DialogoAnular({ factura, onCerrar, onAnulada }) {
  const dialogo = useRef(null);
  const [clave] = useState(nuevaClave);
  // El botón que abrió el diálogo, leído antes de que autoFocus mueva el foco.
  const [opener] = useState(() => document.activeElement);
  const [motivo, setMotivo] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState(null);
  const motivoLimpio = motivo.trim();

  useEffect(() => {
    const d = dialogo.current;
    if (d && !d.open) d.showModal();
    // Al cerrar, el foco vuelve al botón que abrió el diálogo (si sigue en la página).
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [opener]);

  const anular = async (e) => {
    e.preventDefault();
    if (!motivoLimpio || enviando) return;
    setEnviando(true);
    setError(null);
    try {
      onAnulada(await api.anular(factura.id, { clave, motivo: motivoLimpio }));
    } catch (err) {
      setError(err);
      setEnviando(false);
    }
  };

  return (
    <dialog
      ref={dialogo}
      aria-labelledby="anular_titulo"
      onCancel={(e) => {
        e.preventDefault();
        if (!enviando) onCerrar();
      }}
      style={{ ...estilos.caja, boxShadow: 'none', border: '1px solid var(--text-primary)', color: 'var(--text-primary)', padding: '24px', margin: 'auto', width: 'min(480px, calc(100vw - 32px))', maxWidth: 'none' }}
    >
      <form onSubmit={anular} noValidate style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <h2 id="anular_titulo" style={{ ...estilos.titulo, overflowWrap: 'anywhere' }}>Anular la factura {factura.numero}</h2>
        <p style={{ fontSize: '12px', margin: 0 }}>
          La factura quedará anulada ante la DIAN. La venta no se podrá volver a facturar: registre una venta nueva con los datos correctos.
        </p>
        <ErrorApi error={error} />
        <Campo etiqueta="Motivo de la anulación" id="anular_motivo" ayuda={`${motivo.length} de ${MAX_MOTIVO} caracteres`}>
          <textarea
            id="anular_motivo"
            autoFocus
            required
            rows={3}
            maxLength={MAX_MOTIVO}
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            disabled={enviando}
            style={{ ...estilos.entrada, resize: 'vertical', fontFamily: 'inherit' }}
          />
        </Campo>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', flexWrap: 'wrap' }}>
          <Boton variante="secundario" onClick={onCerrar} disabled={enviando}>
            Cancelar
          </Boton>
          <Boton type="submit" disabled={!motivoLimpio || enviando}>
            {enviando ? 'Anulando…' : 'Anular factura'}
          </Boton>
        </div>
      </form>
    </dialog>
  );
}
