import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    UnoCSS()
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src')
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:9090',
        changeOrigin: true
      },
      '/healthz': {
        target: 'http://localhost:9090',
        changeOrigin: true
      },
      '/upload': {
        target: 'http://localhost:9090',
        changeOrigin: true
      },
      '/download': {
        target: 'http://localhost:9090',
        changeOrigin: true
      }
    }
  }
})
