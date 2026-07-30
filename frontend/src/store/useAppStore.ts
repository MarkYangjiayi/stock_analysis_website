import { create } from 'zustand';
import { AnomalyReport } from '@/lib/api';

export interface AnomaliesState {
    data: AnomalyReport[];
    lastFetchTime: number | null;
    setAnomaliesData: (data: AnomalyReport[]) => void;
}

export const useAppStore = create<AnomaliesState>((set) => ({
    data: [],
    lastFetchTime: null,
    setAnomaliesData: (data) => set({ data, lastFetchTime: Date.now() }),
}));
