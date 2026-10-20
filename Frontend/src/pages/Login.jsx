import { useState } from 'react';
import { loginUsuario } from '../services/api';
import logoConcorde from '../assets/images/LogoH1.png';
import portada from '../assets/images/ImagenCober.png';

export default function Login({ onLoginExitoso, tema, setTema }) {
  const [usuario, setUsuario] = useState('');
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const [cargando, setCargando] = useState(false);
  // Cada intento fallido vuelve a montar el aviso para que tiemble otra vez.
  const [intentos, setIntentos] = useState(0);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setCargando(true);
    try {
      await loginUsuario(usuario, pin);
      onLoginExitoso();
    } catch {
      setError('Usuario o PIN incorrectos');
      setIntentos((n) => n + 1);
    } finally {
      setCargando(false);
    }
  };

  return (
    <div className="ingreso" style={{ backgroundColor: 'var(--bg-main)' }}>
      {/* Foto de la marca a la izquierda (se oculta en tablet y celular). */}
      <div className="ingreso__foto" aria-hidden="true">
        <img src={portada} alt="" />
        <div className="ingreso__lema">
          <strong>Concorde, mil razones para viajar</strong>
          <span>Estación de servicio · Facturación electrónica</span>
        </div>
      </div>

      <div className="ingreso__panel">
      {/* Botón Flotante para cambiar Modo Claro / Oscuro */}
      <button 
        onClick={() => setTema(tema === 'dark' ? 'light' : 'dark')}
        className="boton--secundario"
        style={{
          position: 'absolute',
          zIndex: 1,
          top: '24px',
          right: '24px',
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          color: 'var(--text-primary)',
          padding: '8px 14px',
          borderRadius: '4px',
          fontSize: '12px',
          fontWeight: '600',
          cursor: 'pointer',
          boxShadow: 'var(--shadow)'
        }}
      >
        {tema === 'dark' ? 'Modo Luz' : 'Modo Noche'}
      </button>

      {/* Tarjeta de Login Centrada */}
      <div className="vista" style={{
        background: 'var(--bg-card)',
        borderRadius: '6px',
        padding: '36px 32px',
        width: '100%',
        maxWidth: '380px',
        boxShadow: 'var(--sombra-panel)',
        textAlign: 'center'
      }}>
        {/* Logo de Concorde con Inversión de Color en Tema Oscuro */}
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '20px' }}>
          <img 
            src={logoConcorde} 
            alt="EDS Concorde Logo" 
            style={{ 
              maxWidth: '220px',
              height: 'auto', 
              maxHeight: '60px',
              objectFit: 'contain',
              filter: tema === 'dark' ? 'brightness(0) invert(1)' : 'none',
              transition: 'filter 0.2s ease'
            }} 
          />
        </div>

        <p style={{ 
          fontSize: '13px', 
          color: 'var(--text-secondary)', 
          marginBottom: '28px'
        }}>
          Estación de servicio · facturación
        </p>

        {error && (
          <div key={intentos} role="alert" className="aviso--sacudir" style={{
            background: 'rgba(225, 29, 72, 0.1)',
            border: '1px solid #e11d48',
            color: '#e11d48',
            padding: '10px',
            borderRadius: '6px',
            fontSize: '12px',
            marginBottom: '20px',
            fontWeight: '500'
          }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ textAlign: 'left' }}>
          <div style={{ marginBottom: '18px' }}>
            <label style={{ 
              display: 'block', 
              fontSize: '12px', 
              fontWeight: '600', 
              color: 'var(--text-secondary)', 
              marginBottom: '6px'
            }}>
              Usuario
            </label>
            <input
              type="text"
              value={usuario}
              onChange={(e) => setUsuario(e.target.value)}
              placeholder="ej. admin1"
              required
              style={{
                width: '100%',
                padding: '11px 14px',
                background: 'var(--bg-input)',
                border: '1px solid var(--border-color)',
                color: 'var(--text-primary)',
                borderRadius: '4px',
                fontSize: '13px'
              }}
            />
          </div>

          <div style={{ marginBottom: '24px' }}>
            <label style={{ 
              display: 'block', 
              fontSize: '12px', 
              fontWeight: '600', 
              color: 'var(--text-secondary)', 
              marginBottom: '6px'
            }}>
              PIN de acceso
            </label>
            <input
              type="password"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              placeholder="••••"
              required
              style={{
                width: '100%',
                padding: '11px 14px',
                background: 'var(--bg-input)',
                border: '1px solid var(--border-color)',
                color: 'var(--text-primary)',
                borderRadius: '4px',
                fontSize: '13px'
              }}
            />
          </div>

          <button
            type="submit"
            disabled={cargando}
            className="boton--primario"
            style={{
              width: '100%',
              padding: '12px',
              backgroundColor: 'var(--btn-primary)',
              color: 'var(--btn-primary-text)',
              border: 'none',
              borderRadius: '4px',
              fontWeight: '600',
              fontSize: '13px',
              cursor: 'pointer'
            }}
          >
            {cargando ? 'Ingresando…' : 'Iniciar sesión'}
          </button>
        </form>
      </div>

      <p style={{ marginTop: '20px', fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
        Concorde, mil razones para viajar
      </p>
      </div>
    </div>
  );
}