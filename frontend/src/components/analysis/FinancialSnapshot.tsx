import { ArrowRight } from "lucide-react";
import type { MarketSnapshotResponse, StockDataResponse } from "@/lib/api";
import { formatCompactNumber } from "@/lib/format";

export default function FinancialSnapshot({ stock, snapshot, statementDate, onDetails }: {
    stock: StockDataResponse;
    snapshot: MarketSnapshotResponse | null;
    statementDate?: string | null;
    onDetails: () => void;
}) {
    const currency = stock.profile.currency || "USD";
    const rows = [
        { label: "Revenue", basis: "TTM", value: stock.valuation_metrics?.ttm.revenue, unit: currency, date: statementDate },
        { label: "Operating margin", basis: "TTM", value: snapshot?.metrics.operating_margin?.value, unit: "%", date: snapshot?.metrics.operating_margin?.source_date },
        { label: "Free cash flow", basis: "TTM · reported", value: stock.valuation_metrics?.ttm.free_cash_flow, unit: currency, date: statementDate },
        { label: "Market cap", basis: "Snapshot", value: snapshot?.metrics.market_cap?.value, unit: snapshot?.currency || currency, date: snapshot?.metrics.market_cap?.source_date },
    ];
    return (
        <section className="research-panel flex flex-col p-4" aria-labelledby="financial-snapshot-title">
            <h2 id="financial-snapshot-title" className="text-lg font-semibold tracking-tight">Financial snapshot</h2>
            <dl className="mt-2 flex-1 divide-y">
                {rows.map((row) => {
                    const available = typeof row.value === "number" && Number.isFinite(row.value);
                    return <div key={row.label} className="flex items-center justify-between gap-3 py-2">
                        <dt className="text-sm">{row.label}<span className="mt-0.5 block text-xs text-[var(--text-muted)]">{row.basis}</span></dt>
                        <dd className="text-right">
                            <span className="text-base font-semibold leading-5">{available ? row.unit === "%" ? `${((row.value as number) * 100).toFixed(1)}%` : formatCompactNumber(row.value as number, 1) : "—"}</span>
                            <span className="mt-0.5 block text-xs text-[var(--text-muted)]">{available ? `${row.unit === "%" ? "" : `${row.unit} · `}${row.date || "Date unavailable"}` : "Data unavailable"}</span>
                        </dd>
                    </div>;
                })}
            </dl>
            <button type="button" className="research-link mt-1 self-start" onClick={onDetails}>Explore financials <ArrowRight size={14} /></button>
        </section>
    );
}
