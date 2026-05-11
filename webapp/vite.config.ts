import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// base-path выбирается через VITE_BASE_PATH:
//   '/VpnBot/' — для GitHub Pages (lemonov911.github.io/VpnBot/)
//   '/'        — для production maxvpnesim.com и dev-сервера
// CI делает два билда с разным VITE_BASE_PATH (см. deploy-webapp.yml).
const base = process.env.VITE_BASE_PATH || '/'

export default defineConfig({
  base,
  plugins: [react(), tailwindcss()],
  server: {
    allowedHosts: true,
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})
