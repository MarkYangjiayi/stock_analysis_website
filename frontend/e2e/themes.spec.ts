import { expect, test, type Page, type Route } from "@playwright/test";
import {
    makeThemeDetailFixture,
    makeThemeHistory,
    makeThemeListItem,
    makeThemeMeta,
    makeThemeProposalsFixture,
    makeThemesListFixture,
    makeTrendingCompaniesFixture,
} from "../src/test/themesFixture";

// Every API call is answered from fixtures; nothing reaches a backend or a provider.
const ADMIN_KEY = "e2e-themes-key";
const list = makeThemesListFixture();
const crowdedOnly = makeThemesListFixture({
    themes: list.themes.filter((theme) => theme.stage === "crowded"),
});
const detail = makeThemeDetailFixture({
    history: makeThemeHistory(120),
    theme: makeThemeListItem({ heat_sparkline: makeThemeHistory(60).heat_now }),
});
// "Market closed" is the widest status chip, so the nav-width check runs against the worst case.
const marketClosed = {
    phase: "closed",
    session_date: null,
    opens_at: null,
    closes_at: null,
    next_open: "2026-10-12T13:30:00Z",
    as_of: "2026-10-11T00:00:00Z",
};
const rrgPoints = makeThemeHistory(12).dates.map((date, index) => ({ date, rs_ratio: 99 + index * 0.3, rs_momentum: 101 - index * 0.2 }));

interface MockOptions {
    rejectThemes?: boolean;
}

async function mockThemesApi(page: Page, { rejectThemes = false }: MockOptions = {}) {
    const requests: string[] = [];
    const keys = new Set<string>();
    const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body });
    await page.route("**/api/**", (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname;
        const key = request.headers()["x-api-key"];
        requests.push(`${request.method()} ${path}${url.search}`);
        if (path === "/api/v1/market/status") return json(route, marketClosed);
        if (path === "/api/personal/watchlist") {
            return key === ADMIN_KEY ? json(route, { tickers: [] }) : json(route, { detail: "Invalid API key" }, 401);
        }
        if (path.startsWith("/api/v1/themes") || path.startsWith("/api/personal/themes") || path === "/api/v1/trending-companies") {
            if (key) keys.add(key);
            if (key !== ADMIN_KEY || rejectThemes) return json(route, { detail: "Invalid API key" }, 401);
        }
        if (path === "/api/v1/themes") {
            const view = url.searchParams.get("view") === "trend" ? "trend" : "now";
            const source = url.searchParams.get("stage") === "crowded" ? crowdedOnly : list;
            return json(route, { ...source, view });
        }
        if (path === "/api/v1/themes/ai-compute") return json(route, detail);
        if (path === "/api/v1/themes/ai-compute/history") {
            return json(route, { meta: makeThemeMeta(), slug: "ai-compute", history: makeThemeHistory(Number(url.searchParams.get("days")) || 252) });
        }
        if (path === "/api/v1/trending-companies") return json(route, makeTrendingCompaniesFixture());
        if (path === "/api/personal/themes/proposals") return json(route, makeThemeProposalsFixture());
        if (path === "/api/v1/rrg") {
            return json(route, {
                benchmark: "SPY.US",
                update_time: "2026-10-10T09:00:00Z",
                data_as_of_date: "2026-10-09",
                data_complete: false,
                missing_tickers: ["QTUM.US"],
                data: { "SMH.US": rrgPoints, "NLR.US": rrgPoints.map((point) => ({ ...point, rs_ratio: point.rs_ratio + 1 })) },
            });
        }
        return json(route, { detail: "Outside this fixture" }, 503);
    });
    return { requests, keys };
}

async function seedSession(page: Page) {
    await page.addInitScript((key) => {
        window.sessionStorage.setItem("personal_workspace_api_key", key);
        // Skips the one-time legacy watchlist import POST.
        window.localStorage.setItem("personal_watchlist_migration_attempted", "true");
    }, ADMIN_KEY);
}

const collectErrors = (page: Page) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    return errors;
};

// `.app-page` clips its own overflow, so also check the document: an absolutely positioned
// descendant without a positioned ancestor widens the layout viewport without moving `.app-page`.
const expectNoHorizontalOverflow = (page: Page) =>
    expect.poll(() => page.locator(".app-page").evaluate((element) => {
        const root = document.documentElement;
        return element.scrollWidth <= element.clientWidth && root.scrollWidth <= root.clientWidth;
    })).toBe(true);

