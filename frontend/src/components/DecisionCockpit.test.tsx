import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DecisionCockpit from "@/components/DecisionCockpit";
import type { DecisionSupportResponse, DecisionValuation } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
    calculateDecisionValuation: vi.fn(),
    savePersonalValuationScenarios: vi.fn(),
    resetPersonalValuationScenarios: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
    const original = await importOriginal<typeof import("@/lib/api")>();
    return {
        ...original,
        ...apiMocks,
    };
});

const scenarios = [
    { scenario: "bear" as const, fcf_growth_rate: 0.05, wacc: 0.105, perpetual_growth: 0.02 },
    { scenario: "base" as const, fcf_growth_rate: 0.10, wacc: 0.09, perpetual_growth: 0.025 },
    { scenario: "bull" as const, fcf_growth_rate: 0.15, wacc: 0.08, perpetual_growth: 0.03 },
];

const valuation: DecisionValuation = {
    available: true,
    unavailable_reasons: [],
    inputs: { fcf: 100, cash: 50, debt: 10, shares: 10, financial_statement_date: "2025-12-31" },
    current_price: 100,
    scenario_source: "default",
    scenarios: scenarios.map((assumptions, index) => ({
        scenario: assumptions.scenario,
        assumptions,
        available: true,
        intrinsic_value_per_share: 80 + index * 20,
        upside_downside: -0.2 + index * 0.2,
    })),
    position: { status: "between_bear_base", text: "Price is between the Bear- and Base-case intrinsic values." },
    sensitivity: {
        growth_values: [0, 0.05, 0.10, 0.15, 0.20],
        wacc_values: [0.07, 0.08, 0.09, 0.10, 0.11],
        terminal_growth: 0.025,
        values: Array.from({ length: 5 }, (_, row) => Array.from({ length: 5 }, (_, column) => 80 + row * 5 - column * 3)),
        cell_reasons: Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => null)),
    },
    formula: { forecast_years: 5, cash_treatment: "added", debt_treatment: "deducted", terminal_value: "formula" },
};

const metric = {
    key: "sales_growth_ttm",
    label: "Sales growth (TTM)",
    direction: "higher_better" as const,
    format: "percent" as const,
    evidence_id: "E7",
    value: 0.20,
    industry: { scope: "industry" as const, minimum_observations: 10, observation_count: 12, available: true, raw_percentile: 80, desirability_percentile: 80, reason: null },
    sector: { scope: "sector" as const, minimum_observations: 20, observation_count: 40, available: true, raw_percentile: 75, desirability_percentile: 75, reason: null },
    summary_scope: "industry" as const,
    summary_percentile: 80,
};

const lowerIsBetterMetric = {
    ...metric,
    key: "debt_to_equity",
    label: "Debt / equity",
    direction: "lower_better" as const,
    format: "ratio" as const,
    evidence_id: "E26",
    value: 2.30,
    industry: { ...metric.industry, raw_percentile: 90, desirability_percentile: 10 },
    sector: { ...metric.sector, raw_percentile: 90, desirability_percentile: 10 },
    summary_percentile: 10,
};

const unavailableMetric = {
    ...metric,
    key: "roic",
    label: "Return on invested capital",
    format: "percent" as const,
    value: null,
    industry: { ...metric.industry, available: false, raw_percentile: null, desirability_percentile: null, reason: "The ticker does not have a valid value for this metric." },
    sector: { ...metric.sector, available: false, raw_percentile: null, desirability_percentile: null, reason: "The ticker does not have a valid value for this metric." },
    summary_percentile: null,
};

const summaryMetric = {
    key: metric.key,
    label: metric.label,
    value: metric.value,
    format: metric.format,
    direction: metric.direction,
    scope: "industry" as const,
    desirability_percentile: 80,
    evidence_id: "E7",
};

const warning = {
    id: "fcf_decline",
    severity: "high" as const,
    title: "Negative free cash flow",
    message: "TTM free cash flow is negative.",
    metric: "fcf",
    current: -10,
    previous: 20,
    evidence_metric: "fcf" as const,
    evidence_id: "E30",
};

