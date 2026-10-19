"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, CalendarDays, RefreshCw } from "lucide-react";
import {
    ApiError,
    fetchToday,
    type ReportTagColor,
    type TodayCluster,
    type TodayEvent,
    type TodayMover,
    type TodayRegimeItem,
    type TodayResponse,
    type WatchlistQuoteItem,
} from "@/lib/api";
import { formatSignedPercent } from "@/lib/format";
import { DataState } from "@/components/ui/DataState";
import { Panel, SectionHeader } from "@/components/ui/Panel";
import { Sparkline } from "@/components/ui/Sparkline";

interface TodayDashboardProps {
    adminKey: string;
    watchlist: string[];
    quotes: Record<string, WatchlistQuoteItem>;
    onSelectTicker: (ticker: string) => void;
    onUnauthorized: () => void;
}

const REPORT_LABELS: Record<string, string> = { morning_briefing: "Morning report", post_market_summary: "Post-market report" };

// Report tones are Feishu colours; warnings use the caution (violet) token so they never read as the amber accent.
const toneClass = (tone: string) =>
    tone === "green" ? "text-up" : tone === "red" ? "text-down" : tone === "orange" ? "text-caution" : "text-fg-muted";

const tagClass = (color: ReportTagColor) => {
    if (color === "green" || color === "turquoise") return "border-up/40 text-up";
    if (color === "red" || color === "carmine") return "border-down/40 text-down";
    if (color === "purple") return "border-caution/40 text-caution";
    if (color === "blue") return "border-info/40 text-info";
    if (color === "orange") return "border-accent/40 text-accent";
    return "border-line text-fg-muted";
};

const changeClass = (value?: number | null) =>
    value == null || value === 0 ? "text-flat" : value > 0 ? "text-up" : "text-down";

const symbol = (ticker: string) => ticker.replace(/\.US$/, "");

const newYorkTime = (iso?: string | null) => {
    if (!iso) return "—";
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return iso;
    return `${new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(parsed).replace(",", "")} ET`;
};

