import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { LEGACY_WATCHLIST_KEY, PERSONAL_SESSION_KEY, WATCHLIST_MIGRATION_KEY, readLegacyWatchlist, usePersonalWorkspace } from "@/hooks/usePersonalWorkspace";

const apiMocks = vi.hoisted(() => ({
    createAdminSession: vi.fn(),
    fetchAdminSession: vi.fn(),
    deleteAdminSession: vi.fn(),
    fetchPersonalWatchlist: vi.fn(),
    importPersonalWatchlist: vi.fn(),
    addPersonalWatchlistItem: vi.fn(),
    removePersonalWatchlistItem: vi.fn(),
    setPersonalWatchlistCore: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
    const original = await importOriginal<typeof import("@/lib/api")>();
    return {
        ...original,
        ...apiMocks,
    };
});

describe("usePersonalWorkspace", () => {
    beforeEach(() => {
        vi.clearAllMocks();
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
        Object.defineProperty(window, "localStorage", { value: storage(), configurable: true });
        Object.defineProperty(window, "sessionStorage", { value: storage(), configurable: true });
        apiMocks.createAdminSession.mockResolvedValue({ authenticated: true, expires_at: "2026-11-27T00:00:00+00:00" });
        apiMocks.fetchAdminSession.mockResolvedValue({ authenticated: false, expires_at: null });
        apiMocks.deleteAdminSession.mockResolvedValue({ authenticated: false, expires_at: null });
        apiMocks.fetchPersonalWatchlist.mockResolvedValue({ tickers: [] });
        apiMocks.importPersonalWatchlist.mockResolvedValue({ tickers: ["AAPL.US", "NVDA.US"], imported: true });
        apiMocks.addPersonalWatchlistItem.mockResolvedValue({ tickers: ["MSFT.US", "AAPL.US", "NVDA.US"], added: true });
        apiMocks.removePersonalWatchlistItem.mockResolvedValue({ tickers: ["NVDA.US"], removed: true });
    });

    it("restores a cookie session and transactionally imports the legacy watchlist", async () => {
        window.localStorage.setItem(LEGACY_WATCHLIST_KEY, JSON.stringify(["AAPL", "NVDA", "AAPL.US"]));
        // A key left over from before sessions moved to a cookie is discarded, not reused.
        window.sessionStorage.setItem(PERSONAL_SESSION_KEY, "old-secret");
        apiMocks.fetchAdminSession.mockResolvedValueOnce({ authenticated: true, expires_at: "2026-11-27T00:00:00+00:00" });
        const { result } = renderHook(() => usePersonalWorkspace());

        await waitFor(() => expect(result.current.restoring).toBe(false));
        expect(apiMocks.createAdminSession).not.toHaveBeenCalled();
        expect(apiMocks.fetchPersonalWatchlist).toHaveBeenCalledWith();
        expect(apiMocks.importPersonalWatchlist).toHaveBeenCalledWith(["AAPL.US", "NVDA.US"]);
        expect(result.current.watchlist).toEqual(["AAPL.US", "NVDA.US"]);
        expect(result.current.isUnlocked).toBe(true);
        expect(window.sessionStorage.getItem(PERSONAL_SESSION_KEY)).toBeNull();
        expect(window.localStorage.getItem(PERSONAL_SESSION_KEY)).toBeNull();
    });

    it("stays locked without a session and never stores the key it is given", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        expect(result.current.isUnlocked).toBe(false);
        expect(apiMocks.fetchPersonalWatchlist).not.toHaveBeenCalled();

        await act(async () => { await result.current.unlock("  session-secret "); });
        expect(apiMocks.createAdminSession).toHaveBeenCalledWith("session-secret");
        expect(result.current.isUnlocked).toBe(true);
        expect(window.sessionStorage.length).toBe(0);
        for (let index = 0; index < window.localStorage.length; index += 1) {
            expect(window.localStorage.getItem(window.localStorage.key(index)!)).not.toContain("session-secret");
        }
    });

    it("reports a rejected key without unlocking", async () => {
        apiMocks.createAdminSession.mockRejectedValueOnce(new ApiError("Invalid API key", 401));
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));

        let unlocked = true;
        await act(async () => { unlocked = await result.current.unlock("guess"); });
        expect(unlocked).toBe(false);
        expect(result.current.isUnlocked).toBe(false);
        expect(result.current.error).toBe("The Admin Key was rejected.");
        expect(apiMocks.fetchPersonalWatchlist).not.toHaveBeenCalled();
    });

    it("preserves an intentionally empty legacy watchlist", () => {
        window.localStorage.setItem(LEGACY_WATCHLIST_KEY, "[]");

        expect(readLegacyWatchlist()).toEqual([]);
        expect(window.localStorage.getItem(LEGACY_WATCHLIST_KEY)).toBe("[]");
    });

    it("uses the server as authority after unlock and ends the session on a 401", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });

        await act(async () => { await result.current.addToWatchlist("msft"); });
        expect(apiMocks.addPersonalWatchlistItem).toHaveBeenCalledWith("MSFT.US");
        expect(result.current.watchlist).toEqual(["MSFT.US", "AAPL.US", "NVDA.US"]);

        await act(async () => { await result.current.removeFromWatchlist("AAPL.US"); });
        expect(apiMocks.removePersonalWatchlistItem).toHaveBeenCalledWith("AAPL.US");
        expect(result.current.watchlist).toEqual(["NVDA.US"]);

        apiMocks.removePersonalWatchlistItem.mockRejectedValueOnce(new ApiError("Invalid API key", 401));
        await act(async () => { await result.current.removeFromWatchlist("NVDA.US"); });
        expect(result.current.isUnlocked).toBe(false);
        expect(apiMocks.deleteAdminSession).toHaveBeenCalledTimes(1);
    });

    it("serializes overlapping edits and ignores stale responses", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });
        expect(result.current.watchlist).toEqual(["AAPL.US", "NVDA.US"]);

        let resolveAdd!: (value: { tickers: string[]; added: boolean }) => void;
        let resolveRemove!: (value: { tickers: string[]; removed: boolean }) => void;
        apiMocks.addPersonalWatchlistItem.mockImplementationOnce(() => new Promise((resolve) => {
            resolveAdd = resolve;
        }));
        apiMocks.removePersonalWatchlistItem.mockImplementationOnce(() => new Promise((resolve) => {
            resolveRemove = resolve;
        }));

        let addRequest!: Promise<boolean>;
        let removeRequest!: Promise<boolean>;
        act(() => {
            addRequest = result.current.addToWatchlist("MSFT");
            removeRequest = result.current.removeFromWatchlist("AAPL.US");
        });
        expect(result.current.watchlist).toEqual(["MSFT.US", "NVDA.US"]);
        await waitFor(() => expect(apiMocks.addPersonalWatchlistItem).toHaveBeenCalledTimes(1));
        expect(apiMocks.removePersonalWatchlistItem).not.toHaveBeenCalled();

        await act(async () => {
            resolveAdd({ tickers: ["MSFT.US", "AAPL.US", "NVDA.US"], added: true });
            await addRequest;
        });
        expect(result.current.watchlist).toEqual(["MSFT.US", "NVDA.US"]);
        await waitFor(() => expect(apiMocks.removePersonalWatchlistItem).toHaveBeenCalledTimes(1));

        await act(async () => {
            resolveRemove({ tickers: ["MSFT.US", "NVDA.US"], removed: true });
            await removeRequest;
        });
        expect(result.current.watchlist).toEqual(["MSFT.US", "NVDA.US"]);
        expect(apiMocks.addPersonalWatchlistItem).toHaveBeenCalledWith("MSFT.US");
        expect(apiMocks.removePersonalWatchlistItem).toHaveBeenCalledWith("AAPL.US");
    });

    it("reloads the watchlist when the page comes back into view", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });

        apiMocks.fetchPersonalWatchlist.mockResolvedValueOnce({ tickers: ["TSLA.US", "AAPL.US", "NVDA.US"] });
        act(() => { window.dispatchEvent(new Event("focus")); });

        await waitFor(() => expect(result.current.watchlist).toEqual(["TSLA.US", "AAPL.US", "NVDA.US"]));
        expect(apiMocks.fetchPersonalWatchlist).toHaveBeenLastCalledWith();
    });

    it("drops a reload that started before a newer local edit", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });

        let resolveReload!: (value: { tickers: string[] }) => void;
        let resolveAdd!: (value: { tickers: string[]; added: boolean }) => void;
        apiMocks.fetchPersonalWatchlist.mockImplementationOnce(() => new Promise((resolve) => {
            resolveReload = resolve;
        }));
        apiMocks.addPersonalWatchlistItem.mockImplementationOnce(() => new Promise((resolve) => {
            resolveAdd = resolve;
        }));
        act(() => { window.dispatchEvent(new Event("focus")); });
        await waitFor(() => expect(apiMocks.fetchPersonalWatchlist).toHaveBeenCalledTimes(2));

        let addRequest!: Promise<boolean>;
        act(() => { addRequest = result.current.addToWatchlist("MSFT"); });
        await act(async () => { resolveReload({ tickers: ["AAPL.US", "NVDA.US"] }); });
        expect(result.current.watchlist).toEqual(["MSFT.US", "AAPL.US", "NVDA.US"]);

        await waitFor(() => expect(apiMocks.addPersonalWatchlistItem).toHaveBeenCalledTimes(1));
        await act(async () => {
            resolveAdd({ tickers: ["MSFT.US", "TSLA.US", "AAPL.US", "NVDA.US"], added: true });
            await addRequest;
        });
        expect(result.current.watchlist).toEqual(["MSFT.US", "TSLA.US", "AAPL.US", "NVDA.US"]);
    });

    it("marks core tickers in watchlist order and reverts a refused change", async () => {
        apiMocks.importPersonalWatchlist.mockResolvedValueOnce({ tickers: ["AAPL.US", "NVDA.US", "MSFT.US"], core: ["MSFT.US"], imported: false });
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });
        expect(result.current.core).toEqual(["MSFT.US"]);

        let resolveCore!: (value: { tickers: string[]; core: string[]; changed: boolean }) => void;
        apiMocks.setPersonalWatchlistCore.mockImplementationOnce(() => new Promise((resolve) => {
            resolveCore = resolve;
        }));
        let request!: Promise<boolean>;
        act(() => { request = result.current.setCore("aapl", true); });
        // Watchlist order, as the server returns it, not the order of marking.
        expect(result.current.core).toEqual(["AAPL.US", "MSFT.US"]);
        await waitFor(() => expect(apiMocks.setPersonalWatchlistCore).toHaveBeenCalledWith("AAPL.US", true));
        await act(async () => {
            resolveCore({ tickers: ["AAPL.US", "NVDA.US", "MSFT.US"], core: ["AAPL.US", "MSFT.US"], changed: true });
            await request;
        });
        expect(result.current.core).toEqual(["AAPL.US", "MSFT.US"]);

        apiMocks.setPersonalWatchlistCore.mockRejectedValueOnce(new ApiError("At most 8 watchlist tickers can be core.", 422));
        await act(async () => { await result.current.setCore("NVDA.US", true); });
        expect(result.current.core).toEqual(["AAPL.US", "MSFT.US"]);
        expect(result.current.error).toBe("At most 8 watchlist tickers can be core.");
    });

    it("drops a removed ticker from the core tier", async () => {
        apiMocks.importPersonalWatchlist.mockResolvedValueOnce({ tickers: ["AAPL.US", "NVDA.US"], core: ["NVDA.US"], imported: false });
        apiMocks.removePersonalWatchlistItem.mockResolvedValueOnce({ tickers: ["AAPL.US"], core: [], removed: true });
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });

        let request!: Promise<boolean>;
        act(() => { request = result.current.removeFromWatchlist("NVDA.US"); });
        expect(result.current.core).toEqual([]);
        await act(async () => { await request; });
        expect(result.current.watchlist).toEqual(["AAPL.US"]);
        expect(result.current.core).toEqual([]);
    });

    it("does not re-import legacy data after the one-time migration was attempted", async () => {
        window.localStorage.setItem(LEGACY_WATCHLIST_KEY, JSON.stringify(["AAPL.US"]));
        window.localStorage.setItem(WATCHLIST_MIGRATION_KEY, "true");
        apiMocks.fetchAdminSession.mockResolvedValueOnce({ authenticated: true, expires_at: "2026-11-27T00:00:00+00:00" });
        apiMocks.fetchPersonalWatchlist.mockResolvedValueOnce({ tickers: [] });
        const { result } = renderHook(() => usePersonalWorkspace());

        await waitFor(() => expect(result.current.restoring).toBe(false));
        expect(apiMocks.importPersonalWatchlist).not.toHaveBeenCalled();
        expect(result.current.watchlist).toEqual([]);
        expect(result.current.isUnlocked).toBe(true);
    });
});
