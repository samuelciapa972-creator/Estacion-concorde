/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// En desarrollo la API corre aparte (`make api`, puerto 8000) y Vite la sirve bajo /api: mismo origen, sin CORS.
const API = process.env.ESTACION_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icono.svg"],
      manifest: {
        name: "EDS CONCORDE — pista",
        short_name: "EDS CONCORDE",
        description: "Facturación en la pista (banco de pruebas)",
        lang: "es-CO",
        display: "standalone",
        orientation: "landscape",
        start_url: "/",
        background_color: "#0e0e0e",
        theme_color: "#0e0e0e",
        icons: [{ src: "icono.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
      },
      workbox: {
        // Solo el "cascarón" de la aplicación. Las respuestas de la API NUNCA se guardan en caché:
        // una venta pendiente o el estado de una factura vieja en pantalla llevaría a errores en la pista.
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [],
      },
    }),
  ],
  build: {
    // SUPUESTO: Chrome de la tablet sin confirmar (ver PROMPT §6). Objetivo conservador.
    target: ["es2020", "chrome87"],
  },
  server: {
    host: true,
    proxy: { "/api": { target: API, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
  preview: {
    proxy: { "/api": { target: API, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/preparar.ts"],
    css: false,
  },
});
