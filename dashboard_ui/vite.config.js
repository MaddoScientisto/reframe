import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8090',
      '/photos': 'http://127.0.0.1:8090',
      '/dithered': 'http://127.0.0.1:8090',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
