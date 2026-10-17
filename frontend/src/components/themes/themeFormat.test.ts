import { describe, expect, it } from "vitest";

import {
    displayTicker,
    formatHeat,
    formatMultiple,
    formatPointChange,
    formatRankChange,
    formatShare,
    formatSigned,
    seriesChange,
    sparkValues,
    tickerHref,
    toneClass,
} from "./themeFormat";

describe("theme formatters", () => {
    it("render missing values as an em dash, never zero", () => {
        for (const value of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
            expect(formatHeat(value)).toBe("—");
            expect(formatShare(value)).toBe("—");
            expect(formatPointChange(value)).toBe("—");
            expect(formatSigned(value)).toBe("—");
            expect(formatMultiple(value)).toBe("—");
            expect(formatRankChange(value)).toBe("—");
            expect(toneClass(value)).toBe("text-flat");
        }
    });

    it("formats signed values with a true minus sign and unsigned zero", () => {
        expect(formatSigned(1.234)).toBe("+1.23");
        expect(formatSigned(-0.5, 1)).toBe("−0.5");
        expect(formatSigned(-0.001)).toBe("0.00");
        expect(formatPointChange(0.12)).toBe("+12 pts");
        expect(formatPointChange(-0.034, 1)).toBe("−3.4 pts");
        expect(formatPointChange(0.001)).toBe("0 pts");
        expect(formatShare(0.825, 1)).toBe("82.5%");
        expect(formatMultiple(1.3)).toBe("1.30×");
        expect(formatHeat(78.44)).toBe("78.4");
    });

    it("shows rank improvements as up arrows", () => {
        expect(formatRankChange(3)).toBe("↑3");
        expect(formatRankChange(-2)).toBe("↓2");
        expect(formatRankChange(0)).toBe("0");
        expect(toneClass(3)).toBe("text-up");
        expect(toneClass(-2)).toBe("text-down");
        expect(toneClass(0)).toBe("text-flat");
    });

    it("keeps sparkline gaps out of the drawn line and measures direction on finite points", () => {
        expect(sparkValues([1, null, 3])).toEqual([1, Number.NaN, 3]);
        expect(seriesChange([null, 60, null, 72.5])).toBe(12.5);
        expect(seriesChange([null, 60])).toBeNull();
    });

    it("links tickers to Analysis and drops the US suffix for display", () => {
        expect(displayTicker("BRK-B.US")).toBe("BRK-B");
        expect(displayTicker("7203.TSE")).toBe("7203.TSE");
        expect(tickerHref("BRK-B.US")).toBe("/?ticker=BRK-B.US");
    });
});
