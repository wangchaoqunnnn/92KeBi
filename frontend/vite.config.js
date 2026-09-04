import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 产物由后端 FastAPI 静态挂载；base 用相对路径 './'，
// 确保部署到任意子路径/反向代理前缀下静态资源都能相对加载
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../backend/app/static',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8720',
    },
  },
})
