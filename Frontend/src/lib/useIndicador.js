import { useLayoutEffect, useRef } from 'react';

/**
 * Indicador que se desliza hasta la opción activa (pestañas, segmentado, barra lateral).
 * El contenedor marca la opción activa con `data-activo`; el indicador es un `.indicador` dentro del contenedor
 * (que debe tener `position: relative`). Se mueve con transform; la primera vez aparece en su sitio, sin deslizar.
 * `subrayado`: solo sigue el ancho y la posición horizontal (la línea bajo la pestaña tiene su propio alto).
 */
export function useIndicador(activo, { subrayado = false } = {}) {
  const contenedor = useRef(null);
  const indicador = useRef(null);

  useLayoutEffect(() => {
    const caja = contenedor.current;
    const marca = indicador.current;
    if (!caja || !marca) return undefined;
    const medir = () => {
      const el = caja.querySelector('[data-activo="true"]');
      if (!el) {
        marca.style.visibility = 'hidden';
        return;
      }
      marca.style.visibility = '';
      marca.style.transform = `translate(${el.offsetLeft}px, ${subrayado ? 0 : el.offsetTop}px)`;
      marca.style.width = `${el.offsetWidth}px`;
      if (!subrayado) marca.style.height = `${el.offsetHeight}px`;
    };
    medir();
    // Ya ubicado: desde aquí los cambios se deslizan.
    const cuadro = requestAnimationFrame(() => marca.setAttribute('data-listo', ''));
    const observador = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(medir);
    observador?.observe(caja);
    return () => {
      cancelAnimationFrame(cuadro);
      observador?.disconnect();
    };
  }, [activo, subrayado]);

  return { contenedor, indicador };
}
