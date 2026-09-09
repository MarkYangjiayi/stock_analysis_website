import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AnalysisNavigation from "./AnalysisNavigation";

describe("AnalysisNavigation", () => {
    it("exposes one accessible research tablist and changes sections", () => {
        const onChange = vi.fn();
        render(<AnalysisNavigation active="overview" onChange={onChange} />);

        expect(screen.getAllByRole("tab")).toHaveLength(5);
        expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
        fireEvent.click(screen.getByRole("tab", { name: "Financials" }));
        expect(onChange).toHaveBeenCalledWith("financials");
    });

    it("supports arrow, home and end keyboard navigation", () => {
        const onChange = vi.fn();
        render(<AnalysisNavigation active="overview" onChange={onChange} />);

        fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), { key: "ArrowLeft" });
        expect(onChange).toHaveBeenLastCalledWith("events");
        fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), { key: "End" });
        expect(onChange).toHaveBeenLastCalledWith("events");
    });
});
