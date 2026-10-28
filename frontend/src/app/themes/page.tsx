"use client";

import { useCallback, useEffect, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { Building2, Flame, Layers3, Lightbulb, RotateCcw, TrendingUp } from "lucide-react";

import ThemeAccessGate, { ThemePageHeader, type ThemeAccess } from "@/components/themes/ThemeAccessGate";
import ThemeLeaderboard from "@/components/themes/ThemeLeaderboard";
import { ThemeMetaChips, ThemeMetaNotices, useThemeStatusBar } from "@/components/themes/ThemeMeta";
import ThemeProposalsPanel from "@/components/themes/ThemeProposalsPanel";
import ThemeRotation from "@/components/themes/ThemeRotation";
import ThemeTreemap from "@/components/themes/ThemeTreemap";
import TrendingCompaniesPanel from "@/components/themes/TrendingCompaniesPanel";
import { STAGE_DESCRIPTIONS, STAGE_DOTS, STAGE_FILTERS, STAGE_LABELS, formatRankChange, toneClass } from "@/components/themes/themeFormat";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { ApiError, fetchThemes, type ThemeListResponse, type ThemeStage, type ThemeView } from "@/lib/api";

type StageFilter = ThemeStage | "all";
type DisplayMode = "table" | "treemap" | "rotation";
type ThemesTab = "themes" | "companies" | "proposals";

const HEADER = {
    eyebrow: "Theme rotation",
    title: "Trending themes",
    description: "Rank curated investment themes by basket momentum, breadth, volume and news attention, and see which stage of the cycle each one is in.",
};

const TABS: Array<{ value: ThemesTab; label: string; icon: typeof Layers3 }> = [
    { value: "themes", label: "Themes", icon: Layers3 },
    { value: "companies", label: "Trending companies", icon: Building2 },
    { value: "proposals", label: "Proposals", icon: Lightbulb },
];

const KPI_STAGES = ["emerging", "accelerating", "leading", "crowded", "fading"] as const;

export default function ThemesPage() {
    return (
        <div className="app-page">
            <div className="page-container max-w-[1500px]">
                <ThemeAccessGate header={HEADER}>
                    {(access) => <ThemesDashboard {...access} />}
                </ThemeAccessGate>
            </div>
        </div>
    );
}

function ThemeTabs({ active, onChange }: { active: ThemesTab; onChange: (tab: ThemesTab) => void }) {
    const move = (event: KeyboardEvent<HTMLButtonElement>, direction: -1 | 1) => {
        event.preventDefault();
        const index = TABS.findIndex((tab) => tab.value === active);
        const next = TABS[(index + direction + TABS.length) % TABS.length];
        onChange(next.value);
        document.getElementById(`themes-tab-${next.value}`)?.focus();
    };
    return (
        <div className="scrollbar-hide flex overflow-x-auto border-b px-2 sm:px-3" role="tablist" aria-label="Theme views">
            {TABS.map((tab) => {
                const Icon = tab.icon;
                const selected = tab.value === active;
                return (
                    <button
                        key={tab.value}
                        id={`themes-tab-${tab.value}`}
                        type="button"
                        role="tab"
                        aria-selected={selected}
                        aria-controls="themes-tabpanel"
                        tabIndex={selected ? 0 : -1}
                        onClick={() => onChange(tab.value)}
                        onKeyDown={(event) => {
                            if (event.key === "ArrowLeft") move(event, -1);
                            if (event.key === "ArrowRight") move(event, 1);
                        }}
                        className="analysis-navigation-item"
                    >
                        <Icon size={16} aria-hidden="true" />
                        {tab.label}
                    </button>
                );
            })}
        </div>
    );
}

function ThemesDashboard({ onUnauthorized }: ThemeAccess) {
    const [view, setView] = useState<ThemeView>("now");
    const [stage, setStage] = useState<StageFilter>("all");
    const [display, setDisplay] = useState<DisplayMode>("table");
    const [tab, setTab] = useState<ThemesTab>("themes");
    const [data, setData] = useState<ThemeListResponse | null>(null);
    const [themeNames, setThemeNames] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [retryKey, setRetryKey] = useState(0);

    const loadData = useCallback(async (signal?: AbortSignal) => {
        // Keep the previous publication on screen while a filter change loads.
        setLoading(true);
        setError("");
        try {
            const response = await fetchThemes({ view, stage: stage === "all" ? null : stage }, signal);
            setData(response);
            setThemeNames((current) => ({
                ...current,
                ...Object.fromEntries(response.themes.map((theme) => [theme.slug, theme.name_en])),
            }));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401) {
                onUnauthorized();
                return;
            }
            setData(null);
            setError(caught instanceof Error ? caught.message : "Unable to load trending themes.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [onUnauthorized, stage, view]);

    useEffect(() => {
        const controller = new AbortController();
        void loadData(controller.signal);
        return () => controller.abort();
    }, [loadData, retryKey]);

    useThemeStatusBar(data?.meta, Boolean(error));

    const summary = data?.summary;
    const topRiser = summary?.top_riser ? data?.themes.find((theme) => theme.slug === summary.top_riser) : undefined;

    return (
        <>
            <ThemePageHeader {...HEADER} aside={data && <ThemeMetaChips meta={data.meta} />} />

            {data && <ThemeMetaNotices meta={data.meta} />}

            {error && (
                <div className="error-panel flex items-center justify-between gap-4" role="alert">
                    <span>{error}</span>
                    <button type="button" className="secondary-button shrink-0" onClick={() => setRetryKey((value) => value + 1)}>
                        <RotateCcw size={15} /> Retry
                    </button>
                </div>
            )}

            {summary && (
                <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6" aria-label="Theme stage summary">
                    {KPI_STAGES.map((key) => (
                        <div key={key} className="surface-panel px-4 py-3" title={STAGE_DESCRIPTIONS[key]}>
                            <p className="metric-label flex items-center gap-1.5">
                                <span className={`h-2 w-2 rounded-full ${STAGE_DOTS[key]}`} aria-hidden="true" />
                                {STAGE_LABELS[key]}
                            </p>
                            <p className="metric-value">{summary[key]}</p>
                        </div>
                    ))}
                    <div className="surface-panel min-w-0 px-4 py-3">
                        <p className="metric-label flex items-center gap-1.5"><TrendingUp size={13} className="text-accent" aria-hidden="true" /> Top riser</p>
                        {summary.top_riser ? (
                            <p className="mt-0.5 flex min-w-0 items-baseline gap-2">
                                <Link
                                    href={`/themes/${encodeURIComponent(summary.top_riser)}`}
                                    title={themeNames[summary.top_riser] ?? summary.top_riser}
                                    className="truncate text-sm font-semibold text-fg hover:text-accent-strong hover:underline"
                                >
                                    {themeNames[summary.top_riser] ?? summary.top_riser}
                                </Link>
                                {topRiser && <span className={`shrink-0 font-mono text-sm ${toneClass(topRiser.rank_change)}`}>{formatRankChange(topRiser.rank_change)}</span>}
                            </p>
                        ) : <p className="metric-value">—</p>}
                        <p className="mt-0.5 text-xs text-fg-muted">{summary.ranked} themes ranked</p>
                    </div>
                </section>
            )}

            <section className="surface-panel min-w-0 overflow-hidden" aria-label="Trending themes workspace">
                <ThemeTabs active={tab} onChange={setTab} />
                <div id="themes-tabpanel" role="tabpanel" aria-labelledby={`themes-tab-${tab}`}>
                    {tab === "themes" && (
                        <>
                            <div className="flex flex-col gap-3 border-b px-4 py-3 lg:flex-row lg:flex-wrap lg:items-center lg:justify-between">
                                <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
                                    <SegmentedControl
                                        label="Ranking window"
                                        value={view}
                                        options={[{ value: "now", label: "Now (5D)" }, { value: "trend", label: "Trend (20D)" }]}
                                        onChange={setView}
                                        className="self-start whitespace-nowrap sm:self-auto"
                                    />
                                    {/* Seven stages do not fit a phone row; the filter scrolls instead of wrapping labels. */}
                                    <div className="scrollbar-hide max-w-full overflow-x-auto">
                                        <SegmentedControl
                                            label="Stage filter"
                                            value={stage}
                                            options={[{ value: "all", label: "All stages" }, ...STAGE_FILTERS.map((value) => ({ value, label: STAGE_LABELS[value] }))]}
                                            onChange={setStage}
                                            className="whitespace-nowrap"
                                        />
                                    </div>
                                </div>
                                <SegmentedControl
                                    label="Display"
                                    value={display}
                                    options={[{ value: "table", label: "Table" }, { value: "treemap", label: "Treemap" }, { value: "rotation", label: "Rotation" }]}
                                    onChange={setDisplay}
                                    className="self-start whitespace-nowrap lg:self-auto"
                                />
                            </div>
                            {!data && error ? (
                                <div className="flex h-[280px] items-center justify-center px-6 text-center text-sm text-fg-muted">Theme rankings are unavailable.</div>
                            ) : display === "table" ? (
                                <ThemeLeaderboard themes={data?.themes ?? []} view={view} loading={loading} />
                            ) : !data ? (
                                <div className="flex h-[360px] items-center justify-center text-sm text-fg-muted">Loading theme heat…</div>
                            ) : display === "treemap" ? (
                                <ThemeTreemap themes={data.themes} view={view} />
                            ) : (
                                <ThemeRotation themes={data.themes} />
                            )}
                        </>
                    )}
                    {tab === "companies" && <TrendingCompaniesPanel onUnauthorized={onUnauthorized} themeNames={themeNames} />}
                    {tab === "proposals" && <ThemeProposalsPanel onUnauthorized={onUnauthorized} />}
                </div>
            </section>

            <footer className="grid gap-2 rounded-lg border px-4 py-3 text-xs leading-5 text-fg-muted lg:grid-cols-3">
                <span>
                    <strong className="font-semibold text-fg">Heat:</strong> 0–100 logistic of 40% price, 30% breadth, 15% volume and 15% news attention, each a robust z-score against all themes and industry baskets. 50 is neutral.
                </span>
                <span>
                    <strong className="font-semibold text-fg">Windows:</strong> Now smooths heat over about 5 sessions and Trend over about 20. Excess returns are measured against SPY.
                </span>
                <span>
                    <strong className="font-semibold text-fg">Publication:</strong>{" "}
                    {data ? (
                        <>
                            <Flame size={12} className="inline text-accent" aria-hidden="true" /> {data.meta.scoring_version} · taxonomy {data.meta.taxonomy_version ?? "—"} · price coverage{" "}
                            {data.meta.coverage_pct == null ? "—" : `${data.meta.coverage_pct.toFixed(1)}%`}
                        </>
                    ) : "—"}
                </span>
            </footer>
        </>
    );
}
