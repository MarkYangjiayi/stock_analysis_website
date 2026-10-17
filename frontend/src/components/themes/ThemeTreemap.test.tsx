import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { chartTheme, divergingColor } from "@/lib/chartTheme";
import { makeInsufficientTheme, makeThemeList, makeThemeListItem } from "@/test/themesFixture";

const push = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
    useRouter: () => ({ push, replace: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next-themes", () => ({
    useTheme: () => ({ resolvedTheme: "light" }),
}));

interface TreemapDatum {
    name: string;
    value: number;
    slug: string;
    itemStyle: { color: string };
}

vi.mock("echarts-for-react", () => ({
    default: ({ option, onEvents }: {
        option: {
            series: Array<{ type: string; data: TreemapDatum[] }>;
            tooltip: { formatter: (params: { data?: { slug?: string } }) => string };
        };
        onEvents: { click: (params: { data?: { slug?: string } }) => void };
    }) => {
        const data = option.series[0].data;
        return (
            <div
                data-testid="echarts"
                data-type={option.series[0].type}
                data-tiles={JSON.stringify(data.map((item) => [item.slug, item.value, item.itemStyle.color]))}
                data-tooltip={option.tooltip.formatter({ data: { slug: data[0]?.slug } })}
            >
                {data.map((item) => (
                    <button key={item.slug} type="button" onClick={() => onEvents.click({ data: { slug: item.slug } })}>
                        {item.name}
                    </button>
                ))}
            </div>
        );
    },
}));

import ThemeTreemap from "./ThemeTreemap";

const tiles = () => JSON.parse(screen.getByTestId("echarts").getAttribute("data-tiles") ?? "[]") as Array<[string, number, string]>;

describe("divergingColor", () => {
    const stops = ["#000000", "#808080", "#ffffff"];

    it("interpolates evenly spaced stops and clamps the range", () => {
        expect(divergingColor(0, stops)).toBe("#000000");
        expect(divergingColor(25, stops)).toBe("#404040");
        expect(divergingColor(50, stops)).toBe("#808080");
        expect(divergingColor(100, stops)).toBe("#ffffff");
        expect(divergingColor(140, stops)).toBe("#ffffff");
        expect(divergingColor(-5, stops)).toBe("#000000");
    });

    it("returns null for missing values instead of a fake midpoint", () => {
        expect(divergingColor(null, stops)).toBeNull();
        expect(divergingColor(Number.NaN, stops)).toBeNull();
        expect(divergingColor(50, [])).toBeNull();
    });

    it("keeps the heat scale off the up and down colours", () => {
        for (const dark of [false, true]) {
            const colors = chartTheme(dark);
            expect(colors.heatDiverging).toHaveLength(3);
            expect(colors.heatDiverging).not.toContain(colors.positive);
            expect(colors.heatDiverging).not.toContain(colors.negative);
        }
    });
});

describe("ThemeTreemap", () => {
    it("sizes tiles by priced members and colours them by the selected heat window", () => {
        const colors = chartTheme(false);
        const themes = [
            makeThemeListItem({ slug: "hot", name_en: "Hot", heat_now: 100, heat_trend: 0, n_priced: 12 }),
            makeThemeListItem({ slug: "neutral", name_en: "Neutral", heat_now: 50, heat_trend: 50, n_priced: 6 }),
            makeInsufficientTheme({ slug: "no-heat", name_en: "No heat", n_priced: 2 }),
            makeThemeListItem({ slug: "unpriced", name_en: "Unpriced", n_priced: 0 }),
        ];
        const { rerender } = render(<ThemeTreemap themes={themes} view="now" />);

        expect(screen.getByTestId("echarts")).toHaveAttribute("data-type", "treemap");
        expect(tiles()).toEqual([
            ["hot", 12, colors.heatDiverging[2]],
            ["neutral", 6, colors.heatDiverging[1]],
            ["no-heat", 2, colors.heatMissing],
        ]);

        rerender(<ThemeTreemap themes={themes} view="trend" />);
        expect(tiles()[0]).toEqual(["hot", 12, colors.heatDiverging[0]]);
    });

    it("escapes theme names in the tooltip and opens a theme on click", () => {
        render(<ThemeTreemap themes={[makeThemeListItem({ name_en: "<img src=x onerror=alert(1)>", excess_5d: null })]} view="now" />);

        const tooltip = screen.getByTestId("echarts").getAttribute("data-tooltip") ?? "";
        expect(tooltip).not.toContain("<img");
        expect(tooltip).toContain("&#60;img");
        expect(tooltip).toContain("Members priced");
        expect(tooltip).toContain("— / +8.5%");

        fireEvent.click(screen.getByRole("button"));
        expect(push).toHaveBeenCalledWith("/themes/ai-compute");
    });

    it("explains an empty map instead of drawing nothing", () => {
        render(<ThemeTreemap themes={makeThemeList().map((theme) => ({ ...theme, n_priced: 0 }))} view="now" />);

        expect(screen.queryByTestId("echarts")).not.toBeInTheDocument();
        expect(screen.getByText("No theme has priced members for this session.")).toBeInTheDocument();
    });
});
