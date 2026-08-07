import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FieldControl, parseColumns, parsePage, updateScreenerFilters } from "./page";
import { ScreenerField, ScreenerFilter } from "@/lib/screener";

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
    it("shows human-readable comparison labels while preserving API operator values", () => {
        render(
            <FieldControl
                field={{
                    ...numericField,
                    operators: ["lt", "lte", "gt", "gte", "between"],
                }}
                onChange={vi.fn()}
            />,
        );

        expect(screen.getByRole("option", { name: "< Less than" })).toHaveValue("lt");
        expect(screen.getByRole("option", { name: "≤ At most" })).toHaveValue("lte");
        expect(screen.getByRole("option", { name: "> Greater than" })).toHaveValue("gt");
        expect(screen.getByRole("option", { name: "≥ At least" })).toHaveValue("gte");
        expect(screen.getByRole("option", { name: "↔ Between" })).toHaveValue("between");
    });

    it("converts displayed percentages to decimal API values", () => {
        const onChange = vi.fn();
        render(<FieldControl field={numericField} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText("Return on Equity value"), { target: { value: "15" } });
        expect(onChange).toHaveBeenLastCalledWith({ field: "roe", operator: "gte", value: 0.15 });
    });

    it("preserves a negative numeric draft until it is complete", () => {
        const onChange = vi.fn();
        render(<FieldControl field={numericField} onChange={onChange} />);
        const input = screen.getByLabelText("Return on Equity value");

        fireEvent.change(input, { target: { value: "-" } });
        expect(input).toHaveValue("-");
        expect(onChange).not.toHaveBeenCalled();

        fireEvent.change(input, { target: { value: "-10" } });
        expect(onChange).toHaveBeenLastCalledWith({ field: "roe", operator: "gte", value: -0.1 });
    });

    it("preserves a trailing decimal separator until the number is complete", () => {
        const onChange = vi.fn();
        render(<FieldControl field={numericField} onChange={onChange} />);
        const input = screen.getByLabelText("Return on Equity value");

        fireEvent.change(input, { target: { value: "10." } });
        expect(input).toHaveValue("10.");
        expect(onChange).not.toHaveBeenCalled();

        fireEvent.change(input, { target: { value: "10.5" } });
        expect(onChange).toHaveBeenLastCalledWith({
            field: "roe",
            operator: "gte",
            value: 0.105,
        });
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

        expect(screen.getByLabelText("Return on Equity value")).toHaveValue("10");
        fireEvent.change(screen.getByLabelText("Return on Equity maximum"), { target: { value: "20" } });
        expect(onChange).toHaveBeenLastCalledWith({
            field: "roe",
            operator: "between",
            value: [0.1, 0.2],
        });
    });

    it("disables additional enum values at the API limit", () => {
        const options = Array.from({ length: 101 }, (_, index) => ({
            value: `industry_${index}`,
            label: `Industry ${index}`,
        }));
        const field: ScreenerField = {
            ...numericField,
            id: "industry",
            label: "Industry",
            type: "enum",
            unit: "text",
            operators: ["in"],
            options,
        };
        render(
            <FieldControl
                field={field}
                filter={{
                    field: "industry",
                    operator: "in",
                    value: options.slice(0, 100).map((option) => option.value),
                }}
                onChange={vi.fn()}
            />,
        );
        expect(screen.getByRole("checkbox", { name: "Industry 99" })).toBeEnabled();
        expect(screen.getByRole("checkbox", { name: "Industry 100" })).toBeDisabled();
    });
});

describe("parsePage", () => {
    it.each([
        [null, 0],
        ["abc", 0],
        ["1.5", 0],
        ["0", 0],
        ["3", 2],
        ["20002", 20_000],
    ])("normalizes %s to %s", (value, expected) => {
        expect(parsePage(value)).toBe(expected);
    });
});

describe("parseColumns", () => {
    it("deduplicates and caps shared URL columns at the API limit", () => {
        const columns = Array.from({ length: 31 }, (_, index) => `field_${index}`);
        expect(parseColumns([...columns, "field_0"].join(","))).toEqual(columns.slice(0, 30));
    });
});

describe("updateScreenerFilters", () => {
    it("caps new filters at the API limit while allowing replacements", () => {
        const filters: ScreenerFilter[] = Array.from({ length: 64 }, (_, index) => ({
            field: `field_${index}`,
            operator: "gte",
            value: index,
        }));

        expect(updateScreenerFilters(filters, "field_64", {
            field: "field_64",
            operator: "gte",
            value: 64,
        })).toBe(filters);
        expect(updateScreenerFilters(filters, "field_0", {
            field: "field_0",
            operator: "gte",
            value: 100,
        })).toHaveLength(64);
        expect(updateScreenerFilters(filters, "field_0")).toHaveLength(63);
    });
});
