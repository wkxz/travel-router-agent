import { defineConfig } from 'vite';

// 开发时前端跑在 5173，接口转发到 python -m server（默认 8000）；生产环境由 server 直接托管 dist/，无需代理。
const backend = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
const apiRoutes = ['/chat', '/conversations', '/history', '/clear-history', '/delete-conversation', '/health'];

export default defineConfig({
  server: {
    port: 5173,
    proxy: Object.fromEntries(apiRoutes.map(route => [route, { target: backend, changeOrigin: true }])),
  },
});
