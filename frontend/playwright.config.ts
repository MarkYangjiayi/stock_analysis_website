import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
    testDir: "./e2e",
    timeout: 30_000,
    fullyParallel: false,
    retries: 0,
    reporter: "list",
    use: {
        baseURL: "http://127.0.0.1:3000",
        trace: "retain-on-failure",
    },
    webServer: [
        {
            command: "cd .. && PYTHONPATH=. DATABASE_URL=sqlite+aiosqlite:///./data/screener_e2e.db ENVIRONMENT=test ./venv/bin/python scripts/seed_screener_e2e.py && PYTHONPATH=. DATABASE_URL=sqlite+aiosqlite:///./data/screener_e2e.db ENVIRONMENT=test ./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8010",
            url: "http://127.0.0.1:8010/docs",
            timeout: 60_000,
            reuseExistingServer: false,
        },
        {
            command: "NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 npm run dev -- --hostname 127.0.0.1 --port 3000",
            url: "http://127.0.0.1:3000/screener",
            timeout: 60_000,
            reuseExistingServer: false,
        },
    ],
    projects: [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
        { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" } },
    ],
});
