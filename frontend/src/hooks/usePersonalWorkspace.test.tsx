import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { LEGACY_WATCHLIST_KEY, PERSONAL_SESSION_KEY, WATCHLIST_MIGRATION_KEY, readLegacyWatchlist, usePersonalWorkspace } from "@/hooks/usePersonalWorkspace";

const apiMocks = vi.hoisted(() => ({
    fetchPersonalWatchlist: vi.fn(),
    importPersonalWatchlist: vi.fn(),
    addPersonalWatchlistItem: vi.fn(),
    removePersonalWatchlistItem: vi.fn(),
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
        apiMocks.fetchPersonalWatchlist.mockResolvedValue({ tickers: [] });
        apiMocks.importPersonalWatchlist.mockResolvedValue({ tickers: ["AAPL.US", "NVDA.US"], imported: true });
        apiMocks.addPersonalWatchlistItem.mockResolvedValue({ tickers: ["MSFT.US", "AAPL.US", "NVDA.US"], added: true });
        apiMocks.removePersonalWatchlistItem.mockResolvedValue({ tickers: ["NVDA.US"], removed: true });
    });

    it("restores a session-only key and transactionally imports the legacy watchlist", async () => {
        window.localStorage.setItem(LEGACY_WATCHLIST_KEY, JSON.stringify(["AAPL", "NVDA", "AAPL.US"]));
        window.sessionStorage.setItem(PERSONAL_SESSION_KEY, "session-secret");
        const { result } = renderHook(() => usePersonalWorkspace());

        await waitFor(() => expect(result.current.restoring).toBe(false));
        expect(apiMocks.fetchPersonalWatchlist).toHaveBeenCalledWith("session-secret");
        expect(apiMocks.importPersonalWatchlist).toHaveBeenCalledWith(["AAPL.US", "NVDA.US"], "session-secret");
        expect(result.current.watchlist).toEqual(["AAPL.US", "NVDA.US"]);
        expect(result.current.isUnlocked).toBe(true);
        expect(window.sessionStorage.getItem(PERSONAL_SESSION_KEY)).toBe("session-secret");
        expect(window.localStorage.getItem(PERSONAL_SESSION_KEY)).toBeNull();
    });

    it("preserves an intentionally empty legacy watchlist", () => {
        window.localStorage.setItem(LEGACY_WATCHLIST_KEY, "[]");

        expect(readLegacyWatchlist()).toEqual([]);
        expect(window.localStorage.getItem(LEGACY_WATCHLIST_KEY)).toBe("[]");
    });

    it("uses the server as authority after unlock and clears the key on a 401", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });

        await act(async () => { await result.current.addToWatchlist("msft"); });
        expect(apiMocks.addPersonalWatchlistItem).toHaveBeenCalledWith("MSFT.US", "session-secret");
        expect(result.current.watchlist).toEqual(["MSFT.US", "AAPL.US", "NVDA.US"]);

        await act(async () => { await result.current.removeFromWatchlist("AAPL.US"); });
        expect(apiMocks.removePersonalWatchlistItem).toHaveBeenCalledWith("AAPL.US", "session-secret");
        expect(result.current.watchlist).toEqual(["NVDA.US"]);

        apiMocks.removePersonalWatchlistItem.mockRejectedValueOnce(new ApiError("Invalid API key", 401));
        await act(async () => { await result.current.removeFromWatchlist("NVDA.US"); });
        expect(result.current.isUnlocked).toBe(false);
        expect(window.sessionStorage.getItem(PERSONAL_SESSION_KEY)).toBeNull();
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
        expect(apiMocks.addPersonalWatchlistItem).toHaveBeenCalledWith("MSFT.US", "session-secret");
        expect(apiMocks.removePersonalWatchlistItem).toHaveBeenCalledWith("AAPL.US", "session-secret");
    });

    it("reloads the watchlist when the page comes back into view", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });

        apiMocks.fetchPersonalWatchlist.mockResolvedValueOnce({ tickers: ["TSLA.US", "AAPL.US", "NVDA.US"] });
        act(() => { window.dispatchEvent(new Event("focus")); });

        await waitFor(() => expect(result.current.watchlist).toEqual(["TSLA.US", "AAPL.US", "NVDA.US"]));
        expect(apiMocks.fetchPersonalWatchlist).toHaveBeenLastCalledWith("session-secret");
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

    it("does not re-import legacy data after the one-time migration was attempted", async () => {
        window.localStorage.setItem(LEGACY_WATCHLIST_KEY, JSON.stringify(["AAPL.US"]));
        window.localStorage.setItem(WATCHLIST_MIGRATION_KEY, "true");
        window.sessionStorage.setItem(PERSONAL_SESSION_KEY, "session-secret");
        apiMocks.fetchPersonalWatchlist.mockResolvedValueOnce({ tickers: [] });
        const { result } = renderHook(() => usePersonalWorkspace());

        await waitFor(() => expect(result.current.restoring).toBe(false));
        expect(apiMocks.importPersonalWatchlist).not.toHaveBeenCalled();
        expect(result.current.watchlist).toEqual([]);
        expect(result.current.isUnlocked).toBe(true);
    });
});
