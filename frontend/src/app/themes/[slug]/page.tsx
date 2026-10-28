"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2, RotateCcw, SearchX } from "lucide-react";

import ThemeAccessGate, { ThemePageHeader, type ThemeAccess } from "@/components/themes/ThemeAccessGate";
import ThemeBriefCard from "@/components/themes/ThemeBriefCard";
import ThemeDetailChart from "@/components/themes/ThemeDetailChart";
import ThemeMembersTable from "@/components/themes/ThemeMembersTable";
import { ThemeMetaChips, ThemeMetaNotices, useThemeStatusBar } from "@/components/themes/ThemeMeta";
import ThemeStageChip from "@/components/themes/ThemeStageChip";
import { STAGE_DESCRIPTIONS, displayTicker, formatHeat, formatPointChange, formatRankChange, formatShare, toneClass } from "@/components/themes/themeFormat";
import { SectionHeader } from "@/components/ui/Panel";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { ApiError, fetchThemeDetail, fetchThemeHistory, type ThemeDetailResponse, type ThemeHistory } from "@/lib/api";
import { formatSignedPercent } from "@/lib/format";

type HistoryRange = "120" | "252";

const HEADER = {
    eyebrow: "Theme detail",
    title: "Trending theme",
};

// Mirrors the API's slug path pattern; anything else would be a 422, so it is not requested.
const THEME_SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

const safeDecode = (value: string) => {
    try {
        return decodeURIComponent(value);
    } catch {
        return value;
    }
};

function BackLink() {
    return (
        <Link href="/themes" className="research-link w-fit">
            <ArrowLeft size={14} aria-hidden="true" /> All themes
        </Link>
    );
}

export default function ThemeDetailPage() {
    const params = useParams<{ slug: string }>();
    const slug = typeof params?.slug === "string" ? safeDecode(params.slug) : "";
    return (
        <div className="app-page">
            <div className="page-container max-w-[1500px]">
                <BackLink />
                <ThemeAccessGate header={HEADER}>
                    {(access) => (THEME_SLUG_PATTERN.test(slug)
                        ? <ThemeDetail key={slug} slug={slug} {...access} />
                        : <ThemeNotFound slug={slug} />)}
                </ThemeAccessGate>
            </div>
        </div>
    );
}

function ThemeNotFound({ slug }: { slug: string }) {
    return (
        <>
            <ThemePageHeader {...HEADER} />
            <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center">
                <SearchX className="text-fg-muted" size={30} aria-hidden="true" />
                <h2 className="mt-4 text-lg font-semibold">Theme not found</h2>
                <p className="mt-2 max-w-md text-sm text-fg-muted">No active theme uses the slug “<span className="break-all">{slug}</span>”. It may have been retired from the taxonomy.</p>
                <Link href="/themes" className="secondary-button mt-5">Back to all themes</Link>
            </section>
        </>
    );
}

function Kpi({ label, value, detail, valueClass = "" }: { label: string; value: string; detail?: ReactNode; valueClass?: string }) {
    return (
        <div className="surface-panel min-w-0 px-4 py-3">
            <p className="metric-label">{label}</p>
            <p className={`metric-value ${valueClass}`}>{value}</p>
            {/* Details wrap rather than truncate: they carry the secondary numbers (trend rank, 63D, 10D change). */}
            {detail && <p className="mt-0.5 font-mono text-xs leading-5 text-fg-muted">{detail}</p>}
        </div>
    );
}

