import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import type { HistoricalDataPoint, StockDataResponse } from "../src/lib/api";
import { makeValuationHistoryFixture } from "../src/test/valuationHistoryFixture";

const daily: HistoricalDataPoint[] = Array.from({ length: 600 }, (_, index) => {
    const date = new Date(Date.UTC(2024, 0, index + 1));
    const close = 40 + index * 0.08 + Math.sin(index / 13) * 3;
    return { date: date.toISOString().slice(0, 10), open: close - 0.4, high: close + 1, low: close - 1, close, volume: 1e7 + Math.sin(index) * 1e6, MA20: close - 1, MA50: close - 2 };
}).filter((point) => ![0, 6].includes(new Date(point.date).getUTCDay()));

function sample(interval: string) {
    const groups = new Map<string, HistoricalDataPoint>();
    for (const point of daily) {
        const date = new Date(point.date);
        if (interval === "1wk") date.setUTCDate(date.getUTCDate() + (5 - date.getUTCDay() + 7) % 7);
        if (interval === "1mo") date.setUTCMonth(date.getUTCMonth() + 1, 0);
        const label = date.toISOString().slice(0, 10);
        groups.set(label, { ...point, date: label });
    }
    return [...groups.values()];
}

const fixtures: Record<string, StockDataResponse> = process.env.VALUATION_QA_FIXTURE
    ? JSON.parse(readFileSync(process.env.VALUATION_QA_FIXTURE, "utf8"))
    : Object.fromEntries(["1d", "1wk", "1mo"].map((interval) => {
        const data = sample(interval);
        const history = makeValuationHistoryFixture(data);
        history.interval = interval as "1d" | "1wk" | "1mo";
        history.bases[0].inputs = { eps: 0.000000123456, revenue: 1.25e9, book: 750e6, fcf: 125e6, ebitda: 250e6 };
        return [interval, {
            profile: { ticker: "TEST.US", name: "Test Company", currency: "USD", exchange: "NASDAQ", sector: "Technology", industry: "Software", description: "", last_updated: "2025-08-22" },
            historical_data: data, historical_financials: [], valuation_history: history,
        }];
    }));

test("historical multiples share the price timeline on desktop and mobile", async ({ page }, testInfo) => {
    const ticker = fixtures["1d"].profile.ticker;
    const errors: string[] = [];
    const intervals: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/**", async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === `/api/stocks/${ticker}`) {
            const interval = url.searchParams.get("interval") || "1d";
            intervals.push(interval);
            return route.fulfill({ json: fixtures[interval] });
        }
        return route.fulfill({ status: 503, json: { detail: "Other panels are outside this chart fixture." } });
    });
    await page.goto(`/?ticker=${ticker}&section=technical`);
    const panel = page.getByRole("tabpanel");
    const chart = panel.getByRole("img", { name: "Linked stock price, volume and P/E chart", exact: true });
    await expect(panel.getByRole("heading", { name: "Price & valuation history" })).toBeVisible();
    await expect(chart.locator("canvas").first()).toBeVisible();
    await expect(panel.getByRole("combobox", { name: "Historical valuation multiple" }).locator("option")).toHaveCount(6);
    for (const [key, label] of [["pe", "P/E"], ["ps", "P/S"], ["pb", "P/B"], ["pfcf", "P/FCF"], ["ev_revenue", "EV/Revenue"], ["ev_ebitda", "EV/EBITDA"]]) {
        await panel.getByLabel("Historical valuation multiple").selectOption(key);
        await expect(panel.getByLabel(`Latest ${label}`, { exact: true })).not.toHaveText("N/M");
    }
    await panel.getByLabel("Historical valuation multiple").selectOption("pe");
    for (const [button, interval] of [["W", "1wk"], ["M", "1mo"], ["D", "1d"]]) {
        await panel.getByRole("button", { name: button, exact: true }).click();
        await expect(panel.getByRole("button", { name: button, exact: true })).toHaveAttribute("aria-pressed", "true");
        await expect.poll(() => intervals.at(-1)).toBe(interval);
        await expect(panel.getByRole("button", { name: button, exact: true })).toBeEnabled();
    }
    await expect.poll(() => page.locator(".app-page").evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
    const originalViewport = page.viewportSize()!;
    const historyPanel = panel.locator('section[aria-labelledby="valuation-history-title"]');
    for (const theme of ["light", "dark"] as const) {
        await page.getByRole("button", { name: theme === "light" ? "Use light theme" : "Use dark theme" }).click();
        await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
        // The app uses an internal scroll container. Give the capture enough
        // height so a full element screenshot isn't clipped by that container.
        const height = await historyPanel.evaluate((node) => node.getBoundingClientRect().height);
        await page.setViewportSize({ width: originalViewport.width, height: Math.ceil(height) + 250 });
        await historyPanel.evaluate((node) => node.scrollIntoView({ block: "start" }));
        await historyPanel.screenshot({ path: testInfo.outputPath(`valuation-${theme}.png`), animations: "disabled", scale: "css" });
        const canvas = chart.locator("canvas").first();
        const canvasBox = (await canvas.boundingBox())!;
        await canvas.hover({ position: { x: 12 + (canvasBox.width - 90) * 0.9, y: canvasBox.height * 0.78 } });
        const denominator = chart.getByText(/^TTM earnings \/ share:/);
        await expect(denominator).toBeVisible();
        await expect(denominator).not.toContainText("Unavailable");
        if (!process.env.VALUATION_QA_FIXTURE) {
            await expect(denominator).toHaveText("TTM earnings / share: USD 0.000000123456 per share");
        }
        await expect(chart.getByText(/^TTM quarters:/)).toBeVisible();
        const denominatorBox = (await denominator.boundingBox())!;
        const chartBox = (await chart.boundingBox())!;
        expect(denominatorBox.x).toBeGreaterThanOrEqual(chartBox.x);
        expect(denominatorBox.x + denominatorBox.width).toBeLessThanOrEqual(chartBox.x + chartBox.width);
        await historyPanel.screenshot({ path: testInfo.outputPath(`valuation-tooltip-${theme}.png`), animations: "disabled", scale: "css" });
        await page.mouse.move(0, 0);
        await expect(denominator).toBeHidden();
        await page.setViewportSize(originalViewport);
    }
    await panel.getByText("Calculation & data coverage", { exact: true }).click();
    await expect(panel.getByText(/Forward P\/E requires archived analyst expectations/)).toBeVisible();
    expect(errors).toEqual([]);
});
