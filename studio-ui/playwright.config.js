import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// End-to-end tests drive the built UI (`npm run build`) served by a real engine.
// The engine is started automatically; point GLADTALKER_PYTHON at your GladTalker venv's Python
// if it isn't ../venv (e.g. ../venv-dml/Scripts/python.exe for AMD GPUs on Windows).
const repo = path.resolve(import.meta.dirname, '..');
const defaultPython = process.platform === 'win32'
  ? path.join(repo, 'venv', 'Scripts', 'python.exe')
  : path.join(repo, 'venv', 'bin', 'python');
const python = process.env.GLADTALKER_PYTHON ? path.resolve(process.env.GLADTALKER_PYTHON) : defaultPython;
const port = Number(process.env.GLADTALKER_E2E_PORT || 7863);

export default defineConfig({
  testDir: 'e2e',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `"${python}" studio_server.py --port ${port} --workspace "${path.join(repo, 'results', 'studio-e2e')}" --log-level warning`,
    cwd: repo,
    url: `http://127.0.0.1:${port}/health`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
