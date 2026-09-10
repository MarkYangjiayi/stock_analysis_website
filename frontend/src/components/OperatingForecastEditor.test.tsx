import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import OperatingForecastEditor from "./OperatingForecastEditor";
import type { DecisionValuationScenarioInput } from "@/lib/api";

type Draft = Pick<DecisionValuationScenarioInput, "scenario" | "operating_forecast" | "terminal_roic" | "forecast_as_of"> & { fcf_growth_rate: string; wacc: string; perpetual_growth: string };
function Harness() {
    const [rows, setRows] = useState<Draft[]>(["bear", "base", "bull"].map(s => ({ scenario: s as Draft["scenario"], fcf_growth_rate: "10", wacc: "9", perpetual_growth: "2.5" })));
    return <><OperatingForecastEditor scenarios={rows} disabled={false} onChange={setRows} /><output data-testid="draft">{JSON.stringify(rows)}</output></>;
}

describe("operating forecast inputs", () => {
    it("preserves currency units, case separation and source metadata", async () => {
        const user = userEvent.setup();
        render(<Harness />);
        await user.click(screen.getByText("Annual operating forecast"));
        await user.click(screen.getByRole("checkbox"));
        expect(screen.getByLabelText("base year 1 Revenue")).toHaveValue(null);
        await user.type(screen.getByLabelText("base year 1 Revenue"), "123");
        await user.type(screen.getByLabelText("base year 1 EBIT margin %"), "25");
        await user.type(screen.getByLabelText("base forecast source"), "Company guidance and investment plan");
        const rows = JSON.parse(screen.getByTestId("draft").textContent!);
        expect(rows[1].operating_forecast[0].revenue).toBe(123_000_000);
        expect(rows[1].operating_forecast[0].operating_margin).toBe(.25);
        expect(rows[1].operating_forecast[9].source).toBe("Company guidance and investment plan");
        expect(rows[0].operating_forecast[0].revenue).toBeNull();
        await user.click(screen.getByRole("button", { name: "bear" }));
        expect(screen.getByLabelText("bear year 1 Revenue")).toHaveValue(null);
        await user.click(screen.getByRole("checkbox"));
        expect(JSON.parse(screen.getByTestId("draft").textContent!)[1].operating_forecast).toBeUndefined();
    });
});
