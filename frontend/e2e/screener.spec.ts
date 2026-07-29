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
    await page.getByLabel("P/E value", { exact: true }).fill("20");
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
