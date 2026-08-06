"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
    ApiError,
    fetchPersonalWatchlist,
    importPersonalWatchlist,
    replacePersonalWatchlist,
} from "@/lib/api";

export const PERSONAL_SESSION_KEY = "personal_workspace_api_key";
export const LEGACY_WATCHLIST_KEY = "my_watchlist";
export const WATCHLIST_MIGRATION_KEY = "personal_watchlist_migration_attempted";
export const DEFAULT_WATCHLIST = ["AAPL.US", "AMAT.US", "ASTS.US", "UNH.US"];

const canonicalizeForDisplay = (ticker: string) => {
    const value = ticker.trim().toUpperCase();
    if (!value) return "";
    return value.includes(".") ? value : `${value}.US`;
};

export const readLegacyWatchlist = (): string[] => {
    const stored = window.localStorage.getItem(LEGACY_WATCHLIST_KEY);
    if (stored) {
        try {
            const parsed: unknown = JSON.parse(stored);
            if (Array.isArray(parsed)) {
                const values = parsed
                    .filter((value): value is string => typeof value === "string")
                    .map(canonicalizeForDisplay)
                    .filter(Boolean);
                return [...new Set(values)].slice(0, 100);
            }
        } catch { /* Fall through to the defaults. */ }
    }
    window.localStorage.setItem(LEGACY_WATCHLIST_KEY, JSON.stringify(DEFAULT_WATCHLIST));
    return DEFAULT_WATCHLIST;
};

export function usePersonalWorkspace() {
    const [adminKey, setAdminKey] = useState<string | null>(null);
    const [watchlist, setWatchlist] = useState<string[]>([]);
    const [restoring, setRestoring] = useState(true);
    const [unlocking, setUnlocking] = useState(false);
    const [error, setError] = useState("");
    const legacyRef = useRef<string[]>([]);
    const confirmedWatchlistRef = useRef<string[]>([]);
    const watchlistMutationVersionRef = useRef(0);
    const watchlistRequestQueueRef = useRef<Promise<void>>(Promise.resolve());

    const applyWatchlist = useCallback((next: string[]) => {
        setWatchlist(next);
    }, []);

    const lock = useCallback((message = "") => {
        watchlistMutationVersionRef.current += 1;
        window.sessionStorage.removeItem(PERSONAL_SESSION_KEY);
        setAdminKey(null);
        confirmedWatchlistRef.current = legacyRef.current;
        applyWatchlist(legacyRef.current);
        setError(message);
    }, [applyWatchlist]);

    const handleUnauthorized = useCallback(() => {
        lock("The personal workspace was locked because the Admin Key was rejected.");
    }, [lock]);

    const authenticate = useCallback(async (key: string, restoringSession = false) => {
        const normalizedKey = key.trim();
        if (!normalizedKey) {
            setError("Enter the Admin Key to unlock personal data.");
            return false;
        }
        if (!restoringSession) setUnlocking(true);
        setError("");
        try {
            const serverWatchlist = await fetchPersonalWatchlist(normalizedKey);
            const migrationAttempted = window.localStorage.getItem(WATCHLIST_MIGRATION_KEY) === "true";
            const authoritative = migrationAttempted
                ? { tickers: serverWatchlist.tickers, imported: false }
                : await importPersonalWatchlist(legacyRef.current, normalizedKey);
            window.sessionStorage.setItem(PERSONAL_SESSION_KEY, normalizedKey);
            window.localStorage.setItem(WATCHLIST_MIGRATION_KEY, "true");
            setAdminKey(normalizedKey);
            confirmedWatchlistRef.current = authoritative.tickers;
            applyWatchlist(authoritative.tickers);
            return true;
        } catch (caught) {
            window.sessionStorage.removeItem(PERSONAL_SESSION_KEY);
            setAdminKey(null);
            setError(
                caught instanceof ApiError && caught.status === 401
                    ? "The Admin Key was rejected."
                    : caught instanceof Error
                        ? caught.message
                        : "Unable to unlock the personal workspace.",
            );
            return false;
        } finally {
            if (!restoringSession) setUnlocking(false);
        }
    }, [applyWatchlist]);

    useEffect(() => {
        legacyRef.current = readLegacyWatchlist();
        confirmedWatchlistRef.current = legacyRef.current;
        applyWatchlist(legacyRef.current);
        const savedKey = window.sessionStorage.getItem(PERSONAL_SESSION_KEY);
        if (!savedKey) {
            setRestoring(false);
            return;
        }
        void authenticate(savedKey, true).finally(() => setRestoring(false));
    }, [applyWatchlist, authenticate]);

    const replaceWatchlist = useCallback(async (next: string[]) => {
        if (!adminKey) {
            setError("Unlock the personal workspace to edit the watchlist.");
            return false;
        }
        const normalized = [...new Set(next.map(canonicalizeForDisplay).filter(Boolean))].slice(0, 100);
        const mutationVersion = watchlistMutationVersionRef.current + 1;
        watchlistMutationVersionRef.current = mutationVersion;
        applyWatchlist(normalized);
        setError("");
        const request = watchlistRequestQueueRef.current.then(async () => {
            try {
                const result = await replacePersonalWatchlist(normalized, adminKey);
                confirmedWatchlistRef.current = result.tickers;
                if (mutationVersion === watchlistMutationVersionRef.current) {
                    applyWatchlist(result.tickers);
                }
                return true;
            } catch (caught) {
                if (caught instanceof ApiError && caught.status === 401) {
                    handleUnauthorized();
                } else if (mutationVersion === watchlistMutationVersionRef.current) {
                    applyWatchlist(confirmedWatchlistRef.current);
                    setError(caught instanceof Error ? caught.message : "Unable to update the watchlist.");
                }
                return false;
            }
        });
        watchlistRequestQueueRef.current = request.then(() => undefined);
        return request;
    }, [adminKey, applyWatchlist, handleUnauthorized]);

    return {
        adminKey,
        watchlist,
        restoring,
        unlocking,
        error,
        isUnlocked: Boolean(adminKey),
        unlock: authenticate,
        lock,
        handleUnauthorized,
        replaceWatchlist,
    };
}
