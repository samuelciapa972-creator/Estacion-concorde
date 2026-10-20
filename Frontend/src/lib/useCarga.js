import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Carga datos de la API: { datos, error, cargando, recargar, previos, consultas }. Vuelve a cargar cuando cambian `deps`.
 * Una respuesta vieja (de filtros anteriores) nunca pisa a una más nueva. `previos` son los datos de la carga
 * anterior con los MISMOS filtros (null tras cambiarlos), para resaltar lo que llegó nuevo; `consultas` cuenta
 * las cargas que terminaron bien y `actualizado` es la hora (ms) de la última.
 */
export function useCarga(cargar, deps, { cadaMs } = {}) {
  const [estado, setEstado] = useState({ datos: null, previos: null, error: null, cargando: true, consultas: 0, actualizado: null });
  const turno = useRef(0);
  const filtrosNuevos = useRef(true);
  const fn = useRef(cargar);
  fn.current = cargar;

  const recargar = useCallback(async () => {
    const mio = ++turno.current;
    setEstado((e) => ({ ...e, cargando: true }));
    try {
      const datos = await fn.current();
      if (mio !== turno.current) return;
      const sinBase = filtrosNuevos.current;
      filtrosNuevos.current = false;
      setEstado((e) => ({ datos, previos: sinBase ? null : e.datos, error: null, cargando: false, consultas: e.consultas + 1, actualizado: Date.now() }));
    } catch (error) {
      if (mio === turno.current) setEstado((e) => ({ ...e, error, cargando: false }));
    }
  }, []);

  useEffect(() => {
    filtrosNuevos.current = true;
    recargar();
    if (!cadaMs) return undefined;
    const intervalo = setInterval(recargar, cadaMs);
    return () => clearInterval(intervalo);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, cadaMs]);

  return { ...estado, recargar };
}
