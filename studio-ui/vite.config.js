import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// `npm run dev` serves the UI on :5173 and forwards API calls to a running engine
// (`python studio_server.py`, default http://127.0.0.1:7860; override with GLADTALKER_ENGINE).
const engine = process.env.GLADTALKER_ENGINE || 'http://127.0.0.1:7860';
const API = '^/(health|hardware|models|voices|uploads|cast|tts|script|audio|render|jobs)(/|$)';

// The proxy forwards requests untouched, including the browser's Origin and Host headers, so the
// engine's Origin check still applies: a page on http://localhost:5173 is the same origin as the
// Host it asked for, and any other site's Origin is refused with 403, exactly as without Vite.
// (Dropping Origin would turn that check off; changeOrigin would rewrite Host so the dev page
// looked cross-origin and was refused.)

export default defineConfig({
  plugins: [svelte()],
  base: './', // relative URLs: works when served at / by the engine and inside the Tauri shell
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2022', sourcemap: false },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      [API]: { target: engine },
      '/ws': { target: engine.replace(/^http/, 'ws'), ws: true },
    },
  },
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.js'],
    setupFiles: ['tests/setup.js'],
  },
});
