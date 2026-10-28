import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WATCHLIST_MIGRATION_KEY } from "@/hooks/usePersonalWorkspace";
import { ApiError } from "@/lib/api";
import { useStatusBar } from "@/store/useStatusBar";
import {
    makeThemeListItem,
    makeThemeProposal,
    makeThemeProposalsFixture,
    makeThemesListFixture,
    makeTrendingCompaniesFixture,
} from "@/test/themesFixture";

const apiMocks = vi.hoisted(() => ({
    createAdminSession: vi.fn(),
    fetchAdminSession: vi.fn(),
    deleteAdminSession: vi.fn(),
    fetchThemes: vi.fn(),
    fetchTrendingCompanies: vi.fn(),
    fetchThemeProposals: vi.fn(),
    decideThemeProposal: vi.fn(),
    fetchPersonalWatchlist: vi.fn(),
    importPersonalWatchlist: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    ...apiMocks,
}));

vi.mock("@/components/themes/ThemeTreemap", () => ({
    default: ({ themes, view }: { themes: unknown[]; view: string }) => (
        <div data-testid="theme-treemap" data-count={themes.length} data-view={view} />
    ),
}));

vi.mock("@/components/themes/ThemeRotation", () => ({
    default: ({ themes }: { themes: unknown[] }) => <div data-testid="theme-rotation" data-count={themes.length} />,
}));

import ThemesPage from "./page";

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

const leaderboard = () => screen.getByRole("table", { name: "Theme leaderboard" });

const rowFor = (name: string) => {
    const row = within(leaderboard()).getByRole("link", { name }).closest("tr");
    if (!row) throw new Error(`No leaderboard row for ${name}`);
    return row;
};

const cellTexts = (row: HTMLElement) => within(row).getAllByRole("cell").map((cell) => cell.textContent?.trim());

