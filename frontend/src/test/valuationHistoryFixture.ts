import type { HistoricalDataPoint, MultipleKey, ValuationHistoryResponse } from "@/lib/api";

export function makeValuationHistoryFixture(data: HistoricalDataPoint[]): ValuationHistoryResponse {
    const keys: MultipleKey[] = ["pe", "ps", "pb", "pfcf", "ev_revenue", "ev_ebitda"];
    const labels = ["P/E", "P/S", "P/B", "P/FCF", "EV/Revenue", "EV/EBITDA"];
    const points = data.map((point, index) => ({
        date: point.date, price_date: point.date, basis_id: 0,
        values: { pe: 20 + index, ps: 4 + index, pb: 3 + index, pfcf: 25 + index, ev_revenue: 5 + index, ev_ebitda: 12 + index },
        reason: null, ev_reason: null,
    }));
    return {
        ticker: "TEST.US", interval: "1d", currency: "USD", price_basis: "split_only",
        history_basis: "reconstructed_estimates", split_reference_date: data.at(-1)?.date ?? null,
        methodology: ["Reconstructed estimates; historical statements may contain later restatements."], warnings: [],
        metrics: keys.map((key, index) => ({
            key, label: labels[index], description: "Reported inputs · estimate", formula: `${labels[index]} formula`,
            valid_points: points.length, total_points: points.length,
            latest_value: points.at(-1)?.values[key] ?? null, latest_date: data.at(-1)?.date ?? null,
            latest_reason: null, median: points.length ? points[Math.floor(points.length / 2)].values[key] : null,
        })),
        bases: [{ id: 0, period_end: "2024-09-30", available_from: "2024-11-04", periods: ["2024-09-30", "2024-06-30", "2024-03-31", "2023-12-31"], source: "EODHD", raw_snapshot_ids: [1], share_reference_dates: ["2025-02-01"], inputs: {}, reasons: {} }],
        points,
    };
}
