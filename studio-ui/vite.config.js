import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// `npm run dev` serves the UI on :5173 and forwards API calls to a running engine
// (`python studio_server.py`, default http://127.0.0.1:7860; override with GLADTALKER_ENGINE).
const engine = process.env.GLADTALKER_ENGINE || 'http://127.0.0.1:7860';
const API = '^/(health|hardware|models|voices|uploads|cast|tts|script|audio|render|jobs)(/|$)';

// The engine rejects browser requests from foreign origins (the dev server is one), so the dev
// proxy drops the Origin header. Production builds are served by the engine itself (same origin).
const stripOrigin = (proxy) => {
  proxy.on('proxyReq', (req) => req.removeHeader('origin'));
  proxy.on('proxyReqWs', (req) => req.removeHeader('origin'));
};

export default defineConfig({
  plugins: [svelte()],
  base: './', // relative URLs: works when served at / by the engine and inside the Tauri shell
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2022', sourcemap: false },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      [API]: { target: engine, changeOrigin: true, configure: stripOrigin },
      '/ws': { target: engine.replace(/^http/, 'ws'), ws: true, changeOrigin: true, configure: stripOrigin },
    },
  },
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.js'],
    setupFiles: ['tests/setup.js'],
  },
});
