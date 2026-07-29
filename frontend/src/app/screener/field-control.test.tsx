import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FieldControl, parsePage } from "./page";
import { ScreenerField } from "@/lib/screener";

const numericField: ScreenerField = {
    id: "roe",
    label: "Return on Equity",
    category: "Fundamental",
    type: "number",
    unit: "percent",
    operators: ["gte", "between"],
    presets: [],
    options: [],
    coverage: 1,
    available: true,
    default_column: false,
    result_column: true,
};

describe("FieldControl", () => {
    it("converts displayed percentages to decimal API values", () => {
        const onChange = vi.fn();
        render(<FieldControl field={numericField} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText("Return on Equity value"), { target: { value: "15" } });
        expect(onChange).toHaveBeenLastCalledWith({ field: "roe", operator: "gte", value: 0.15 });
    });

    it("supports a custom between range", () => {
        const onChange = vi.fn();
        render(
            <FieldControl
                field={numericField}
                filter={{ field: "roe", operator: "between", value: [0.1, 0.2] }}
                onChange={onChange}
            />,
        );
        fireEvent.change(screen.getByLabelText("Return on Equity maximum"), { target: { value: "25" } });
        expect(onChange).toHaveBeenLastCalledWith({ field: "roe", operator: "between", value: [0.1, 0.25] });
    });

    it("keeps the first draft bound until a new between range is complete", () => {
        const onChange = vi.fn();
        render(<FieldControl field={numericField} onChange={onChange} />);

        fireEvent.change(screen.getByLabelText("Return on Equity operator"), { target: { value: "between" } });
        fireEvent.change(screen.getByLabelText("Return on Equity value"), { target: { value: "10" } });

        expect(screen.getByLabelText("Return on Equity value")).toHaveValue(10);
        fireEvent.change(screen.getByLabelText("Return on Equity maximum"), { target: { value: "20" } });
        expect(onChange).toHaveBeenLastCalledWith({
            field: "roe",
            operator: "between",
            value: [0.1, 0.2],
        });
    });
});

describe("parsePage", () => {
    it.each([
        [null, 0],
        ["abc", 0],
        ["1.5", 0],
        ["0", 0],
        ["3", 2],
    ])("normalizes %s to %s", (value, expected) => {
        expect(parsePage(value)).toBe(expected);
    });
});
