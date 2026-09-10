import { expect, test } from "@playwright/test";

const PAGES = [
    { path: "/screener", name: "screener", heading: "Stock Screener" },
    { path: "/anomalies", name: "anomalies", heading: "Market Anomalies" },
    { path: "/market", name: "market", heading: "US Market Overview" },
    { path: "/research", name: "factor-lab", heading: "Factor Lab" },
    { path: "/rrg", name: "sector-rotation", heading: "US Sector Rotation" },
];

test("captures the shared desktop shell across primary pages", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "explicit mobile coverage lives with each page flow");
    await page.setViewportSize({ width: 1440, height: 900 });
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    for (const theme of ["light", "dark"] as const) {
        for (const item of PAGES) {
            await page.goto(item.path);
            await expect(page.getByRole("heading", { name: item.heading })).toBeVisible();
            if (item.name === "screener") {
                await expect(page.getByText("Matches", { exact: true }).locator("..").getByText("120", { exact: true })).toBeVisible();
            }
            await page.getByRole("button", { name: theme === "light" ? "Use light theme" : "Use dark theme" }).click();
            await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
            await expect.poll(() => page.locator(".app-page").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
            await page.screenshot({
                path: `../docs/frontend_redesign_visual_fix/${item.name}-${theme}-1440x900.png`,
                animations: "disabled",
            });
        }
    }
    expect(pageErrors).toEqual([]);
});
