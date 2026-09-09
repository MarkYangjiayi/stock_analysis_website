import { describe, expect, it } from "vitest";

import { formatCompactNumber, formatMoney, formatSignedPercent } from "./format";

describe("shared numeric formatting", () => {
    it("uses em dashes for unavailable values instead of zero", () => {
        expect(formatMoney(null, "USD")).toBe("—");
        expect(formatSignedPercent(undefined)).toBe("—");
        expect(formatCompactNumber(Number.NaN)).toBe("—");
    });

    it("distinguishes positive, negative and neutral changes", () => {
        expect(formatSignedPercent(0.125)).toBe("+12.50%");
        expect(formatSignedPercent(-0.125)).toBe("−12.50%");
        expect(formatSignedPercent(0)).toBe("0.00%");
    });
});
