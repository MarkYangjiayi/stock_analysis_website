import { expect, test } from "@playwright/test";
const dates = Array.from({ length: 61 }, (_, i) => new Date(Date.UTC(2026, 5, 15 + i)).toISOString().slice(0, 10));
const returns = dates.map((_, i) => i * .002 + Math.sin(i * .7) * .015);
test("similar stocks stay fixed and compare within overview", async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    let requests = 0;
    await page.route("**/api/**", route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith("/similar")) {
            requests++;
            return route.fulfill({ json: { ticker: "TEST.US", window_days: 60, as_of: dates.at(-1), status: "ok", dates, target_returns: returns, eligible_count: 100,
                matches: ["BETA", "GAMMA", "DELTA", "EPSILON", "ZETA"].map((ticker, i) => ({ ticker: `${ticker}.US`, name: `${ticker} Technologies Corporation`, industry: "Semiconductors & semiconductor equipment", correlation: .9 - i * .03, returns: returns.map(v => v * (1.4 + i * .1)) })) } });
        }
        if (path === "/api/stocks/TEST.US") return route.fulfill({ json: {
            profile: { ticker: "TEST.US", name: "Test Company", currency: "USD", exchange: "NASDAQ", sector: "Technology", industry: "Software" },
            historical_data: dates.map((date, i) => ({ date, close: 100 * (1 + returns[i]), open: 100, high: 120, low: 90, volume: 100000 })), historical_financials: [],
        } });
        return route.fulfill({ status: 503, json: { detail: "Outside this fixture" } });
    });
    await page.goto("/?ticker=TEST.US");
    const panel = page.locator('section[aria-labelledby="similar-stocks-title"]');
    await expect(panel.getByRole("button", { name: "Compare", exact: true })).toHaveCount(5);
    const initialRequests = requests;
    await page.getByRole("button", { name: "Weekly", exact: true }).click();
    await expect(page.getByRole("button", { name: "Weekly", exact: true })).toBeEnabled();
    expect(requests).toBe(initialRequests);
    const firstRow = panel.locator("li").first();
    const positions = () => firstRow.evaluate(row => Array.from(row.children).map(child => {
        const box = child.getBoundingClientRect();
        return { x: box.x, width: box.width };
    }));
    const beforeCompare = await positions();
    await panel.getByRole("button", { name: "Compare", exact: true }).first().click();
    await expect(panel.getByText("TEST vs BETA")).toBeVisible();
    expect(await positions()).toEqual(beforeCompare);
    await firstRow.getByRole("button", { name: "Close", exact: true }).click();
    expect(await positions()).toEqual(beforeCompare);
    await firstRow.getByRole("button", { name: "Compare", exact: true }).click();
    await panel.getByRole("slider").fill("20");
    await panel.getByRole("button", { name: "Compare", exact: true }).first().click();
    await expect(panel.getByText("TEST vs GAMMA")).toBeVisible();
    await expect(panel.getByText("TEST vs BETA")).toHaveCount(0);
    for (const theme of ["light", "dark"]) {
        await page.getByRole("button", { name: `Use ${theme} theme` }).click();
        const viewport = page.viewportSize()!;
        const height = await panel.evaluate(node => node.getBoundingClientRect().height);
        await page.setViewportSize({ width: viewport.width, height: Math.ceil(height) + 250 });
        await panel.evaluate(node => node.scrollIntoView({ block: "start" }));
        expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
        await panel.screenshot({ path: testInfo.outputPath(`similar-${theme}.png`), animations: "disabled" });
    }
    await panel.getByRole("button", { name: "Unlock to edit watchlist" }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect(errors).toEqual([]);
});
