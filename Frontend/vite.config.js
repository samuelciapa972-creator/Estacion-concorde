import process from 'node:process'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
const API = process.env.ESTACION_API ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  // En desarrollo la API corre aparte (`make api`) y se sirve bajo /api, igual que detrás de Caddy en Docker.
  server: {
    host: true,
    proxy: { '/api': { target: API, rewrite: (p) => p.replace(/^\/api/, '') } },
  },
})
