import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WATCHLIST_MIGRATION_KEY } from "@/hooks/usePersonalWorkspace";
import { ApiError, type ThemeHistory } from "@/lib/api";
import { useStatusBar } from "@/store/useStatusBar";
import { makeThemeBrief, makeThemeDetailFixture, makeThemeHistory, makeThemeMeta } from "@/test/themesFixture";

const apiMocks = vi.hoisted(() => ({
    createAdminSession: vi.fn(),
    fetchAdminSession: vi.fn(),
    deleteAdminSession: vi.fn(),
    fetchThemeDetail: vi.fn(),
    fetchThemeHistory: vi.fn(),
    fetchPersonalWatchlist: vi.fn(),
    importPersonalWatchlist: vi.fn(),
}));

const navigation = vi.hoisted(() => ({ slug: "ai-compute" }));

vi.mock("next/navigation", () => ({
    useParams: () => ({ slug: navigation.slug }),
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    ...apiMocks,
}));

vi.mock("@/components/themes/ThemeDetailChart", () => ({
    default: ({ history, proxyEtf }: { history: ThemeHistory; proxyEtf: string | null }) => (
        <div data-testid="theme-detail-chart" data-sessions={history.dates.length} data-proxy={proxyEtf ?? ""} />
    ),
}));

import ThemeDetailPage from "./page";

const storage = () => {
    const values = new Map<string, string>();
    return {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, String(value)),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
        key: (index: number) => [...values.keys()][index] ?? null,
        get length() { return values.size; },
    } as Storage;
};

const unlockSession = () => {
    apiMocks.fetchAdminSession.mockResolvedValue({ authenticated: true, expires_at: "2026-11-27T00:00:00+00:00" });
    window.localStorage.setItem(WATCHLIST_MIGRATION_KEY, "true");
};

const memberRow = (ticker: string) => {
    const table = screen.getByRole("table", { name: "Theme members" });
    const row = within(table).getByRole("link", { name: ticker }).closest("tr");
    if (!row) throw new Error(`No member row for ${ticker}`);
    return row;
};

