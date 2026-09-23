import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: 'chromium',
    viewport: { width: 1440, height: 1050 },
    reducedMotion: 'reduce',
  },
  reporter: 'list',
})