const dayLabel = (isoDate: string) => {
    const parsed = new Date(`${isoDate}T12:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return isoDate;
    return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "2-digit", day: "2-digit" }).format(parsed);
};

const sessionLabel = (session?: string | null) => {
    const value = (session || "").toLowerCase();
    if (value.startsWith("before")) return "before open";
    if (value.startsWith("after")) return "after close";
    return session || "time n/a";
};

function RegimePanel({ report }: { report: NonNullable<TodayResponse["report"]> | null }) {
    const regime = report?.regime;
    return (
        <Panel aria-label="Market regime">
            <SectionHeader
                eyebrow="Market regime"
                title="市场状态"
                description="Rule-based states from the latest daily report, the same line the Feishu push shows."
                action={report?.regime_captured_at ? <DataState label={`Captured ${newYorkTime(report.regime_captured_at)}`} tone="ready" /> : undefined}
            />
            {!regime?.items.length ? (
                <p className="px-4 pb-4 text-sm text-fg-muted">No report with a market regime has been collected yet.</p>
            ) : (
                <>
                    <dl className="grid grid-cols-2 gap-px border-t bg-line sm:grid-cols-4 xl:grid-cols-7">
                        {regime.items.map((item: TodayRegimeItem) => (
                            <div key={item.key} className="bg-surface p-3">
                                <dt className="text-xs text-fg-muted">{item.label}</dt>
                                <dd className={`mt-1 text-lg font-semibold ${toneClass(item.tone)}`}>{item.state}</dd>
                                <dd className="mt-1 text-xs leading-5 text-fg-muted">{item.detail}</dd>
                                {item.as_of && <dd className="mt-1 font-mono text-[11px] text-fg-muted">{item.as_of}</dd>}
                            </div>
                        ))}
                    </dl>
                    {regime.unavailable.length > 0 && (
                        <p className="border-t px-4 py-2 text-xs text-caution">Unavailable: {regime.unavailable.join("、")}</p>
                    )}
                </>
            )}
        </Panel>
    );
}

function WatchlistPanel({ data, watchlist, quotes, earnings, onSelectTicker }: {
    data: TodayResponse["watchlist"];
    watchlist: string[];
    quotes: Record<string, WatchlistQuoteItem>;
    earnings: Map<string, TodayEvent>;
    onSelectTicker: (ticker: string) => void;
}) {
    const tags = new Map((data?.items || []).map((item) => [item.ticker, item]));
    return (
        <Panel aria-label="Watchlist signals">
            <SectionHeader
                eyebrow="Watchlist"
                title="自选股"
                description="Delayed quotes with the report's RSI, 52-week, estimate-revision and short-interest tags."
                action={data?.target ? <DataState label={`Tags as of ${data.target}`} tone="ready" /> : undefined}
            />
            {watchlist.length === 0 ? (
                <p className="px-4 pb-4 text-sm text-fg-muted">The watchlist is empty.</p>
            ) : (
                <div className="overflow-x-auto border-t">
                    <table className="w-full min-w-[640px] text-sm" aria-label="Watchlist signals">
                        <thead className="text-left text-xs text-fg-muted">
                            <tr>
                                <th className="px-4 py-2 font-medium">Ticker</th>
                                <th className="px-2 py-2 text-right font-medium">Price</th>
                                <th className="px-2 py-2 text-right font-medium">Change</th>
                                <th className="w-24 px-2 py-2 font-medium">30d</th>
                                <th className="px-2 py-2 font-medium">Signals</th>
                                <th className="px-4 py-2 font-medium">Earnings</th>
                            </tr>
                        </thead>
                        <tbody>
                            {watchlist.map((ticker) => {
                                const quote = quotes[ticker]?.quote;
                                const row = tags.get(ticker);
                                const upcoming = earnings.get(ticker);
                                return (
                                    <tr key={ticker} className="border-t hover:bg-surface-muted">
                                        <td className="px-4 py-2">
                                            <button type="button" className="font-mono font-semibold hover:text-accent" onClick={() => onSelectTicker(ticker)}>{symbol(ticker)}</button>
                                        </td>
                                        <td className="num px-2 py-2 text-right">{quote ? quote.price.toFixed(2) : "—"}</td>
                                        <td className={`num px-2 py-2 text-right ${changeClass(quote?.change)}`}>{formatSignedPercent(quote?.change_pct)}</td>
                                        <td className={`px-2 py-2 ${changeClass(quote?.change)}`}>
                                            <Sparkline values={(quotes[ticker]?.sparkline || []).map((point) => point.close)} />
                                        </td>
                                        <td className="px-2 py-2" title={row?.as_of || undefined}>
                                            <div className="flex flex-wrap gap-1">
                                                {(row?.tags || []).map((tag) => (
                                                    <span key={tag.text} className={`rounded border px-1.5 py-0.5 text-xs ${tagClass(tag.color)}`}>{tag.text}</span>
                                                ))}
                                            </div>
                                        </td>
                                        <td className="px-4 py-2 text-xs">
                                            {upcoming ? <span className="text-accent">{dayLabel(upcoming.date)} · {sessionLabel(upcoming.session)}</span> : <span className="text-fg-muted">—</span>}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </Panel>
    );
}

function EventsPanel({ events, watchlist }: { events: TodayResponse["events"]; watchlist: Set<string> }) {
    const days = useMemo(() => {
        const grouped = new Map<string, TodayEvent[]>();
        for (const item of events?.items || []) {
            const day = item.date.slice(0, 10);
            grouped.set(day, [...(grouped.get(day) || []), item]);
        }
        return [...grouped.entries()];
    }, [events]);
    return (
        <Panel aria-label="Next seven days">
            <SectionHeader
                eyebrow={`Next ${events?.window_days ?? 7} days`}
                title="财报与宏观"
                description="US macro releases and earnings for the core assets and watchlist."
            />
            {!events ? (
                <p className="px-4 pb-4 text-sm text-caution">The calendar is unavailable right now.</p>
            ) : days.length === 0 ? (
                <p className="px-4 pb-4 text-sm text-fg-muted">No major US releases or watchlist earnings in this window.</p>
            ) : (
                <ol className="border-t">
                    {days.map(([day, items]) => (
                        <li key={day} className="border-b px-4 py-2 last:border-b-0">
                            <p className="flex items-center gap-1.5 text-xs font-semibold text-fg-muted"><CalendarDays size={13} />{dayLabel(day)}</p>
                            <ul className="mt-1 space-y-1 text-sm">
                                {items.map((item, index) => (
                                    <li key={`${item.kind}-${item.name}-${index}`} className="flex flex-wrap items-baseline gap-x-2">
                                        {item.kind === "earnings" ? (
                                            <>
                                                <span className={`font-mono font-semibold ${watchlist.has(item.name) ? "text-accent" : ""}`}>{symbol(item.name)}</span>
                                                <span className="text-fg-muted">earnings · {sessionLabel(item.session)}</span>
                                            </>
                                        ) : (
                                            <>
                                                <span>{item.name}</span>
                                                <span className="text-xs text-fg-muted">
                                                    {[item.date.length > 10 ? item.date.slice(11, 16) : null, item.period,
                                                        item.estimate != null ? `est. ${item.estimate}` : null].filter(Boolean).join(" · ")}
                                                </span>
                                            </>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        </li>
                    ))}
                </ol>
            )}
            {(events && (!events.economic_available || !events.earnings_available)) && (
                <p className="border-t px-4 py-2 text-xs text-caution">
                    {!events.economic_available ? "Macro calendar unavailable. " : ""}{!events.earnings_available ? "Earnings calendar unavailable." : ""}
                </p>
            )}
            {events?.timezone_note && days.length > 0 && <p className="border-t px-4 py-2 text-xs text-fg-muted">{events.timezone_note}</p>}
        </Panel>
    );
}

function MoversPanel({ report, onSelectTicker }: { report: NonNullable<TodayResponse["report"]> | null; onSelectTicker: (ticker: string) => void }) {
    const clusters: TodayCluster[] = report?.anomaly_clusters?.clusters || [];
    const grouped = new Set(clusters.flatMap((cluster) => cluster.tickers));
    const others: TodayMover[] = (report?.movers || []).filter((mover) => !grouped.has(mover.ticker));
    return (
        <Panel aria-label="Movers by theme">
            <SectionHeader
                eyebrow="Movers"
                title="异动"
                description="Large moves from the latest report; three or more same-direction movers in one theme or industry are grouped."
                action={<Link href="/anomalies" className="research-link inline-flex items-center gap-1 text-xs">All movers <ArrowUpRight size={13} /></Link>}
            />
            {!report ? (
                <p className="px-4 pb-4 text-sm text-fg-muted">No daily report has been collected yet.</p>
            ) : report.movers.length === 0 ? (
                <p className="px-4 pb-4 text-sm text-fg-muted">The latest report had no large movers.</p>
            ) : (
                <div className="border-t">
                    {clusters.map((cluster) => (
                        <div key={`${cluster.kind}-${cluster.name}-${cluster.direction}`} className="border-b px-4 py-3">
                            <p className="text-sm">
                                <span className="font-semibold">{cluster.name}</span>
                                {cluster.kind === "industry" && <span className="ml-1.5 text-xs text-fg-muted">industry</span>}
                                <span className="ml-2 text-fg-muted">{cluster.tickers.length} {cluster.direction === "down" ? "down" : "up"} together · avg </span>
                                <span className={`num ${changeClass(cluster.average_move)}`}>{formatSignedPercent(cluster.average_move / 100, 1)}</span>
                            </p>
                            <div className="mt-1.5 flex flex-wrap gap-1">
                                {cluster.tickers.map((ticker) => (
                                    <button key={ticker} type="button" className="rounded border px-1.5 py-0.5 font-mono text-xs hover:border-accent hover:text-accent" onClick={() => onSelectTicker(ticker)}>{symbol(ticker)}</button>
                                ))}
                            </div>
                        </div>
                    ))}
                    <ul>
                        {others.map((mover) => (
                            <li key={mover.ticker} className="flex flex-wrap items-baseline gap-x-2 border-b px-4 py-2 text-sm last:border-b-0">
                                <button type="button" className="font-mono font-semibold hover:text-accent" onClick={() => onSelectTicker(mover.ticker)}>{symbol(mover.ticker)}</button>
                                <span className={`num ${changeClass(mover.price_change)}`}>{mover.price_change == null ? "—" : formatSignedPercent(mover.price_change / 100)}</span>
                                <span className="min-w-0 truncate text-fg-muted">{mover.company_name}</span>
                                {mover.news_title && (
                                    <a className="w-full truncate text-xs text-fg-muted hover:text-fg" href={mover.news_link || undefined} target="_blank" rel="noreferrer" title="Headline lead, not a verified cause">
                                        {mover.news_title}
                                    </a>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </Panel>
    );
}

/** Owner-only home view: the latest report's regime and movers plus live watchlist context. */
export default function TodayDashboard({ adminKey, watchlist, quotes, onSelectTicker, onUnauthorized }: TodayDashboardProps) {
    const [version, setVersion] = useState(0);
    // The settled result remembers which request produced it, so loading is derived, not set in the effect.
    const requestKey = `${adminKey}|${watchlist.join(",")}|${version}`;
    const [result, setResult] = useState<{ key: string; data: TodayResponse | null; error: string }>({ key: "", data: null, error: "" });

    useEffect(() => {
        const controller = new AbortController();
        fetchToday(adminKey, controller.signal)
            .then((response) => setResult({ key: requestKey, data: response, error: "" }))
            .catch((caught: unknown) => {
                if (caught instanceof DOMException && caught.name === "AbortError") return;
                if (caught instanceof ApiError && caught.status === 401) {
                    onUnauthorized();
                    return;
                }
                const message = caught instanceof Error ? caught.message : "Unable to load Today.";
                setResult((previous) => ({ key: requestKey, data: previous.data, error: message }));
            });
        return () => controller.abort();
    }, [adminKey, onUnauthorized, requestKey]);

    const loading = result.key !== requestKey;
    const data = result.data;
    const error = loading ? "" : result.error;
    const refresh = useCallback(() => setVersion((value) => value + 1), []);
    const earnings = useMemo(() => new Map(
        (data?.events?.items || []).filter((item) => item.kind === "earnings").map((item) => [item.name, item] as const),
    ), [data]);
    const report = data?.report ?? null;

    if (loading && !data) {
        return (
            <div className="grid gap-5" aria-label="Loading Today">
                <div className="surface-panel h-40 animate-pulse bg-surface-muted" />
                <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
                    <div className="surface-panel h-96 animate-pulse bg-surface-muted" />
                    <div className="surface-panel h-96 animate-pulse bg-surface-muted" />
                </div>
            </div>
        );
    }
    if (error && !data) {
        return (
            <div className="error-panel flex min-h-[240px] flex-col items-center justify-center text-center" role="alert">
                <h2 className="text-lg font-semibold">Today is unavailable</h2>
                <p className="mt-2 max-w-lg">{error}</p>
                <button type="button" className="secondary-button mt-5" onClick={refresh}>Try again</button>
            </div>
        );
    }

    return (
        <section className="grid grid-cols-[minmax(0,1fr)] gap-5" aria-label="Today">
            <header className="flex flex-col justify-between gap-3 border-b pb-4 sm:flex-row sm:items-end">
                <div>
                    <p className="eyebrow">Today</p>
                    <h1 className="page-title mt-1">Market and watchlist</h1>
                    <p className="page-description">
                        {report
                            ? <>From the {REPORT_LABELS[report.report_type]?.toLowerCase() || "daily report"} captured {newYorkTime(report.captured_at)}{report.delivered ? "" : " (not delivered to Feishu)"}.</>
                            : "No daily report has been collected yet."}
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {data?.unavailable.length ? <DataState label={`Unavailable: ${data.unavailable.join("、")}`} tone="error" /> : null}
                    <button type="button" className="secondary-button inline-flex items-center gap-1.5" onClick={refresh} disabled={loading}>
                        <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
                    </button>
                </div>
            </header>
            {error && <div className="error-panel" role="alert">{error} The previous snapshot remains visible.</div>}
            <RegimePanel report={report} />
            {/* minmax(0, …) keeps the wide watchlist table scrolling inside its panel on phones. */}
            <div className="grid grid-cols-[minmax(0,1fr)] gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
                <WatchlistPanel data={data?.watchlist ?? null} watchlist={watchlist} quotes={quotes} earnings={earnings} onSelectTicker={onSelectTicker} />
                <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-5">
                    <MoversPanel report={report} onSelectTicker={onSelectTicker} />
                    <EventsPanel events={data?.events ?? null} watchlist={new Set(watchlist)} />
                </div>
            </div>
            <footer className="px-1 text-xs text-fg-muted">
                States follow fixed rules; headlines are leads, not verified causes; the high-yield spread comes from FRED (ICE BofA). Not investment advice.
            </footer>
        </section>
    );
}