const decision: DecisionSupportResponse = {
    metadata: {
        ticker: "AAA.US", company_name: "Alpha", currency: "USD", industry: "Software", sector: "Technology",
        price_date: "2026-01-02", screener_date: "2025-12-31", screener_published_at: "2026-01-01",
        financial_statement_date: "2025-12-31", factor_date: "2025-12-31", factor_published_at: "2026-01-01",
    },
    summary: {
        valuation_position: valuation.position,
        strongest_peer_metrics: [summaryMetric],
        weakest_peer_metrics: [],
        fundamental_warnings: [warning],
        coverage: { quarterly_statements: 8, peer_metrics_available: 1, peer_metrics_total: 20, published_factor_count: 5, missing_data_reasons: [], data_quality_notes: [] },
    },
    valuation,
    peer_comparison: {
        ticker_in_screener: true, industry: "Software", sector: "Technology", industry_member_count: 12, sector_member_count: 40,
        metrics: [metric], strongest: [summaryMetric], weakest: [], available_metric_count: 1, total_metric_count: 20,
    },
    risks: { warnings: [warning], data_quality_notes: [], high_count: 1, warning_count: 0 },
    evidence: [{ id: "E1", kind: "price", label: "Current price", value: 100, source_date: "2026-01-02", available: true }],
};

const props = () => ({
    ticker: "AAA.US",
    decision,
    loading: false,
    error: "",
    adminKey: null,
    onUnlock: vi.fn(),
    onUnauthorized: vi.fn(),
    onRetry: vi.fn(),
    onRefresh: vi.fn(async () => undefined),
    onShowEvidence: vi.fn(),
});

