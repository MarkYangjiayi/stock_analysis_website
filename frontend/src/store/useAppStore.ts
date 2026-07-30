import { create } from 'zustand';
import type { AnomalyReport, AnomalyScan } from '@/lib/api';

export interface AnomaliesState {
    data: AnomalyReport[];
    lastFetchTime: number | null;
    latestScan: AnomalyScan | null;
    setAnomalyScan: (scan: AnomalyScan) => void;
}

export const useAppStore = create<AnomaliesState>((set) => ({
    data: [],
    lastFetchTime: null,
    latestScan: null,
    setAnomalyScan: (scan) => set({
        data: scan.results,
        latestScan: scan,
        lastFetchTime: scan.finished_at
            ? new Date(scan.finished_at).getTime()
            : Date.now(),
    }),
}));
