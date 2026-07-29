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
