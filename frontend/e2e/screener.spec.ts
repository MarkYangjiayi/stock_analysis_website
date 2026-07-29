import { expect, test } from "@playwright/test";

test("filters, sorts, paginates and restores URL state", async ({ page }, testInfo) => {
    await page.goto("/screener");
    await expect(page.getByRole("heading", { name: "Stock Screener" })).toBeVisible();
    await expect(page.getByText("120", { exact: true })).toBeVisible();
    await expect(page.getByText(/market sessions behind/)).toBeVisible();

    if (testInfo.project.name === "mobile") {
        await page.getByRole("button", { name: /^Filters/ }).click();
        await expect(page.getByRole("heading", { name: "Filters" })).toBeVisible();
    }

    await page.getByRole("button", { name: "Fundamental" }).click();
    await page.getByLabel("P/E operator", { exact: true }).selectOption("lte");
    await page.getByLabel("P/E value", { exact: true }).click();
    await page.keyboard.type("20");
    await expect(page.getByLabel("P/E value", { exact: true })).toHaveValue("20");
    await expect(page.getByText("25", { exact: true })).toBeVisible();
    await expect(page.getByText(/P\/E ≤ 20/)).toBeVisible();
    await expect(page).toHaveURL(/filters=/);

    await page.reload();
    await expect(page.getByText("25", { exact: true })).toBeVisible();
    if (testInfo.project.name === "mobile") {
        await page.getByRole("button", { name: /^Filters/ }).click();
    }
    await expect(page.getByText(/P\/E ≤ 20/)).toBeVisible();

    if (testInfo.project.name === "mobile") {
        await page.getByRole("button", { name: "Show 25 matches" }).click();
    }
    await expect(page.getByText("T024", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "T024", exact: true }))
        .toHaveAttribute("href", "/?ticker=T024.US");
});

test("supports result column selection and empty states", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "column picker behavior is covered on desktop");
    await page.goto("/screener");
    await page.getByText("Columns", { exact: true }).click();
    await page.getByLabel("P/B").check();
    await expect(page.getByRole("button", { name: "P/B" })).toBeVisible();

    await page.getByRole("button", { name: "Fundamental" }).click();
    await page.getByLabel("P/E operator", { exact: true }).selectOption("gt");
    await page.getByLabel("P/E value", { exact: true }).fill("1000");
    await expect(page.getByText("No stocks match these filters")).toBeVisible();
});

test("resets and reapplies a cleared preset", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "preset state is viewport-independent");
    await page.goto("/screener");
    await page.getByRole("button", { name: "Fundamental" }).click();

    const preset = page.getByLabel("P/E preset", { exact: true });
    await preset.selectOption({ label: "Under 1" });
    await expect(page.getByText(/P\/E ≤ 1/)).toBeVisible();
    await page.getByRole("button", { name: "Clear all" }).click();
    await expect(preset).toHaveValue("-1");

    await preset.selectOption({ label: "Under 1" });
    await expect(page.getByText(/P\/E ≤ 1/)).toBeVisible();
});

test("retries the initial metadata request", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "metadata retry is viewport-independent");
    let attempts = 0;
    await page.route("**/api/stocks/screener/metadata", async (route) => {
        attempts += 1;
        if (attempts === 1) {
            await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
            return;
        }
        await route.continue();
    });

    await page.goto("/screener");
    await expect(page.getByText("Unable to load screener fields.")).toBeVisible();
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("120", { exact: true })).toBeVisible();
    expect(attempts).toBe(2);
});

test("recovers from an unknown URL sort field", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "URL validation is viewport-independent");
    await page.goto("/screener?sort=removed_field:desc");
    await expect(page.getByText("120", { exact: true })).toBeVisible();
    await expect(page).not.toHaveURL(/removed_field/);
    await expect(page.getByText(/unsupported sort field/)).not.toBeVisible();
});

test("recovers from unknown URL columns and an out-of-range page", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "URL validation is viewport-independent");
    await page.goto("/screener?columns=sector,removed_field&page=999");
    await expect(page.getByText("120", { exact: true })).toBeVisible();
    await expect(page.getByText("Page 3 / 3")).toBeVisible();
    await expect(page).toHaveURL(/columns=sector/);
    await expect(page).not.toHaveURL(/removed_field/);
    await expect(page).toHaveURL(/page=3/);
    await expect(page.getByText("No stocks match these filters")).not.toBeVisible();
});

test("discards unsupported URL filters", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "URL validation is viewport-independent");
    const filters = encodeURIComponent(JSON.stringify([
        { field: "sector", operator: "in", value: ["Technology"] },
        { field: "removed_field", operator: "gte", value: 1 },
    ]));
    await page.goto(`/screener?filters=${filters}`);
    await expect(page.locator("header").getByText("60", { exact: true })).toBeVisible();
    await expect(page).not.toHaveURL(/removed_field/);
    await expect(page.getByText(/Sector in Technology/)).toBeVisible();
});

test("ignores a stale manual refresh response after filters change", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "request ordering is viewport-independent");
    await page.goto("/screener");
    await expect(page.getByText("120", { exact: true })).toBeVisible();

    let releaseManualRequest: (() => void) | undefined;
    const manualRequestStarted = new Promise<void>((resolve) => {
        releaseManualRequest = resolve;
    });
    let intercepted = false;
    await page.route("**/api/stocks/screener/query", async (route) => {
        if (!intercepted) {
            intercepted = true;
            releaseManualRequest?.();
            await new Promise((resolve) => setTimeout(resolve, 1_000));
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    total: 999,
                    items: [],
                    limit: 50,
                    offset: 0,
                    as_of_date: "2025-01-10",
                    freshness: { status: "stale", lag_sessions: 2, latest_completed_session: "2025-01-14" },
                }),
            });
            return;
        }
        await route.continue();
    });

    await page.getByRole("button", { name: "Refresh results" }).click();
    await manualRequestStarted;
    await page.getByRole("button", { name: "Fundamental" }).click();
    await page.getByLabel("P/E operator", { exact: true }).selectOption("lte");
    await page.getByLabel("P/E value", { exact: true }).fill("20");
    await expect(page.getByText("25", { exact: true })).toBeVisible();
    await page.waitForTimeout(1_000);
    await expect(page.getByText("25", { exact: true })).toBeVisible();
    await expect(page.getByText("999", { exact: true })).not.toBeVisible();
});
