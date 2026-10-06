import { create } from "zustand";

export type StatusTone = "ready" | "stale" | "error" | "neutral";

export interface StatusItem {
    label: string;
    value: string;
    tone?: StatusTone;
}

interface StatusBarState {
    items: StatusItem[];
    note: string | null;
    setStatus: (items: StatusItem[], note?: string | null) => void;
    clearStatus: () => void;
}

/** Page-scoped data-freshness readouts shown in the global bottom status bar. */
export const useStatusBar = create<StatusBarState>((set) => ({
    items: [],
    note: null,
    setStatus: (items, note = null) => set({ items, note }),
    clearStatus: () => set({ items: [], note: null }),
}));
