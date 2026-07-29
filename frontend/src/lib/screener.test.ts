import { describe, expect, it } from "vitest";

import {
    decodeFilters,
    encodeFilters,
    filterLabel,
    formatScreenerValue,
    sanitizeFilters,
    ScreenerField,
} from "./screener";

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
    result_column: true,
};

describe("screener URL state", () => {
    it("round-trips typed filters", () => {
        const filters = [{ field: "roe", operator: "gte" as const, value: 0.15 }];
        expect(decodeFilters(encodeFilters(filters))).toEqual(filters);
    });

    it("fails closed for malformed state", () => {
        expect(decodeFilters("{bad")).toEqual([]);
    });

    it("keeps supported filters and drops stale or malformed clauses", () => {
        const filters = [
            { field: "roe", operator: "gte" as const, value: 0.15 },
            { field: "removed", operator: "gte" as const, value: 1 },
            { field: "roe", operator: "between" as const, value: [0.1] },
        ];
        expect(sanitizeFilters(filters, [percentField])).toEqual([filters[0]]);
    });

    it("caps restored URL filters at the API request limit", () => {
        const filters = Array.from({ length: 65 }, (_, index) => ({
            field: "roe",
            operator: "gte" as const,
            value: index,
        }));
        expect(sanitizeFilters(filters, [percentField])).toHaveLength(64);
    });
});

describe("screener formatting", () => {
    it("converts decimal rates to human percentages", () => {
        expect(formatScreenerValue(0.156, percentField)).toBe("15.6%");
        expect(filterLabel({ field: "roe", operator: "gte", value: 0.15 }, percentField))
            .toBe("Return on Equity ≥ 15%");
        expect(filterLabel({ field: "roe", operator: "gte", value: 0.105 }, percentField))
            .toBe("Return on Equity ≥ 10.5%");
    });
});