describe("ThemesPage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.fetchAdminSession.mockResolvedValue({ authenticated: false, expires_at: null });
        apiMocks.createAdminSession.mockResolvedValue({ authenticated: true, expires_at: "2026-11-27T00:00:00+00:00" });
        apiMocks.deleteAdminSession.mockResolvedValue({ authenticated: false, expires_at: null });
        Object.defineProperty(window, "localStorage", { value: storage(), configurable: true });
        Object.defineProperty(window, "sessionStorage", { value: storage(), configurable: true });
        useStatusBar.getState().clearStatus();
        apiMocks.fetchPersonalWatchlist.mockResolvedValue({ tickers: [] });
        apiMocks.importPersonalWatchlist.mockResolvedValue({ tickers: [], imported: false });
        apiMocks.fetchThemes.mockResolvedValue(makeThemesListFixture());
        apiMocks.fetchTrendingCompanies.mockResolvedValue(makeTrendingCompaniesFixture());
        apiMocks.fetchThemeProposals.mockResolvedValue(makeThemeProposalsFixture());
    });

    it("stays locked without requesting theme data, then loads once unlocked", async () => {
        render(<ThemesPage />);

        expect(await screen.findByRole("heading", { name: "Themes are private to your workspace" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { level: 1, name: "Trending themes" })).toBeInTheDocument();
        expect(apiMocks.fetchThemes).not.toHaveBeenCalled();
        expect(apiMocks.fetchTrendingCompanies).not.toHaveBeenCalled();
        expect(apiMocks.fetchThemeProposals).not.toHaveBeenCalled();
        expect(apiMocks.fetchPersonalWatchlist).not.toHaveBeenCalled();
        expect(screen.queryByRole("tablist")).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Unlock personal workspace" }));
        fireEvent.change(screen.getByLabelText("Admin Key"), { target: { value: "typed-key" } });
        fireEvent.click(screen.getByRole("button", { name: "Unlock workspace" }));

        expect(await screen.findByRole("table", { name: "Theme leaderboard" })).toBeInTheDocument();
        expect(apiMocks.createAdminSession).toHaveBeenCalledWith("typed-key");
        expect(apiMocks.fetchPersonalWatchlist).toHaveBeenCalledWith();
        expect(apiMocks.fetchThemes).toHaveBeenCalledWith({ view: "now", stage: null }, expect.any(AbortSignal));
        expect(window.sessionStorage.length).toBe(0);
    });

    it("renders the leaderboard, KPI strip and status bar from a restored session", async () => {
        unlockSession();
        const { unmount } = render(<ThemesPage />);

        const row = await waitFor(() => rowFor("AI compute"));
        expect(apiMocks.fetchThemes).toHaveBeenCalledWith({ view: "now", stage: null }, expect.any(AbortSignal));
        expect(within(row).getByText("AI算力")).toBeInTheDocument();
        expect(within(row).getByText("Leading")).toBeInTheDocument();
        expect(within(row).getByText("78.4")).toBeInTheDocument();
        expect(within(row).getByText("+3.1%")).toBeInTheDocument();
        expect(within(row).getByText("+8.5%")).toBeInTheDocument();
        expect(within(row).getByText("82%")).toBeInTheDocument();
        expect(within(row).getByRole("link", { name: "NVDA" })).toHaveAttribute("href", "/?ticker=NVDA.US");
        expect(within(row).getByRole("link", { name: "AI compute" })).toHaveAttribute("href", "/themes/ai-compute");
        expect(within(row).getByText("SMH")).toBeInTheDocument();
        expect(cellTexts(rowFor("Nuclear power"))[6]).toBe("−1.2%");

        const summary = screen.getByRole("region", { name: "Theme stage summary" });
        expect(within(summary).getByText("Leading").closest("div")).toHaveTextContent("Leading1");
        expect(within(summary).getByText("Accelerating").closest("div")).toHaveTextContent("Accelerating0");
        expect(within(summary).getByRole("link", { name: "Quantum computing" })).toHaveAttribute("href", "/themes/quantum-computing");
        expect(within(summary).getByText("↑4")).toBeInTheDocument();
        expect(within(summary).getByText("3 themes ranked")).toBeInTheDocument();

        expect(screen.getByText(/Data through 2026-10-09/)).toHaveClass("status-pill");
        expect(screen.queryByText("Backfilled · not point-in-time")).not.toBeInTheDocument();
        await waitFor(() => expect(useStatusBar.getState().items).toEqual([{ label: "Themes", value: "2026-10-09", tone: "ready" }]));
        expect(useStatusBar.getState().note).toBe("scoring theme-heat-v1");

        unmount();
        expect(useStatusBar.getState().items).toEqual([]);
    });

    it("renders missing metrics as an em dash instead of zero", async () => {
        unlockSession();
        render(<ThemesPage />);

        const cells = cellTexts(await waitFor(() => rowFor("Space economy")));
        expect(cells[0]).toBe("——");
        expect(cells[2]).toBe("Insufficient members");
        expect(cells[3]).toBe("—");
        expect(cells[6]).toBe("—");
        expect(cells[7]).toBe("—");
        expect(cells[8]).toBe("—");
        expect(cells[11]).toBe("—");
        expect(cells[12]).toBe("—");
        expect(within(rowFor("Space economy")).queryByText(/0\.0%|^0%$/)).not.toBeInTheDocument();
    });

    it("shows stale, backfilled, degraded-attention and quality notices", async () => {
        unlockSession();
        apiMocks.fetchThemes.mockResolvedValue(makeThemesListFixture({
            meta: {
                as_of_date: "2026-10-08",
                expected_as_of_date: "2026-10-09",
                stale: true,
                point_in_time: false,
                attention_degraded: true,
                coverage_pct: null,
                warnings: ["price coverage 91.0% below target"],
            },
            summary: { top_riser: null },
        }));
        render(<ThemesPage />);

        expect(await screen.findByText(/2026-10-09 was expected\. Showing the last complete publication/)).toBeInTheDocument();
        expect(screen.getByText("Backfilled · not point-in-time")).toBeInTheDocument();
        expect(screen.getByText(/News attention is incomplete for this session/)).toBeInTheDocument();
        expect(screen.getByText(/price coverage 91\.0% below target/)).toBeInTheDocument();
        expect(screen.getByText(/Data through 2026-10-08/)).toHaveClass("text-caution");
        expect(within(screen.getByRole("region", { name: "Theme stage summary" })).getByText("Top riser").closest("div")).toHaveTextContent("Top riser—");
        expect(screen.getByText(/price coverage —/)).toBeInTheDocument();
        await waitFor(() => expect(useStatusBar.getState().items).toEqual([{ label: "Themes", value: "2026-10-08", tone: "stale" }]));
    });

    it("refetches for the stage filter and ranking window", async () => {
        unlockSession();
        render(<ThemesPage />);
        await waitFor(() => rowFor("AI compute"));

        apiMocks.fetchThemes.mockResolvedValueOnce(makeThemesListFixture({
            themes: [makeThemeListItem({ slug: "nuclear-power", name_en: "Nuclear power", name_zh: "核电", stage: "crowded" })],
        }));
        const stageFilter = screen.getByRole("group", { name: "Stage filter" });
        fireEvent.click(within(stageFilter).getByRole("button", { name: "Crowded" }));

        await waitFor(() => expect(apiMocks.fetchThemes).toHaveBeenLastCalledWith(
            { view: "now", stage: "crowded" },
            expect.any(AbortSignal),
        ));
        await waitFor(() => expect(within(leaderboard()).queryByRole("link", { name: "AI compute" })).not.toBeInTheDocument());
        expect(within(stageFilter).getByRole("button", { name: "Crowded" })).toHaveAttribute("aria-pressed", "true");
        expect(rowFor("Nuclear power")).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Trend (20D)" }));
        await waitFor(() => expect(apiMocks.fetchThemes).toHaveBeenLastCalledWith(
            { view: "trend", stage: "crowded" },
            expect.any(AbortSignal),
        ));
        expect(within(leaderboard()).getByRole("columnheader", { name: "Heat 20D" })).toBeInTheDocument();

        fireEvent.click(within(stageFilter).getByRole("button", { name: "All stages" }));
        await waitFor(() => expect(apiMocks.fetchThemes).toHaveBeenLastCalledWith(
            { view: "trend", stage: null },
            expect.any(AbortSignal),
        ));
        expect(apiMocks.fetchThemes).toHaveBeenCalledTimes(4);
    });

    it("switches between table, treemap and rotation without refetching", async () => {
        unlockSession();
        render(<ThemesPage />);
        await waitFor(() => rowFor("AI compute"));

        fireEvent.click(screen.getByRole("button", { name: "Treemap" }));
        expect(screen.getByTestId("theme-treemap")).toHaveAttribute("data-count", "4");
        expect(screen.getByTestId("theme-treemap")).toHaveAttribute("data-view", "now");

        fireEvent.click(screen.getByRole("button", { name: "Rotation" }));
        expect(screen.getByTestId("theme-rotation")).toHaveAttribute("data-count", "4");
        expect(screen.queryByRole("table", { name: "Theme leaderboard" })).not.toBeInTheDocument();
        expect(apiMocks.fetchThemes).toHaveBeenCalledTimes(1);
    });

    it("relocks the workspace when the key is rejected", async () => {
        unlockSession();
        apiMocks.fetchThemes.mockRejectedValue(new ApiError("Invalid API key", 401, "Invalid API key"));
        render(<ThemesPage />);

        expect(await screen.findByRole("heading", { name: "Themes are private to your workspace" })).toBeInTheDocument();
        expect(screen.getByText("The personal workspace was locked because the session was rejected. Unlock it again with the Admin Key.")).toBeInTheDocument();
        expect(apiMocks.deleteAdminSession).toHaveBeenCalled();
        expect(apiMocks.fetchThemes).toHaveBeenCalledTimes(1);
    });

    it("shows a server error and retries the same request", async () => {
        unlockSession();
        apiMocks.fetchThemes
            .mockRejectedValueOnce(new ApiError("Theme trends have not been published yet", 503, "Theme trends have not been published yet"))
            .mockResolvedValueOnce(makeThemesListFixture());
        render(<ThemesPage />);

        expect(await screen.findByRole("alert")).toHaveTextContent("Theme trends have not been published yet");
        expect(screen.getByText("Theme rankings are unavailable.")).toBeInTheDocument();
        await waitFor(() => expect(useStatusBar.getState().items).toEqual([{ label: "Themes", value: "Unavailable", tone: "error" }]));

        fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
        expect(await waitFor(() => rowFor("AI compute"))).toBeInTheDocument();
        expect(apiMocks.fetchThemes).toHaveBeenCalledTimes(2);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("loads trending companies with theme names and dashes for missing values", async () => {
        unlockSession();
        render(<ThemesPage />);
        await waitFor(() => rowFor("AI compute"));

        fireEvent.click(screen.getByRole("tab", { name: "Trending companies" }));
        const table = await screen.findByRole("table", { name: "Trending companies" });
        expect(screen.getByRole("tab", { name: "Trending companies" })).toHaveAttribute("aria-selected", "true");
        expect(apiMocks.fetchTrendingCompanies).toHaveBeenCalledWith({ limit: 50 }, expect.any(AbortSignal));

        const ionq = (await within(table).findByRole("link", { name: "IONQ" })).closest("tr")!;
        expect(within(ionq).getByRole("link", { name: "Quantum computing" })).toHaveAttribute("href", "/themes/quantum-computing");
        expect(within(ionq).getByText("Ignited")).toBeInTheDocument();
        expect(within(ionq).getByText("+21.4%")).toBeInTheDocument();

        const sparse = cellTexts(within(table).getByRole("link", { name: "XYZ" }).closest("tr")!);
        expect(sparse[2]).toBe("—");
        expect(sparse[3]).toBe("—");
        expect(sparse[5]).toBe("—");
        expect(sparse[6]).toBe("—");
        expect(sparse[8]).toBe("−2.1%");
        expect(sparse[9]).toBe("—");
        expect(sparse[13]).toBe("—");
        expect(sparse[14]).toBe("Attention only");
    });

    it("relocks when the trending companies request is rejected", async () => {
        unlockSession();
        apiMocks.fetchTrendingCompanies.mockRejectedValue(new ApiError("Invalid API key", 401, "Invalid API key"));
        render(<ThemesPage />);
        await waitFor(() => rowFor("AI compute"));

        fireEvent.click(screen.getByRole("tab", { name: "Trending companies" }));
        expect(await screen.findByRole("heading", { name: "Themes are private to your workspace" })).toBeInTheDocument();
        expect(apiMocks.deleteAdminSession).toHaveBeenCalled();
    });

    it("reviews proposals and shows the taxonomy snippet after accepting", async () => {
        unlockSession();
        apiMocks.decideThemeProposal.mockResolvedValue({
            proposal: { ...makeThemeProposalsFixture().proposals[0], status: "accepted" },
            yaml_snippet: "- slug: humanoid-robots\n  name_en: Humanoid robots",
        });
        render(<ThemesPage />);
        await waitFor(() => rowFor("AI compute"));

        fireEvent.click(screen.getByRole("tab", { name: "Proposals" }));
        expect(await screen.findByRole("heading", { name: "Humanoid robots" })).toBeInTheDocument();
        expect(apiMocks.fetchThemeProposals).toHaveBeenCalledWith("pending", expect.any(AbortSignal));
        expect(screen.getByRole("link", { name: "HSAI" })).toHaveAttribute("href", "/?ticker=HSAI.US");
        // The worker lists company profiles (E1–E3) before headlines; the cited headline still leads the card.
        const card = screen.getByRole("heading", { name: "Humanoid robots" }).closest("li")!;
        const cited = within(card).getByText("Humanoid robot demo day").closest("li")!;
        expect(cited).toHaveAttribute("id", "proposal-7-evidence-E4");
        expect(cited.closest("details")).toBeNull();
        expect(within(cited).getByRole("link", { name: "Open source for Humanoid robot demo day" })).toHaveAttribute("href", "https://example.com/robots");
        expect(within(card).getByRole("link", { name: "E4" })).toHaveAttribute("href", "#proposal-7-evidence-E4");
        expect(within(card).getByText("Company profiles (3)").closest("details")).toContainElement(within(card).getByText("TSLA.US Tesla, Inc."));

        fireEvent.click(screen.getByRole("button", { name: "Accept" }));
        expect(await screen.findByText("Add to services/themes/taxonomy/themes_v1.yaml")).toBeInTheDocument();
        expect(apiMocks.decideThemeProposal).toHaveBeenCalledWith(7, "accepted");
        expect(screen.getByText(/slug: humanoid-robots/)).toBeInTheDocument();
        expect(screen.getByText("accepted")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "All" }));
        await waitFor(() => expect(apiMocks.fetchThemeProposals).toHaveBeenLastCalledWith(null, expect.any(AbortSignal)));
    });

    it("shows a cited headline even when the cluster has more companies than the card lists", async () => {
        unlockSession();
        const tickers = ["TSLA.US", "HSAI.US", "RR.US", "ABB.US", "TER.US", "SYM.US"];
        apiMocks.fetchThemeProposals.mockResolvedValue(makeThemeProposalsFixture([makeThemeProposal({
            tickers,
            rationale: "Suppliers moved together on launch headlines [E7].",
            evidence: [
                ...tickers.map((ticker, index) => ({ id: `E${index + 1}`, kind: "company", label: `${ticker} Company ${index + 1}` })),
                { id: "E7", kind: "news", label: "Humanoid launch headline", link: "https://example.com/launch" },
                { id: "E8", kind: "news", label: "Actuator supply headline", link: "https://example.com/actuators" },
            ],
        })]));
        render(<ThemesPage />);
        await waitFor(() => rowFor("AI compute"));

        fireEvent.click(screen.getByRole("tab", { name: "Proposals" }));
        const card = (await screen.findByRole("heading", { name: "Humanoid robots" })).closest("li")!;
        const listed = card.querySelector(":scope > ul")!;
        expect([...listed.querySelectorAll(":scope > li")].map((item) => item.id)).toEqual(["proposal-7-evidence-E7", "proposal-7-evidence-E8"]);
        expect(within(card).getByRole("link", { name: "Open source for Humanoid launch headline" })).toHaveAttribute("href", "https://example.com/launch");
        expect(within(card).getByText("Company profiles (6)")).toBeInTheDocument();
    });

    it("recovers the snippet of an accepted proposal after the tab remounts and lets a rejection be undone", async () => {
        unlockSession();
        const snippet = "- slug: humanoid-robots\n  name_en: Humanoid robots";
        const accepted = { ...makeThemeProposal(), status: "accepted" as const };
        apiMocks.decideThemeProposal.mockResolvedValue({ proposal: accepted, yaml_snippet: snippet });
        render(<ThemesPage />);
        await waitFor(() => rowFor("AI compute"));

        fireEvent.click(screen.getByRole("tab", { name: "Proposals" }));
        fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
        expect(await screen.findByText("Add to services/themes/taxonomy/themes_v1.yaml")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Show snippet" })).not.toBeInTheDocument();

        // Switching tabs unmounts the panel, and the list response never carries the snippet.
        apiMocks.fetchThemeProposals.mockResolvedValue(makeThemeProposalsFixture([accepted]));
        fireEvent.click(screen.getByRole("tab", { name: "Themes" }));
        fireEvent.click(screen.getByRole("tab", { name: "Proposals" }));
        fireEvent.click(screen.getByRole("button", { name: "All" }));
        await waitFor(() => expect(apiMocks.fetchThemeProposals).toHaveBeenLastCalledWith(null, expect.any(AbortSignal)));
        expect(await screen.findByText("accepted")).toBeInTheDocument();
        expect(screen.queryByText("Add to services/themes/taxonomy/themes_v1.yaml")).not.toBeInTheDocument();

        apiMocks.decideThemeProposal.mockClear();
        fireEvent.click(screen.getByRole("button", { name: "Show snippet" }));
        expect(await screen.findByText("Add to services/themes/taxonomy/themes_v1.yaml")).toBeInTheDocument();
        expect(apiMocks.decideThemeProposal).toHaveBeenCalledWith(7, "accepted");
        expect(screen.getByText(/slug: humanoid-robots/)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Show snippet" })).not.toBeInTheDocument();

        // A misclicked rejection can be reversed from the card.
        apiMocks.fetchThemeProposals.mockResolvedValue(makeThemeProposalsFixture([{ ...accepted, status: "rejected" }]));
        fireEvent.click(screen.getByRole("tab", { name: "Themes" }));
        fireEvent.click(screen.getByRole("tab", { name: "Proposals" }));
        fireEvent.click(screen.getByRole("button", { name: "All" }));
        expect(await screen.findByText("rejected")).toBeInTheDocument();
        apiMocks.decideThemeProposal.mockClear();
        fireEvent.click(screen.getByRole("button", { name: "Accept instead" }));
        expect(await screen.findByText("Add to services/themes/taxonomy/themes_v1.yaml")).toBeInTheDocument();
        expect(apiMocks.decideThemeProposal).toHaveBeenCalledWith(7, "accepted");
        expect(screen.getByText("accepted")).toBeInTheDocument();
    });
});