describe("ThemeDetailPage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.fetchAdminSession.mockResolvedValue({ authenticated: false, expires_at: null });
        apiMocks.createAdminSession.mockResolvedValue({ authenticated: true, expires_at: "2026-11-27T00:00:00+00:00" });
        apiMocks.deleteAdminSession.mockResolvedValue({ authenticated: false, expires_at: null });
        navigation.slug = "ai-compute";
        Object.defineProperty(window, "localStorage", { value: storage(), configurable: true });
        Object.defineProperty(window, "sessionStorage", { value: storage(), configurable: true });
        useStatusBar.getState().clearStatus();
        apiMocks.fetchPersonalWatchlist.mockResolvedValue({ tickers: [] });
        apiMocks.importPersonalWatchlist.mockResolvedValue({ tickers: [], imported: false });
        apiMocks.fetchThemeDetail.mockResolvedValue(makeThemeDetailFixture());
        apiMocks.fetchThemeHistory.mockResolvedValue({
            meta: makeThemeMeta(),
            slug: "ai-compute",
            history: makeThemeHistory(252),
        });
    });

    it("stays locked without requesting the theme", async () => {
        render(<ThemeDetailPage />);

        expect(await screen.findByRole("heading", { name: "Themes are private to your workspace" })).toBeInTheDocument();
        expect(screen.getByRole("link", { name: "All themes" })).toHaveAttribute("href", "/themes");
        expect(apiMocks.fetchThemeDetail).not.toHaveBeenCalled();
        expect(apiMocks.fetchThemeHistory).not.toHaveBeenCalled();
        expect(apiMocks.fetchPersonalWatchlist).not.toHaveBeenCalled();
    });

    it("renders the definition, KPIs, brief and members for the slug", async () => {
        unlockSession();
        render(<ThemeDetailPage />);

        expect(await screen.findByRole("heading", { level: 1, name: "AI compute" })).toBeInTheDocument();
        expect(apiMocks.fetchThemeDetail).toHaveBeenCalledWith("ai-compute", null, expect.any(AbortSignal));
        expect(screen.getByText("AI算力")).toBeInTheDocument();
        expect(screen.getByText(/design, manufacture or supply accelerated computing/)).toBeInTheDocument();
        expect(screen.getByText("artificial intelligence")).toHaveClass("eyebrow");
        expect(screen.getByText("Leading")).toBeInTheDocument();
        expect(screen.getByText("SOXX")).toBeInTheDocument();
        expect(screen.getByText("HBM")).toBeInTheDocument();

        const summary = screen.getByRole("region", { name: "Theme summary" });
        expect(within(summary).getByText("Rank now").closest("div")).toHaveTextContent("#1");
        expect(within(summary).getByText("Heat now").closest("div")).toHaveTextContent("78.4");
        expect(within(summary).getByText("21D vs SPY").closest("div")).toHaveTextContent("+8.5%");
        expect(within(summary).getByText("% > MA50").closest("div")).toHaveTextContent("10D +12 pts");

        const chart = screen.getByTestId("theme-detail-chart");
        expect(chart).toHaveAttribute("data-sessions", "6");
        expect(chart).toHaveAttribute("data-proxy", "SMH.US");

        const brief = screen.getByRole("region", { name: "Why it's moving" });
        expect(within(brief).getByText("Brief for 2026-10-09")).toHaveClass("status-pill");
        expect(within(brief).getByText(/主题篮子X21为\+8\.5%/)).toBeInTheDocument();
        expect(within(brief).getAllByRole("link", { name: "E1" })[0]).toHaveAttribute("href", "#theme-evidence-E1");
        expect(within(brief).getByRole("link", { name: "Open source for E2" })).toHaveAttribute("href", "https://example.com/capex");
        expect(within(brief).queryByRole("link", { name: "Open source for E3" })).not.toBeInTheDocument();

        const nvda = memberRow("NVDA");
        expect(within(nvda).getByText("Core")).toBeInTheDocument();
        expect(within(nvda).getByText("accelerated computing platforms for data centers")).toBeInTheDocument();
        expect(within(nvda).getByText("as of 2026-10-04")).toBeInTheDocument();
        expect(within(nvda).getByText("ETF SMH")).toBeInTheDocument();
        expect(within(nvda).getByText("#1")).toBeInTheDocument();
        expect(within(nvda).getByText("1.34×")).toBeInTheDocument();

        const sparse = within(memberRow("SMCI")).getAllByRole("cell").map((cell) => cell.textContent?.trim());
        expect(sparse[1]).toBe("SMCI");
        expect(sparse[2]).toBe("—");
        expect(sparse[4]).toMatch(/^—Exposure 1\/3/);
        expect(sparse[4]).toContain("ETF only · unverified");
        expect(sparse.slice(5, 12)).toEqual(["—", "—", "—", "—", "—", "—", "—"]);

        await waitFor(() => expect(useStatusBar.getState().items).toEqual([{ label: "Themes", value: "2026-10-09", tone: "ready" }]));
        expect(useStatusBar.getState().note).toBe("scoring theme-heat-v1");
    });

    it("labels a stale brief and shows missing headline metrics as dashes", async () => {
        unlockSession();
        const fixture = makeThemeDetailFixture({ brief: makeThemeBrief({ date: "2026-10-06", stale: true }) });
        fixture.theme = { ...fixture.theme, rank_now: null, rank_change: null, heat_now: null, excess_21d: null, proxy_etf: null, proxy_excess_21d: null };
        apiMocks.fetchThemeDetail.mockResolvedValue(fixture);
        render(<ThemeDetailPage />);

        const brief = await screen.findByRole("region", { name: "Why it's moving" });
        expect(within(brief).getByText("Stale · brief from 2026-10-06")).toHaveClass("text-caution");
        expect(within(brief).getByText(/written for an earlier session/)).toBeInTheDocument();

        const summary = screen.getByRole("region", { name: "Theme summary" });
        expect(within(summary).getByText("Rank now").nextElementSibling).toHaveTextContent(/^—$/);
        expect(within(summary).getByText("Heat now").nextElementSibling).toHaveTextContent(/^—$/);
        expect(within(summary).getByText("21D vs SPY").nextElementSibling).toHaveTextContent(/^—$/);
        expect(within(summary).getByText("Proxy ETF 21D").closest("div")).toHaveTextContent("No proxy");
    });

    it("shows the empty brief state when no brief is published", async () => {
        unlockSession();
        apiMocks.fetchThemeDetail.mockResolvedValue(makeThemeDetailFixture({ brief: null }));
        render(<ThemeDetailPage />);

        expect(await screen.findByText("No validated brief for this theme yet")).toBeInTheDocument();
    });

    it("loads the 1Y history on demand", async () => {
        unlockSession();
        render(<ThemeDetailPage />);
        await screen.findByRole("heading", { level: 1, name: "AI compute" });
        expect(apiMocks.fetchThemeHistory).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: "1Y" }));
        await waitFor(() => expect(screen.getByTestId("theme-detail-chart")).toHaveAttribute("data-sessions", "252"));
        expect(apiMocks.fetchThemeHistory).toHaveBeenCalledWith("ai-compute", 252, expect.any(AbortSignal));

        fireEvent.click(screen.getByRole("button", { name: "120D" }));
        expect(screen.getByTestId("theme-detail-chart")).toHaveAttribute("data-sessions", "6");
        fireEvent.click(screen.getByRole("button", { name: "1Y" }));
        expect(apiMocks.fetchThemeHistory).toHaveBeenCalledTimes(1);
    });

    it("keeps the embedded history when the 1Y request fails", async () => {
        unlockSession();
        apiMocks.fetchThemeHistory.mockRejectedValue(new Error("History is unavailable"));
        render(<ThemeDetailPage />);
        await screen.findByRole("heading", { level: 1, name: "AI compute" });

        fireEvent.click(screen.getByRole("button", { name: "1Y" }));
        expect(await screen.findByText("History is unavailable Showing the last 120 sessions.")).toBeInTheDocument();
        expect(screen.getByTestId("theme-detail-chart")).toHaveAttribute("data-sessions", "6");
    });

    it("shows not found for an unknown slug", async () => {
        unlockSession();
        navigation.slug = "retired-theme";
        apiMocks.fetchThemeDetail.mockRejectedValue(new ApiError("Theme not found", 404, "Theme not found"));
        render(<ThemeDetailPage />);

        expect(await screen.findByRole("heading", { name: "Theme not found" })).toBeInTheDocument();
        expect(screen.getByText(/retired-theme/)).toBeInTheDocument();
        expect(apiMocks.fetchThemeDetail).toHaveBeenCalledWith("retired-theme", null, expect.any(AbortSignal));
    });

    it.each(["AI-Compute", "ai_compute", "-ai-compute", "a".repeat(65)])("shows not found without a request for the malformed slug %s", async (slug) => {
        unlockSession();
        navigation.slug = slug;
        render(<ThemeDetailPage />);

        expect(await screen.findByRole("heading", { name: "Theme not found" })).toBeInTheDocument();
        expect(screen.getByText(slug)).toHaveClass("break-all");
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(apiMocks.fetchThemeDetail).not.toHaveBeenCalled();
    });

    it("treats a slug validation error from the API as not found", async () => {
        unlockSession();
        // apiRequest passes FastAPI's validation array through as the message.
        apiMocks.fetchThemeDetail.mockRejectedValue(new ApiError("[object Object]", 422, "[object Object]"));
        render(<ThemeDetailPage />);

        expect(await screen.findByRole("heading", { name: "Theme not found" })).toBeInTheDocument();
        expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
    });

    it("relocks the workspace when the key is rejected", async () => {
        unlockSession();
        apiMocks.fetchThemeDetail.mockRejectedValue(new ApiError("Invalid API key", 401, "Invalid API key"));
        render(<ThemeDetailPage />);

        expect(await screen.findByRole("heading", { name: "Themes are private to your workspace" })).toBeInTheDocument();
        expect(screen.getByText("The personal workspace was locked because the session was rejected. Unlock it again with the Admin Key.")).toBeInTheDocument();
        expect(apiMocks.deleteAdminSession).toHaveBeenCalled();
        expect(apiMocks.fetchThemeDetail).toHaveBeenCalledTimes(1);
    });

    it("shows a server error and retries", async () => {
        unlockSession();
        apiMocks.fetchThemeDetail
            .mockRejectedValueOnce(new ApiError("Theme trends have not been published yet", 503))
            .mockResolvedValueOnce(makeThemeDetailFixture());
        render(<ThemeDetailPage />);

        expect(await screen.findByRole("alert")).toHaveTextContent("Theme trends have not been published yet");
        fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
        expect(await screen.findByRole("heading", { level: 1, name: "AI compute" })).toBeInTheDocument();
        expect(apiMocks.fetchThemeDetail).toHaveBeenCalledTimes(2);
    });
});
