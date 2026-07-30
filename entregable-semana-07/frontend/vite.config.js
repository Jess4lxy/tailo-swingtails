import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'https://swingtails-api-yz02.onrender.com',
        changeOrigin: true,
        secure: false,
      }
    }
  },
  preview: {
    proxy: {
      '/api': {
        target: 'https://swingtails-api-yz02.onrender.com',
        changeOrigin: true,
        secure: false,
      }
    }
  }
})
