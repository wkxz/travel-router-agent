import { defineConfig } from 'vite';

// 本地开发代理：把 /chat 与 /chat/stop 转发到本地 uvicorn 实例 (8088)
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/chat': { target: 'http://127.0.0.1:8088', changeOrigin: true },
      '/chat/stop': { target: 'http://127.0.0.1:8088', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8088', changeOrigin: true },
    },
  },
});
