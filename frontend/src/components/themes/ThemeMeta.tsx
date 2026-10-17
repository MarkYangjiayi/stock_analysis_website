"use client";

import { useEffect } from "react";
import { CalendarClock, History, Newspaper, TriangleAlert } from "lucide-react";

import type { ThemeTrendsMeta } from "@/lib/api";
import { useStatusBar } from "@/store/useStatusBar";

const CAUTION_CHIP = "inline-flex items-center gap-1.5 rounded-md border border-caution/30 bg-caution/6 px-2 py-0.5 font-mono text-xs font-medium text-caution";

/** Header chips: publication date (caution when stale) and the backfill badge. */
export function ThemeMetaChips({ meta }: { meta: ThemeTrendsMeta }) {
    return (
        <>
            <span className={meta.stale ? CAUTION_CHIP : "status-pill"}>
                <CalendarClock size={13} aria-hidden="true" /> Data through {meta.as_of_date}
            </span>
            {!meta.point_in_time && (
                <span
                    className={CAUTION_CHIP}
                    title="Rows for this date were computed with today's theme membership, so they are not a point-in-time record."
                >
                    <History size={13} aria-hidden="true" /> Backfilled · not point-in-time
                </span>
            )}
            <span className="inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-xs font-medium text-fg-muted">
                {meta.scoring_version}
            </span>
        </>
    );
}

/** Stale, degraded-attention and quality banners for a theme publication. */
// The pipeline's own attention warning repeats the dedicated degraded-attention notice.
const ATTENTION_WARNING = /^Attention degraded\b/i;

export function ThemeMetaNotices({ meta }: { meta: ThemeTrendsMeta }) {
    const warnings = meta.attention_degraded
        ? meta.warnings.filter((warning) => !ATTENTION_WARNING.test(warning))
        : meta.warnings;
    return (
        <>
            {meta.stale && (
                <div className="rounded-lg border border-caution/30 bg-caution/6 px-4 py-3 text-sm text-caution" role="status">
                    <TriangleAlert className="mr-2 inline" size={16} aria-hidden="true" />
                    The latest theme publication is from {meta.as_of_date}; {meta.expected_as_of_date} was expected. Showing the last complete publication.
                </div>
            )}
            {meta.attention_degraded && (
                <div className="rounded-lg border border-caution/20 bg-caution/5 px-4 py-3 text-sm text-caution" role="status">
                    <Newspaper className="mr-2 inline" size={16} aria-hidden="true" />
                    News attention is incomplete for this session, so heat uses price, breadth and volume only and the attention score shows “—”.
                </div>
            )}
            {warnings.length > 0 && (
                <div className="rounded-lg border border-caution/20 bg-caution/5 px-4 py-3 text-sm text-caution" role="status">
                    <strong>Quality note:</strong> {warnings.join(" · ")}
                </div>
            )}
        </>
    );
}

/** Publishes the theme data date to the global status bar and clears it on unmount. */
export function useThemeStatusBar(meta: ThemeTrendsMeta | null | undefined, failed = false) {
    const setStatus = useStatusBar((state) => state.setStatus);
    const clearStatus = useStatusBar((state) => state.clearStatus);
    const asOf = meta?.as_of_date;
    const stale = meta?.stale ?? false;
    const version = meta?.scoring_version;

    useEffect(() => {
        if (asOf) {
            setStatus([{ label: "Themes", value: asOf, tone: stale ? "stale" : "ready" }], `scoring ${version}`);
        } else if (failed) {
            setStatus([{ label: "Themes", value: "Unavailable", tone: "error" }]);
        }
    }, [asOf, failed, setStatus, stale, version]);
    useEffect(() => () => clearStatus(), [clearStatus]);
}
