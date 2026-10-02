import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { ErrorApi } from "./api/cliente";
import { AvisoSinConexion } from "./componentes/Conexion";
import { Marco, Protegida } from "./componentes/Marco";
import { Anomalias } from "./paginas/admin/Anomalias";
import { Clientes } from "./paginas/admin/Clientes";
import { Reportes } from "./paginas/admin/Reportes";
import { Vehiculos } from "./paginas/admin/Vehiculos";
import { Ventas } from "./paginas/admin/Ventas";
import { Facturar } from "./paginas/bombero/Facturar";
import { Pendientes } from "./paginas/bombero/Pendientes";
import { VentaManual } from "./paginas/bombero/VentaManual";
import { Ingreso } from "./paginas/Ingreso";
import { Registro } from "./paginas/publico/Registro";

export const rutas = [
  { path: "/", element: <Navigate to="/pista" replace /> },
  { path: "/ingreso", element: <Ingreso /> },
  { path: "/registro", element: <Registro /> },
  {
    element: (
      <Protegida>
        <Marco />
      </Protegida>
    ),
    children: [
      { path: "/pista", element: <Pendientes /> },
      { path: "/pista/venta-manual", element: <VentaManual /> },
      { path: "/pista/facturar", element: <Facturar /> },
      ...(
        [
          ["/admin/ventas", <Ventas />],
          ["/admin/reportes", <Reportes />],
          ["/admin/anomalias", <Anomalias />],
          ["/admin/clientes", <Clientes />],
          ["/admin/vehiculos", <Vehiculos />],
        ] as const
      ).map(([path, pagina]) => ({ path, element: <Protegida rol="ADMIN">{pagina}</Protegida> })),
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
];

export function nuevoClienteConsultas(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Un 4xx no se arregla reintentando; un corte de red sí puede.
        retry: (n, e) => n < 2 && !(e instanceof ErrorApi && e.estado >= 400 && e.estado < 500),
        refetchOnWindowFocus: true,
      },
      // Nunca reintentar emisiones ni registros por cuenta propia: lo decide la persona (y la clave lo hace seguro).
      mutations: { retry: false },
    },
  });
}

export function App() {
  const [consultas] = useState(nuevoClienteConsultas);
  const [router] = useState(() => createBrowserRouter(rutas));
  return (
    <QueryClientProvider client={consultas}>
      <AvisoSinConexion />
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
