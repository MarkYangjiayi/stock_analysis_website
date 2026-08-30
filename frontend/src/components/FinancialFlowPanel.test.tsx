import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";

import FinancialFlowPanel from "@/components/FinancialFlowPanel";
import type { FinancialFlowResponse } from "@/lib/api";

const { echartsMock } = vi.hoisted(() => ({ echartsMock: vi.fn() }));
vi.mock("echarts-for-react", () => ({
    default: (props: unknown) => {
        echartsMock(props);
        return <div data-testid="financial-flow-chart" />;
    },
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));

const data = (overrides: Partial<FinancialFlowResponse> = {}): FinancialFlowResponse => ({
    ticker: "AMZN.US",
    currency: "USD",
    period_type: "quarterly",
    period_end: "2026-06-30",
    available_periods: ["2026-06-30", "2026-03-31"],
    status: "ready",
    coverage_level: "full",
    unsupported_reason: null,
    chart_available: true,
    summary_cards: [
        { key: "revenue", label: "Total revenue", value: 200_606_000_000, yoy_change: 0.2, margin: 1, note: "20.0% increase year over year" },
        { key: "operating_income", label: "Operating income", value: 27_461_000_000, yoy_change: 0.43, margin: 0.137, note: null },
        { key: "net_income", label: "Net income", value: 62_647_000_000, yoy_change: 2.4, margin: 0.312, note: "Investment gains were material." },
    ],
    nodes: [
        { id: "revenue", label: "Total revenue", value: 200_606_000_000, kind: "income", source_id: "SEC:1", evidence_type: "fact_source_reported", confidence: "high", original_label: "Net sales" },
        { id: "gross_profit", label: "Gross profit", value: 104_828_000_000, kind: "profit", source_id: "EODHD:1", evidence_type: "derived_calculation", confidence: "medium", original_label: "Gross profit" },
    ],
    links: [{ source: "revenue", target: "gross_profit", value: 104_828_000_000, kind: "profit" }],
    insights: [{ code: "material_non_operating", severity: "warning", message: "Non-operating items were material." }],
    validation: { reconciled: true, warnings: [] },
    sources: [{ source_id: "SEC:1", document_type: "10-Q", filing_date: "2026-07-31", url: "https://www.sec.gov/example" }],
    enrichment: { status: "completed", run_id: 7, last_error: null, updated_at: "2026-08-30T10:00:00" },
    ...overrides,
});

const renderPanel = (props: Partial<ComponentProps<typeof FinancialFlowPanel>> = {}) => {
    const defaults: ComponentProps<typeof FinancialFlowPanel> = {
        data: data(),
        loading: false,
        error: "",
        timePeriod: "quarterly",
        onTimePeriodChange: vi.fn(),
        onPeriodEndChange: vi.fn(),
        onRetry: vi.fn(),
    };
    return render(<FinancialFlowPanel {...defaults} {...props} />);
};

describe("FinancialFlowPanel", () => {
    it("renders summary cards, auditable detail, sources and Sankey chart", () => {
        renderPanel();

        expect(screen.getByText("Profit flow")).toBeInTheDocument();
        expect(screen.getAllByText("$200.6B")).toHaveLength(2);
        expect(screen.getByText("13.7% margin")).toBeInTheDocument();
        expect(screen.getByText("Official detail")).toBeInTheDocument();
        expect(screen.getByTestId("financial-flow-chart")).toBeInTheDocument();
        expect(screen.getByText("derived calculation")).toBeInTheDocument();
        expect(screen.getByText("Non-operating items were material.")).toBeInTheDocument();
    });

    it("pins financial stages to adjacent columns and colors expense flows separately", () => {
        const makeNode = (
            id: string,
            label: string,
            value: number,
            kind: "income" | "profit" | "expense",
        ): FinancialFlowResponse["nodes"][number] => ({
            id,
            label,
            value,
            kind,
            source_id: "EODHD:1",
            evidence_type: "fact_provider_standardized",
            confidence: "medium",
            original_label: label,
        });
        renderPanel({
            data: data({
                coverage_level: "consolidated",
                nodes: [
                    makeNode("revenue", "Total revenue", 200, "income"),
                    makeNode("cost_of_revenue", "Cost of revenue", 95, "expense"),
                    makeNode("gross_profit", "Gross profit", 105, "profit"),
                    makeNode("operating_expenses", "Operating expenses", 77, "expense"),
                    makeNode("operating_income", "Operating income", 28, "profit"),
                    makeNode("non_operating_income", "Net non-operating income", 53, "income"),
                    makeNode("pretax_income", "Income before taxes", 81, "profit"),
                    makeNode("income_tax", "Income taxes", 18, "expense"),
                    makeNode("net_income", "Net income", 63, "profit"),
                ],
                links: [
                    { source: "revenue", target: "cost_of_revenue", value: 95, kind: "expense" },
                    { source: "revenue", target: "gross_profit", value: 105, kind: "profit" },
                    { source: "gross_profit", target: "operating_expenses", value: 77, kind: "expense" },
                    { source: "gross_profit", target: "operating_income", value: 28, kind: "profit" },
                    { source: "operating_income", target: "pretax_income", value: 28, kind: "profit" },
                    { source: "non_operating_income", target: "pretax_income", value: 53, kind: "income" },
                    { source: "pretax_income", target: "income_tax", value: 18, kind: "expense" },
                    { source: "pretax_income", target: "net_income", value: 63, kind: "profit" },
                ],
            }),
        });

        const props = echartsMock.mock.calls.at(-1)?.[0] as {
            option: { series: Array<{ data: Array<{ name: string; depth: number }>; links: Array<{ kind: string; lineStyle: { color: string } }> }> };
        };
        const series = props.option.series[0];
        const depths = new Map(series.data.map((node) => [node.name, node.depth]));
        expect(depths.get("revenue")).toBe(0);
        expect(depths.get("cost_of_revenue")).toBe(1);
        expect(depths.get("gross_profit")).toBe(1);
        expect(depths.get("operating_expenses")).toBe(2);
        expect(depths.get("non_operating_income")).toBe(2);
        expect(depths.get("pretax_income")).toBe(3);
        expect(depths.get("income_tax")).toBe(4);
        expect(depths.get("net_income")).toBe(4);

        const expenseColor = series.links.find((link) => link.kind === "expense")?.lineStyle.color;
        const profitColor = series.links.find((link) => link.kind === "profit")?.lineStyle.color;
        expect(expenseColor).not.toBe(profitColor);
    });

    it("adds a leading column only when reported revenue segments are present", () => {
        const segmented = data({
            nodes: [
                ...data().nodes,
                { id: "revenue_segment_0", label: "AWS", value: 42_000_000_000, kind: "income", source_id: "SEC:1", evidence_type: "fact_source_reported", confidence: "high", original_label: "AWS" },
            ],
            links: [
                { source: "revenue_segment_0", target: "revenue", value: 42_000_000_000, kind: "income" },
                ...data().links,
            ],
        });
        renderPanel({ data: segmented });

        const props = echartsMock.mock.calls.at(-1)?.[0] as {
            option: { series: Array<{ data: Array<{ name: string; depth: number; label: { position: string } }> }> };
        };
        const nodes = new Map(props.option.series[0].data.map((node) => [node.name, node]));
        expect(nodes.get("revenue_segment_0")?.depth).toBe(0);
        expect(nodes.get("revenue_segment_0")?.label.position).toBe("left");
        expect(nodes.get("revenue")?.depth).toBe(1);
        expect(nodes.get("gross_profit")?.depth).toBe(2);
    });

    it("changes only the selected profit-flow history period", async () => {
        const user = userEvent.setup();
        const onPeriodEndChange = vi.fn();
        renderPanel({ onPeriodEndChange });

        await user.selectOptions(screen.getByLabelText("Profit flow reporting period"), "2026-03-31");
        expect(onPeriodEndChange).toHaveBeenCalledWith("2026-03-31");
    });

    it("shows consolidated data while official detail is queued", () => {
        renderPanel({ data: data({ coverage_level: "consolidated", status: "partial", enrichment: { status: "queued", run_id: 8, last_error: null, updated_at: null } }) });

        expect(screen.getByText("Consolidated")).toBeInTheDocument();
        expect(screen.getByText(/Official SEC detail is being checked/)).toBeInTheDocument();
        expect(screen.getByTestId("financial-flow-chart")).toBeInTheDocument();
    });

    it("offers a status refresh after the bounded polling window", async () => {
        const user = userEvent.setup();
        const onRetry = vi.fn();
        renderPanel({
            data: data({ enrichment: { status: "pending_refresh", run_id: 8, last_error: null, updated_at: null } }),
            onRetry,
        });

        expect(screen.getByText(/still processing/)).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Refresh status" }));
        expect(onRetry).toHaveBeenCalledOnce();
    });

    it("keeps a refresh error visible when existing data is retained", () => {
        renderPanel({ error: "The selected report could not be refreshed." });

        expect(screen.getByRole("alert")).toHaveTextContent("The selected report could not be refreshed.");
        expect(screen.getByTestId("financial-flow-chart")).toBeInTheDocument();
    });

    it("does not manufacture a Sankey for a negative or unreconciled period", () => {
        renderPanel({ data: data({ chart_available: false, links: [] }) });

        expect(screen.queryByTestId("financial-flow-chart")).not.toBeInTheDocument();
        expect(screen.getByText(/cannot be represented as a non-negative Sankey flow/)).toBeInTheDocument();
    });

    it("explains why TTM is not synthesized and lets the user choose a reported frequency", async () => {
        const user = userEvent.setup();
        const onTimePeriodChange = vi.fn();
        renderPanel({ timePeriod: "ttm", onTimePeriodChange });

        expect(screen.getByText(/TTM segment mixes are not combined/)).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Quarterly" }));
        expect(onTimePeriodChange).toHaveBeenCalledWith("quarterly");
    });

    it("returns a structured unsupported state for financial companies", () => {
        renderPanel({ data: data({ status: "unsupported", coverage_level: "none", unsupported_reason: "Financial companies require an industry-specific flow template.", chart_available: false, links: [], nodes: [], summary_cards: [] }) });

        expect(screen.getByText(/industry-specific flow template/)).toBeInTheDocument();
        expect(screen.queryByTestId("financial-flow-chart")).not.toBeInTheDocument();
    });
});
