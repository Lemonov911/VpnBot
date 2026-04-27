import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const isCI = process.env.CI === 'true'

export default defineConfig({
  base: isCI ? '/VpnBot/' : '/',
  plugins: [react(), tailwindcss()],
  server: {
    allowedHosts: true,
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})
