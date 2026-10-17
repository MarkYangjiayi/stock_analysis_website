"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";
import { useRouter } from "next/navigation";

import type { ThemeListItem, ThemeView } from "@/lib/api";
import { chartMonoFont, chartTheme, divergingColor } from "@/lib/chartTheme";
import { formatSignedPercent } from "@/lib/format";
import { STAGE_LABELS, formatHeat } from "./themeFormat";

interface TreemapParams {
    data?: { slug?: string };
}

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);

const viewHeat = (theme: ThemeListItem, view: ThemeView) => (view === "now" ? theme.heat_now : theme.heat_trend);

/** Area = priced members, colour = heat on the diverging chartTheme scale; click opens the theme. */
export default function ThemeTreemap({ themes, view }: { themes: ThemeListItem[]; view: ThemeView }) {
    const router = useRouter();
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";
    const colors = chartTheme(dark);
    const sized = useMemo(() => themes.filter((theme) => theme.n_priced > 0), [themes]);

    const option = useMemo(() => {
        const palette = chartTheme(dark);
        const monoFont = chartMonoFont();
        const bySlug = new Map(sized.map((theme) => [theme.slug, theme]));
        const tooltipFormatter = (params: TreemapParams) => {
            const theme = params.data?.slug ? bySlug.get(params.data.slug) : undefined;
            if (!theme) return "";
            const row = (label: string, value: string) =>
                `<div style="display:flex;justify-content:space-between;gap:18px"><span>${label}</span><b>${value}</b></div>`;
            return `<div style="min-width:220px;color:${palette.text}">
                <div style="font-weight:600">${escapeHtml(theme.name_en)}</div>
                <div style="color:${palette.textMuted};margin-bottom:6px">${escapeHtml(theme.name_zh)}</div>
                ${row("Stage", STAGE_LABELS[theme.stage])}
                ${row(view === "now" ? "Heat 5D" : "Heat 20D", formatHeat(viewHeat(theme, view)))}
                ${row("Rank", String((view === "now" ? theme.rank_now : theme.rank_trend) ?? "—"))}
                ${row("Members priced", `${theme.n_priced} / ${theme.n_members}`)}
                ${row("5D / 21D vs SPY", `${formatSignedPercent(theme.excess_5d, 1)} / ${formatSignedPercent(theme.excess_21d, 1)}`)}
            </div>`;
        };
        return {
            animationDuration: 300,
            aria: {
                enabled: true,
                description: "Theme treemap. Tile area is the number of priced members; colour is theme heat from cold to hot.",
            },
            tooltip: {
                confine: true,
                backgroundColor: palette.tooltipBackground,
                borderColor: palette.border,
                textStyle: { color: palette.text, fontSize: 12, fontFamily: monoFont },
                formatter: tooltipFormatter,
            },
            series: [{
                type: "treemap",
                name: "Themes",
                roam: false,
                nodeClick: false,
                breadcrumb: { show: false },
                left: 0,
                right: 0,
                top: 0,
                bottom: 0,
                itemStyle: { borderColor: palette.background, borderWidth: 2, gapWidth: 2 },
                label: {
                    show: true,
                    color: palette.heatText,
                    fontSize: 12,
                    overflow: "truncate",
                    formatter: (params: TreemapParams & { name?: string }) => {
                        const theme = params.data?.slug ? bySlug.get(params.data.slug) : undefined;
                        return theme ? `${params.name ?? theme.name_en}\n${formatHeat(viewHeat(theme, view), 0)}` : params.name ?? "";
                    },
                },
                emphasis: { itemStyle: { borderColor: palette.brand } },
                data: sized.map((theme) => ({
                    name: theme.name_en,
                    value: theme.n_priced,
                    slug: theme.slug,
                    itemStyle: { color: divergingColor(viewHeat(theme, view), palette.heatDiverging) ?? palette.heatMissing },
                })),
            }],
        };
    }, [dark, sized, view]);

    const onEvents = useMemo(() => ({
        click: (params: TreemapParams) => {
            const slug = params.data?.slug;
            if (slug) router.push(`/themes/${encodeURIComponent(slug)}`);
        },
    }), [router]);

    if (sized.length === 0) {
        return <div className="flex h-[360px] items-center justify-center px-6 text-center text-sm text-fg-muted">No theme has priced members for this session.</div>;
    }

    return (
        <div>
            <ReactECharts option={option} onEvents={onEvents} notMerge lazyUpdate style={{ width: "100%", height: "520px" }} opts={{ renderer: "canvas" }} />
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t px-4 py-3 text-xs text-fg-muted">
                <span className="inline-flex items-center gap-2">
                    <span>Cold</span>
                    <span className="h-2 w-32 rounded-full" style={{ backgroundImage: `linear-gradient(90deg, ${colors.heatDiverging.join(", ")})` }} aria-hidden="true" />
                    <span>Hot</span>
                </span>
                <span className="inline-flex items-center gap-2">
                    <span className="h-2 w-4 rounded-sm border" style={{ background: colors.heatMissing }} aria-hidden="true" /> No heat
                </span>
                <span>Area is priced members · 50 is neutral heat · click a tile to open the theme</span>
            </div>
        </div>
    );
}
