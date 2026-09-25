import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

// 开发期把 /api 代理到 FastAPI，前端只认相对路径，
// 这样构建产物挂到后端时不用改任何代码（SRS §5.2）。
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
