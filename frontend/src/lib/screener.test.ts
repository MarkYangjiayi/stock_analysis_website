import { describe, expect, it } from "vitest";

import { decodeFilters, encodeFilters, filterLabel, formatScreenerValue, ScreenerField } from "./screener";

const percentField: ScreenerField = {
    id: "roe",
    label: "Return on Equity",
    category: "Fundamental",
    type: "number",
    unit: "percent",
    operators: ["gte"],
    presets: [],
    options: [],
    coverage: 1,
    available: true,
    default_column: false,
};

describe("screener URL state", () => {
    it("round-trips typed filters", () => {
        const filters = [{ field: "roe", operator: "gte" as const, value: 0.15 }];
        expect(decodeFilters(encodeFilters(filters))).toEqual(filters);
    });

    it("fails closed for malformed state", () => {
        expect(decodeFilters("{bad")).toEqual([]);
    });
});

describe("screener formatting", () => {
    it("converts decimal rates to human percentages", () => {
        expect(formatScreenerValue(0.156, percentField)).toBe("16%");
        expect(filterLabel({ field: "roe", operator: "gte", value: 0.15 }, percentField))
            .toBe("Return on Equity ≥ 15%");
    });
});
