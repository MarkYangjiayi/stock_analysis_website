import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { DecisionImpliedExpectations } from "@/lib/api";
import ImpliedExpectationsPanel from "./ImpliedExpectationsPanel";

const data: DecisionImpliedExpectations = {
    available: true,
    reasons: [],
    model: { forecast_years: 10, terminal_growth: 0.025 },
    required_returns: [0.08, 0.1, 0.12],
    default_required_return: 0.1,
    references: {
        history: { revenue_growth: 0.107, revenue_growth_years: 5, operating_margin_low: 0.257, operating_margin_high: 0.308 },
        consensus: { next_year_growth: 0.405, following_year_growth: 0.457, operating_margin: 0.3 },
    },
    inputs: { operating_margin_ttm: 0.324, sales_to_capital: 0.39 },
    hurdles: [
        {
            required_return: 0.08,
            growth_by_margin: [
                { basis: "trailing", target_margin: 0.324, revenue_growth: 0.248 },
                { basis: "consensus", target_margin: 0.3, revenue_growth: 0.293 },
                { basis: "five_year_high", target_margin: 0.308, revenue_growth: 0.276 },
            ],
            margin_at_consensus_growth: 0.276,
            sales_to_capital_at_consensus: 0.34,
        },
        {
            required_return: 0.1,
            growth_by_margin: [
                { basis: "trailing", target_margin: 0.324, revenue_growth: 0.435 },
                { basis: "consensus", target_margin: 0.3, revenue_growth: 0.544 },
                { basis: "five_year_high", target_margin: 0.308, revenue_growth: 0.499 },
            ],
            margin_at_consensus_growth: 0.35,
            sales_to_capital_at_consensus: 0.49,
        },
        {
            required_return: 0.12,
            growth_by_margin: [
                { basis: "trailing", target_margin: 0.324, revenue_growth: 0.919 },
                { basis: "consensus", target_margin: 0.3, revenue_growth: null },
                { basis: "five_year_high", target_margin: 0.308, revenue_growth: null },
            ],
            margin_at_consensus_growth: 0.427,
            sales_to_capital_at_consensus: 0.7,
        },
    ],
    implied_return: { status: "solved", rate: 0.087, minimum_rate: 0.03, margin_basis: "consensus", target_margin: 0.3 },
};

const lever = (label: string) => within(screen.getByText(label).closest("div")!.parentElement!);

describe("ImpliedExpectationsPanel", () => {
    it("leads with the return consensus offers and shows each lever against its evidence", () => {
        render(<ImpliedExpectationsPanel data={data} />);

        expect(screen.getByText("8.7%")).toBeInTheDocument();
        expect(screen.getByText("−1.3 pp vs 10% required")).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "To earn 10% a year" })).toBeInTheDocument();

        const growth = lever("Revenue growth, 5 years");
        expect(growth.getByText("at consensus margin 30.0%")).toBeInTheDocument();
        expect(growth.getByText("54.4%")).toBeInTheDocument();
        expect(growth.getByText("Consensus 40.5% to 45.7% · 5Y history 10.7%")).toBeInTheDocument();
        const margin = lever("Operating margin");
        expect(margin.getByText("35.0%")).toBeInTheDocument();
        expect(margin.getByText("Now 32.4% · consensus 30.0% · 5Y 25.7% to 30.8%")).toBeInTheDocument();
        const capital = lever("Revenue per $1 of capital");
        expect(capital.getByText("0.49×")).toBeInTheDocument();
        expect(capital.getByText("Now 0.39×")).toBeInTheDocument();
        expect(screen.getByText("Growth at trailing margin 32.4%").nextElementSibling).toHaveTextContent("43.5%");
    });

    it("re-solves the hurdles for another required return and marks levers with no solution", async () => {
        const user = userEvent.setup();
        render(<ImpliedExpectationsPanel data={data} />);

        await user.click(screen.getByRole("button", { name: "12%" }));

        expect(screen.getByText("−3.3 pp vs 12% required")).toBeInTheDocument();
        expect(lever("Revenue growth, 5 years").getByText("No solution")).toBeInTheDocument();
        expect(lever("Operating margin").getByText("42.7%")).toBeInTheDocument();
        expect(screen.getByText("Growth at 5Y-high margin 30.8%").nextElementSibling).toHaveTextContent("No solution");
    });

    it("solves only the growth hurdle without consensus", () => {
        render(<ImpliedExpectationsPanel data={{
            ...data,
            references: { ...data.references, consensus: null },
            implied_return: { status: "unavailable", rate: null, minimum_rate: 0.03, margin_basis: "trailing", target_margin: 0.324 },
        }} />);

        expect(screen.getByText("No current consensus")).toBeInTheDocument();
        expect(lever("Revenue growth, 5 years").getByText("43.5%")).toBeInTheDocument();
        expect(lever("Revenue growth, 5 years").getByText("5Y history 10.7%")).toBeInTheDocument();
        expect(screen.queryByText("Operating margin")).not.toBeInTheDocument();
    });

    it("says when the return is below the lowest rate solved", () => {
        render(<ImpliedExpectationsPanel data={{
            ...data,
            implied_return: { status: "below_range", rate: null, minimum_rate: 0.03, margin_basis: "consensus", target_margin: 0.3 },
        }} />);

        expect(screen.getByText("< 3%")).toBeInTheDocument();
        expect(screen.getByText("Below the lowest rate solved")).toBeInTheDocument();
    });

    it("lists the reasons when the model has no inputs", () => {
        render(<ImpliedExpectationsPanel data={{
            available: false,
            reasons: ["The model needs stockholders' equity."],
            model: data.model,
            required_returns: data.required_returns,
            default_required_return: 0.1,
            references: { history: {}, consensus: null },
        }} />);

        expect(screen.getByText("The model needs stockholders' equity.")).toBeInTheDocument();
        expect(screen.queryByText("Return if consensus holds")).not.toBeInTheDocument();
    });
});
