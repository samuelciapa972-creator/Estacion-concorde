import { ChartColumn, Fuel, LogOut, Menu, Search, TriangleAlert, Truck, Users, X, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { cerrarSesion } from "../api/sesion";
import type { Rol } from "../api/tipos";
import { Marca } from "./Marca";
import { useSesion } from "./SesionContexto";

/** Exige sesión (y rol, si se indica); si no, manda al ingreso y vuelve después. */
export function Protegida({ rol, children }: { rol?: Rol; children: ReactNode }) {
  const sesion = useSesion();
  const ubicacion = useLocation();
  if (!sesion) return <Navigate to="/ingreso" replace state={{ volver: ubicacion.pathname }} />;
  if (rol && sesion.usuario.rol !== rol) {
    return (
      <p role="alert" className="p-6 text-lg">
        Esta sección es solo para administradores.
      </p>
    );
  }
  return <>{children}</>;
}

interface Seccion {
  ruta: string;
  texto: string;
  icono: LucideIcon;
  soloAdmin?: boolean;
}

const SECCIONES: Seccion[] = [
  { ruta: "/pista", texto: "Pista", icono: Fuel },
  { ruta: "/admin/ventas", texto: "Ventas", icono: Search, soloAdmin: true },
  { ruta: "/admin/reportes", texto: "Reportes", icono: ChartColumn, soloAdmin: true },
  { ruta: "/admin/clientes", texto: "Clientes", icono: Users, soloAdmin: true },
  { ruta: "/admin/vehiculos", texto: "Vehículos", icono: Truck, soloAdmin: true },
  { ruta: "/admin/anomalias", texto: "Anomalías", icono: TriangleAlert, soloAdmin: true },
];

const ANCHO = "mx-auto w-full max-w-[1400px]";

export function Marco() {
  const sesion = useSesion();
  const { pathname } = useLocation();
  const [abierto, setAbierto] = useState(false);
  const secciones = SECCIONES.filter((s) => sesion?.usuario.rol === "ADMIN" || !s.soloAdmin);
  const enlace = ({ isActive }: { isActive: boolean }) =>
    `flex min-h-12 items-center gap-2 rounded-lg px-3 text-base font-semibold transition-colors duration-150 ${
      isActive ? "bg-dorado text-negro" : "text-crema hover:bg-negro-suave active:bg-negro-suave"
    }`;
  const enlaces = (alElegir?: () => void) =>
    secciones.map(({ ruta, texto, icono: Icono }) => (
      <NavLink key={ruta} to={ruta} className={enlace} onClick={alElegir}>
        <Icono className="size-5" aria-hidden="true" />
        {texto}
      </NavLink>
    ));
  const salir = (
    <button
      type="button"
      onClick={cerrarSesion}
      className="boton-tactil flex min-h-12 items-center gap-2 rounded-xl border-2 border-dorado/70 px-4 text-base font-semibold text-dorado hover:bg-negro-suave active:bg-negro-suave"
    >
      <LogOut className="size-5" aria-hidden="true" />
      Salir
    </button>
  );
  return (
    <div className="flex min-h-screen flex-col bg-fondo">
      <header className="border-b-2 border-dorado bg-negro px-4 py-2">
        <div className={`${ANCHO} flex flex-wrap items-center gap-3`}>
          <Marca />
          {/* Computador y tablet: menú completo. Celular: botón que despliega el menú. */}
          <nav className="hidden flex-wrap gap-1 md:flex" aria-label="Secciones">
            {enlaces()}
          </nav>
          <span className="ml-auto hidden text-base text-crema md:inline">{sesion?.usuario.nombre}</span>
          <span className="hidden md:inline-flex">{salir}</span>
          <button
            type="button"
            className="boton-tactil ml-auto flex size-12 items-center justify-center rounded-xl border-2 border-dorado/70 text-dorado md:hidden"
            aria-expanded={abierto}
            aria-controls="menu-celular"
            aria-label={abierto ? "Cerrar menú" : "Abrir menú"}
            onClick={() => setAbierto(!abierto)}
          >
            {abierto ? <X className="size-6" aria-hidden="true" /> : <Menu className="size-6" aria-hidden="true" />}
          </button>
        </div>
        {abierto && (
          <nav id="menu-celular" aria-label="Menú" className="animar-aviso mt-2 flex flex-col gap-1 pb-2 md:hidden">
            {enlaces(() => setAbierto(false))}
            <span className="px-3 pt-2 text-sm text-crema/80">{sesion?.usuario.nombre}</span>
            {salir}
          </nav>
        )}
      </header>
      <div className="bg-amber-100 px-4 py-1 text-center text-sm text-amber-900">
        Banco de pruebas: no se envía nada a la DIAN.
      </div>
      {/* La clave reinicia la animación de entrada en cada cambio de pantalla. */}
      <main key={pathname} className={`${ANCHO} animar-entrada flex-1 p-4`}>
        <Outlet />
      </main>
    </div>
  );
}
