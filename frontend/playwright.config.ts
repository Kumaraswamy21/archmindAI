import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.browser.ts",
  workers: 1,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3000", browserName: "chromium", headless: true,
    trace: "retain-on-failure",
    launchOptions: process.env.ARCHMIND_CHROMIUM_EXECUTABLE ? {
      executablePath: process.env.ARCHMIND_CHROMIUM_EXECUTABLE,
    } : undefined,
  },
  reporter: "list",
});
