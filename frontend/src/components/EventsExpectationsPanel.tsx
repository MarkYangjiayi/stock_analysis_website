"use client";

import {
    CalendarClock,
    CalendarDays,
    Minus,
    Target,
    TrendingDown,
    TrendingUp,
} from "lucide-react";
import type {
    EarningsExpectation,
    EventsExpectationsResponse,
    StockEvent,
} from "@/lib/api";

interface EventsExpectationsPanelProps {
    data: EventsExpectationsResponse | null;
    loading?: boolean;
    error?: string;
    currency?: string | null;
    detail?: boolean;
}

const formatDate = (value?: string | null) => {
    if (!value) return "—";
    const parsed = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
    }).format(parsed);
};

const formatNumber = (value?: number | null, digits = 2) =>
    value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);

const formatPercent = (value?: number | null, digits = 1) =>
    value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;

const formatMoney = (value?: number | null, currency?: string | null) => {
    if (value == null || !Number.isFinite(value)) return "—";
    try {
        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: currency?.trim().toUpperCase() || "USD",
            notation: "compact",
            maximumFractionDigits: 1,
        }).format(value);
    } catch {
        return `$${value.toLocaleString("en-US", { maximumFractionDigits: 1 })}`;
    }
};

const eventDateLabel = (event: StockEvent) => {
    const timing = event.timing === "AfterMarket"
        ? "after market"
        : event.timing === "BeforeMarket"
            ? "before market"
            : null;
    return `${formatDate(event.event_date)}${timing ? ` · ${timing}` : ""}`;
};

const expectationRevision = (expectation?: EarningsExpectation) => {
    if (!expectation || expectation.eps_trend_current == null || expectation.eps_trend_30d == null || expectation.eps_trend_30d === 0) return null;
    return expectation.eps_trend_current / expectation.eps_trend_30d - 1;
};

const revisionTone = (value: number | null) => {
    if (value == null || Math.abs(value) < 0.0005) return { label: "Flat", className: "text-slate-500", Icon: Minus };
    return value > 0
        ? { label: "Raised", className: "text-emerald-600 dark:text-emerald-400", Icon: TrendingUp }
        : { label: "Lowered", className: "text-rose-500", Icon: TrendingDown };
};

