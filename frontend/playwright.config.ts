import { defineConfig } from "@playwright/test";

const FRONTEND_PORT = 4173;
const BACKEND_PORT = 8000;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      // Backend: requiere `pip install -e ".[api]"` previo en la raíz del repo.
      // Asumimos HACIENDA_AI_API_KEY no definida (API abierta) para los tests.
      command: `python -m hacienda_ai serve --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: "..",
      url: `http://127.0.0.1:${BACKEND_PORT}/health`,
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
      env: { HACIENDA_AI_API_KEY: "" },
    },
    {
      // Frontend: el comando reconstruye y sirve el bundle. La primera vez
      // tarda algunos segundos; las siguientes reutilizan el server existente
      // (excepto en CI, donde siempre arranca limpio).
      command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${FRONTEND_PORT}`,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
