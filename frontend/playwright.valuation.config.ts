import { defineConfig, devices } from "@playwright/test";
// Focused UI regression with mocked APIs; no database is seeded or changed.
export default defineConfig({
    grep: /edits sourced operating forecasts|historical multiples/,
    testDir: './e2e', timeout: 30000, fullyParallel: false, retries: 0, reporter: 'list',
    use: { baseURL: 'http://127.0.0.1:3012', trace: 'retain-on-failure' },
    webServer: { command: 'npm run start -- --hostname 127.0.0.1 --port 3012', url: 'http://127.0.0.1:3012', timeout: 60000, reuseExistingServer: false },
    projects: [ { name: 'chromium', use: { ...devices['Desktop Chrome'] } }, { name: 'mobile', use: { ...devices['iPhone 13'], browserName: 'chromium' } } ]
});
