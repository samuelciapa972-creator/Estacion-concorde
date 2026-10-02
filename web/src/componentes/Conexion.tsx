import { useSyncExternalStore } from "react";

function suscribir(f: () => void) {
  window.addEventListener("online", f);
  window.addEventListener("offline", f);
  return () => {
    window.removeEventListener("online", f);
    window.removeEventListener("offline", f);
  };
}

export function useEnLinea(): boolean {
  return useSyncExternalStore(suscribir, () => navigator.onLine);
}

/** Aviso fijo cuando la tablet pierde la red: sin conexión no se factura (nada se guarda para enviar luego). */
export function AvisoSinConexion() {
  const enLinea = useEnLinea();
  if (enLinea) return null;
  return (
    <div role="alert" className="sticky top-0 z-50 bg-amber-500 px-4 py-3 text-center text-lg font-bold text-black">
      Sin conexión. No se puede facturar hasta que vuelva la red.
    </div>
  );
}