const themeRequests = (requests: string[]) => requests.filter((request) => /\/api\/(v1\/themes|v1\/trending-companies|personal\/themes)/.test(request));

// `next dev` runs effects twice under StrictMode (the first request is aborted), so compare distinct requests.
const distinct = (requests: string[]) => [...new Set(requests)];

test("themes stay locked without requests until the workspace is unlocked", async ({ page }) => {
    const errors = collectErrors(page);
    const { requests } = await mockThemesApi(page);
    await page.addInitScript(() => window.localStorage.setItem("personal_watchlist_migration_attempted", "true"));

    await page.goto("/themes");
    await expect(page.getByRole("heading", { name: "Themes are private to your workspace" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    expect(themeRequests(requests)).toEqual([]);

    await page.getByRole("button", { name: "Unlock personal workspace" }).click();
    await page.getByLabel("Admin Key").fill(ADMIN_KEY);
    await page.getByRole("button", { name: "Unlock workspace" }).click();
    await expect(page.getByRole("table", { name: "Theme leaderboard" }).getByRole("link", { name: "AI compute" })).toBeVisible();
    expect(distinct(themeRequests(requests))).toEqual(["GET /api/v1/themes?view=now"]);
    expect(errors).toEqual([]);
});

test("a rejected key relocks the themes page", async ({ page }) => {
    const errors = collectErrors(page);
    await seedSession(page);
    await mockThemesApi(page, { rejectThemes: true });

    await page.goto("/themes");
    await expect(page.getByRole("heading", { name: "Themes are private to your workspace" })).toBeVisible();
    await expect(page.getByText("The personal workspace was locked because the Admin Key was rejected.")).toBeVisible();
    expect(await page.evaluate(() => window.sessionStorage.getItem("personal_workspace_api_key"))).toBeNull();
    expect(errors).toEqual([]);
});

test("themes dashboard filters, switches views and tabs within the page width", async ({ page }, testInfo) => {
    const errors = collectErrors(page);
    await seedSession(page);
    const { requests, keys } = await mockThemesApi(page);

    await page.goto("/themes");
    const table = page.getByRole("table", { name: "Theme leaderboard" });
    await expect(table.getByRole("link", { name: "AI compute" })).toBeVisible();
    await expect(table.getByRole("link", { name: "NVDA" }).first()).toHaveAttribute("href", "/?ticker=NVDA.US");
    await expect(page.getByRole("region", { name: "Theme stage summary" }).getByRole("link", { name: "Quantum computing" })).toBeVisible();
    await expect(page.getByText("Data through 2026-10-09")).toBeVisible();
    if (testInfo.project.name !== "mobile") {
        await expect(page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Themes" })).toHaveAttribute("aria-current", "page");
    }
    await expectNoHorizontalOverflow(page);

    await page.getByRole("group", { name: "Stage filter" }).getByRole("button", { name: "Crowded" }).click();
    await expect(table.getByRole("link", { name: "AI compute" })).toHaveCount(0);
    await expect(table.getByRole("link", { name: "Nuclear power" })).toBeVisible();
    await page.getByRole("button", { name: "Trend (20D)" }).click();
    await expect(table.getByRole("columnheader", { name: "Heat 20D" })).toBeVisible();
    await expect.poll(() => themeRequests(requests).at(-1)).toBe("GET /api/v1/themes?view=trend&stage=crowded");
    await page.getByRole("group", { name: "Stage filter" }).getByRole("button", { name: "All stages" }).click();
    await expect(table.getByRole("link", { name: "AI compute" })).toBeVisible();

    await page.getByRole("button", { name: "Treemap", exact: true }).click();
    await expect(page.getByRole("tabpanel").locator("canvas").first()).toBeVisible();
    await expect(page.getByText("Area is priced members", { exact: false })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByRole("button", { name: "Rotation", exact: true }).click();
    await expect(page.getByText("No rotation history for QTUM.", { exact: false })).toBeVisible();
    await expect(page.getByRole("tabpanel").locator("canvas").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByRole("tab", { name: "Trending companies" }).click();
    await expect(page.getByRole("table", { name: "Trending companies" }).getByRole("link", { name: "IONQ" })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.getByRole("tab", { name: "Proposals" }).click();
    await expect(page.getByRole("heading", { name: "Humanoid robots" })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    for (const theme of ["dark", "light"] as const) {
        await page.getByRole("button", { name: `Use ${theme} theme` }).click();
        await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
        await page.getByRole("tab", { name: "Themes" }).click();
        await page.getByRole("button", { name: "Table", exact: true }).click();
        await expect(table).toBeVisible();
        await expectNoHorizontalOverflow(page);
        await page.screenshot({ path: testInfo.outputPath(`themes-${theme}.png`), animations: "disabled" });
    }

    expect([...keys]).toEqual([ADMIN_KEY]);
    expect(errors).toEqual([]);
});

test("theme detail renders history, brief and members", async ({ page }, testInfo) => {
    const errors = collectErrors(page);
    await seedSession(page);
    const { requests } = await mockThemesApi(page);

    await page.goto("/themes");
    await page.getByRole("table", { name: "Theme leaderboard" }).getByRole("link", { name: "AI compute" }).click();
    await expect(page).toHaveURL(/\/themes\/ai-compute$/);
    await expect(page.getByRole("heading", { level: 1, name: "AI compute" })).toBeVisible();
    if (testInfo.project.name !== "mobile") {
        await expect(page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Themes" })).toHaveAttribute("aria-current", "page");
    }

    const history = page.getByRole("region", { name: "Theme history" });
    await expect(history.locator("canvas").first()).toBeVisible();
    const brief = page.getByRole("region", { name: "Why it's moving" });
    await expect(brief.getByText("同期新闻包括", { exact: false })).toBeVisible();
    await expect(brief.getByRole("link", { name: "Open source for E2" })).toHaveAttribute("href", "https://example.com/capex");
    const members = page.getByRole("table", { name: "Theme members" });
    await expect(members.getByRole("link", { name: "NVDA" })).toHaveAttribute("href", "/?ticker=NVDA.US");
    await expect(members.getByText("ETF only · unverified")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await history.getByRole("button", { name: "1Y" }).click();
    await expect.poll(() => requests.some((request) => request === "GET /api/v1/themes/ai-compute/history?days=252")).toBe(true);
    await expect(history.getByRole("button", { name: "1Y" })).toHaveAttribute("aria-pressed", "true");

    for (const theme of ["dark", "light"] as const) {
        await page.getByRole("button", { name: `Use ${theme} theme` }).click();
        await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
        await expectNoHorizontalOverflow(page);
        await page.screenshot({ path: testInfo.outputPath(`theme-detail-${theme}.png`), animations: "disabled" });
    }
    expect(errors).toEqual([]);
});

test("a malformed theme slug shows not found without a request", async ({ page }) => {
    const errors = collectErrors(page);
    await seedSession(page);
    const { requests } = await mockThemesApi(page);

    await page.goto("/themes/AI-Compute");
    await expect(page.getByRole("heading", { name: "Theme not found" })).toBeVisible();
    await expect(page.getByText("[object Object]")).toHaveCount(0);
    expect(themeRequests(requests)).toEqual([]);
    expect(errors).toEqual([]);
});

test("the primary navigation keeps all six sections and the theme toggle on one row", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "below lg the sections live in the menu");
    await seedSession(page);
    await mockThemesApi(page);
    await page.goto("/themes");
    const nav = page.getByRole("navigation", { name: "Primary navigation" });

    // 1024 is the first width with inline links; 1279/1280 straddle the xl step back to the roomy layout.
    for (const width of [1024, 1100, 1279, 1280]) {
        await page.setViewportSize({ width, height: 800 });
        await expect(nav.getByText("Market closed")).toBeVisible();
        await expect(nav.getByRole("button", { name: "Use dark theme" })).toBeInViewport({ ratio: 1 });
        const layout = await nav.evaluate((element) => {
            const row = element.firstElementChild as HTMLElement;
            const wrapped = [...element.querySelectorAll("a")].filter((link) => {
                const text = [...link.childNodes].find((node) => node.nodeType === Node.TEXT_NODE);
                if (!text) return false;
                const range = document.createRange();
                range.selectNodeContents(text);
                return range.getClientRects().length > 1;
            }).map((link) => link.textContent);
            return { overflow: row.scrollWidth - row.clientWidth, wrapped };
        });
        expect(layout, `nav layout at ${width}px`).toEqual({ overflow: 0, wrapped: [] });
        for (const name of ["Analysis", "Screener", "Anomalies", "Market", "Themes"]) {
            await expect(nav.getByRole("link", { name, exact: true })).toBeInViewport({ ratio: 1 });
        }
    }
});
