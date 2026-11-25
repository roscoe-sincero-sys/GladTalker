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
// A second engine that requires a token, for the documented http://…/#token=… launch.
const tokenPort = port + 1;
export const E2E_TOKEN = 'e2e-token-123';
const engineCommand = (p, workspace, extra = '') =>
  `"${python}" studio_server.py --port ${p} --workspace "${path.join(repo, 'results', workspace)}" --log-level warning${extra}`;

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
  webServer: [
    {
      command: engineCommand(port, 'studio-e2e'),
      cwd: repo,
      url: `http://127.0.0.1:${port}/health`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: engineCommand(tokenPort, 'studio-e2e-token', ` --token ${E2E_TOKEN}`),
      cwd: repo,
      url: `http://127.0.0.1:${tokenPort}/health`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
