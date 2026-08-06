import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { LEGACY_WATCHLIST_KEY, PERSONAL_SESSION_KEY, WATCHLIST_MIGRATION_KEY, readLegacyWatchlist, usePersonalWorkspace } from "@/hooks/usePersonalWorkspace";

const apiMocks = vi.hoisted(() => ({
    fetchPersonalWatchlist: vi.fn(),
    importPersonalWatchlist: vi.fn(),
    replacePersonalWatchlist: vi.fn(),
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
        apiMocks.replacePersonalWatchlist.mockResolvedValue({ tickers: ["MSFT.US"] });
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

        await act(async () => { await result.current.replaceWatchlist(["MSFT"]); });
        expect(apiMocks.replacePersonalWatchlist).toHaveBeenCalledWith(["MSFT.US"], "session-secret");
        expect(result.current.watchlist).toEqual(["MSFT.US"]);

        apiMocks.replacePersonalWatchlist.mockRejectedValueOnce(new ApiError("Invalid API key", 401));
        await act(async () => { await result.current.replaceWatchlist(["AAPL"]); });
        expect(result.current.isUnlocked).toBe(false);
        expect(window.sessionStorage.getItem(PERSONAL_SESSION_KEY)).toBeNull();
    });

    it("serializes overlapping replacements and ignores stale responses", async () => {
        const { result } = renderHook(() => usePersonalWorkspace());
        await waitFor(() => expect(result.current.restoring).toBe(false));
        await act(async () => { await result.current.unlock("session-secret"); });

        let resolveFirst!: (value: { tickers: string[] }) => void;
        let resolveSecond!: (value: { tickers: string[] }) => void;
        apiMocks.replacePersonalWatchlist
            .mockImplementationOnce(() => new Promise((resolve) => {
                resolveFirst = resolve;
            }))
            .mockImplementationOnce(() => new Promise((resolve) => {
                resolveSecond = resolve;
            }));

        let firstRequest!: Promise<boolean>;
        let secondRequest!: Promise<boolean>;
        act(() => {
            firstRequest = result.current.replaceWatchlist(["AAPL"]);
            secondRequest = result.current.replaceWatchlist(["MSFT"]);
        });
        expect(result.current.watchlist).toEqual(["MSFT.US"]);
        await waitFor(() => {
            expect(apiMocks.replacePersonalWatchlist).toHaveBeenCalledTimes(1);
        });

        await act(async () => {
            resolveFirst({ tickers: ["AAPL.US"] });
            await firstRequest;
        });
        expect(result.current.watchlist).toEqual(["MSFT.US"]);
        await waitFor(() => {
            expect(apiMocks.replacePersonalWatchlist).toHaveBeenCalledTimes(2);
        });

        await act(async () => {
            resolveSecond({ tickers: ["MSFT.US"] });
            await secondRequest;
        });
        expect(result.current.watchlist).toEqual(["MSFT.US"]);
        expect(apiMocks.replacePersonalWatchlist.mock.calls).toEqual([
            [["AAPL.US"], "session-secret"],
            [["MSFT.US"], "session-secret"],
        ]);
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