function ThemeDetail({ slug, onUnauthorized }: ThemeAccess & { slug: string }) {
    const [data, setData] = useState<ThemeDetailResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [notFound, setNotFound] = useState(false);
    const [retryKey, setRetryKey] = useState(0);
    const [range, setRange] = useState<HistoryRange>("120");
    const [longHistory, setLongHistory] = useState<ThemeHistory | null>(null);
    const [historyError, setHistoryError] = useState("");

    const loadData = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError("");
        setNotFound(false);
        try {
            setData(await fetchThemeDetail(slug, null, signal));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401) {
                onUnauthorized();
                return;
            }
            setData(null);
            // 422 is the API rejecting the slug itself; its detail is a validation array, not a message.
            if (caught instanceof ApiError && (caught.status === 404 || caught.status === 422)) setNotFound(true);
            else setError(caught instanceof Error ? caught.message : "Unable to load this theme.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [onUnauthorized, slug]);

    useEffect(() => {
        const controller = new AbortController();
        void loadData(controller.signal);
        return () => controller.abort();
    }, [loadData, retryKey]);

    // The detail payload embeds 120 sessions; the 1Y range is fetched on demand.
    const loadLongHistory = useCallback(async (signal?: AbortSignal) => {
        setHistoryError("");
        try {
            const response = await fetchThemeHistory(slug, 252, signal);
            setLongHistory(response.history);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401) {
                onUnauthorized();
                return;
            }
            setHistoryError(caught instanceof Error ? caught.message : "Unable to load the 1Y history.");
        }
    }, [onUnauthorized, slug]);

    useEffect(() => {
        if (range !== "252" || longHistory) return;
        const controller = new AbortController();
        void loadLongHistory(controller.signal);
        return () => controller.abort();
    }, [loadLongHistory, longHistory, range]);

    useThemeStatusBar(data?.meta, Boolean(error));

    if (loading && !data) {
        return (
            <>
                <ThemePageHeader {...HEADER} description={slug} />
                <section className="surface-panel flex min-h-[420px] flex-col items-center justify-center">
                    <Loader2 className="animate-spin text-accent" size={34} />
                    <p className="mt-4 text-sm text-fg-muted">Loading theme heat, members and brief…</p>
                </section>
            </>
        );
    }

    if (notFound) return <ThemeNotFound slug={slug} />;

    if (!data) {
        return (
            <>
                <ThemePageHeader {...HEADER} />
                <div className="error-panel flex items-center justify-between gap-4" role="alert">
                    <span>{error || "This theme is unavailable."}</span>
                    <button type="button" className="secondary-button shrink-0" onClick={() => setRetryKey((value) => value + 1)}>
                        <RotateCcw size={15} /> Retry
                    </button>
                </div>
            </>
        );
    }

    const { theme, meta } = data;
    const history = range === "252" && longHistory ? longHistory : data.history;

    return (
        <>
            <ThemePageHeader
                eyebrow={theme.megatrend ? theme.megatrend.replace(/-/g, " ") : "Theme detail"}
                title={theme.name_en}
                aside={<ThemeMetaChips meta={meta} />}
            >
                <p className="mt-1 text-sm text-fg-muted" lang="zh-CN">{theme.name_zh}</p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    <ThemeStageChip stage={theme.stage} changed={theme.stage_changed} />
                    <span className="text-xs text-fg-muted">{STAGE_DESCRIPTIONS[theme.stage]}</span>
                </div>
                <p className="page-description">{data.definition}</p>
                {(data.keywords.length > 0 || data.proxy_etfs.length > 0) && (
                    <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                        {data.proxy_etfs.map((etf) => (
                            <span key={etf} className="rounded border bg-surface px-1.5 py-0.5 font-mono font-semibold text-fg" title="Proxy ETF">{displayTicker(etf)}</span>
                        ))}
                        {data.keywords.slice(0, 10).map((keyword) => (
                            <span key={keyword} className="rounded border border-dashed px-1.5 py-0.5 text-fg-muted">{keyword}</span>
                        ))}
                    </div>
                )}
            </ThemePageHeader>

            <ThemeMetaNotices meta={meta} />

            <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6" aria-label="Theme summary">
                <Kpi
                    label="Rank now"
                    value={theme.rank_now == null ? "—" : `#${theme.rank_now}`}
                    detail={<><span className={toneClass(theme.rank_change)}>{formatRankChange(theme.rank_change)}</span> · trend {theme.rank_trend == null ? "—" : `#${theme.rank_trend}`}</>}
                />
                <Kpi label="Heat now" value={formatHeat(theme.heat_now)} detail={`Trend ${formatHeat(theme.heat_trend)} · raw ${formatHeat(theme.heat)}`} />
                <Kpi
                    label="21D vs SPY"
                    value={formatSignedPercent(theme.excess_21d, 1)}
                    valueClass={toneClass(theme.excess_21d)}
                    detail={`5D ${formatSignedPercent(theme.excess_5d, 1)} · 63D ${formatSignedPercent(theme.excess_63d, 1)}`}
                />
                <Kpi
                    label="% > MA50"
                    value={formatShare(theme.pct_above_ma50)}
                    detail={`10D ${formatPointChange(theme.pct_above_ma50_change_10d)} · near high ${formatShare(theme.near_high_share)}`}
                />
                <Kpi label="Ignitions 5D" value={String(theme.ignitions_5d)} detail={`${theme.ignitions_today} today · top-5 streak ${theme.streak_top5}`} />
                <Kpi
                    label="Proxy ETF 21D"
                    value={formatSignedPercent(theme.proxy_excess_21d, 1)}
                    valueClass={toneClass(theme.proxy_excess_21d)}
                    detail={`${theme.proxy_etf ? displayTicker(theme.proxy_etf) : "No proxy"} · ${theme.n_priced}/${theme.n_members} members priced`}
                />
            </section>

            <section className="surface-panel min-w-0 overflow-hidden" aria-label="Theme history">
                <SectionHeader
                    eyebrow="Daily publications"
                    title="Heat, components and breadth"
                    description="Shaded bands mark the displayed stage. Basket, SPY and proxy are cumulative returns from the first session shown."
                    action={(
                        <SegmentedControl
                            label="History range"
                            value={range}
                            options={[{ value: "120", label: "120D" }, { value: "252", label: "1Y" }]}
                            onChange={setRange}
                        />
                    )}
                />
                {range === "252" && historyError && (
                    <p className="border-b px-4 py-2 text-sm text-caution" role="status">{historyError} Showing the last 120 sessions.</p>
                )}
                {range === "252" && !historyError && !longHistory && (
                    <p className="border-b px-4 py-2 text-sm text-fg-muted" role="status">Loading the 1Y history…</p>
                )}
                <ThemeDetailChart history={history} proxyEtf={theme.proxy_etf} />
            </section>

            <ThemeBriefCard brief={data.brief} />

            <section className="surface-panel min-w-0 overflow-hidden" aria-label="Theme members">
                <SectionHeader
                    eyebrow={`${data.members.length} members`}
                    title="Members"
                    description="Leaders rank by 21-day excess return, proximity to the 52-week high and news attention. Each membership quotes the company description that justifies it."
                />
                <ThemeMembersTable members={data.members} />
            </section>

            <footer className="grid gap-2 rounded-lg border px-4 py-3 text-xs text-fg-muted sm:grid-cols-3">
                <span><strong className="font-semibold text-fg">Scoring:</strong> {meta.scoring_version}</span>
                <span><strong className="font-semibold text-fg">Taxonomy:</strong> {meta.taxonomy_version ?? "—"}</span>
                <span>
                    <strong className="font-semibold text-fg">Price coverage:</strong> {meta.coverage_pct == null ? "—" : `${meta.coverage_pct.toFixed(1)}%`}
                    {meta.attention_degraded && " · attention degraded"}
                </span>
            </footer>
        </>
    );
}