function EventRow({ event, currency }: { event: StockEvent; currency?: string | null }) {
    const surprise = event.eps_surprise_percent;
    return (
        <li className="flex gap-3 rounded-xl border p-3.5">
            <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${event.kind === "earnings" ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-sky-50 text-sky-600 dark:bg-sky-950/40 dark:text-sky-300"}`}>
                {event.kind === "earnings" ? <CalendarClock size={16} /> : <CalendarDays size={16} />}
            </span>
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                        <p className="text-sm font-black">{event.title}</p>
                        <p className="mt-1 text-xs text-slate-500">{eventDateLabel(event)}</p>
                    </div>
                    <span className="rounded-full border px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                        {event.status}
                    </span>
                </div>
                {event.kind === "earnings" && event.status === "upcoming" && (
                    <p className="mt-2 text-xs text-slate-500">
                        Period ending {formatDate(event.period_end)} · EPS consensus {formatNumber(event.eps_estimate)}
                    </p>
                )}
                {event.kind === "earnings" && event.status === "reported" && (
                    <p className="mt-2 text-xs text-slate-500">
                        EPS {formatNumber(event.eps_actual)} vs {formatNumber(event.eps_estimate)} estimate
                        {surprise == null ? "" : ` · ${surprise >= 0 ? "+" : ""}${surprise.toFixed(1)}% surprise`}
                    </p>
                )}
                {event.kind === "dividend" && (
                    <p className="mt-2 text-xs text-slate-500">
                        Payment date {formatDate(event.payment_date)}{currency ? ` · ${currency}` : ""}
                    </p>
                )}
            </div>
        </li>
    );
}

function ExpectationTable({
    expectations,
    currency,
}: {
    expectations: EarningsExpectation[];
    currency?: string | null;
}) {
    return (
        <div className="overflow-x-auto rounded-xl border">
            <table className="w-full min-w-[760px] text-left text-xs">
                <thead className="surface-subtle">
                    <tr>
                        <th className="px-4 py-3">Period</th>
                        <th className="px-4 py-3">EPS consensus</th>
                        <th className="px-4 py-3">Revenue consensus</th>
                        <th className="px-4 py-3">Growth</th>
                        <th className="px-4 py-3">Analysts</th>
                        <th className="px-4 py-3">30d revisions</th>
                    </tr>
                </thead>
                <tbody className="divide-y">
                    {expectations.map((item) => {
                        const revision = expectationRevision(item);
                        const tone = revisionTone(revision);
                        const RevisionIcon = tone.Icon;
                        return (
                            <tr key={`${item.period}-${item.period_end}`}>
                                <td className="px-4 py-3">
                                    <p className="font-bold">{item.label}</p>
                                    <p className="mt-1 font-mono text-[10px] text-slate-500">{formatDate(item.period_end)}</p>
                                </td>
                                <td className="px-4 py-3 font-mono font-bold">
                                    {formatNumber(item.eps_average)}
                                    <span className="ml-1 text-[10px] font-normal text-slate-500">({formatNumber(item.eps_low)}–{formatNumber(item.eps_high)})</span>
                                </td>
                                <td className="px-4 py-3 font-mono font-bold">
                                    {formatMoney(item.revenue_average, currency)}
                                    <span className="ml-1 text-[10px] font-normal text-slate-500">({formatMoney(item.revenue_low, currency)}–{formatMoney(item.revenue_high, currency)})</span>
                                </td>
                                <td className="px-4 py-3 font-mono">
                                    <p>EPS {formatPercent(item.eps_growth)}</p>
                                    <p className="mt-1 text-slate-500">Revenue {formatPercent(item.revenue_growth)}</p>
                                </td>
                                <td className="px-4 py-3 font-mono">
                                    <p>EPS {item.eps_analyst_count ?? "—"}</p>
                                    <p className="mt-1 text-slate-500">Revenue {item.revenue_analyst_count ?? "—"}</p>
                                </td>
                                <td className={`px-4 py-3 font-mono font-bold ${tone.className}`}>
                                    <span className="inline-flex items-center gap-1"><RevisionIcon size={13} />{tone.label}</span>
                                    <p className="mt-1 text-[10px] font-normal">↑{item.eps_revisions_up_30d ?? 0} / ↓{item.eps_revisions_down_30d ?? 0}</p>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

export default function EventsExpectationsPanel({
    data,
    loading = false,
    error = "",
    currency,
    detail = false,
}: EventsExpectationsPanelProps) {
    const leadExpectation = data?.expectations[0];
    const revision = expectationRevision(leadExpectation);
    const tone = revisionTone(revision);
    const RevisionIcon = tone.Icon;

    return (
        <section className="rounded-xl border p-4 sm:p-5" aria-labelledby={detail ? "events-expectations-detail-title" : "events-expectations-title"}>
            <header className="flex flex-col justify-between gap-2 sm:flex-row sm:items-start">
                <div>
                    <p className="eyebrow">Events & expectations</p>
                    <h3 id={detail ? "events-expectations-detail-title" : "events-expectations-title"} className="mt-1 text-sm font-black">What could move the stock next</h3>
                </div>
                <span className="text-[10px] text-slate-500">{data?.as_of ? `Provider snapshot ${formatDate(data.as_of)}` : "Point-in-time provider data"}</span>
            </header>

            {loading && <div className="mt-4 rounded-xl border p-4 text-sm text-slate-500" role="status">Loading events and expectations…</div>}
            {error && !loading && <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300" role="alert">{error}</div>}
            {!loading && !error && !data?.available && <div className="mt-4 rounded-xl border p-4 text-sm text-slate-500">No event or forward-consensus data is available for this ticker yet.</div>}

            {!loading && !error && data?.available && (
                <>
                    <div className="mt-4 grid gap-3 lg:grid-cols-3">
                        <article className="surface-subtle rounded-xl border p-4">
                            <p className="text-[10px] font-black uppercase tracking-wide text-slate-500">Next catalyst</p>
                            <p className="mt-2 text-sm font-black">{data.next_event?.title || "No date published"}</p>
                            <p className="mt-1 text-xs text-slate-500">{data.next_event ? eventDateLabel(data.next_event) : "Provider has not published an upcoming event date."}</p>
                            {data.next_event?.kind === "dividend" && <p className="mt-2 text-xs text-slate-500">Payment date {formatDate(data.next_event.payment_date)}</p>}
                            {data.next_event?.kind === "earnings" && <p className="mt-2 text-xs text-slate-500">Period ending {formatDate(data.next_event.period_end)}</p>}
                        </article>
                        <article className="surface-subtle rounded-xl border p-4">
                            <p className="text-[10px] font-black uppercase tracking-wide text-slate-500">Consensus snapshot</p>
                            <p className="mt-2 text-sm font-black">{leadExpectation?.label || "Forward estimates"}</p>
                            <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-xs">
                                <span>EPS {formatNumber(leadExpectation?.eps_average)}</span>
                                <span>Revenue {formatMoney(leadExpectation?.revenue_average, currency)}</span>
                                <span>EPS growth {formatPercent(leadExpectation?.eps_growth)}</span>
                                <span>Revenue growth {formatPercent(leadExpectation?.revenue_growth)}</span>
                            </div>
                        </article>
                        <article className="surface-subtle rounded-xl border p-4">
                            <p className="text-[10px] font-black uppercase tracking-wide text-slate-500">Expectation signal</p>
                            <p className={`mt-2 flex items-center gap-1 text-sm font-black ${tone.className}`}><RevisionIcon size={16} />{tone.label}</p>
                            <p className="mt-1 text-xs text-slate-500">EPS trend vs 30 days ago {formatPercent(revision)}</p>
                            <p className="mt-2 text-xs text-slate-500">30d revisions ↑{leadExpectation?.eps_revisions_up_30d ?? "—"} / ↓{leadExpectation?.eps_revisions_down_30d ?? "—"}</p>
                            {data.wall_street_target_price != null && <p className="mt-2 flex items-center gap-1 text-xs text-slate-500"><Target size={13} /> Target {formatMoney(data.wall_street_target_price, currency)}</p>}
                        </article>
                    </div>

                    {detail && (
                        <div className="mt-5 space-y-5">
                            <div className="grid gap-5 xl:grid-cols-2">
                                <section>
                                    <div className="mb-3 flex items-center justify-between"><h4 className="text-sm font-black">Upcoming events</h4><span className="text-[10px] text-slate-500">{data.upcoming_events.length} published</span></div>
                                    {data.upcoming_events.length ? <ul className="space-y-2">{data.upcoming_events.map((event) => <EventRow key={event.id} event={event} currency={currency} />)}</ul> : <p className="rounded-xl border p-4 text-sm text-slate-500">No upcoming event date is available.</p>}
                                </section>
                                <section>
                                    <div className="mb-3 flex items-center justify-between"><h4 className="text-sm font-black">Recent earnings history</h4><span className="text-[10px] text-slate-500">EPS surprise</span></div>
                                    {data.recent_earnings.length ? <ul className="space-y-2">{data.recent_earnings.slice(0, 4).map((event) => <EventRow key={event.id} event={event} currency={currency} />)}</ul> : <p className="rounded-xl border p-4 text-sm text-slate-500">No reported earnings history is available.</p>}
                                </section>
                            </div>
                            <section>
                                <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-black">Forward consensus</h4><span className="text-[10px] text-slate-500">Point-in-time estimates, not a recommendation</span></div>
                                {data.expectations.length ? <ExpectationTable expectations={data.expectations} currency={currency} /> : <p className="rounded-xl border p-4 text-sm text-slate-500">No forward consensus estimates are available.</p>}
                            </section>
                            {(data.annual_dividend_per_share != null || data.dividend_yield != null || data.data_quality_notes.length > 0) && <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-500"><span>Annual dividend {formatNumber(data.annual_dividend_per_share)}</span><span>Dividend yield {formatPercent(data.dividend_yield)}</span>{data.data_quality_notes.map((note) => <span key={note}>{note}</span>)}</div>}
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
