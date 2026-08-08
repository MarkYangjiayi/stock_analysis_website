import { expect, test } from "@playwright/test";


const scan = {
    id: 42,
    trigger: "manual",
    status: "queued",
    requested_limit: 20,
    threshold_pct: 4,
    universe_as_of: null,
    quote_as_of: null,
    results: [],
    error_message: null,
    created_at: "2026-07-30T15:29:00Z",
    started_at: null,
    finished_at: null,
};

test("queues, polls and renders a source-backed anomaly scan", async ({ page }) => {
    let statusPolls = 0;
    await page.route("**/api/market/anomalies", async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: "null",
        });
    });
    await page.route("**/api/market/anomalies/scans", async (route) => {
        expect(route.request().method()).toBe("POST");
        await route.fulfill({
            status: 202,
            contentType: "application/json",
            body: JSON.stringify(scan),
        });
    });
    await page.route("**/api/market/anomalies/scans/42", async (route) => {
        statusPolls += 1;
        const completed = statusPolls >= 2;
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                ...scan,
                status: completed ? "completed" : "running",
                universe_as_of: completed ? "2026-07-29" : null,
                quote_as_of: completed ? "2026-07-30T15:30:00Z" : null,
                started_at: "2026-07-30T15:29:01Z",
                finished_at: completed ? "2026-07-30T15:30:00Z" : null,
                results: completed ? [{
                    ticker: "BE.US",
                    company_name: "Bloom Energy",
                    date: "2026-07-30",
                    quote_timestamp: "2026-07-30T15:30:00Z",
                    price_change: 25.73,
                    ai_analysis: "Earnings drove the move [1].",
                    attribution_status: "completed",
                    news: [{
                        title: "Bloom Energy earnings",
                        link: "https://finance.yahoo.com/bloom",
                        pub_date: "2026-07-30T14:00:00Z",
                        summary: "Results exceeded expectations.",
                        publisher: "Example News",
                    }],
                    top_news_links: ["https://finance.yahoo.com/bloom"],
                }] : [],
            }),
        });
    });

    await page.goto("/anomalies");
    await expect(
        page.getByRole("heading", { name: "No completed scan yet" }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Run scan" }).click();
    await expect(page.getByRole("button", { name: "Scanning…" })).toBeDisabled();
    const result = page.locator("article").filter({ hasText: "Bloom Energy" });
    await expect(
        result.getByText("Bloom Energy", { exact: true }).last(),
    ).toBeVisible({ timeout: 10_000 });
    await expect(result.getByText("+25.73%")).toBeVisible();
    await expect(
        result.getByRole("link", { name: /Bloom Energy earnings/ }),
    ).toHaveAttribute("href", "https://finance.yahoo.com/bloom");
    await expect(page.getByRole("button", { name: "Refresh scan" })).toBeEnabled();
    expect(statusPolls).toBe(2);
});
