import { defineConfig, devices } from "@playwright/test";

const baseURL = "http://127.0.0.1:4173";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    // Always rebuild so standalone local runs cannot reuse a stale dist/ bundle.
    command: "npm run build && npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
    url: baseURL,
    env: { VITE_ORCHESTRATOR_URL: "." },
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