describe("DecisionCockpit", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.calculateDecisionValuation.mockResolvedValue(valuation);
        apiMocks.savePersonalValuationScenarios.mockResolvedValue({ ticker: "AAA.US", is_saved: true, scenarios });
        apiMocks.resetPersonalValuationScenarios.mockResolvedValue({ ticker: "AAA.US", is_saved: false, scenarios });
    });

    it("keeps personal saves locked while allowing stateless scenario calculation", async () => {
        const user = userEvent.setup();
        const componentProps = props();
        render(<DecisionCockpit {...componentProps} />);

        await user.click(screen.getByRole("button", { name: "Valuation" }));
        expect(screen.getByRole("button", { name: /Unlock to save/ })).toBeInTheDocument();

        const growth = screen.getByRole("spinbutton", { name: "bear FCF growth" });
        await user.clear(growth);
        await user.type(growth, "6");
        await user.click(screen.getByRole("button", { name: "Calculate" }));
        expect(apiMocks.calculateDecisionValuation).toHaveBeenCalledWith(
            "AAA.US",
            expect.arrayContaining([expect.objectContaining({ scenario: "bear", fcf_growth_rate: 0.06 })]),
        );

        await user.click(screen.getByRole("button", { name: /Unlock to save/ }));
        expect(componentProps.onUnlock).toHaveBeenCalledTimes(1);
        expect(apiMocks.savePersonalValuationScenarios).not.toHaveBeenCalled();
    });

    it("formats price and intrinsic values in the ticker currency", () => {
        render(
            <DecisionCockpit
                {...props()}
                decision={{
                    ...decision,
                    metadata: { ...decision.metadata, currency: "JPY" },
                }}
            />,
        );

        expect(screen.getByText("¥80")).toBeInTheDocument();
        expect(screen.queryByText("$80.00")).not.toBeInTheDocument();
    });

    it("shows unavailable warning coverage instead of a clean result", async () => {
        const user = userEvent.setup();
        const dataQualityNote = {
            code: "revenue_comparison_unavailable",
            message: "Revenue history is incomplete.",
        };
        const partialDecision: DecisionSupportResponse = {
            ...decision,
            summary: {
                ...decision.summary,
                fundamental_warnings: [],
                coverage: {
                    ...decision.summary.coverage,
                    data_quality_notes: [dataQualityNote],
                },
            },
            risks: {
                warnings: [],
                data_quality_notes: [dataQualityNote],
                high_count: 0,
                warning_count: 0,
            },
            evidence: [
                ...decision.evidence,
                {
                    id: "E27",
                    kind: "fundamental_warning",
                    label: "Revenue Decline",
                    value: { triggered: null, assessment: "unavailable" },
                    source_date: "2025-12-31",
                    available: false,
                },
            ],
        };

        render(<DecisionCockpit {...props()} decision={partialDecision} />);

        expect(screen.getByText(/1 fundamental warning check is unavailable/)).toBeInTheDocument();
        expect(screen.queryByText(/fully evaluated history/)).not.toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "Risks" }));
        expect(screen.getByText(/1 fundamental warning check is unavailable/)).toBeInTheDocument();
        expect(screen.getByText("Revenue history is incomplete.")).toBeInTheDocument();
    });

    it("pauses the evidence brief while calculated scenarios are unsaved", async () => {
        const user = userEvent.setup();
        apiMocks.calculateDecisionValuation.mockImplementationOnce(
            async (_ticker: string, inputs: typeof scenarios) => ({
                ...valuation,
                scenario_source: "request",
                scenarios: valuation.scenarios.map((item, index) => ({
                    ...item,
                    assumptions: inputs[index],
                })),
            }),
        );
        render(<DecisionCockpit {...props()} />);

        await user.click(screen.getByRole("button", { name: "Valuation" }));
        const growth = screen.getByRole("spinbutton", { name: "bear FCF growth" });
        await user.clear(growth);
        await user.type(growth, "6");
        await user.click(screen.getByRole("button", { name: "Calculate" }));
        await waitFor(() => expect(apiMocks.calculateDecisionValuation).toHaveBeenCalled());

        await user.click(screen.getByRole("button", { name: "Evidence Brief" }));
        expect(screen.getByRole("status")).toHaveTextContent("Brief paused");
        expect(screen.getByText(/working assumptions that are not in the server evidence/)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Generate brief" })).not.toBeInTheDocument();
    });

    it("discards a valuation response after the ticker changes", async () => {
        const user = userEvent.setup();
        let resolveOldCalculation!: (value: DecisionValuation) => void;
        apiMocks.calculateDecisionValuation.mockReturnValueOnce(
            new Promise((resolve) => {
                resolveOldCalculation = resolve;
            }),
        );
        const { rerender } = render(<DecisionCockpit {...props()} />);
        await user.click(screen.getByRole("button", { name: "Valuation" }));
        await user.click(screen.getByRole("button", { name: "Calculate" }));
        await waitFor(() => {
            expect(apiMocks.calculateDecisionValuation).toHaveBeenCalledTimes(1);
        });

        const nextValuation: DecisionValuation = {
            ...valuation,
            scenarios: valuation.scenarios.map((item) => ({
                ...item,
                intrinsic_value_per_share: (item.intrinsic_value_per_share ?? 0) + 100,
            })),
        };
        rerender(
            <DecisionCockpit
                {...props()}
                ticker="BBB.US"
                decision={{
                    ...decision,
                    metadata: {
                        ...decision.metadata,
                        ticker: "BBB.US",
                        company_name: "Beta",
                    },
                    valuation: nextValuation,
                }}
            />,
        );
        await waitFor(() => expect(screen.getByText("$180.00")).toBeInTheDocument());

        const staleValuation: DecisionValuation = {
            ...valuation,
            scenarios: valuation.scenarios.map((item) => ({
                ...item,
                intrinsic_value_per_share: 999,
            })),
        };
        await act(async () => {
            resolveOldCalculation(staleValuation);
            await Promise.resolve();
        });

        expect(screen.queryByText("$999.00")).not.toBeInTheDocument();
        expect(screen.getByText("$180.00")).toBeInTheDocument();
    });

    it("saves and resets scenarios when the workspace is unlocked", async () => {
        const user = userEvent.setup();
        const componentProps = { ...props(), adminKey: "secret" };
        render(<DecisionCockpit {...componentProps} />);
        await user.click(screen.getByRole("button", { name: "Valuation" }));
        await user.click(screen.getByRole("button", { name: "Save scenarios" }));
        expect(apiMocks.savePersonalValuationScenarios).toHaveBeenCalledWith("AAA.US", scenarios, "secret");
        await user.click(screen.getByRole("button", { name: "Reset defaults" }));
        expect(apiMocks.resetPersonalValuationScenarios).toHaveBeenCalledWith("AAA.US", "secret");
        expect(apiMocks.calculateDecisionValuation).toHaveBeenLastCalledWith("AAA.US", scenarios);
    });

    it("locks scenario inputs while a save is in flight", async () => {
        const user = userEvent.setup();
        let resolveSave!: (value: { ticker: string; is_saved: boolean; scenarios: typeof scenarios }) => void;
        apiMocks.savePersonalValuationScenarios.mockReturnValueOnce(
            new Promise((resolve) => {
                resolveSave = resolve;
            }),
        );
        render(<DecisionCockpit {...props()} adminKey="secret" />);
        await user.click(screen.getByRole("button", { name: "Valuation" }));
        const growth = screen.getByRole("spinbutton", { name: "bear FCF growth" });
        await user.click(screen.getByRole("button", { name: "Save scenarios" }));
        await waitFor(() => {
            expect(apiMocks.savePersonalValuationScenarios).toHaveBeenCalled();
        });
        expect(growth).toBeDisabled();

        await act(async () => {
            resolveSave({ ticker: "AAA.US", is_saved: true, scenarios });
            await Promise.resolve();
        });
        await waitFor(() => expect(growth).not.toBeDisabled());
    });

    it("clears a saved valuation while default recalculation is pending", async () => {
        const user = userEvent.setup();
        let resolveCalculation!: (value: DecisionValuation) => void;
        apiMocks.calculateDecisionValuation.mockReturnValueOnce(new Promise((resolve) => {
            resolveCalculation = resolve;
        }));
        const savedValuation: DecisionValuation = {
            ...valuation,
            scenario_source: "saved",
            scenarios: valuation.scenarios.map((item) => ({
                ...item,
                intrinsic_value_per_share: 999,
            })),
        };
        const componentProps = {
            ...props(),
            adminKey: "secret",
            decision: { ...decision, valuation: savedValuation },
        };
        render(<DecisionCockpit {...componentProps} />);
        await user.click(screen.getByRole("button", { name: "Valuation" }));
        expect(screen.getAllByText("$999.00")).toHaveLength(3);

        await user.click(screen.getByRole("button", { name: "Reset defaults" }));
        await waitFor(() => expect(apiMocks.resetPersonalValuationScenarios).toHaveBeenCalled());
        expect(screen.queryByText("$999.00")).not.toBeInTheDocument();

        resolveCalculation(valuation);
        await waitFor(() => expect(screen.getAllByText("$80.00").length).toBeGreaterThan(0));
        expect(componentProps.onRefresh).toHaveBeenCalled();
    });

    it("shows a bounded error when reset succeeds but default recalculation fails", async () => {
        const user = userEvent.setup();
        apiMocks.calculateDecisionValuation.mockRejectedValueOnce(new Error("calculation timed out"));
        const componentProps = { ...props(), adminKey: "secret" };
        render(<DecisionCockpit {...componentProps} />);
        await user.click(screen.getByRole("button", { name: "Valuation" }));
        await user.click(screen.getByRole("button", { name: "Reset defaults" }));

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "Scenarios were reset, but defaults could not be recalculated: calculation timed out",
        );
        expect(screen.getByText("Saved scenarios reset to defaults.")).toBeInTheDocument();
        expect(componentProps.onRefresh).toHaveBeenCalled();
    });

    it("keeps fractional and negative scenario drafts editable until calculation", async () => {
        const user = userEvent.setup();
        render(<DecisionCockpit {...props()} />);
        await user.click(screen.getByRole("button", { name: "Valuation" }));

        const growth = screen.getByRole("spinbutton", { name: "bear FCF growth" });
        const wacc = screen.getByRole("spinbutton", { name: "bear WACC" });
        await user.clear(growth);
        await user.type(growth, "-5");
        await user.clear(wacc);
        await user.type(wacc, "10.5");

        expect(growth).toHaveValue(-5);
        expect(wacc).toHaveValue(10.5);
        await user.click(screen.getByRole("button", { name: "Calculate" }));
        expect(apiMocks.calculateDecisionValuation).toHaveBeenCalledWith(
            "AAA.US",
            expect.arrayContaining([
                expect.objectContaining({ scenario: "bear", fcf_growth_rate: -0.05, wacc: 0.105 }),
            ]),
        );
    });

    it("switches peer scope and sends risk evidence to the financial chart", async () => {
        const user = userEvent.setup();
        const componentProps = props();
        render(<DecisionCockpit {...componentProps} />);
        await user.click(screen.getByRole("button", { name: "Peer Benchmarks" }));
        await user.click(screen.getByRole("button", { name: "sector" }));
        expect(screen.getByText(/40 valid/)).toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "Risks" }));
        await user.click(screen.getByRole("button", { name: /Show evidence/ }));
        expect(componentProps.onShowEvidence).toHaveBeenCalledWith("fcf");
    });

    it("colors better-positioned percentiles and keeps unavailable metrics neutral", async () => {
        const user = userEvent.setup();
        const componentProps = props();
        componentProps.decision = {
            ...decision,
            peer_comparison: {
                ...decision.peer_comparison,
                metrics: [metric, lowerIsBetterMetric, unavailableMetric],
            },
        };
        render(<DecisionCockpit {...componentProps} />);
        await user.click(screen.getByRole("button", { name: "Peer Benchmarks" }));

        expect(screen.getByTitle("Strong Better-positioned percentile")).toHaveClass("text-emerald-800");
        expect(screen.getByTitle("Weak Better-positioned percentile")).toHaveClass("text-rose-800");
        expect(screen.getByTitle("Unavailable Better-positioned percentile")).toHaveClass("text-slate-500");
        expect(screen.getByRole("group", { name: "Better-positioned legend" })).toHaveTextContent("75–100 strong");
    });
});
